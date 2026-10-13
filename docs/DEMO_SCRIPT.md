# Demo video script (about 3 minutes)

**The hackathon requires the demo to show Ring integration working through the official sandbox or a real device.** The community `ring-sandbox` emulator is fine for development but must not stand in for that part. Record the sandbox segments last, once Phase 2 has been checked against the sandbox.

Before recording:
- Deploy the stack ([DEPLOY.md](DEPLOY.md)) and link the sandbox account.
- Seed the demo home with `python -m tools.aws_demo seed ...`.
- Verify your own email and phone in SES and SNS.
- Have the dashboard open in one browser tab and your phone visible.

## 0:00 to 0:20. The problem

*Show:* a photo or illustration of an older person at home, then a phone with no messages.

> "More than a quarter of people over 65 live alone. Their families worry, but cameras feel like surveillance and fall-detection pendants often stay in a drawer. Most families already have something better: a Ring doorbell and a few sensors that know when the day starts, when the aide arrives and when the door opens."

## 0:20 to 0:45. What Routine Guardian does

*Show:* the dashboard's day timeline for a normal day: the wake-up band, the aide's ring at 10:00 and movement through the afternoon.

> "Routine Guardian learns one home's normal rhythm from Ring events (motion, doorbell presses and door sensors) and tells family only when something meaningful changes. No video is analysed. Every alert comes with a reason in plain English."

## 0:45 to 1:15. Ring integration (sandbox)

*Show:* the terminal running `python -m tools.ring_dev smoke` against the sandbox, listing devices and event history. Then trigger a doorbell press in the sandbox and show the webhook arriving, with its signature verified and the event stored.

> "This is the Ring API: OAuth with rotating refresh tokens, device discovery, event history for backfill, and signed webhooks. Every webhook is checked against Ring's HMAC signature before anything else happens, and retries are stored once."

## 1:15 to 2:00. The missed morning

*Show:* replay the `missed_morning` scenario and run the check:

```bash
python -m simulator.replay simulator/scenarios/missed_morning.yml <ApiUrl>/ring/webhook --account ava1.ring.account.demo --date <today>
python -m tools.aws_demo check --at <today>T09:45
```

Then the phone buzzes with an SMS. Read it out.

> "It's 9:45 and there's been no movement. Mom is usually up by 8:30, nine days out of ten. Caregiver 1 gets a text and an email with the reason. If the home has a Ring chime, Routine Guardian can first ask at home: 'press your doorbell if you're OK.'"

*Show:* tap the link, tap **I'm on it**, then the Step Functions execution succeeding in the console.

> "One tap and the second caregiver is never paged. If nobody responds within 15 minutes, it moves to the next person."

## 2:00 to 2:30. Ask about the week

*Show:* the dashboard's Ask box: "How was Mom's week?" The answer cites Friday's missed aide visit (acknowledged by Caregiver 1) and today's late start.

> "A Strands agent running Claude on Amazon Bedrock answers questions using only the facts our rules produced. It cites days and alerts. It never decides whether to alert, and it doesn't make health claims."

## 2:30 to 2:50. Privacy and trust

*Show:* the Settings panel: vacation pause, snapshot opt-in, caregiver order.

> "Metadata only by default. Snapshots only during an active alert, only if the family opted in, and only as links that expire. Mom can pause monitoring. If the household unlinks the app in Ring, their data is deleted."

## 2:50 to 3:00. Close

*Show:* the architecture diagram, then the repository and the `ring-routine-baseline` package.

> "It runs on pay-per-request serverless AWS, so an idle home costs almost nothing, and the baseline-and-rules core is open source as `ring-routine-baseline`. Routine Guardian: peace of mind without a camera."
