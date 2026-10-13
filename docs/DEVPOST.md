# Devpost submission draft

## Tagline

Peace of mind without a camera: Routine Guardian learns an older person's daily rhythm from Ring events and tells family only when something meaningful changes.

## Inspiration

Many older people live alone, and their families worry about the morning nobody hears from them. The usual answers are cameras, which feel like surveillance, or pendants, which get left in a drawer. A Ring doorbell and a couple of sensors already know when the day starts, when the aide arrives and when the front door opens. We wanted to turn those signals into something a worried daughter can act on, without anyone watching video.

## What it does

- **Learns the home's normal day** from the last 28 days of Ring events, in the home's own time zone: when the first activity usually happens, how busy each hour is on each weekday, recurring visits such as a weekday aide, and how often the door opens at night.
- **Runs five plain rules** every 15 minutes, each with a reason a family member can read:
  - "No activity yet today. Usually first active by 8:30 AM (9 of 10 days)."
  - "The usual 10:00 AM visit didn't happen."
  - "Front door opened at 2:14 AM, which is unusual for this home."
  - "Doorbell rang 3 times since 4:02 PM with no answer."
  - "Doorbell offline since 1:30 PM, so monitoring is limited."
- **Escalates gently.** It can first ask at home through the Ring chime, "press your doorbell if you're OK", then texts and emails caregivers in order, each with a one-tap "I'm on it" link that stops the escalation.
- **Explains the week.** A daily digest and a "How was Mom's week?" agent, both Claude on Amazon Bedrock, narrate the facts the rules produced and cite days and alerts.
- **Dashboard** for caregivers: the day's timeline, activity against the usual pattern, alerts, the digest, and settings (caregiver order, quiet hours, check-in and snapshot opt-ins, vacation pause).

## How we built it

- **Ring API:** OAuth with rotating refresh tokens, one-way account linking (HMAC nonce), device discovery with status and capabilities, event history for a 28-day backfill, and v1.1 webhooks verified with HMAC-SHA256 on the raw body and deduplicated on `request_id`. Snapshots are requested only during an active, opted-in alert, as pre-signed expiring links.
- **AWS:** API Gateway, Lambda (Python 3.12), DynamoDB single-table with TTL, EventBridge Scheduler, Step Functions with task-token callbacks for escalation, SNS SMS, SES, Secrets Manager, Cognito, and S3 + CloudFront for the React dashboard. The whole stack is one AWS CDK (Python) app.
- **AI:** Claude Opus 5.5 on Amazon Bedrock, called through the Anthropic SDK's Bedrock client, for the daily digest. The caregiver agent is built with Strands Agents and has three tools over DynamoDB (`get_day_summary`, `get_alerts`, `get_baseline`). Claude never decides whether to alert; it only narrates structured facts.
- **Open source:** the baseline, rules, explanations, Ring client and webhook verification are packaged as `ring-routine-baseline` (MIT).
- **Testing:** a simulator generates 28 days of history with realistic jitter (weekday aide, later weekends, deliveries, night bathroom trips, Wi-Fi drops). Scenario files each trigger exactly one rule, and over 280 normal synthetic days the rules raised one false alert. The AWS pipeline is tested end to end against moto.

## Challenges we ran into

- **Telling who is home.** Ring device data has no "indoor" or "outdoor" type, but a doorbell's motion is mostly cars, so we tag each event with whether it means someone is inside.
- **Webhook retries.** Ring retries on 5xx and timeouts, so we mark a webhook as seen only after it is stored. A failed write gets retried rather than lost.
- **Link previews.** Email scanners open links on their own, so the acknowledgement link shows a confirm button and never acknowledges on GET.
- **Packaging Strands on Windows.** pip evaluates platform markers for the build machine, so Linux Lambda bundling from Windows needed `uv`.

The full list is in [FRICTION_LOG.md](FRICTION_LOG.md).

## Accomplishments we're proud of

- Every alert is explainable in one sentence, and the rule behind it is about 15 lines of Python.
- 1 false alert in 280 synthetic normal days, with no machine-learning black box.
- Privacy by default: no video analysis, opt-in expiring snapshots, a vacation pause, and data deleted on unlink.

## What's next

- Run with real households and an aide agency to tune the rule thresholds on real data.
- Per-home digest times and more languages.
- A multi-home view for home-care agencies.

## Business model and cost

- **Family plan:** a monthly subscription through the Ring App Store, which supports paid subscriptions with a 10% revenue share.
- **Agency plan:** per monitored client, for home-care agencies and senior-living operators, with a multi-home dashboard.
- **Cost per home:** usage per home per month is roughly as follows. These are estimates from the design and still need measuring after deployment.
  - About 1,200 webhook Lambda invocations and about 3,600 DynamoDB reads and writes, assuming 40 events a day.
  - 2,880 deviation-check runs, shared across all homes.
  - 30 nightly baseline runs, each reading about 1,100 items.
  - 30 digest calls to Claude: about 1,500 input and 300 output tokens each.
  - A handful of agent questions.
  - Under 100 emails or texts.
  
  Everything is pay-per-request, so an idle home costs close to nothing. Measure the real figure with AWS Cost Explorer filtered on the stack's `project=routine-guardian` tag, and replace this estimate with it before submitting.

## Built with

amazon-web-services, aws-lambda, amazon-dynamodb, aws-step-functions, amazon-eventbridge, amazon-bedrock, claude, strands-agents, aws-cdk, amazon-cognito, amazon-ses, amazon-sns, cloudfront, ring-api, python, react, vite
