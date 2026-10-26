# Exercises API: rest timers, new exercises, exercise settings

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

## New exercises and variants (user-managed)

The app can add exercises that are not in `data/catalog/exercises.yaml` and set up any exercise at
the current gym (e.g. a new gym with no machines yet). Bearer token like the other APIs. All three
calls are online-only in the app (never queued); the app writes the answer into its local cache
right away and the next pull confirms it.

### POST /api/v1/exercises

```json
{"name": "Pec Deck", "muscle_group": "chest", "equipment_type": "machine_stack",
 "timed": false, "gym_id": 6, "increment_kg": 2.5}
```

- `muscle_group`: `chest | lats | back | traps | shoulders | rear_delts | biceps | triceps | forearms |
  quads | hamstrings | glutes | adductors | calves | abs` (sets `body_region`).
- `equipment_type`: `barbell | dumbbell | machine_stack | plate_loaded | cable | bodyweight | smith | other`.
- `timed` (default false): logged in seconds. `increment_kg` (optional, `0 < x <= 50`, rounded to
  0.25): the variant's load step. `gym_id` (optional): the gym of a machine, default the current gym.
- Creates an `Exercise` with slug `user_<name>` (outside the catalog: the catalog sync never touches
  it) and its first variant: machines (`machine_stack | plate_loaded | cable | smith`) at the gym as
  `"<Name> (<Machine|Plate Loaded|Cable|Smith Machine>) @ <gym>"`, everything else generic as
  `"<Name> (<Barbell|Dumbbell|Bodyweight>)"` / `"<Name>"` for `other`.
- `201` `{"exercise": Exercise, "variant": Variant}` (the pull shapes, see `docs/api/sync.md`).
  `409` if an exercise with that name (case-insensitive) exists, `422` for an invalid body, `404`
  for an unknown gym.

### POST /api/v1/exercises/{exercise_id}/variants

```json
{"equipment_type": "cable", "gym_id": 6}
```

The variant of the exercise with that equipment, **idempotent**: an existing live one is returned
(for machines: the exercise's active variant at that gym, whatever its type), a tombstoned one is
revived (same id), otherwise a new one is made. Machines go through the gyms API rules
(`docs/api/gyms.md`: deterministic key, program exercises keep the program's name so the plan finds
them) and re-run the engine. Generic variants get a deterministic key too
(uuid5 over exercise id + equipment), and the Strong importer never tombstones them.
`200` Variant (pull shape), `404` unknown exercise / gym, `422` invalid body.

### PATCH /api/v1/exercises/{exercise_id}

```json
{"muscle_group": "shoulders"}
```

-> `200` Exercise. Marks the exercise `edited_by_user`, so a later catalog sync keeps the user's
muscle. `404` / `422` as above.

### Variant settings: PATCH /api/v1/variants/{id}

The gyms API's variant PATCH (`docs/api/gyms.md`) also takes `equipment_type` (any type above) and
`increment_kg` (`0 < x <= 50`, rounded to 0.25; `null` = back to the ladder / equipment default).
`increment_kg` is the flat load step of that variant: the app's +/- buttons and the engine's
progression use it instead of the weight ladder and the equipment default (a program
`increment_kg` in `program.yaml` only applies when the variant has none). Both re-run the engine.

### In the pull

- `Exercise` gains `body_region` (`Chest | Back | Shoulders | Arms | Legs | Core | Other`),
  `default_equipment` and `is_timed`.
- `Variant` gains `increment_kg` (null = default steps).

### iOS behaviour (for reference)

- Add / Swap exercise: every exercise, in sections by body region, searchable. An exercise without a
  usable variant at the current gym shows "Not set up here yet"; picking it asks for the equipment
  and calls `POST /exercises/{id}/variants`. "New exercise" calls `POST /exercises`.
- "Edit exercise" (exercise menu in the workout): muscle (`PATCH /exercises/{id}`), type and weight
  step (`PATCH /variants/{id}`).
