# Lifting progression: how other systems do it, and what to do here

Researched 2026-10-25. Confidence tags: **[V]** = checked against a source this session; **[S]** = secondary or marketing source only; **[M]** = from my own background knowledge, not re-verified. Most apps do not publish their algorithms, so the app rows are partly **[S]**/**[M]**. The numbers in section 3 are my engineering defaults, not published constants.

## 1. Comparison

| System | Progression variable | Trigger | Fallback on a miss | Stall handling | Deload | Autoregulation input |
|---|---|---|---|---|---|---|
| **RP Hypertrophy** [S] | Sets per muscle per week (+1 set/week from MEV toward MAV), plus load/reps | Weekly schedule, gated by feedback | Hold or lower volume | Reaching MRV or falling performance ends the block | Planned, about 1 week at roughly half volume, brought forward if feedback is bad. [RP on set increases](https://renaissanceperiodization.com/expert-advice/in-defense-of-set-increases-within-the-hypertrophy-mesocycle), [summary](https://arvo.guru/resources/methods/rp-training) | Soreness, pump, performance and joint feedback; RIR target drops over the mesocycle |
| **Nippard programs** [M] | Double progression inside a rep range | Top of range on all sets, then add load | Repeat load | Block structure with a deload week; intensity techniques (myo-reps, drop sets, partials) on the last set only | Planned, at block end | RPE/RIR written into the program |
| **Alpha Progression** | Double progression on weight/reps, with a RIR target per set | Rep range reached at the target RIR. [glossary](https://alphaprogression.com/en/glossary/double-progression) | Lowers weight or reps [S] | Not documented | Planned deloads can be scheduled in a plan | Logged RIR; the app knows your plates and dumbbell increments |
| **JuggernautAI** | Training max per wave, set by AMRAP reps | The rep-max calculator on the AMRAP result sets the next training max. [help](https://help.jtsstrength.com/en/articles/3-how-juggernautai-is-individualized-to-you), [method](https://arvo.guru/resources/methods/juggernaut-method) | Minimum reps leave the max unchanged | Re-estimate the max each wave | Planned deload week per wave | Pre-session readiness, RPE on top and back-off sets, weekly and block check-ins |
| **Fitbod** [S] | Load or reps from estimated 1RM | Finished the prescribed sets "easily" | Downshifts | Not documented | None documented | Per-muscle recovery model, RIR and "too easy/hard" ratings. [help](https://help.fitbod.me/hc/en-us/articles/360004429814-How-Fitbod-Creates-Your-Workout) |
| **SBS** | Strength programs adjust a training max from sets completed at a RIR target [S]; the hypertrophy template's rules are in a paid spreadsheet | Completing more or fewer sets than planned at the target RIR | The training max drops | Not verified | Built into the blocks [M] | RIR and AMRAP |
| **Boostcamp** | A host for the templates above; some auto-adjust targets | Per template | Per template | Per template | Per template | None of its own |
| **GZCLP** | Load, with a rep-scheme "stage" | Hit every rep in every set, then add load | Miss: move to the next stage (T1 5x3 → 6x2 → 10x1; T2 3x10 → 3x8 → 3x6). [guide](https://www.setforset.com/blogs/news/gzclp-beginner-linear-progression-strength-program), [Boostcamp](https://www.boostcamp.app/methodology/gzcl) (versions vary) | After the last stage, reset to a lower load (about 85% in most guides) and rebuild | None | None |
| **5/3/1** | Training max, +5 lb upper / +10 lb lower per cycle | Cycle completed with rep minimums met | Repeat the cycle | A common rule is to reset the training max by about 10% after repeated failures; this is community practice, not an official source. [thread](https://startingstrength.com/resources/forum/general-programming/36968-advice-reset-531-a-2.html) | Built-in deload week each cycle [M] | AMRAP last set |
| **Hevy Trainer / Strong** | Weight | Top of rep range on all sets. [Hevy](https://help.hevyapp.com/hc/en-us/articles/38385724273047-Hevy-Trainer-Explained-How-It-Builds-Your-Workout-Program) | Repeat | None | None | None. I found nothing on Strong's progression. |

**Takeaways.**
- The strict "all sets at the top of the range" gate is the industry norm for rep-based progression. It is also the main reason a lift stalls: one weak set blocks the jump.
- Nobody in the table detects a stall by e1RM alone. They use a miss counter or a stage change. A "no e1RM gain in 3 exposures" rule is unusual and noisy.
- Systems that avoid RPE (GZCLP, 5/3/1, Hevy) use rep outcomes as the signal. That suits your 0–1 RIR constraint.
- Periodisation lives at the block level (volume ramp, then deload). Per-lift ladders handle the misses.

## 2. Evidence summary

- **Load vs reps.** [Plotkin et al. 2022](https://peerj.com/articles/14142) (43 trained participants, 8 weeks) found similar hypertrophy and endurance gains from progressing load or progressing reps at a fixed load. Strength favoured load slightly (2 kg, with a confidence interval spanning zero). Rep-only progression is therefore viable. This supports adding reps within a range and relaxing the rung jump when a rung is too big.
- **Proximity to failure.** [Refalo et al. 2023](https://dro.deakin.edu.au/articles/journal_contribution/Influence_of_Resistance_Training_Proximity-to-Failure_on_Skeletal_Muscle_Hypertrophy_A_Systematic_Review_with_Meta-analysis/22030796) found only a trivial advantage for training to failure. [Robinson et al. 2024](https://rke.abertay.ac.uk/en/publications/exploring-the-dose-response-relationship-between-estimated-resist/) treated RIR as a continuous variable and found that hypertrophy rises non-linearly as sets get closer to failure, while strength is barely affected. Your fixed 0–1 RIR is defensible for muscle growth. Its cost is recovery: failure produces more fatigue per set [M], and you also run three times a week.
- **Volume.** Hypertrophy shows a dose-response with weekly sets and diminishing returns ([Schoenfeld 2017](https://researchgate.net/profile/Brad-Schoenfeld/publication/305455324_Dose-response_relationship_between_weekly_resistance_training_volume_and_increases_in_muscle_mass_A_systematic_review_and_meta-analysis)). A [Pelland et al. meta-regression](https://sportrxiv.org/index.php/server/preprint/download/537/1148/1071) (preprint, **[S]**) put the plateau near 31 fractional sets per week and found frequency matters little for hypertrophy at equal volume. Strength gain is much less sensitive to volume. Conclusion: volume is a lever worth moving slowly, and more is not automatically better for a runner.
- **Deloads.** [Coleman et al. 2024](https://peerj.com/articles/16777) (39 trained people) tested a full week off at the midpoint of 9 weeks. It did not help, and lower-body strength was slightly worse than in continuous training. The [Bell et al. 2023 Delphi consensus](https://pmc.ncbi.nlm.nih.gov/articles/PMC10511399/) shows that deloading is common practice but under-researched. Planned deloads are therefore not proven, and a complete break is probably the wrong shape. A reduced-volume week is the more defensible form.
- **Advanced techniques.** [Coleman et al. 2022](https://doaj.org/article/11aedadd407647bd869d527e5fdf64a3) (drop sets) found no hypertrophy difference from traditional sets, but sessions were shorter. Rest-pause was likewise similar in one trial (Enes 2021, **[S]**). I found no study isolating myo-reps. Use these techniques as time-efficient extra volume, not as a plateau cure.
- **Exercise rotation.** [Baz-Valle et al. 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6934277) found no strength or size benefit from varying exercises every session, only better motivation. A review described in secondary sources (Kassiano) suggests planned variation may help regional growth and random variation hurts. Rotate accessories slowly. Keep the main lifts stable.
- **Concurrent training.** [Schumann et al. 2022](https://sci-sport.com/en/articles/Concurrent-training-Does-cardio-affect-muscle-mass-and-strength-gains-233.php) (43 studies) found no meaningful interference with maximal strength or whole-muscle hypertrophy, with explosive strength the exception. [Lundberg et al. 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9474354/) saw a small negative effect on type I fibre growth with running (preliminary). The expected cost is acute fatigue in the legs, not a permanent ceiling. Upper-body interference should be minimal.
- **Detraining.** Regaining lost strength and size is faster than building it the first time ([Psilander 2019](https://www.diva-portal.org/smash/get/diva2%3A1336656/FULLTEXT01.pdf) and secondary summaries). The timelines are not firm. I could not verify a "2–4 weeks" figure.

## 3. Scenario rules for this user

The core idea: with a fixed 0–1 RIR, **reps at a given load are the performance measure**. No RPE is needed. Reps also work as an in-session feedback loop. Reps can vary by about ±1–2 from day to day, so a single drop of 1–2 reps is noise and should not cause any load change.

Definitions:
- `S` = total reps across the working sets at one load.
- A **clean** exposure is not censored. Censored means: red readiness, a yellow day on which sets were cut, a hard run in the previous 24 h (for lower-body), the first 1–2 exposures after a swap or gym change, or the first exposure after a break.
- Misses on censored days do not count. Hits on censored days do count.

| Scenario | Rule |
|---|---|
| **Good progression** | If every set reaches the ceiling (12), add one rung. The new rung is allowed only if the predicted reps at it are at least 8 (see the rung rule below). Reset the rep target to what the prediction gives, not blindly to 7. |
| **Slow grind (+1 rep/week)** | This counts as progress. Do nothing. Display the target as "beat last time by 1 rep". Relax the gate with a two-strike rule: advance when every set is at least `top-1` on two consecutive clean exposures. This fixes the "one weak last set blocks the jump" problem. Standardise rest (≥2 min, ≥3 min for compounds), because between-set fatigue drives this. |
| **Missed rep target** | If `S` is down by 1–2 reps against last time, treat it as noise and repeat the same target. If `S` is down by 3 or more, or there are two consecutive clean drops, go down one rung (or −5%) and rebuild. A single drop never triggers a reset. |
| **Load jump too big** | After set 1 of the first exposure at a new load: if the first set is below the floor (under 7), change the load for the remaining sets to the previous rung (or to the load the Epley prediction gives for about 9 reps). Allow two exposures for neural adaptation unless set 1 is 5 or fewer. Prevent the problem: compute `ceiling_needed` for the next rung (below) and raise the exercise's rep ceiling, up to 15, instead of taking an oversized jump. |
| **Stall** | No progress event in N clean exposures. A progress event is any of: a higher load with `S >= n_sets × floor`; a higher `S` at the same load; or best-set e1RM up by ≥1%. N = 4 for exercises hit twice a week (the upper days) and N = 3 for once-a-week exercises (the lower day), and at least 12 days must have passed. Your current rule (3 e1RM exposures) is about 10 days for upper lifts, which is too short. Then follow the ladder in section 5. |
| **Regression** | Sustained decline: `S` below the 4-week best by ≥15%, or three drops in the last four clean exposures. One exercise: step down a rung, re-baseline, and set a flag. If ≥3 exercises regress in the same 2 weeks, it is systemic, not lift-specific. Trigger the reactive deload and send it to the advisor. |
| **Return after a break or illness** | Measure days since the last lifting session. Up to 10 days: no change. 11–21 days: same loads, one set fewer in the first session, misses censored. 22–42 days: −5% load and −1 set for the first week. Beyond 42 days: −10%, half sets in week 1, and recalibrate. After illness: wait until HRV and RHR are back inside baseline range before progressing. No PR chasing for the first two sessions. |
| **Bad readiness** | Yellow: drop the last set per exercise (existing rule), keep loads, censor misses. Red day: halve the sets and drop the load by 5–10% (still to 0–1 RIR, so no RIR prescription). Do not count it for stall detection. Red for 4+ days: reactive deload (section 4). Use a rolling HRV baseline, not single-day readings. |
| **Exercise swap or gym change** | Treat it as a new exercise key. Use `(exercise, gym, machine)` as the key, because stacks and leverage differ. Seed the load from the old exercise using a similarity factor (0.85–1.0 of the load predicted for about 9 reps). The first two exposures are calibration: adjust load after set 1 on reps, and exclude them from stall counts. Keep the old history linked for the advisor. |
| **Accumulated fatigue** | The fatigue index is the share of clean exposures this week that were a miss or regression. If it is ≥40% for two consecutive weeks, or ≥50% of exercises show hold-or-worse in one week together with a falling HRV trend, trigger a reactive deload. Do not wait for the calendar. |
| **Add or remove sets** | Add a set when the muscle has been progressing (≥60% progress events), has had no flags for a week, and weekly hard sets are below the cap. Remove a set after two consecutive regressions in that muscle group, after a deload, or when volume is above the cap. Never add sets to an exercise that has just stalled unless the muscle is under its volume floor: it adds fatigue to a lift already failing to recover. |
| **Planned vs reactive deload** | Evidence for scheduled deloads is thin (Coleman 2024). Use a reactive trigger plus a soft planned deload on a long mesocycle (every 6–8 weeks), skipped if the last 2 weeks were all progress. Deload form: keep loads, halve the sets, keep frequency, 5–7 days. Align with a running cutback week. |
| **Interference from running** | A lower session within 24 h of intervals or a long run is censored: no stall or miss counting, no load increases, and −1 set on request. Prefer scheduling Lower the day after an easy run, not after the long run. Upper days need no adjustment, but the same systemic-fatigue flags apply. |

**Rung rule (uneven stacks).** Epley gives `reps_at_new = 30 × (1RM / new_load − 1)`. A 12-rep set loses about 2 reps at +5%, about 4 at +10% and about 5.5 at +15%. So:
```
j = (next_rung - load) / load
ceiling_needed = ceil(30 * ((1 + 8/30) * (1 + j) - 1))   # keeps predicted reps >= 8
ceiling = min(15, max(12, ceiling_needed))
```
If `ceiling_needed > 15`, the rung is infeasible. Advise a smaller increment (add-on magnets, a unilateral variant or a different machine) or skip the rung and use pause reps.

## 4. Recommended algorithm

Inputs: per set `(reps, load, equipment_id)`; session date; gym; readiness colour with a 7-day HRV/RHR trend; run log (date, duration, zones or TRIMP, type); optional joint-pain flag; optional body-weight trend; the rung table per equipment.

```
PARAMS = dict(floor=7, top=12, ceil_cap=15, noise_reps=2, big_miss=3,
              stall_n_2x=4, stall_n_1x=3, stall_min_days=12,
              e1rm_pr=0.01, reset_pct=0.05, rung_min_reps=8,
              fatigue_ex=0.40, fatigue_weeks=2, systemic_k=3,
              deload_sets=0.5, deload_days=(5, 7), vol_cap=18)

on_session_log(ex):
    S = sum(reps); clean = not censored(ex)       # see section 3
    if first_set_outside_range(ex): suggest_load_change_for_remaining_sets()  # in-session
    if load > best_load and S >= n*floor:   event = LOAD_PR
    elif S > best_S_at_load:                event = REP_PR
    elif e1rm(best_set) >= best_e1rm*1.01:  event = E1RM_PR
    else:                                    event = NONE
    if clean: stall_counter = 0 if event else stall_counter + 1

next_target(ex):
    if all(r >= top for r in reps) or two_strike(reps):   # every set >= top-1, twice in a row
        if rung_feasible(ex): return next_rung, rep_target(predicted)
        else:                  return same_load, ceiling=min(15, ceiling_needed)
    if S <= prev_S - big_miss or two_clean_drops(ex):
        return rung_down_or_minus(reset_pct)
    return same_load, target = prev_S + 1

if stall_counter >= N(ex): apply_ladder(ex)             # section 5
if count(regressions, 14d) >= systemic_k
   or fatigue_index >= fatigue_ex for fatigue_weeks
   or red_days >= 4:                  reactive_deload()  # halve sets, keep loads
if progressing(muscle) and sets(muscle) < vol_cap and no_flags: add_set(muscle)
```

**Send to the LLM advisor** (the engine should not decide these):
- The root cause when ≥3 lifts stall together (running load, sleep, energy intake, pain, life events).
- Which exercise to switch to, and how similar it is to the old one.
- Pain or illness notes in free text.
- Trade-offs between running and lifting in key weeks (race build, taper), including whether to shorten the deload.
- Any change to the rep range, session structure or target sets.
- Overrides when the data are suspect (a gym where the machine changed, or entry errors).

## 5. The "8 lifts stalled → −10% reset" situation

**Is 10% a good default? No, not as a blanket action on eight lifts.** By the Epley estimate, a 10% cut buys about 4 more reps, so you lose roughly four weeks of +1 rep/week progress per lift. Applied to eight lifts at once, it is a large unplanned regression for unknown benefit. Eight simultaneous stalls point to a shared cause (accumulated fatigue, sleep, energy intake, running load, or an over-strict rule such as the e1RM trigger), which a load cut does not fix. Also, because you train near failure at any load, a lighter weight still gives a similar hypertrophy stimulus, so the cut is cheap for muscle but expensive for morale and for strength. Reset is fine as a per-lift escalation after other options (5/3/1 community practice is about 10% after repeated failures), but 5% is the better first step.

**Recommended sequence:**
1. **Audit first.** Replay your logs through the new stall definition (rep PR, 4 exposures for upper lifts). Many "stalls" may be noise, or caused by the all-sets-at-top gate. Apply the two-strike rule and the rung rule and see how many remain.
2. **Systemic response.** If ≥3 lifts are still stalled, run one soft deload week (halve sets, keep loads, line it up with a running cutback week).
3. **Deload-then-test.** Test the original loads after the deload. If `S` beats the old best, fatigue was the cause and no reset is needed. This is the most useful diagnostic you have.
4. **Per-lift ladder for lifts still stalled.** Try these in order:
   1. −5% (nearest rung at or below), restart double progression.
   2. If the exercise is a machine or cable isolation, change the range to 10–15 instead of cutting load. Or add one myo-rep / rest-pause extension on the last set only, as cheap extra volume.
   3. Swap to a close variation (for example flat to incline, or barbell to dumbbell) and calibrate the new load.
   4. Add one set only if the muscle is under its volume floor. This is not a fix for a fatigued lift.
   5. Last resort: −10% with a new rep target.
5. **Lower-body lifts** get the same ladder, but check run timing first.

## 6. Open questions and trade-offs

- **Fixed 0–1 RIR everywhere.** It is the most fatiguing option, particularly with three runs a week. Robinson 2024 suggests strength is insensitive to RIR and the hypertrophy gradient is gentle further out. Leaving 1–2 RIR on early compound sets would be the best fatigue lever. This is your decision.
- **Stall exposures vs days.** Upper lifts are hit twice a week and lower lifts once. Are the two upper days identical, or an A/B pair with different exercises? That changes N.
- **Epley accuracy.** It is rough at 7–12 reps and across different machines. Treat predictions as ±1–2 reps. Over time, fit per-exercise corrections from your logs.
- **Readiness thresholds.** What are the HRV/RHR/sleep cutoffs behind green/yellow/red? A sensitive threshold produces many censored sessions and slows stall detection.
- **Volume caps.** The 18-set cap is a placeholder. A runner's recoverable volume is probably lower than the published plateau (Pelland, about 31 fractional sets). Tune by muscle once you have a mesocycle of data.
- **Run load as an input.** You need a number, not RPE. Options: pace/HR zones from the watch, or duration-weighted TRIMP.
- **Unverified items.** I could not confirm exact RP set-progression rules, SBS hypertrophy rules or Nippard's rules from primary sources, and Fitbod and Alpha Progression publish no algorithm. Treat the table's app rows as the general approach, not exact specifications.
