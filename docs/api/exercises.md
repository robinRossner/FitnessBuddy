# Exercises API: per-exercise rest timers

The rest timer is **2:00 for every exercise** (`lifting.rest_default_s: 120` in the engine config).
The user can change it per exercise (e.g. 3:00 for squats). The value lives on the server in the
engine config as `exercise_overrides.<slug>.rest_s`, so it applies to every future workout with that
exercise and survives an app reinstall (the app gets it back with its next pull).

All variants of one exercise (catalog slug) share the setting; the endpoint takes any variant id.
Bearer token like the other APIs.

## PUT /api/v1/exercises/{variant_id}/rest

```json
{"rest_s": 180}
```

- `rest_s`: integer seconds, **30..600**; `null` resets the exercise to the default. The key is
  required (`{}` is a 422, not a reset), floats / strings / booleans are 422.
- Creates a config version (`author: "user"`, reason "Rest time for Squat (Smith Machine) set to
  3:00" / "... reset to the default (2:00)") and makes it active. The variant name is shown without
  its gym suffix.
- **Idempotent**: setting the value the exercise already has (including resetting an exercise that
  has no override) creates no new version and answers the same body.
- Open lifting plan sessions are touched, so the next incremental `GET /sync/pull` re-sends them
  with the new `rest_s`.

Response `200`:

```json
{"variant_id": 122, "rest_s": 180, "is_default": false, "config_version": "v3.4"}
```

`rest_s` is the effective value afterwards (the default after a reset), `is_default` is true when the
exercise has no override. `404` for an unknown or deleted variant, `401` without a valid token,
`422` for an invalid body.

## Where the value shows up

- **Plan items** (`/plan/today`, `/plan/week`, `planned_sessions` in pull): every lifting exercise
  has `rest_s` (resolved when the plan is read). See `docs/api/sync.md`.
- **Sync pull** `config.lifting`: `{"rest_default_s": 120, "rest_s_by_variant": {"122": 180}}`, the
  overrides listed per live variant id of the exercise. The iOS app reconciles its local overrides
  with it on every pull (a local override that is still pending upload is kept).
- **Coach** (`docs/api/coach.md`): `exercise_overrides.<slug>.rest_s` and `lifting.rest_default_s`
  are on the allowlist (30..600 s, always low risk).
- `GET /config` shows the versions; `POST /config/rollback/{version}` undoes a change.

## iOS behaviour (for reference)

Rest used by the workout timer: the local override row for the variant (pending, or the last value reconciled
with the server; a row without a value means "default"), else the plan's `rest_s`, else the synced
`config.lifting.rest_default_s`, else 120. The local row comes first because it is at least as new as the plan
(a running workout started from a plan with the old value). A change is stored locally at once (also applied to a
rest that is currently running for that exercise), sent to this endpoint when online (retried with the next sync
when offline, before the pull), and reconciled with `config.lifting.rest_s_by_variant` on every pull. The +-15 s
buttons stay a one-off change of the current rest.
