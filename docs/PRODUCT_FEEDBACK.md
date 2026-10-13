# Product feedback for the Ring developer platform

What we'd ask for after building Routine Guardian on the Ring API. Day-to-day details are in [FRICTION_LOG.md](FRICTION_LOG.md); this is the short version, most useful first.

## 1. Tell us what kind of device it is

Device resources carry a name, status and capabilities but no device type (doorbell, indoor camera, contact sensor, motion sensor) and no indoor or outdoor placement. For any routine or presence use case, doorbell motion (mostly cars and passers-by) has to be treated differently from indoor motion. Today we guess from the device name.

**Ask:** a `device_type` attribute, and ideally the location or placement the user set in the Ring app.

## 2. Document the full account-linking exchange

The one-way linking flow is well explained, but two payloads are missing from the docs:
- the body Ring posts to the partner's Token Exchange URL (JSON or form? which field holds the code?)
- the `GET /v1/users/me` response (which field is the account ID used in the nonce?)

There's also a scope mismatch: the parameter table says `ava.v1:read`, while the example uses `scope=ava`.

**Ask:** example request and response bodies for both, and one consistent scope string.

## 3. Time range filters on event history

`/v1/history/devices/{id}/events` has an `event_types` filter and cursor pagination but no `start` or `end`. A 28-day backfill has to page from newest to oldest until it passes the cutoff, and checking a single past day means paging through everything newer.

**Ask:** `start` and `end` query parameters, in epoch milliseconds to match the payloads.

## 4. List the motion `sub_type` values

`motion_detected` webhooks may carry `attributes.sub_type`, and event history filters on `motion.human`, but we couldn't find the complete list of values for either.

**Ask:** an enumerated list, and the same names in webhooks and history.

## 5. Spell out the HMAC key encoding

The docs explain hex signatures for webhooks and URL-safe Base64 nonces for linking, which is great, but don't say whether the HMAC key itself is used as UTF-8 text or decoded first.

**Ask:** one sentence, plus a test vector: key, body and expected signature.

## 6. A webhook test button

The Common Mistakes section is excellent. A "send test webhook" button in the developer console, with a signed sample of each event type, would let partners verify signatures before the sandbox or a device is wired up.

## 7. Chime audio for check-ins

Playing a prompt on a chime ("press your doorbell if you're OK") is a natural, camera-free way to check on someone. Chime access needs a separate request, and we couldn't find the playback request body in the docs.

**Ask:** a documented playback example, plus a small set of standard spoken prompts partners can use without uploading audio.

## 8. One page of the docs is a lot

The API documentation is a single page of about 370,000 characters. Splitting it per area (auth, devices, webhooks, history, media, chimes) would make it easier to read and to link to specific sections in support questions.

## What worked well

- The JSON:API `include=` for status and capabilities kept device discovery to a single call.
- The retry schedule, 5-second timeout and dead-letter replay window are clearly documented, which made idempotent handling straightforward.
- Returning snapshots as a 303 to a pre-signed URL fits privacy-sensitive apps well: we can share an expiring link instead of storing images.
