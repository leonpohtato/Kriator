# Kriator Live Telemetry Capability Audit

Date: 2026-06-06

This audit states what the live beginner drawer currently senses, records, remembers, and explicitly does not claim.

## Current Capture Path

The Krita docker captures two streams during a live session:

- A cleaned visual snapshot of the current Krita document with KGA/reference/guide layers hidden.
- App-wide Qt tablet/mouse events exposed to the PyKrita docker.

The backend stores each live interval as JSONL:

```text
D:\data\krita-guide-agent\storage\artworks\<artwork-id>\live_sessions\<session-id>.jsonl
```

## Recorded When Exposed

Each interval can record:

- raw tablet/mouse event timing
- event source, kind, x/y position, button state
- pressure
- x/y tilt
- rotation
- tangential pressure
- grouped stroke summaries
- stroke duration, distance, bounds, average speed, max speed
- per-stroke pressure min/average/max
- active Krita layer name/type/visibility/lock/opacity
- active beginner layer category
- visible layer counts by category
- visual coach state, selected step, stage, progress, and comments
- brush/tool fields if the Krita Python view API exposes them
- a `capabilityMatrix` saying which signals were captured in that interval

## Multiple Layers Per Category

Visible layers are assessed as one combined visible artwork. This means layers like:

```text
My Shadows 1
My Shadows 2
Wing Shadows
Soft Chest Shadows
```

can all contribute to the same visible shadow-stage result. Telemetry records the active layer and maps it to a beginner category such as `Shadows`.

## Explicit Unsupported Scope

Kriator does not guess hidden Krita internals. The capability matrix records unsupported internals explicitly, including:

- Krita brush-engine internal matrices not exposed by this docker.
- Hidden stabilizer internals and full per-dab brush calculations that are not guaranteed through PyKrita/Qt events.

If a signal is not exposed during an interval, it is recorded as unavailable instead of fabricated.

## Verification Evidence

Verified API session:

```text
GET /api/artworks/20260606-141011-f4dde0/live-sessions/codex-matrix-test
```

The stored record included:

- `capabilityMatrix.pressure: true`
- `capabilityMatrix.tilt: true`
- `capabilityMatrix.rotation: true`
- `capabilityMatrix.tangentialPressure: true`
- `context.activeCategory: "Shadows"`
- `context.tool.brushPreset: "b) Basic-1"`
- unsupported internals listed in `capabilityMatrix.unsupported`

## Demo Claim

Use this claim:

> Kriator records live drawing telemetry that Krita/PyQt exposes, plus visual snapshot analysis. It records pressure, tilt, rotation, stroke metrics, layer/category context, and brush/tool context when available. It does not fake unsupported Krita brush-engine internals; it reports them as unavailable.
