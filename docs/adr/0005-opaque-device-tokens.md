# ADR 0005: Opaque random device tokens instead of JWT

Status: Accepted (M2, 2026-10-10)

## Context

Each paired phone needs a long-lived credential for `/ws/camera` that the
admin can revoke instantly and that survives server restarts.

## Decision

A device token is `secrets.token_urlsafe(32)` (256 bits), returned once in
the redeem response and stored only as `sha256(token)` in
`device_credentials.token_hash` (no salt is needed at this entropy). It is
bound to exactly one camera, has no expiry, and is invalidated by setting
`revoked_at` (admin revoke, session delete, re-pairing via `replace_camera_id`).
Authentication is a single indexed lookup by hash.

## Alternatives

- Signed JWTs: stateless, but revocation needs a deny-list anyway, the signing
  key becomes a secret to manage, and a leaked token stays valid until expiry.
- Session-style short-lived tokens with refresh: more protocol for a fixed
  sensor that may run for days.

## Consequences

- Revocation is immediate and visible in `security_log` (`token_revoked`).
- A leaked token is useless once revoked; the admin re-pairs the camera with
  "Replace phone" so zones and history keep the same camera id.
- The database never holds a plaintext token; the test suite greps logs,
  responses and rows for it.
