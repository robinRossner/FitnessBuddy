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

## PUT /api/v1/program/{kind}

"Update template" after a finished strength workout: the exercise list the user actually did
becomes the lifting template of that day kind. `kind` is `upper` or `lower` (else `422`). Bearer
token like the other APIs.

```json
{"exercises": [{"variant_id": 122, "sets": 3}, {"variant_id": 57}]}
```

- `exercises`: non-empty, in order. `variant_id` is the integer `ExerciseVariant.id` (JSON number,
  as in `/sync/pull`); `sets` is optional (1..10; default: the old slot's sets, else 3). An unknown
  or deleted variant is `404`; the same exercise twice is `422`.
- Optional `slot` (additive, 2026-11-23): the plan item's `slot` (the program entry's variant name).
  When it names a current entry of that kind, the entry is **kept as it is** (only `sets` applies)
  whatever `variant_id` says: the app sends it for a substitute the engine chose for a slot that
  is not set up at this gym, and for such a slot that could not be done at all (then without
  `variant_id`). Each item needs `variant_id` or `slot` (else `422`); a `slot` that is not in the
  template and no `variant_id` is `422`. An exercise the user swapped in explicitly is sent without
  `slot`, so it replaces the default.
- `sets` is the template's base count: the app sends the plan's `template_sets`, not the reduced
  count of a deload or comeback week.
- The server rewrites only `days.<kind>` of the **live program file** (comments and the other keys
  stay), after copying the old file to `program.backups/program_YYYYmmdd-HHMMSS.yaml` (last 20 are
  kept) and writes atomically. Slots are `{exercise, variant (name without " @ gym"), sets}`;
  `rep_range` / `increment_kg` / `note` carry over from the old slot of the same exercise.
  The live file is `program.yaml` next to the database (`backend/var/` locally, `deploy/data/` =
  `/app/var` on the server, so a deploy never overwrites it).
- Then the engine re-plans (as `POST /engine/run`): future planned sessions use the new list.
- **Idempotent**: an identical list writes nothing (`backup` is `null`).

Response `200`:

```json
{"kind": "upper",
 "exercises": [{"exercise": "chest_fly", "variant": "Chest Fly (Machine)", "sets": 3, "rep_range": [10, 15]}],
 "backup": "program_20261029-101500.yaml"}
```

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

The app can add exercises that are not in `apps/fitness/data/catalog/exercises.yaml` and set up any exercise at
the current gym (e.g. a new gym with no machines yet). Bearer token like the other APIs. All three
calls are online-only in the app (never queued); the app writes the answer into its local cache
right away and the next pull confirms it.

### POST /api/v1/exercises

```json
{"name": "Pec Deck", "muscle_group": "chest", "equipment_type": "machine_stack",
 "timed": false, "gym_id": 6, "increment_kg": 2.5}
```

- `muscle_group`: `chest | lats | back | traps | shoulders | biceps | triceps | forearms |
  quads | hamstrings | glutes | adductors | calves | abs` (sets `body_region`). `rear_delts` (merged into `shoulders`) is still accepted and stored as `shoulders`.
- `equipment_type`: `barbell | dumbbell | machine_stack | plate_loaded | cable | bodyweight | smith | other`.
- `progression` (optional, `load | reps | time`, default `load`): how the exercise progresses, see
  "Progression mode" below. `timed` (optional, default false): logged in seconds; `timed: true`
  alone means `progression: "time"`. Both given and contradicting (`timed: true` with `load` /
  `reps`, `timed: false` with `time`) is `422`. `increment_kg` (optional, `0 < x <= 50`, rounded to
  0.25): the variant's load step. `gym_id` (optional): the gym of a machine, default the current gym.
- Creates an `Exercise` with slug `user_<name>` (outside the catalog: the catalog sync never touches
  it) and its first variant: machines (`machine_stack | plate_loaded | cable | smith`) at the gym as
  `"<Name> (<Machine|Plate Loaded|Cable|Smith Machine>) @ <gym>"`, everything else generic as
  `"<Name> (<Barbell|Dumbbell|Bodyweight>)"` / `"<Name>"` for `other`.
- `201` `{"exercise": Exercise, "variant": Variant}` (the pull shapes, see `docs/api/sync.md`).
  `422` for an invalid body, `404` for an unknown gym.
- **Names may repeat when the equipment differs.** If an exercise with that name
  (case-insensitive) exists and has no variant with this equipment yet (at this gym for machines),
  the variant is added to that exercise and the existing exercise is returned (its muscle,
  `timed` and `progression` win over the request's): the same movement keeps its history together. Only the same
  name **and** equipment (at the same gym for machines) is a duplicate: `409`. One edge: a gym has
  one machine per exercise, so a machine of another type at a gym where the same-named exercise
  already has one becomes its own `Exercise` row with the same name (slug `user_<name>_2`).

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
{"name": "Pec Fly", "muscle_group": "shoulders", "progression": "reps"}
```

All keys are optional (at least one is useful), `name` is 1..100 characters (whitespace is
collapsed), `progression` is `load | reps`. -> `200` Exercise. Marks the exercise `edited_by_user`
for a changed muscle or progression, so a later catalog sync keeps the user's name, muscle and
progression. `progression: "time"` and any change of a timed exercise's progression is `422`
(timed exercises log durations: switching would break their history; `time` is set at creation
only). Sending the current value again changes nothing. A changed progression re-runs the engine.
`404` / `422` as above.

**Rename.** The exercise and all its live variants get the new name, so it shows everywhere:
each variant becomes `"<name> (<Machine|Plate Loaded|Cable|Smith Machine|Barbell|Dumbbell|Bodyweight>)"`
(just `"<name>"` for `other`) and keeps its `" @ <gym>"` suffix. A custom variant name (e.g. a
machine named in the gym settings) is replaced too. Ids, keys and sets do not change, so history
and progression stay attached. If the exercise is in the lifting program, the plan line finds its
variant by name, so the rename also writes `exercise_overrides.<slug>.variant` as a config version
(`author: "user"`, rolled back like any other) and re-runs the engine.

The response is the Exercise plus an additive `variants` list (pull shape) with the variants that
were renamed, so the app updates its cache at once; the next pull carries the same rows
(`server_seq`). The `name` of a variant is unique per gym and an exercise name may repeat across
exercises, but only with other equipment:

- `409` if another exercise of that name (case-insensitive) already has a live variant with the
  same equipment at the same gym (for generic equipment: the same equipment), or a variant with the
  new name already exists at that gym. Nothing is changed then.

Catalog exercises: the catalog sync no longer overwrites the name of an `edited_by_user` exercise
(it still updates its other fields). A muscle edit sets the same flag, so it also keeps the name.

### Variant settings: PATCH /api/v1/variants/{id}

The gyms API's variant PATCH (`docs/api/gyms.md`) also takes `equipment_type` (any type above; another type answers with a new variant and `replaced_variant_id`, see there) and
`increment_kg` (`0 < x <= 50`, rounded to 0.25; `null` = back to the ladder / equipment default).
`increment_kg` is the flat load step of that variant: the app's +/- buttons and the engine's
progression use it instead of the weight ladder and the equipment default (a program
`increment_kg` in `program.yaml` only applies when the variant has none). Both re-run the engine.

### In the pull

- `Exercise` gains `body_region` (`Chest | Back | Shoulders | Arms | Legs | Core | Other`),
  `default_equipment`, `is_timed` and `progression` (`load | reps | time`; `is_timed` stays and is
  always `progression == "time"`).
- `Variant` gains `increment_kg` (null = default steps).

### iOS behaviour (for reference)

- Add / Swap exercise: every exercise, in sections by body region, searchable. An exercise without a
  usable variant at the current gym shows "Not set up here yet"; picking it asks for the equipment
  and calls `POST /exercises/{id}/variants`. "New exercise" calls `POST /exercises`.
- "Edit exercise" (exercise menu in the workout): name and muscle (`PATCH /exercises/{id}`, the name
  field is the displayed name without the equipment label, which is added again), type and weight
  step (`PATCH /variants/{id}`). The renamed variants from the answer are written to the local cache
  right away.
- "New exercise" has no client-side duplicate check: the server decides (a same name with other
  equipment is added as a variant of the existing exercise, see above) and its `409` text is shown.

## Progression mode

`Exercise.progression` decides how the engine moves an exercise (decisions.md "Per-exercise
progression mode"):

- `load` (default): double progression, reps first, then the next load rung.
- `reps`: reps only. The plan never prescribes added weight (`target_weight` stays at the logged
  load, 0 for bodyweight); each set's target is that set's reps last time + 1, up to the top of the
  rep range (`strength.reps_rep_range` 10-20 unless the program line or `exercise_overrides` sets
  `rep_range`). At the top it holds (`rule: "reps_progression:at_cap"`, flag `rep_cap`) and the
  `progression_note` suggests a harder variation. It is never counted as a stalled lift.
- `time`: timed holds (`timed_step_s` more than the best hold); set at creation only.

The catalog sets it per exercise (`progression: reps` on `knee_raise`); a user's PATCH wins over
later catalog syncs for `load` / `reps`.
