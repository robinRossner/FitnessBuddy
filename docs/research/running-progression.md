# Running progression: how serious systems do it, and rules for this user

Researched 2026-10-25. User: 3 runs/week (Wed easy, Fri intervals, Sun long), Upper/Lower/Upper lifting (Lower on Tue), ~VDOT 38, goals HM + sub-25 5k, no race date, wrist HR (Garmin watch), HRmax 190, LTHR 171, uneven volume (0-28 km).

**Evidence grading.** Web-verified: items with links. From my own knowledge of the books and not re-verified online: Daniels' numeric limits, FIRST (run-less), Wilson 2012 interference meta-analysis, Seiler's work; marked *(book)*. Polar Training Load Pro, COROS EvoLab and Garmin's Daily Suggested Workouts (DSW) algorithm details were not found in primary docs; treat those cells as low confidence. Anything labelled "default" is my recommendation, not a sourced fact.

**Reality check on goals.** Sub-25 5k is ~VDOT 40, i.e. +2 VDOT (~+5%) from now. On 3 runs/week that is a 6-12 month project (my estimate), so progress will be slow; the HM (~2:05 now) is the nearer goal and shares most of its training (threshold + long run) with the 5k.

---

## 1. Comparison of systems

| System | What is progressed | Fitness estimate | Adaptation trigger | Missed / failed session | Load management |
|---|---|---|---|---|---|
| **Daniels** (VDOT) *(book)* | Phases I-IV (base, early quality, peak quality, taper); T, I, R, M paces from VDOT; session volume capped as a % of weekly volume | VDOT from a recent race/time trial; paces then fixed to that number | Re-race or a workout that is clearly easy at current paces; VDOT stepped by small amounts | Explicit layoff table: reduce VDOT/volume by time off, rebuild gradually; do not "make up" | Per-session caps (T ~10%, I ~8%, R ~5% of weekly mileage); +1 hard element at a time |
| **Pfitzinger** ([half plans](https://runningwithrock.com/pete-pfitzinger-half-marathon-plans/), [11-wk L1](https://www.finalsurge.com/coach/petepfitzingercoaching/plan/112546)) | Lactate-threshold work early, VO2max late; mid-week medium-long run; long runs, some with a progressive finish to LT | Race-pace tables from recent race | Plan is fixed; athlete swaps/repeats | Repeat or skip; protect the key LT/long run | Mileage tiers (lowest-volume plan still 4 days, ~24 km base) |
| **Hansons** ([16-miler](https://lukehumphreyrunning.com/hansons-marathon-method-the-16-miler/)) | Frequency + cumulative fatigue; long run capped at ~16 mi, run tired | Goal-pace based | Plan fixed | Keep the three "something of substance" days | Fatigue spread over many mid-length runs; not a 3-run model |
| **80/20** ([review](https://athleticsillustrated.com/book-review-8020-training-run-stronger-and-race-faster-by-training-slower/)) | Intensity distribution ~80% low / 20% mod-high, higher low-share in base | Pace/HR zones from a test | Phase-driven | Drop intensity before volume | Cap hard share; easy truly easy |
| **Lydiard** ([wiki](https://en.wikipedia.org/wiki/Arthur_Lydiard), [base](https://runnersconnect.net/coach-corner/base-training-running/)) | Long aerobic base -> hills -> anaerobic -> sharpening -> race | Effort based | Phase length | n/a (periodisation) | Base length as the load-limiter; base already includes fartlek and steady runs |
| **Norwegian singles** ([overview](https://marathonhandbook.com/norwegian-singles-training/)) | Sub-threshold volume: reps like 10x3', 5x6', 3x12' at ~LT1-LT2 (2-3.5 mmol, "comfortably hard") | Lactate (elites); HR/RPE for amateurs | Pace creeps only when HR/lactate stays in band | Keep reps controlled; never "race" the session | 20-25% of weekly time sub-threshold; amateur evidence is practitioner-level only |
| **Canova** ([special blocks](https://runnersconnect.net/special-block-training/)) | Race-specific endurance: extend duration at 90-110% of race pace, "special blocks" late | Race-pace coefficients | Distance/duration of specific work grows | Part-time athlete caveat: adapt, do not copy | High specific load; unsuited to 3 runs/wk now; useful late as long-run finish idea |
| **Runna** ([analysis](https://the5krunner.com/2026/09/28/is-runna-ai/), [heat](https://9to5mac.com/2026/07/29/runna-now-automatically-adapts-training-paces-based-on-heat-and-humidity/), [Not Feeling 100%](https://www.tomsguide.com/wellness/fitness/im-deep-into-marathon-training-fighting-the-flu-and-runnas-new-not-feeling-100-percent-tool-couldnt-be-more-timely)) | Plan weeks, paces | Pace analysis of tempo/interval sessions, goal time | Needs a consistent pattern, one outlier does not change targets; algorithm unpublished | Manual "Not Feeling 100%" (min 3 days; keep/shorten/break; return pace) | Heat-adjusted paces from forecast |
| **Garmin Coach / DSW** ([load](https://www8.garmin.com/manuals-apac/webhelp/forerunner265series/EN-SG/GUID-6A5D6D66-1F75-45B3-9E37-3AA5C189148F-8221.html), [DSW](https://the5krunner.com/daily-suggested-workouts)) | Daily suggestion from load balance | VO2max estimate, acute/chronic load (EPOC-based, Firstbeat), load focus (low/high aerobic, anaerobic over 4 wks), recovery time | Spike in acute load or unfinished recovery -> easier suggestion | Garmin Coach plans cannot be rescheduled; plan "adapts" ([forum](https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-web/400635/reschedule-a-missed-workout-in-garmin-coach-plan/1883220)) | Optimal/high load bands, recovery countdown |
| **TrainingPeaks / intervals.icu** ([PMC explainer](https://roadmancycling.com/blog/training-load-ctl-atl-tsb-explained-cyclists), [formulas](https://forum.intervals.icu/t/calculate-needed-training-load/5788/16)) | Daily load (TSS/hrTSS) | CTL (42 d EWMA) / ATL (7 d EWMA) / TSB = CTL-ATL | Ramp rate, TSB ranges (e.g. < -30 = overreach risk) | Decays naturally; coach edits plan | Ramp-rate limits (blog guidance: ~4-6 CTL/wk sustainable) |
| **Stryd / COROS / Polar** ([Stryd](https://help.stryd.com/en/articles/8880934-fitness-in-the-stryd-app), [CP](https://intercom.help/stryd/en/articles/6879345-critical-power)) | Power zones from auto Critical Power; COROS/Polar load models (not verified) | Auto-CP updated from hard efforts (can exclude runs) | CP rises/falls with data | Fitness loss reflected automatically | Load/ratio displays |

**Takeaways.** (a) Nobody progresses on one noisy session: Runna needs a pattern, Stryd lets you exclude runs, Daniels re-anchors on races. (b) All platforms treat pace/power as the target and HR/load as context. (c) Missed sessions are never "made up" in volume terms by serious systems; the plan decays and rebuilds.

---

## 2. Scenarios and recommended rules (this user)

Notation: *pass* = target pace held (+-3%) on >=80% of reps and the last rep no slower than the first by >3%.

1. **Interval passed easily** (pace held, last-rep HR < Z4 floor of 150, RPE <= 6/10): count as a "green". Do not bump pace after one. After 2 greens in 3 weeks, progress one lever (see section 3) and allow a +0.5 VDOT candidate (scenario 8).
2. **Passed hard** (pace held, last-rep fade <3%, peak HR >= 95% LTHR, RPE >= 9): hold the prescription for one more repeat. No progression, no deload.
3. **Failed session** (pace not held / aborted / HR runaway): classify first. Environmental (heat > 25 C, illness, poor sleep, <48 h after hard leg day): no penalty, repeat same session. Genuine (2 fails of the same prescription in 3 weeks): step back one level (-1 rep or -1 pace step), 4-day ease-off of readiness, flag to the advisor. Never reduce VDOT on one fail.
4. **Easy run, HR too high**: keep the avg-HR<=152 rule but (a) ignore the first 5 min, (b) add a heat allowance (e.g. +1 bpm per 2 C above 18 C), (c) if still over, the verdict is "slow down", not "fail"; if 3 easy runs in a row are +5 bpm over expected at the same pace, treat as fatigue signal (deload check). Easy run <=24 h after Lower day is exempt from a hard fail ([interference sequencing](https://link.springer.com/article/10.1007/s40279-017-0784-1)). Easy pace should be pace-capped too, otherwise HR-only control makes people crawl in heat.
5. **HR drift on long runs**: decoupling >5% over steady, flat, >=60 min ([drift](https://220triathlon.com/?p=9524)) -> hold distance next week; >8% or pace fade in the last third -> -10% distance and the advisor checks heat, fuel, sleep. Do not progress the long run in a week where drift is >5% unless temp >25 C. Skip drift on hilly/short runs (<45 min).
6. **Missed session**: no volume make-up. Intervals: replace by a one-time reschedule only to Thu (needs >=48 h before the long run; Thu is upper-body, so fine) otherwise drop and repeat same prescription next Friday. Long run missed: shift by max one day (Sat or Mon) else repeat the same distance next week with no increase. Missed easy run: drop it. Two consecutive missed weeks -> scenario 7.
7. **Return after break**: from my defaults (cf. Daniels' layoff table *(book; check values)* ): 1-7 days: resume as planned. 8-14 days: week 1 at 80% of the 4-week mean, no intervals for 5 days. 15-28 days: 3-week ramp 60/75/90%, easy only in week 1, VDOT -2%. 4-8 weeks: start at 50% volume, no intervals for 2 weeks, VDOT -4 to -6%. >8 weeks: treat as new baseline and re-estimate by a time trial after 3 weeks of consistency. Illness: add 2 easy days after symptoms are gone.
8. **Fitness improvement detected -> bump VDOT**: allowed sources (priority): (1) a validated effort: 5k parkrun/TT or a best 20-30 min segment; (2) 2 qualifying quality sessions at current pace passed *easily* within 3 weeks; (3) improving aerobic efficiency (speed/HR) trend over >= 4 weeks with a rest-HR/HRV not worsening. Step +0.5 VDOT (~+1%), max +1.0 per 4 weeks without a race; with a race, jump to the race-derived value but limited to +2. VDOT only goes down by the layoff table or by 3 environment-adjusted fails in 4 weeks. Never auto-bump from a downhill/tailwind run (use grade-corrected pace).
9. **Plateau** (no VDOT/EF change in 8 weeks despite consistency): check, in order, volume (usually the real limit at 3 runs), recovery/sleep/lifting load, pace model correctness (VDOT too high?), then change stimulus (swap T-focus for I-focus, or the reverse), then consider a 4th run. Escalate to the advisor after 8 weeks of flat data.
10. **Bad readiness**: yellow -> Friday becomes easy + 6x20 s strides (or sub-threshold at -10 s/km if user wants quality); red -> rest or easy 30 min, long run capped at 70%. Readiness inputs need a personal baseline (HRV 7-day trend vs 60-day baseline, Body Battery, sleep), not absolute numbers. Two red days in a week trigger a down-week review.
11. **Volume ramp from irregular base**: use a 4-week trailing mean of actual (not planned) km as base, not the last week. Allow +10% or +2 km, whichever is larger (the 10% rule has weak evidence, see sec. 4). Apply a separate cap: a single run <= 1.1x longest of last 30 days (+1 km allowance) — newer wearable data point at single-run spikes rather than weekly totals ([summary](https://marathonhandbook.com/the-10-rule-new-study-suggests-weve-been-doing-it-wrong-this-whole-time/); authorship not verified). No auto "floor" jump.
12. **Long run toward HM**: +1 km/week (or +10% of the longest run), 3 up / 1 down (down = -25%), plateau at 18-20 km (~2 h). Easy pace until 14 km is routine; then allow 1 run in 3 with the last 15-20 min at steady/HM pace (Pfitzinger-style progression, [source](https://runningwithrock.com/pete-pfitzinger-half-marathon-plans/)). The long run will be 40-50% of weekly km at 3 runs; accept that and skip the usual 25-30% rule.
13. **When to add a 4th run**: all of (a) >=8 consecutive weeks with >=90% of sessions done, (b) 3-run volume >= 28-30 km, (c) long run >= 14 km, (d) no injury flag, (e) a plateau or HM block starting. The 4th run: easy 30-40 min, placed Thu after the upper session or Mon, never Tue. Keeps lifting at 3/week.
14. **Deload**: every 4th week (3:1) or triggered: >=2 red days, 2 genuine fails, 3 easy runs with +5 bpm, resting HR +5 for 3 days. Volume -25%, long run -25%, intervals: same pace, -1/3 reps.
15. **Tuesday leg day**: keep Lower on Tue (72 h before Fri intervals; 24 h before Wed easy run). Rules: Lower sessions stay at RIR >= 2 (no failure on squats/deads) in weeks with a long run >= 16 km or when intervals are VO2max-type; if the leg day was exceptionally heavy/new, Wed becomes optional or pace-capped, and HR-fail is not applied. Within a day, lifting first if both are on the same day (resistance-before-endurance preserved lower-body strength in a [meta-analysis](https://link.springer.com/article/10.1007/s40279-017-0784-1)); the evidence mostly says "separate by hours, ideally on different days", not a hard 24 h rule.

---

## 3. Recommended weekly structure and algorithm

**Week.** Mon Upper / Tue Lower / Wed easy (30-45 min + strides) / Thu Upper / Fri quality / Sat rest / Sun long. Total quality (T+I+strides+optional steady finish) ~20% of running time, consistent with the 80/20 idea; polarized-vs-threshold evidence in recreational runners is not decisive ([Munoz et al.](https://pubmed.ncbi.nlm.nih.gov/23752040/): 5.0% vs 3.6%, not significant; [ongoing 5k trial](https://clinicaltrials.gov/study/NCT07738640)), so consistency beats the exact distribution.

**Approx paces for VDOT 38 (verify against engine's table):** T ~5:30/km, I ~5:05/km, R ~4:45/km, E ~6:30-7:10/km.

**12-week cycle (Friday session):**
- Weeks 1-4, threshold (HM + 5k shared base): 4x5' T /90 s -> 5x6' -> 4x8' -> 3x10' with 60 s jog. Optional sub-threshold variant (~10 s/km slower, HR 88-92% LTHR) when lifting load is high.
- Weeks 5-8, VO2max introduction, alternate T / I Fridays: I = 5x3' -> 6x3' -> 5x1000 m (jog 2-3 min), T = 3x10' / 2x15'.
- Weeks 9-12, race-specific (5k): 6x800 at ~5k pace -> 5x1000 -> 4x1200, alternating with 25-30 min continuous T or HM-pace finish on the long run. End of cycle: 5k time trial (parkrun), re-anchor VDOT, repeat cycle.
- Wed: strides (6x20 s) every week; hill sprints (6x8 s) in weeks 1-4 as low-cost neuromuscular work.
- Sun: easy long run building to 18-20 km; optional steady finish from week 6.

**Progression levers, in order (change one per week):** number of reps -> rep length -> shorter recovery (T only) -> pace (VDOT step). Max total hard time per Friday: T 30 (later 40) min, I 20 min.

```python
# ---- parameters (defaults) ----
PASS_PACE_TOL = 0.03          # rep within +-3% of target
LAST_REP_FADE = 0.03
GREEN_WINDOW = 3              # weeks
VDOT_STEP = 0.5; VDOT_MAX_4WK = 1.0
VOL_CAP_PCT = 0.10; VOL_CAP_ABS_KM = 2.0
LONG_RUN_SPIKE = 1.10; LONG_RUN_ABS_KM = 1.0
DELOAD_EVERY = 4; DELOAD_VOL = 0.75

def week_target_km(actual_weeks):                   # actual_weeks: last 8 weeks of real km
    base = mean(actual_weeks[-4:])
    cap = base + max(base * VOL_CAP_PCT, VOL_CAP_ABS_KM)
    return cap if not is_deload_week() else cap * DELOAD_VOL

def long_run_km(prev_long_km, longest_30d, drift_ok, readiness):
    if not drift_ok or readiness == "red": return prev_long_km
    nxt = prev_long_km + 1.0
    cap = max(longest_30d * LONG_RUN_SPIKE, longest_30d + LONG_RUN_ABS_KM)
    return min(nxt, cap, 20.0 if goal=="HM" else 18.0)

def classify_interval(session):                     # pace first, HR second
    if aborted(session): return "fail"
    ok_reps = frac(rep.pace_err <= PASS_PACE_TOL for rep in session.reps)
    fade = (session.last_rep.pace - session.first_rep.pace)/session.first_rep.pace
    if ok_reps >= 0.8 and fade <= LAST_REP_FADE:
        if session.last_rep.hr_avg_last_60s < Z4_FLOOR and session.rpe <= 6: return "green"
        return "pass_hard" if session.rpe >= 9 else "pass"
    return "env_fail" if environment_excuse(session) else "fail"

def next_friday(state):
    if readiness in ("yellow","red"): return easy_with_strides()
    if state.last == "green" and state.greens_in_window >= 2: return progress_one_lever(state)
    if state.last in ("pass","pass_hard","env_fail"): return repeat(state)
    if state.last == "fail":
        state.fails += 1
        return step_back(state) if state.fails >= 2 else repeat(state)

def update_vdot(vdot, evidence):
    if evidence.race_or_tt: return min(evidence.vdot, vdot + 2.0)
    if evidence.two_easy_passes_3wk or evidence.ef_trend_up_4wk:
        return min(vdot + VDOT_STEP, vdot_4wk_ago + VDOT_MAX_4WK)
    if evidence.layoff_days: return vdot * layoff_factor(evidence.layoff_days)
    return vdot                                       # one bad day never lowers VDOT

def maybe_add_fourth_run(h):
    return h.weeks_consistent >= 8 and h.weekly_km >= 28 and h.long_run_km >= 14 \
           and not h.injury_flag and (h.plateau or h.hm_block)
```

**Fallbacks.** Missed Friday -> Thu once, else drop. Missed long run -> shift <= 1 day or repeat. Failed -> repeat then step back. Readiness yellow/red -> easy + strides. Break -> restart table (scenario 7). Never compensate with extra volume.

**Send to the LLM advisor (edge cases, not routine):** 2 genuine fails in a row; 8-week plateau; HR/EF contradicting pace (e.g. pace passes but HR 10 bpm over for 3 sessions: possible wrong LTHR or illness); pain/injury text from user; conflicting readiness signals; >14-day breaks; deciding whether a race/TT was valid (heat, course); unusual lifting-block changes; whether to switch T- vs I-focus; user asking for a race date.

---

## 4. Critique of the current rules

**Keep**
- Friel zones from LTHR and the easy-run average-HR pass: reasonable, but exclude the first 5 min and add heat allowance. 152 is the top of Z2 and ~80% of HRmax; fine as ceiling, not as target.
- HR drift/decoupling flag >5%: standard; restrict to steady, flat, >=60 min runs.
- VDOT paces from best recent efforts: keep, but add hysteresis and efficiency-trend evidence (scenario 8) and a layoff table.
- Readiness -> intervals to easy: keep, but use personal-baseline trends and keep strides.
- Missed make-ups: keep only the "move <= 1 day or drop" version.

**Change**
- **HR-based work-lap check for 1000 m reps (~4:35):** unreliable. Optical wrist HR lags ~30-60 s at rep start and keeps rising across reps; the first rep's lap average will always look low, later reps look high from drift alone, plus heat. Short-interval HR is a poor pacing guide ([forum](https://uphillathlete.com/forums/topic/should-i-go-by-hr-or-pace-during-ant-intervals-beginner-marathon-plan/); [80/20 on short reps](https://www.goodreads.com/notes/22924668-80-20-running/55660633-edwin-setiadi/c9fc100b-ce95-4d3d-a30e-b2902d008a37)). Fix: pace as primary pass criterion; HR only as the last rep's final 60 s peak vs. Z4 floor (did the effort reach the zone?) and as a safety ceiling (>97% HRmax = stop); for threshold reps >=5 min HR is useful because lag is small relative to rep length. Prefer a chest strap for intervals.
- **Recovery-lap HR drop >= 12 bpm:** depends on recovery type (jog vs stand), starting HR and lap boundaries; fixed HR-based recovery targets can mislead with drift ([JSSM](https://jssm.org/jssm-14-466.xml)). Use as a trend over weeks (same protocol) and not as a pass/fail on one session.
- **"+1 rep to max, then faster pace":** add rep-length evolution, T/I alternation and a cap on total hard minutes; never change two levers in one week; add a green-streak rule so a single pass doesn't ratchet.
- **10% cap with 18 km floor:** the 10% rule has weak causal support: in the novice-runner RCT a graded 10%-rule programme did not reduce injuries vs a standard one (20.8% vs 20.3%, [summary](https://marathonhandbook.com/the-10-rule-new-study-suggests-weve-been-doing-it-wrong-this-whole-time/), [also](https://trainright.com/10-running-rule-reframed/)). ACWR as injury predictor is also heavily criticised ([editorial](https://pmc.ncbi.nlm.nih.gov/articles/PMC8138569/), [Frontiers](https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2021.669687/pdf)), though supporters defend it as a monitoring tool ([view](https://simplifaster.com/articles/acwr-high-performance-tool/)). So: keep ~10% as a sanity brake, but base it on a 4-week actual mean, add an absolute +2 km allowance (percentages are meaningless at 5-10 km), add a single-run spike cap, and apply the 18 km floor only to targets, never as a jump from a lower base (a 0-km week then 18 km is a 100%+ jump). A planned 18 km week for someone averaging 12 km is a ~50% increase.
- **Long run +km/week up to a cap:** add a step-back week and a drift gate; cap at 18-20 km.
- **Yellow readiness turns intervals into easy:** too blunt for a user with only one quality session per week; replace with easy+strides, or sub-threshold at reduced duration, and track consecutive downgrades (3 in a row = fitness stagnation risk).
- **Missing:** a recovery/layoff model, a deload cycle, interference-aware tagging after Lower day, heat adjustments, a validated time-trial every 8-12 weeks, and an LTHR sanity check (171 is 90% of HRmax; plausible but validate with the last 20 min of a hard 30-min effort or T session).

---

## 5. Open questions

1. Is the 171 LTHR measured (field test) or Garmin's estimate? If estimated, can we schedule one 30-min field test?
2. Is the optical wrist HR good enough for intervals, or will the user buy a chest strap?
3. What loop/terrain do the intervals use (flat track/road, hilly)? Pace and decoupling checks depend on it.
4. How heavy is the Lower day in practice (RIR, session RPE) and does it affect Wed/Fri?
5. Is a parkrun or TT race available on a regular basis to re-anchor VDOT?
6. Which goal wins when HM and 5k training conflict, and does "just progress" allow a rolling 12-week cycle?
7. Injury history (shins, knees, Achilles)? It changes the long run cap and ramp rules.
8. Does the user accept a 4th run eventually, and which day?
9. Which "yellow readiness" signals does the Garmin watch actually expose (HRV status, Body Battery, sleep score)?
10. Verify against the books: Daniels' layoff table and per-session caps, Pfitzinger 5k/HM plan details, and the 2025 single-run spike study's authors/numbers. Not done in this pass.
