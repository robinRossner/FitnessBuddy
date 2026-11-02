"""Advisor provider that runs headless Claude Code (`claude -p`) on a Claude subscription.

Why: the Messages API cannot bill a Pro/Max subscription, Claude Code can. `claude setup-token`
(run by the user, interactive browser login) prints a long-lived OAuth token (about one year);
exported as CLAUDE_CODE_OAUTH_TOKEN it lets `claude -p` run unattended on the server.

CLI flags used (all verified against Claude Code 2.1.310, `claude --help`):
  -p / --print                     non-interactive, prompt on stdin
  --output-format json             one JSON envelope (result, is_error, usage, total_cost_usd, ...)
  --model <id>                     ADVISOR_MODEL
  --tools ""                       no tools at all: a pure text completion
  --max-turns 1                    (accepted, hidden from --help)
  --system-prompt <text>           REPLACES Claude Code's own system prompt with the frozen one
  --setting-sources ""             no user / project / local settings
  --no-session-persistence, --disable-slash-commands, --strict-mcp-config, --effort <level>
Not used: --bare (never reads the OAuth token) and --json-schema (structured output is delivered
through an extra tool call, which does not fit "no tools, one turn"; the JSON is prompt-enforced
and validated against the Pydantic schema instead, with one retry).

The subprocess gets an empty temp cwd, a temp HOME and exactly PATH, HOME, the OAuth token and
DISABLE_AUTOUPDATER: never ANTHROPIC_API_KEY (it outranks the subscription token and would bill
the API), never the rest of the server environment. No shell is involved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from apps.fitness.advisor.prompt import SYSTEM_PROMPT
from apps.fitness.advisor.providers.base import AdvisorCallError, ClaudeResult
from apps.fitness.advisor.schema import AdvisorOutput
from apps.fitness.settings import Settings
from core.redact import install_log_redaction, redact, register_secret

log = logging.getLogger(__name__)

AUTH_MESSAGE = (
    "The Claude subscription login is missing or expired. Run `claude setup-token` on your Mac "
    "and put the new CLAUDE_CODE_OAUTH_TOKEN into backend/.env.production, then redeploy."
)
USAGE_LIMIT_MESSAGE = "Subscription usage limit reached, retry later"

_AUTH_RE = re.compile(
    r"not logged in|/login|login expired|token (has )?expired|token is invalid|"
    r"access token is invalid|failed to authenticate|authentication[_ ]error|"
    r"invalid (x-)?api key|invalid bearer|unauthorized|oauth token (is )?(invalid|revoked)",
    re.I,
)
_LIMIT_RE = re.compile(
    r"usage limit|hit your limit|limit reached|rate[ _]limit|too many requests|"
    r"out of (extra )?usage|session limit|weekly limit|resets? (at )?\d",
    re.I,
)
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

SCHEMA_INSTRUCTION = (
    "\n\n---\nAnswer with exactly one JSON object and nothing else: no markdown fences, no "
    "prose before or after. It must validate against this JSON Schema (use the property names "
    'exactly as written, e.g. "from"):\n'
)


@dataclass
class CliRun:
    """The outcome of one `claude -p` process."""

    returncode: int | None
    stdout: str
    stderr: str


@dataclass
class _Attempt:
    envelope: dict[str, Any]
    text: str
    usage: dict[str, Any] = field(default_factory=dict)


def output_schema_json() -> str:
    """The answer schema, built from the model at call time (never copied)."""
    return json.dumps(
        AdvisorOutput.model_json_schema(by_alias=True), separators=(",", ":"), sort_keys=True
    )


def build_prompt(
    user_text: str, schema_json: str, *, previous: str | None, error: str | None
) -> str:
    prompt = user_text + SCHEMA_INSTRUCTION + schema_json
    if previous is not None and error is not None:
        prompt += (
            "\n\n---\nYour previous answer was rejected:\n"
            f"{error[:1500]}\n\nPrevious answer (truncated):\n{previous[:6000]}\n\n"
            "Answer again with one corrected JSON object only."
        )
    return prompt


def extract_json_object(text: str) -> Any:
    """The first JSON object in `text` (tolerates ``` fences and surrounding prose)."""
    decoder = json.JSONDecoder()
    pos = text.find("{")
    while pos != -1:
        try:
            value, _ = decoder.raw_decode(text, pos)
        except ValueError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(value, dict):
            return value
        pos = text.find("{", pos + 1)
    raise ValueError("no JSON object found in the answer")


def parse_envelope(stdout: str) -> dict[str, Any] | None:
    """The `result` message of `--output-format json` (an object, or a list of messages)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(data, list):
        data = next(
            (m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), None
        )
    return data if isinstance(data, dict) else None


def classify_failure(
    envelope: dict[str, Any] | None, stdout: str, stderr: str, returncode: int | None
) -> AdvisorCallError:
    """Map a failed run to AdvisorCallError(kind=auth | usage_limit | cli_error)."""
    status = (envelope or {}).get("api_error_status")
    text = str((envelope or {}).get("result") or "") or f"{stdout[:300]} {stderr[:300]}"
    if status == 429 or _LIMIT_RE.search(text):
        return AdvisorCallError("usage_limit", USAGE_LIMIT_MESSAGE, 429)
    if status in (401, 403) or _AUTH_RE.search(text):
        return AdvisorCallError("auth", AUTH_MESSAGE, status if isinstance(status, int) else 401)
    detail = redact(text.strip())[:300] or "no output"
    return AdvisorCallError(
        "cli_error",
        f"Claude Code failed (exit {returncode}): {detail}",
        status if isinstance(status, int) else None,
    )


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync code, also when called from inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class ClaudeCodeProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ------------------------------------------------------------------ process

    def argv(self, exe: str) -> list[str]:
        s = self.settings
        return [
            exe,
            "-p",
            "--output-format",
            "json",
            "--model",
            s.advisor_model,
            "--tools",
            "",
            "--max-turns",
            "1",
            "--system-prompt",
            SYSTEM_PROMPT,
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--effort",
            s.advisor_cli_effort,
        ]

    @staticmethod
    def env(token: str, home: str) -> dict[str, str]:
        """Exactly what the CLI needs. ANTHROPIC_API_KEY must never be here (it would win)."""
        return {
            "PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin",
            "HOME": home,
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "DISABLE_AUTOUPDATER": "1",
        }

    async def _run_cli(self, prompt: str, token: str) -> CliRun:
        s = self.settings
        exe = shutil.which(s.advisor_cli_path)
        if exe is None:
            raise AdvisorCallError(
                "cli_missing", f"The Claude Code CLI (`{s.advisor_cli_path}`) is not installed."
            )
        with tempfile.TemporaryDirectory(prefix="advisor-cli-") as tmp:
            home, cwd = Path(tmp, "home"), Path(tmp, "cwd")
            home.mkdir()
            cwd.mkdir()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.argv(exe),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(cwd),
                    env=self.env(token, str(home)),
                )
            except OSError as exc:
                raise AdvisorCallError(
                    "cli_missing", redact(f"Could not start the Claude Code CLI: {exc}")
                ) from None
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(prompt.encode()), timeout=s.advisor_cli_timeout_s
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise AdvisorCallError(
                    "timeout",
                    f"Claude Code did not answer within {s.advisor_cli_timeout_s:.0f} s.",
                ) from None
        return CliRun(
            proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        )

    # ------------------------------------------------------------------ one attempt

    async def _attempt(self, prompt: str, token: str) -> _Attempt:
        run = await self._run_cli(prompt, token)
        envelope = parse_envelope(run.stdout)
        failed = run.returncode != 0 or envelope is None or bool(envelope.get("is_error"))
        if failed:
            if run.stderr.strip():
                log.warning("claude cli stderr: %s", redact(run.stderr.strip())[:500])
            raise classify_failure(envelope, run.stdout, run.stderr, run.returncode)
        return _Attempt(envelope, self._text(envelope), _usage(envelope))

    @staticmethod
    def _text(envelope: dict[str, Any]) -> str:
        structured = envelope.get("structured_output")
        if structured is not None:
            return json.dumps(structured)
        return str(envelope.get("result") or "")

    async def _consult(self, user_text: str) -> ClaudeResult:
        token = self.settings.advisor_oauth_token
        if token is None:
            raise AdvisorCallError("auth", AUTH_MESSAGE)
        install_log_redaction()
        register_secret(token)
        schema_json = output_schema_json()

        total: dict[str, Any] = {k: 0 for k in _USAGE_KEYS}
        cost = 0.0
        previous = error = None
        last: _Attempt | None = None
        output: AdvisorOutput | None = None
        for attempt in (1, 2):
            last = await self._attempt(
                build_prompt(user_text, schema_json, previous=previous, error=error), token
            )
            for k in _USAGE_KEYS:
                total[k] += last.usage.get(k, 0)
            cost += float(last.envelope.get("total_cost_usd") or 0.0)
            stop = last.envelope.get("stop_reason")
            if stop in ("refusal", "max_tokens") or not last.text.strip():
                break  # nothing usable to validate or retry
            try:
                output = AdvisorOutput.model_validate(extract_json_object(last.text))
                break
            except (ValidationError, ValueError) as exc:
                previous, error = last.text, str(exc)
                log.warning(
                    "claude cli answer rejected (attempt %d): %s", attempt, redact(error)[:300]
                )
        assert last is not None

        stop = last.envelope.get("stop_reason")
        result = ClaudeResult(
            outcome="ok",
            stop_reason=stop,
            usage={
                **total,
                "billing": "subscription",
                "provider": "claude_code",
                "cli_total_cost_usd": round(cost, 6),
            },
            request_id=last.envelope.get("session_id"),
            model=next(iter(last.envelope.get("modelUsage") or {}), None)
            or self.settings.advisor_model,
        )
        if stop == "refusal":
            result.outcome = "refusal"
        elif stop == "max_tokens":
            result.outcome = "max_tokens"
        elif not last.text.strip():
            result.outcome = "empty"
        elif output is None:
            raise AdvisorCallError(
                "invalid_output", redact(f"Claude's answer did not match the schema: {error}")[:500]
            )
        else:
            result.output = output
        log.info(
            "advisor call (claude_code): %s stop=%s in=%d out=%d cache_read=%d cache_write=%d",
            result.outcome,
            stop,
            total["input_tokens"],
            total["output_tokens"],
            total["cache_read_input_tokens"],
            total["cache_creation_input_tokens"],
        )
        return result

    def consult(self, user_text: str) -> ClaudeResult:
        return _run_sync(self._consult(user_text))


def _usage(envelope: dict[str, Any]) -> dict[str, int]:
    u = envelope.get("usage") or {}
    return {k: int(u.get(k) or 0) for k in _USAGE_KEYS}
