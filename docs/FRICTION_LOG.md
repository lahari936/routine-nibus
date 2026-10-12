# Friction Log

Ring API and SDK friction, logged as it happens. Format: date, area, what happened, workaround.

## Phase 1 (core logic + simulator)

- 2026-10-12, modelling (verify in Phase 2): the rules need to know whether an event means someone is inside. Doorbell motion is mostly cars and passers-by; indoor motion and door contacts mean someone is home. `Event.indoor` is set from the device type at normalization. Check whether Ring's device/capability data exposes this directly.
