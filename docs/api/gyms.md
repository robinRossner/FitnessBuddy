# Gyms API: gyms, machines and weight ladders

Machine / cable / smith / plate-loaded exercises are **gym-bound**: each gym has its own variant
(`"Lat Pulldown (Cable) @ Gym 2"`) with its own progression history and its own `weight_ladder`
(sorted kg of the stack). Barbell, dumbbell and bodyweight variants are generic and never appear
here. This API lets the user choose the current gym, add a gym, map each exercise to the machine
of that gym and enter the weight steps of each stack. Sessions and plans then use that gym's
machines. Bearer token like the other APIs, snake_case, base path `/api/v1`.

Real examples (from a copy of the live database): `docs/api/examples/gyms_list.json`,
`docs/api/examples/gym_machines.json` (regenerate: `uv run python -m apps.fitness.scripts.gen_gym_examples`).

## Where the current gym lives

In the versioned engine config as `gym.current_id` (a config version authored by `user`, reason
"Current gym set to Gym 2"). Unset means the old default: the latest entry of `gyms.yaml`, else
the gym of the newest session. Rolling the config back to an older version therefore also rolls
the current gym back. The pull `config` block carries `current_gym_id` and `gyms` (same objects as
`GET /gyms`) so the app always knows them.

## Objects

**Gym**

```json
{"id": 4, "name": "Gym 2", "created_at": "2026-10-24T20:13:13.487Z",
 "machine_count": 15, "is_current": true, "source": "import"}
```

`machine_count`: active machines set up at the gym. `source`: `import` for gyms that come from
`gyms.yaml` (they have a stable key, the importer manages them), `user` for gyms made here.

**MachineItem** (one exercise at one gym)

```json
{"exercise_id": 7, "exercise_name": "Lateral Raise", "body_region": "shoulders",
 "in_program": true, "equipment_type": "cable",
 "variant": {"id": 123, "name": "Lateral Raise (Cable) @ Gym 2", "equipment_type": "cable",
             "weight_ladder": [5.0], "notes": null, "active": true,
             "sessions": 9, "last_used": "2026-10-23"},
 "previous": {"gym_name": "Gym 2", "variant_id": 106,
              "last_top_set": {"weight_kg": 50.0, "reps": 10},
              "weight_ladder": [40.0, 45.0, 50.0]}}
```

- `variant` is `null` when the exercise is not set up at this gym.
- `previous` is `null` or the most recently used variant of the exercise at another gym, shown as
  a hint (`last_top_set` is the heaviest working set of its latest session, may be `null`).
- `equipment_type` of an item without variant is the expected one (the program's variant, else the
  previous gym's, else the exercise default); one of `machine_stack | cable | smith | plate_loaded | other`.

## Gyms

| Call | Result |
| --- | --- |
| `GET /gyms` | `{"current_gym_id": int \| null, "gyms": [Gym]}`, deleted gyms excluded. |
| `POST /gyms` `{"name"}` | `201` Gym. Name unique case-insensitively (`409`), not empty (`422`). A soft-deleted gym with that name comes back. |
| `PATCH /gyms/{id}` `{"name"}` | Gym. `409` for a duplicate name, `404` unknown. The gym's machines are renamed with it (`"X @ Old"` becomes `"X @ New"`; the engine finds machines by that name). Import gyms are renamed again by `gyms.yaml` on the next import. |
| `DELETE /gyms/{id}` | `204`, soft delete. `409` if the gym has sessions or is the current gym. |
| `PUT /gyms/current` `{"gym_id"}` | `{"current_gym_id": int, "unmapped_count": int}`. Stores `gym.current_id` and re-runs the engine for the upcoming planned sessions. **Idempotent** (no new config version for the same gym). `404` for an unknown / deleted gym. |

`unmapped_count` = program exercises that need a machine at that gym and have none (or only an
inactive one). Show it as "N exercises still need a machine".

```
PUT /api/v1/gyms/current   {"gym_id": 6}
-> {"current_gym_id": 6, "unmapped_count": 13}
```

## Machines

`GET /gyms/{id}/machines` -> `{"gym": Gym, "items": [MachineItem]}`. Items are the gym-bound
exercises of the program first (`in_program: true`, in program order, upper before lower), then the
other gym-bound exercises the user has history with (alphabetical).

`POST /gyms/{id}/machines`

```json
{"exercise_id": 7, "name": null, "equipment_type": "cable",
 "weight_ladder": [5, 10, 12.5], "notes": "left side"}
```

-> `201` MachineItem.

- `name` (optional): default is `"<variant name> @ <gym name>"`, where the variant name is the
  program's name for that exercise (`"Lateral Raise (Cable)"`), else the base name of the variant at
  the previous gym, else the exercise name. Keep the default for program exercises: the plan finds
  the machine by the program's name.
- `equipment_type`: `machine_stack | cable | smith | plate_loaded | other`.
- `weight_ladder`, `notes`: optional.
- The variant gets a deterministic key (uuid5 over gym id and exercise id), so creating it twice
  never duplicates it, and a later Strong import of the same machine adopts it instead of creating
  another one. The importer never deletes it, and never retires a gym made here.
- An inactive or tombstoned variant of that exercise at that gym is **revived** (same id, history
  kept). `409` if an active one exists (use `PATCH /variants/{id}`), or if the name is taken at
  that gym. `404` for an unknown gym / exercise.
- If the gym is the current one the engine re-runs, so the "Set up this machine" plan item turns
  into a baseline item.

`PATCH /variants/{id}` body, all optional: `{"name", "weight_ladder", "notes", "active",
"equipment_type", "increment_kg"}` (the last two: `docs/api/exercises.md`) -> the variant:

```json
{"id": 123, "exercise_id": 7, "gym_id": 4, "name": "Lateral Raise (Cable) @ Gym 2",
 "equipment_type": "cable", "weight_ladder": [5.0, 7.5, 10.0], "increment_kg": null, "notes": null,
 "active": true, "sessions": 9, "last_used": "2026-10-23", "replaced_variant_id": null}
```

- `weight_ladder: null` clears the ladder (the importer may infer one from history again).
- Changing the ladder, `active`, `equipment_type` or `increment_kg` re-runs the engine for the
  upcoming sessions.
- A different `equipment_type` makes another exercise (own history and progression): the variant
  is not changed in place. The variant of the same exercise, gym and new type is created (name
  with the new automatic label, `notes`, `increment_kg` and load convention carried over, no
  history) or, if one exists, reactivated, and the old one is set `active=false` (rows and sets
  stay). The answer is the NEW variant and `replaced_variant_id` is the old id (`null` when the
  type did not change); the other fields of the same PATCH apply to the new variant. When the gym
  is the current one, the live program lines (and a per-exercise `variant` override) naming the
  old variant get the new name (backup first). The next pull carries both rows.
- `404` for an unknown / deleted variant, `409` for a name already used at the gym.

### Weight ladder rules (POST and PATCH)

Sorted ascending, unique, `0 < w <= 500`, at most 60 entries, values are rounded to 0.25 kg
(and must still be unique after rounding). Anything else is `422`. An empty list is `422` (send
`null` to clear).

### POST /weight-ladder/preview

Helper to enter a stack quickly.

```json
{"start": 5, "step": 5, "count": 4, "extra": [2.5, 7.5, 22.6]}
-> {"weight_ladder": [2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 22.5]}
```

`start`, `step` > 0, `1 <= count <= 60`; `extra` are added (rounded to 0.25, deduplicated). `422` if
a weight is above 500 or the result has more than 60 entries. Nothing is stored.

## Plan behaviour

- Gym-bound program exercises resolve to the variant at the current gym.
- No variant there: the engine fills the slot with a substitute of the same movement pattern
  usable at this gym (`substitute_for`, see `docs/api/sync.md`); only when there is none is the plan
  item `unresolved: true`, `needs_setup: true`, no target weights, `progression_note` "Set up this
  machine for <gym>".
- A machine with no history gets `progression_state: "baseline"` and `progression_note` "First
  session at this gym: pick a weight for about 10 reps" (plus " (previous gym: 65 kg × 9; machines
  differ)"). No target weight; `hint_weight_kg` is the previous gym's top-set weight, a hint only.
  Misses are censored for its first two sessions (the calibration rule).
- Sessions pushed by the app carry `gym_id`; use the current gym's `id` and the `variant_id`s from
  the plan.

Errors are plain FastAPI `{"detail": "..."}`: `401` bad token, `404` unknown id, `409` conflict,
`422` validation.
