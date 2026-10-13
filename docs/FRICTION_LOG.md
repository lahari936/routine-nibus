# Friction Log

Ring API and SDK friction, logged as it happens. Format: date, area, what happened, workaround.

## Phase 1 (core logic + simulator)

- 2026-10-12, modelling (verify in Phase 2): the rules need to know whether an event means someone is inside. Doorbell motion is mostly cars and passers-by; indoor motion and door contacts mean someone is home. `Event.indoor` is set from the device type at normalization. Check whether Ring's device/capability data exposes this directly.

## Phase 2 (Ring integration)

- 2026-10-12, docs: the API documentation is one ~370k-character page. The token request, webhook signing and history sections are far apart, which makes it hard to read in one pass.
- 2026-10-12, account linking: the docs don't show the body Ring posts to the Token Exchange URL. The dev server accepts JSON or form data with a `code` field. Confirm against the sandbox.
- 2026-10-12, account linking: the docs don't show the `GET /v1/users/me` response body. We read `data.id`, falling back to `attributes.account_id`. Confirm against the sandbox.
- 2026-10-12, partner-initiated OAuth: the scope parameter is documented as `ava.v1:read`, but the example uses `scope=ava`. Not needed for one-way linking.
- 2026-10-12, HMAC key: the docs don't say whether the key is used as raw UTF-8 bytes or decoded first. We use the UTF-8 bytes of the key string. Confirm with a sandbox webhook.
- 2026-10-12, webhooks: the `motion_detected` `sub_type` values aren't listed in the section we read. We treat `human`, `person` and `motion.human` as human motion and everything else as other. Event history uses `motion.human` as a filter.
- 2026-10-12, devices: device resources have no type field (doorbell, camera, sensor), so we can't tell which devices are outdoors. For now, devices with "doorbell" in the name are treated as outdoor at link time.
- 2026-10-12, event history: there are no start or end time parameters. To backfill N days we page newest-first until we pass the cutoff.
- 2026-10-12, snapshots: the endpoint returns a 303 to a pre-signed URL. Following it while still sending our Bearer header can break the pre-signed request, so the client returns the `Location` URL without following it. That expiring link is also what alerts need.
- 2026-10-12, local dev on Windows: `http://localhost` first tries IPv6 and adds about 2 s per request against an IPv4-only server. Use `127.0.0.1`.

## Phase 3 (AWS pipeline)

- 2026-10-13, chime check-in: the audio playback body isn't in the docs we read. We send `{"audio_ref": ...}` to `/v1/devices/{id}/media/audio/playback`, and it needs Ring's chime-access approval. If the call fails, escalation skips the check-in and goes straight to notifying caregivers.
- 2026-10-13, SES and SNS: new accounts start in the SES sandbox, which only sends to verified addresses, and SMS needs a sandbox-verified number (and origination setup in some countries). For the demo, verify the caregiver email and phone first.
- 2026-10-13, Lambda bundling: pydantic-core is a compiled wheel. The CDK stack installs Linux wheels with `pip --platform manylinux2014_x86_64`, so `cdk synth` doesn't need Docker.

## Phase 4 (dashboard, digest, agent)

- 2026-10-13, Bedrock: Claude Opus 5.5 is served on the Messages-API Bedrock endpoint (`bedrock-mantle`, model `anthropic.claude-opus-5-5`, IAM action `bedrock-mantle:CreateInference`), not as an ARN-versioned Converse model. The agent uses Strands' Anthropic provider with the Anthropic SDK's `AsyncAnthropicBedrockMantle` client in place of the default client.
- 2026-10-13, Bedrock: server-side refusal fallbacks aren't available on Bedrock. The digest falls back to a plain facts template; the agent surfaces the error.
- 2026-10-13, API Gateway: HTTP APIs cap integrations at 30 seconds, so the dashboard's Ask box runs the agent at low effort. Longer questions would need an async job.
