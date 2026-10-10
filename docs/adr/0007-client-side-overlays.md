# ADR 0007: Overlays are rendered client-side from JSON

Status: Accepted (M4, 2026-10-10)

## Context

Dashboards must show person boxes, the three foot anchors, zone polygons and
counts on top of the live frame (F-CV-5). The server could burn them into the
JPEG, as the paper's prototype did, or send them as data.

## Decision

The server never rasterises overlays. Each processed frame yields a
`frame_result` JSON message (boxes in decoded-frame pixels, anchors, zone
states and per-rule counts) and, unless privacy mode is on, the original
JPEG bytes. The browser draws the JPEG on one canvas and the overlays on a
second canvas (`frontend/src/lib/overlay.ts`), scaling by the ratio between
the preview size and `result.w/h` when the server down-scaled before
detection.

## Alternatives

- Server-side drawing: one encode per subscriber per frame, overlays frozen
  into pixels (no privacy mode, no toggling, no re-styling), and the
  evaluation harness would have to carry pixels instead of data.

## Consequences

- Privacy mode is trivial: send the JSON, skip the bytes.
- The zone editor and the overlay share one pixel-to-normalised mapping
  (frame size, not CSS size), which the tests check by saving and reloading.
- Per-frame data is available for the events table and the evaluation CLI
  in exactly the shape the dashboard uses.
