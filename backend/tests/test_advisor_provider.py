"""The Claude Code (subscription) provider with a mocked subprocess; the real CLI is never run.

Covers provider selection, the argv / env of the process, the JSON envelope, schema validation
plus the single retry, and the failure mapping (auth, usage limit, timeout, exit codes, secrets).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import pytest

from app.advisor import claude
from app.advisor.prompt import SYSTEM_PROMPT
from app.advisor.providers import claude_code as cc
from app.advisor.providers import provider_status
from app.settings import Settings
from app.sources.garmin.redact import register_secret

TOKEN = "tok-subscription-0123456789abcdef"
KEY = "sk-ant-api03-test-0000000000000000"

ANSWER = {
    "diagnosis": "Fine.",
    "proposed_changes": [
        {
            "param": "strength.rep_range",
            "from": [7, 12],
            "to": [8, 12],
            "reason": "r",
            "risk": "low",
        }
    ],
    "session_overrides": [],
    "confidence": "high",
    "notes_for_user": "Keep going.",
}


def make_settings(**kw) -> Settings:
    kw.setdefault("claude_code_oauth_token", TOKEN)
    return Settings(_env_file=None, **kw)


def envelope(result=None, *, is_error=False, **extra) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": json.dumps(ANSWER) if result is None else result,
        "stop_reason": "end_turn",
        "session_id": "sess-1",
        "total_cost_usd": 0.5,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 7,
        },
        "modelUsage": {"claude-opus-5-5": {}},
        **extra,
    }


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, hang=False):
        self.stdout, self.stderr, self.returncode, self.hang = stdout, stderr, returncode, hang
        self.killed = False
        self.stdin_seen = b""

    async def communicate(self, data=None):
        self.stdin_seen = data or b""
        if self.hang:
            await asyncio.sleep(30)
        return self.stdout.encode(), self.stderr.encode()

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class Spawner:
    """Replaces asyncio.create_subprocess_exec; hands out the queued FakeProcs in order."""

    def __init__(self, *procs):
        self.procs = list(procs)
        self.calls: list[tuple[tuple, dict]] = []
        self.spawned: list[FakeProc] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        proc = self.procs.pop(0)
        self.spawned.append(proc)
        return proc


@pytest.fixture
def spawn(monkeypatch):
    def install(*procs):
        sp = Spawner(*procs)
        monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", sp)
        monkeypatch.setattr(cc.shutil, "which", lambda name: f"/opt/bin/{name}")
        return sp

    return install


def ok_proc(**kw):
    return FakeProc(json.dumps(envelope(**kw)))


# --------------------------------------------------------------------------- selection


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"claude_code_oauth_token": TOKEN, "anthropic_api_key": KEY}, "claude_code"),
        ({"claude_code_oauth_token": TOKEN}, "claude_code"),
        ({"claude_code_oauth_token": None, "anthropic_api_key": KEY}, "api"),
        ({"claude_code_oauth_token": None}, "none"),
        ({"claude_code_oauth_token": "  ", "anthropic_api_key": " "}, "none"),
        ({"advisor_provider": "api", "anthropic_api_key": KEY}, "api"),
        ({"advisor_provider": "api"}, "none"),  # a token alone does not pick the API
        ({"advisor_provider": "claude_code", "claude_code_oauth_token": None}, "none"),
        ({"advisor_provider": "claude_code", "anthropic_api_key": KEY}, "claude_code"),
    ],
)
def test_provider_selection(kw, expected):
    s = make_settings(**kw)
    assert s.advisor_provider_resolved == expected
    # `advisor_key` is what the service tests against None ("is the advisor usable?")
    assert (s.advisor_key is not None) == (expected != "none")
    st = provider_status(s)
    assert st["provider"] == expected
    assert st["credential_configured"] is (expected != "none")
    assert st["billing"] == {"api": "api", "claude_code": "subscription", "none": None}[expected]
    assert TOKEN not in json.dumps(st) and KEY not in json.dumps(st)


def test_auto_is_the_default():
    assert Settings(_env_file=None).advisor_provider == "auto"


def test_consult_routes_to_the_cli_unless_a_client_is_given(spawn):
    sp = spawn(ok_proc())
    result = claude.consult(make_settings(anthropic_api_key=KEY), "hello")
    assert result.outcome == "ok" and len(sp.calls) == 1

    class Boom:
        class beta:
            class messages:
                @staticmethod
                def parse(**kw):
                    raise RuntimeError("api path")

    with pytest.raises(RuntimeError, match="api path"):
        claude.consult(make_settings(), "hello", client=Boom())
    assert len(sp.calls) == 1  # no second process


# --------------------------------------------------------------------------- process


def test_argv_has_no_shell_and_the_prompt_goes_on_stdin(spawn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    sp = spawn(ok_proc())
    claude.consult(make_settings(advisor_model="claude-x"), "USER-DATA-MARKER")
    (args, kwargs), proc = sp.calls[0], sp.spawned[0]
    assert all(isinstance(a, str) for a in args)
    assert args[0] == "/opt/bin/claude"
    assert "shell" not in kwargs
    assert args[1] == "-p"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "claude-x"
    assert args[args.index("--tools") + 1] == ""  # no tools at all
    assert args[args.index("--max-turns") + 1] == "1"
    assert args[args.index("--system-prompt") + 1] == SYSTEM_PROMPT
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--bare" not in args  # --bare never reads the OAuth token
    assert not any("USER-DATA-MARKER" in a for a in args)
    assert b"USER-DATA-MARKER" in proc.stdin_seen
    assert b'"proposed_changes"' in proc.stdin_seen  # schema comes from AdvisorOutput at runtime
    assert kwargs["cwd"] != "." and kwargs["stdin"] == asyncio.subprocess.PIPE


def test_env_is_minimal_and_never_carries_the_api_key(spawn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("SOME_SERVER_SECRET", "x")
    sp = spawn(ok_proc())
    claude.consult(make_settings(anthropic_api_key=KEY), "hello")
    env = sp.calls[0][1]["env"]
    assert set(env) == {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_AUTOUPDATER"}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert env["HOME"] != "~" and "advisor-cli-" in env["HOME"]
    assert KEY not in json.dumps(env)


def test_missing_cli(monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    with pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(), "hello")
    assert exc.value.kind == "cli_missing"


# --------------------------------------------------------------------------- success


def test_envelope_is_parsed_into_a_result(spawn):
    spawn(ok_proc())
    r = claude.consult(make_settings(), "hello")
    assert r.outcome == "ok"
    assert r.output.proposed_changes[0].param == "strength.rep_range"
    assert r.output.proposed_changes[0].from_ == [7, 12]
    assert r.stop_reason == "end_turn" and r.model == "claude-opus-5-5"
    assert r.request_id == "sess-1"
    assert r.usage["input_tokens"] == 100 and r.usage["output_tokens"] == 20
    assert r.usage["cache_read_input_tokens"] == 5 and r.usage["cache_creation_input_tokens"] == 7
    assert r.usage["billing"] == "subscription" and r.usage["cli_total_cost_usd"] == 0.5


def test_json_is_found_in_fences_and_prose(spawn):
    text = "Here you go:\n```json\n" + json.dumps(ANSWER) + "\n```\nHope it helps."
    spawn(ok_proc(result=text))
    assert claude.consult(make_settings(), "hello").outcome == "ok"


def test_structured_output_field_is_used_when_present(spawn):
    spawn(ok_proc(result="", structured_output=ANSWER))
    assert claude.consult(make_settings(), "hello").output.confidence == "high"


def test_list_shaped_output_picks_the_result_message(spawn):
    spawn(FakeProc(json.dumps([{"type": "system"}, envelope()])))
    assert claude.consult(make_settings(), "hello").outcome == "ok"


def test_truncated_and_refused_answers_do_not_retry(spawn):
    sp = spawn(ok_proc(stop_reason="max_tokens", result='{"diagnosis": "cut'))
    assert claude.consult(make_settings(), "hello").outcome == "max_tokens"
    assert len(sp.calls) == 1
    sp = spawn(ok_proc(stop_reason="refusal", result="I can't help"))
    r = claude.consult(make_settings(), "hello")
    assert r.outcome == "refusal" and r.output is None


def test_empty_answer(spawn):
    spawn(ok_proc(result=""))
    assert claude.consult(make_settings(), "hello").outcome == "empty"


def test_works_inside_a_running_event_loop(spawn):
    spawn(ok_proc())

    async def go():
        return claude.consult(make_settings(), "hello")

    assert asyncio.run(go()).outcome == "ok"


# --------------------------------------------------------------------------- validation + retry


def test_invalid_answer_is_retried_once_with_the_error(spawn):
    bad = json.dumps({"diagnosis": "x"})  # missing required fields
    sp = spawn(ok_proc(result=bad), ok_proc())
    r = claude.consult(make_settings(), "hello")
    assert r.outcome == "ok" and len(sp.calls) == 2
    second = sp.spawned[1].stdin_seen.decode()
    assert "previous answer was rejected" in second
    assert "proposed_changes" in second  # the validation error names the missing field
    assert r.usage["input_tokens"] == 200 and r.usage["cli_total_cost_usd"] == 1.0  # summed


def test_prose_without_json_is_retried_once(spawn):
    sp = spawn(ok_proc(result="Sure, happy to help!"), ok_proc())
    assert claude.consult(make_settings(), "hello").outcome == "ok"
    assert len(sp.calls) == 2


def test_second_invalid_answer_gives_up(spawn):
    sp = spawn(ok_proc(result="{}"), ok_proc(result="{}"), ok_proc())
    with pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(), "hello")
    assert exc.value.kind == "invalid_output" and len(sp.calls) == 2
    assert len(sp.procs) == 1  # no third attempt


# --------------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    "env_kw,stderr",
    [
        ({"result": "Not logged in · Please run /login", "is_error": True}, ""),
        (
            {
                "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
                "is_error": True,
                "api_error_status": 401,
            },
            "",
        ),
        ({"result": "OAuth token has expired", "is_error": True, "api_error_status": 401}, ""),
    ],
)
def test_not_logged_in_maps_to_auth(spawn, env_kw, stderr):
    spawn(FakeProc(json.dumps(envelope(**env_kw)), stderr, returncode=1))
    with pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(), "hello")
    assert exc.value.kind == "auth"
    assert "setup-token" in exc.value.message


def test_auth_without_a_token_does_not_spawn(spawn):
    sp = spawn()
    s = make_settings(claude_code_oauth_token=None, advisor_provider="claude_code")
    with pytest.raises(claude.AdvisorCallError) as exc:
        cc.ClaudeCodeProvider(s).consult("hello")
    assert exc.value.kind == "auth" and sp.calls == []


@pytest.mark.parametrize(
    "env_kw",
    [
        {"result": "Claude AI usage limit reached|1760000000", "is_error": True},
        {"result": "You've hit your limit · resets 5pm", "is_error": True},
        {"result": "API Error: 429 rate limited", "is_error": True, "api_error_status": 429},
    ],
)
def test_usage_limit_maps_to_usage_limit(spawn, env_kw):
    spawn(FakeProc(json.dumps(envelope(**env_kw)), returncode=1))
    with pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(), "hello")
    assert exc.value.kind == "usage_limit"
    assert exc.value.message == "Subscription usage limit reached, retry later"


def test_timeout_kills_the_process(spawn):
    sp = spawn(FakeProc(hang=True))
    with pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(advisor_cli_timeout_s=0.05), "hello")
    assert exc.value.kind == "timeout"
    assert sp.spawned[0].killed


@pytest.fixture
def caplog(monkeypatch):
    """Own handler on the provider logger: other tests' logging config (alembic fileConfig) can
    disable loggers or stop propagation, which would make pytest's caplog miss records."""

    class Collect(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record):
            self.records.append(record)

        @property
        def text(self):
            return "\n".join(r.getMessage() for r in self.records)

        def at_level(self, level):
            return contextlib.nullcontext()

    handler = Collect()
    monkeypatch.setattr(cc.log, "disabled", False)
    monkeypatch.setattr(cc.log, "level", logging.DEBUG)
    cc.log.addHandler(handler)
    yield handler
    cc.log.removeHandler(handler)


def test_nonzero_exit_is_a_cli_error_and_secrets_stay_out_of_logs(spawn, caplog):
    register_secret(TOKEN)
    noisy = f"boom: CLAUDE_CODE_OAUTH_TOKEN={TOKEN} Authorization: Bearer {TOKEN} {KEY}"
    spawn(FakeProc("not json at all " + TOKEN, noisy, returncode=3))
    with caplog.at_level(logging.DEBUG), pytest.raises(claude.AdvisorCallError) as exc:
        claude.consult(make_settings(), "hello")
    assert exc.value.kind == "cli_error" and "exit 3" in exc.value.message
    assert TOKEN not in exc.value.message and KEY not in exc.value.message
    assert any("claude cli stderr" in r.getMessage() for r in caplog.records)
    for r in caplog.records:
        assert TOKEN not in r.getMessage() and KEY not in r.getMessage()
    assert TOKEN not in caplog.text and KEY not in caplog.text


def test_success_logs_never_contain_the_token(spawn, caplog):
    spawn(ok_proc())
    with caplog.at_level(logging.DEBUG):
        claude.consult(make_settings(), "hello")
    assert TOKEN not in caplog.text
