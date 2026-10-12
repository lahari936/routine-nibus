# Routine Guardian: Implementation Spec (for Claude Code)

> Put this file in the repo as `docs/IMPLEMENTATION.md`. Tell Claude Code: *"Read docs/IMPLEMENTATION.md and implement Phase 1. Stop at the acceptance criteria."* Work one phase at a time.

## 0. Context for the coding agent

- **What it is:** A caregiver service that learns an elderly person's normal home rhythm from Ring events (motion, doorbell presses, contact sensors) and alerts family only on meaningful deviations, with no video needed.
- **Hackathon:** Amazon Developer Hackathon, Ring track, plus the AWS Builder and Open Source mini challenges. Deadline: **Oct 24, 2026, 00:30 IST**.
- **Hard requirements:**
  - The code must actually call the Ring API: auth, devices, event history, and webhooks.
  - The demo must show it working through the Ring sandbox/simulator or a real device.
  - Public repo with an MIT LICENSE.
- **Rules for the agent:**
  - Python 3.12, type hints, and AWS CDK (Python) for infrastructure.
  - Verify every webhook's HMAC signature before doing anything else.
  - Make event handling idempotent on the Ring event ID.
  - Never send video or snapshots unless an alert is active and the user has opted in.
  - Log Ring and SDK friction to `docs/FRICTION_LOG.md` as you go.

## 1. Ring platform facts (verify against the current docs before coding)

- Docs: https://developer.amazon.com/docs/ring/api-documentation.html
- **Base URL:** `https://api.amazonvision.com`. Requests use Bearer tokens; responses follow JSON:API style (use `?include=` for capabilities and status).
- **Credentials:** Registering an app gives you a **Client ID**, a **Client Secret**, and an **HMAC signature key**. All three are shown **only once**, so store them in AWS Secrets Manager immediately.
- **HMAC key is used two ways:** to verify webhooks (HMAC-SHA256, hex) and for account-linking nonces (HMAC-SHA256, URL-safe Base64).
- **Account linking:** One-way (Ring-driven) or partner-initiated OAuth 2.0, with refresh tokens.
- **Webhooks:** Motion detection, button press (ding), device online/offline, and app integration added/removed, using the v1.1 payload structure. The docs cover retry logic and a "Common mistakes" section; read it.
- **Sensors:** Contact sensors (door open/close), indoor temperature/humidity, and flood/freeze.
- **Other endpoints:** Event history, image snapshots, media clips, and chimes (playing audio on a chime requires requesting chime access).
- **Testing:** Ring describes a **sandbox environment with synthetic devices and events** (no rate limits). Otherwise you test with a personal Ring account.
- **Fallback:** The community `ring-sandbox` package on PyPI offers a local emulator with scenario playback. It is **unofficial**: use it for local development, but show the official sandbox (or a real device) in the demo video.
- **Monetization:** Ring App Store apps can charge subscriptions, and Ring takes a 10% revenue share.

> First task (Day 1 morning): register on the Ring Developer Portal, create the app, save the credentials, and confirm you can reach the sandbox. If registration or identity verification stalls, post in the hackathon Discord the same day.

## 2. Architecture

```
Ring (sandbox) --webhook--> API Gateway --> Lambda: webhook_handler (HMAC verify, dedupe)
                                              └─> DynamoDB: events
Ring event history --(backfill on link)--> Lambda: backfill --> DynamoDB events
EventBridge Scheduler (every 15 min) --> Lambda: deviation_check
       └─ reads baseline + today's events -> rules -> if deviation: start Step Functions
Step Functions: escalation
   1. (optional) check-in: play chime prompt / wait for ding up to 10 min
   2. notify caregiver 1 (SNS SMS + SES email, with ack link)
   3. wait 15 min for ack (callback task token)
   4. notify caregiver 2 ... then mark unresolved
Lambda: nightly_baseline (recompute baselines from last 28 days)
Lambda: daily_digest (Bedrock writes summary from structured facts) --> SES
Strands agent (Bedrock) --> tools over DynamoDB: "How was Mom's week?"
React dashboard (S3 + CloudFront) <-- API Gateway (Cognito auth)
```

## 3. Repo layout

```
routine-guardian/
  LICENSE (MIT)
  README.md
  docs/{IMPLEMENTATION.md, FRICTION_LOG.md, PRODUCT_FEEDBACK.md, architecture.png}
  core/
    ring_client.py        # typed client: tokens, devices, event history, snapshots
    webhook.py            # HMAC verify + normalize payload -> Event
    models.py             # Event, Home, Baseline, Alert (Pydantic)
    baseline.py           # baseline computation (pure functions)
    rules.py              # deviation rules (pure functions, explainable)
    explain.py            # one-sentence reasons
  lambdas/{webhook_handler, backfill, deviation_check, nightly_baseline, daily_digest, ack, api}/
  stepfunctions/escalation.asl.json
  agent/caregiver_agent.py  # Strands agent with tools
  simulator/
    scenarios/*.yml       # normal_day, missed_morning, missed_aide, door_open_night, unanswered_doorbell
    generate_history.py   # 28 days of synthetic events with realistic variance
    replay.py             # sends signed webhooks to local or deployed endpoint
  web/                    # Vite React dashboard
  infra/                  # CDK
  tests/
```

## 4. Data model (DynamoDB, single table)

| PK | SK | Item |
|---|---|---|
| `HOME#{id}` | `META` | name, timezone, caregivers[], quiet_hours, checkin_enabled, snapshot_optin |
| `HOME#{id}` | `EVT#{ts}#{event_id}` | device_id, kind (`motion_human`/`motion_other`/`ding`/`contact_open`/`contact_close`/`offline`), raw |
| `HOME#{id}` | `BASE#current` | baseline JSON |
| `HOME#{id}` | `ALERT#{ts}` | rule, reason, status, escalation_exec_arn, acked_by |
| `EVTID#{event_id}` | `DEDUPE` | ttl 7 days (for idempotency) |

## 5. Baseline and rules (keep interpretable; this is the core IP)

**Baseline** (recomputed nightly from the last 28 days, in the home's local time):
- `first_activity`: the distribution of the first event of each day, stored as p10, p50, p90 and p95.
- `hourly_rate[weekday][hour]`: mean events per hour (smoothed).
- `recurring_visits`: detect repeating ding-plus-motion clusters on the same weekday or time, e.g. an aide every day 10:00–11:30, as `{label, weekdays, window_start, window_end, hit_rate}`.
- `night_door`: the historical rate of contact_open events between 23:00 and 05:00.

**Rules** (each returns `Deviation(rule, severity, reason)` or None):

| Rule | Condition | Example reason |
|---|---|---|
| `missed_morning` | Now > first_activity p95 + 30 min and no events today | "No activity yet today. Usually first active by 8:10 (9 of 10 days)." |
| `missed_visit` | A recurring visit with hit_rate ≥ 0.8 had no matching events by window_end + 30 min | "The usual 10:00 aide visit didn't happen." |
| `night_door` | contact_open in quiet hours and night_door rate < 5% | "Front door opened at 2:14 AM, which is unusual for this home." |
| `unanswered_doorbell` | ≥ 3 dings within 20 min with no motion inside | "Doorbell rang 3 times since 4:02 PM with no answer." |
| `device_offline` | Doorbell offline for more than 2 hours | "Doorbell offline since 1:30 PM, so monitoring is limited." (low severity) |

**Cold start:** With fewer than 7 days of history, use defaults (first activity by 10:00) and show a "learning" badge.

**Bedrock never decides whether to alert.** It only writes digest text from the structured facts.

## 6. Escalation (Step Functions)

1. If `checkin_enabled` and the chime is available, play the prompt ("Please press your doorbell button if you're OK"). Wait up to 10 minutes for a ding event; a ding during that window resolves the alert as `self_checked_in`.
2. Send caregiver 1 an SMS and email with the reason and an acknowledgement link (`/ack?token=…`, which resumes the task token).
3. Wait 15 minutes. If acknowledged, resolve; otherwise notify caregiver 2, then mark the alert unresolved.
4. If `snapshot_optin` is set and an alert is active, attach the latest snapshot link (pre-signed, expiring).

## 7. Phases and acceptance criteria

**Phase 1: Core logic + simulator (Days 1–2)**
- Models, `generate_history.py` (28 days with realistic jitter, weekends different, aide on weekdays), `baseline.py`, `rules.py`, and `explain.py`.
- ✅ `pytest` passes. Each scenario YAML triggers exactly the expected rule, and `normal_day` triggers none. Report the false-alert count over 28 normal synthetic days (target ≤ 1).

**Phase 2: Ring integration (Days 3–4)**
- `ring_client.py` (token refresh, devices with capabilities and status, event history, snapshot), `webhook.py` (HMAC verification per the docs, normalization), and account linking.
- ✅ Against the Ring sandbox: list devices, pull event history, and receive a live webhook that verifies its signature and is stored once even when retried.

**Phase 3: AWS pipeline (Days 5–6)**
- CDK stack covering all the Lambdas, DynamoDB, Scheduler, Step Functions, SNS, SES, Secrets Manager, API Gateway and Cognito.
- ✅ Replaying `missed_morning` against the deployed endpoint produces an SMS or email with the right reason in under 2 minutes after the check runs. Acknowledging it stops the escalation.

**Phase 4: Dashboard + digest + agent (Days 7–8)**
- React dashboard with a timeline, baseline chart, alerts, and settings (caregivers, quiet hours, opt-ins).
- Daily digest via Bedrock.
- A Strands agent with tools `get_day_summary(date)`, `get_alerts(range)` and `get_baseline()`.
- ✅ "How was Mom's week?" returns a grounded answer that cites days and alerts.

**Phase 5: Submission (Days 9–10)**
- Demo video (script in `devpost-submissions.md`), README with architecture, `PRODUCT_FEEDBACK.md`, and `FRICTION_LOG.md`.
- Open Source: publish `core/` as a reusable package (e.g. `ring-routine-baseline`), or submit a PR to a Ring or Strands sample repo.

## 8. Environment variables

```
AWS_REGION=us-east-1
RING_API_BASE=https://api.amazonvision.com
RING_SECRET_ARN=          # client id/secret/hmac key in Secrets Manager
TABLE_NAME=routine-guardian
BEDROCK_MODEL_ID=
SES_FROM=
SNS_SMS_ENABLED=true
CHECK_INTERVAL_MIN=15
```

## 9. Privacy and safety checklist (say this in the video; judges care)
- Metadata only by default; no video analysis.
- Snapshots only during an active alert with explicit opt-in, using expiring links.
- The elder can see and pause monitoring (a "vacation" toggle).
- No medical claims: this is "a routine change was noticed", not health monitoring.
- Delete a household's data on unlink (handle the app-integration-removed webhook).

## 10. Business model (for the Devpost "Potential Impact" score)
- **Family plan:** A monthly subscription through the Ring App Store (paid subscriptions are supported, with a 10% revenue share).
- **Agency plan:** Per monitored client, with a multi-home dashboard for home-care agencies and senior-living operators.
- **Costs:** Mostly Lambda and DynamoDB pay-per-request plus a few Bedrock calls per day per home. Measure the real per-home cost from your deployment and put it in the submission.
