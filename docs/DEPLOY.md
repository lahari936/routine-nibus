# Deploy (Phase 3)

Prerequisites: AWS credentials, Node.js (for `npx aws-cdk`), Python 3.12+.

```bash
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install pydantic pyyaml tzdata boto3 aws-cdk-lib constructs
npx aws-cdk@2 bootstrap
npx aws-cdk@2 deploy -c ses_from=you@example.com -c sms_enabled=true
```

After deploying:

1. **Ring credentials:** put them in the `RingSecret` secret (stack output `RingSecretArn`) as JSON: `{"client_id": "...", "client_secret": "...", "hmac_key": "..."}`.
2. **SES:** verify `ses_from` and, while the account is in the SES sandbox, every caregiver email.
3. **SMS:** verify the caregiver phone numbers in the SNS SMS sandbox.
4. **Ring console:** using the `ApiUrl` output, set
   - Webhook URL: `<ApiUrl>/ring/webhook`
   - Token Exchange URL: `<ApiUrl>/ring/token-exchange`
   - Account Link URL: `<ApiUrl>/ring/link`

## Acceptance demo: missed morning

```bash
python -m tools.aws_demo seed --email you@example.com --phone +15551234567 --email2 other@example.com --end 2026-10-19
RING_HMAC_KEY=<hmac_key> python -m simulator.replay simulator/scenarios/missed_morning.yml <ApiUrl>/ring/webhook --account ava1.ring.account.demo --date 2026-10-19
python -m tools.aws_demo check --at 2026-10-19T09:45
```

Within seconds of the check, caregiver 1 gets an SMS and an email: "No activity yet today. Usually first active by …". Open the link and press **I'm on it**: the Step Functions execution succeeds and caregiver 2 is never paged. `python -m tools.aws_demo status` shows the alert as `acked`. If nobody acknowledges, caregiver 2 is paged after 15 minutes, and 15 minutes after that the alert is marked `unresolved`.

The 15-minute schedule runs the same check against real time; `--at` only exists to replay a scenario's date.

## Phase 4: dashboard, digest, agent

**Bedrock:** in the Bedrock console, enable model access for Claude Opus 5.5 (`anthropic.claude-opus-5-5`) in the stack's region. To use a different model, pass `-c bedrock_model_id=...` at deploy.

**Dashboard:** build it before deploying so CDK uploads it to S3 behind CloudFront (stack output `DashboardUrl`):

```bash
npm --prefix web install
npm --prefix web run build
npx aws-cdk@2 deploy -c ses_from=you@example.com
```

Sign up on the dashboard with an email that is listed as a caregiver for the home.

**Try the dashboard locally without AWS:** export the demo week, then start the dev server and open http://localhost:5173.

```bash
python -m tools.export_demo
npm --prefix web run dev
```

**Daily digest:** runs at 12:00 UTC for every home. To trigger one now, invoke the `DailyDigestFunction` output with `{"home_id": "demo-home", "day": "2026-10-18"}`.

**Ask the agent from a terminal** (AWS credentials with Bedrock access; `TABLE_NAME` from the stack outputs):

```bash
TABLE_NAME=<TableName> python -m agent.caregiver_agent --home demo-home --today 2026-10-19 "How was Mom's week?"
```
