# Garmin Personal API: what is actually there (verified 2026-10-25)

Sources: Garmin's OpenAPI document (embedded in
`developer.garmin.com/gc-developer-program/personal/swagger-docs/`), the official client
`github.com/garmin/connect-personal-api-python` (Apache-2.0), and live probing with a real key
(about 120 requests in total). No secrets are recorded here.

## Access

- Base URL: `https://personal.connectapi.garmin.com`
- Key: created at `https://connect.garmin.com/app/settings/personalApiToken`, valid 30 / 90 / 180 days
  or 1 year (chosen at creation), shown once, not renewable by code. Garmin e-mails a reminder about a
  week before expiry; the status/expiry date is visible in Garmin Connect (web). The API does **not**
  expose the key's expiry (the bearer JWT carries only `exp` of the *token*), so
  `key_expires_at` is not derivable; `garmin check` says so.
- Token exchange: `POST /personalapitoken-service/personal-api/personal-api-key/token` with header
  `Authorization: ApiKey <key>`. The response body is the bearer token as plain text (a JWT).
  Token lifetime 24 h or until the key expires, no refresh tokens: exchange again. Official client
  renews when less than 1 h is left (we do the same).
- Data calls: `GET <base><path>` with `Authorization: Bearer <token>`, `Accept: application/json`.
- Expired / deleted key: the token endpoint answers 401/403 (we raise `KeyExpiredError`).
- Rate limits: not published. Observed: ~120 requests at 1 req/s, no 429. We pace at 1 req/s
  (`GARMIN_REQUEST_INTERVAL_S`) and back off on 429/5xx (Retry-After respected).
- Cost of one manual sync (`POST /garmin/sync`): 4 GETs (activity list, daily summaries, sleep, HRV;
  one page each) + 1 FIT download per new activity, plus a token exchange only about once a day (the
  token is cached); every retry counts, so >= 4 s at 1 req/s. At the default cap of 30 manual syncs
  a day that is ~120 requests, plus ~30 for the scheduled runs. Third-party copies of Garmin's
  partner docs mention 200 calls per user per day (low confidence it applies to this API).

## Endpoints used (all GET)

| Data | Path | Window / notes |
|---|---|---|
| Activity list | `/activitylist-service/activities/search/activities?start&limit&startDate&endDate&sortOrder=asc` | offset pagination (`limit` default 99); dates filter on local start; no window cap seen. Includes training effect, HR time-in-zone (`hrTimeInZone_1..5`), training load, cadence, power |
| Original file | `/download-service/files/activity/{id}` | ZIP holding one `.fit` (laps, session, zones). Source of the laps |
| Daily summaries | `/usersummary-service/v2/dailysummaries?startDate&endDate` | up to 1 year; resting HR, stress avg, body battery min/max/charged/drained, steps |
| Sleep | `/sleep-service/sleep/dailySleepSummariesByDate?startDate&endDate` | **max 31 days** (32+ gives HTTP 400); stages, sleep score |
| HRV | `/hrv-service/hrv/daily/{from}/{to}` | no cap observed up to 365 d; 204 when empty; last-night avg, weekly avg, status, baseline |
| Weight | `/weight-service/weight/range/{from}/{to}?includeAll=true` | weights in **grams**; no cap observed (1200 d ok) |
| HR zones | `/biometric-service/heartRateZones/v2` | max HR, LTHR, resting HR, zone floors (a list, one entry per sport) |

Other documented endpoints (stress/steps daily: max 28 d, body battery reports: max 31 d,
hydration, SpO2, fitness age, food logs, menstrual cycle, profile settings, single-day
summary/HRV/sleep/heart rate) are not needed: the v2 daily summary already contains stress, body
battery and steps. Anything not in the OpenAPI list returns 404 (`/activity/{id}/splits`,
`/metrics-service/...` were tried).

## What the research got wrong / right

- Right: pull-only, key -> 24 h bearer token, JSON + FIT for activities, JSON for health.
- Windows: the research said stress/steps 7 days; the spec says **28**, sleep and body battery
  **31** (not 28), daily summaries **1 year** (not 180 d), HRV not capped in practice. Our chunk
  sizes: activities 90 d, daily summary 365 d, sleep 31 d, HRV 90 d, weight 365 d.
- The research said "activities back to first activity": true, but see below.
- **There is no splits/laps JSON endpoint**: laps only exist inside the FIT file (the activity
  detail JSON only has `splitSummaries`, e.g. `INTERVAL_ACTIVE`). We decode the FIT with `fitdecode`.
- **Not available**: training readiness, VO2max (no field in the activity list, no max-metrics
  endpoint), lactate-threshold detection other than the *configured* LTHR from the HR zones
  endpoint, Training Status. They stay NULL / absent.
- Anaerobic training effect is present on this account (watch reports both).

## Account-specific finding

This account's Garmin data starts **2026-08-17** (earliest activity 2026-08-26, earliest daily
summary / HRV / sleep 2026-08-17/26). A backfill from 2023-01-01 therefore returns nothing before
that; older Garmin history (if it exists on another account / device) is not reachable through this
API. The Strong history (2023+) is unaffected.

## Quirks

- Garmin `weight` is in grams; the Garmin weight entries of the first day are profile-setup values
  (`sourceType=USER_SETTING`, e.g. 82000 g = 82 kg); only the *latest* weigh-in per day is imported.
- FIT running cadence is per leg (`avg_running_cadence` + `avg_fractional_cadence`); steps/min = x2.
  Autolap laps have `intensity=interval`.
- FIT files embed the user profile (name, weight, height); only lap/session messages are read and
  the files are stored mode 0600 under `backend/var/fit/` (gitignored).
- HR zones reported by Garmin (max 190, zones from % of max) differ from the engine config
  (`max_hr: 192`, polarized ceiling 153). `athlete_metrics` records Garmin's values (also LTHR 171)
  so the engine can *propose* config changes; nothing is applied automatically.
- The device timezone in activity details is `Europe/Paris` (same offset as Vienna); we store
  Garmin's local/GMT start times and the configured `USER_TZ`.
