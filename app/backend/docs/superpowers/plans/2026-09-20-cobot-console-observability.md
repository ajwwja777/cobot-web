# Cobot Console Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished single-port 8015 console with realtime RLT explainability, reusable path search, and node-snapping video replay.

**Architecture:** Add one bounded read-only diagnostics provider behind FastAPI, then split frontend behavior into small dependency-free modules for charts, path selection, and replay timeline. Existing control state machines remain authoritative and unchanged.

**Tech Stack:** Python 3.8, FastAPI, pytest, vanilla JavaScript, Node assert/vm tests, HTML5 video, local SVG/CSS.

**Spec:** `docs/superpowers/specs/2026-09-20-cobot-console-observability-design.md`

## Global Constraints

- Keep port 8015 as the only public entry.
- Do not publish ROS/CAN commands or start RLT/robot services during implementation.
- Do not add CDN, npm, bundler, or browser framework dependencies.
- Diagnostic failures must not disable existing control actions.
- Distinguish training diagnostics from onsite success rate in all UI copy.
- Preserve the current dirty production baseline and modify only files named by this plan.

## Review Focus

- Missing, truncated, malformed, or non-finite metric records return a usable partial payload.
- A release manifest path outside the fixed RLT run root is rejected from diagnostic traversal.
- Directory responses arriving out of order never overwrite the latest input suggestions.
- Timeline snapping works before and after video metadata loads, including episodes without nodes.
- Repeated 1 Hz polls do not overlap and do not append duplicate robot samples.

---

### Task 1: Read-only RLT diagnostics contract

**Files:**
- Create: `cobot_console/diagnostics.py`
- Modify: `cobot_console/api.py`
- Create: `tests/test_console_diagnostics.py`

**Interfaces:**
- Produces: `ConsoleDiagnostics.snapshot(cache, session_loader) -> dict`
- Produces: `GET /api/console/diagnostics`
- Consumes later: frontend diagnostics rendering in Task 4.

- [ ] Write failing tests for waiting explanations, accepted/rejected operations, malformed JSONL, non-finite filtering, bounded 240-point output, fixed-root path validation, and robot joint freshness.
- [ ] Run `pytest -q tests/test_console_diagnostics.py`; expect failures because the provider and route do not exist.
- [ ] Implement atomic bounded readers, update explanation, release/audit/metric aggregation, and joint telemetry without camera payloads.
- [ ] Inject an optional diagnostics provider into `create_app` and add the route without changing control endpoints.
- [ ] Run `pytest -q tests/test_console_diagnostics.py tests/test_console_api.py`; expect all pass.

### Task 2: Reusable searchable path picker

**Files:**
- Create: `segmented_frontend/path_picker.js`
- Modify: `segmented_frontend/index.html`
- Modify: `segmented_frontend/app.js`
- Modify: `segmented_frontend/styles.css`
- Create: `tests/path_picker.test.js`
- Modify: `tests/directory_browser.test.js`

**Interfaces:**
- Produces: `CobotPathPicker.create(options)` with `refresh()`, `remember(path)`, and `setDisabled(bool)`.
- Consumes: existing `/api/segmented-teach/storage/directories?path=` contract.
- Consumed by: both normal and RLT storage controls.

- [ ] Write failing Node tests for recent ordering, prefix queries, child continuation, keyboard selection, missing-directory copy, and stale response suppression.
- [ ] Run `node tests/path_picker.test.js`; expect failure because module is absent.
- [ ] Implement the dependency-free picker module and accessible listbox markup/styles.
- [ ] Replace normal and RLT datalists with picker instances; persist at most 12 recent paths per profile.
- [ ] Run `node tests/path_picker.test.js && node tests/directory_browser.test.js && node tests/unified_console.test.js`; expect all pass.

### Task 3: Node-snapping replay timeline

**Files:**
- Create: `segmented_frontend/replay_timeline.js`
- Modify: `segmented_frontend/index.html`
- Modify: `segmented_frontend/app.js`
- Modify: `segmented_frontend/styles.css`
- Modify: `capture_core/episode_preview.py`
- Create: `tests/replay_timeline.test.js`
- Modify: `tests/test_segmented_preview.py`

**Interfaces:**
- Produces: `CobotReplayTimeline.create(video, elements, callbacks)`.
- Consumes: node `frame_index`, preview `source_fps`, video duration.
- Produces: preview status fields `source_frame_count`, `source_fps`, `output_fps`.

- [ ] Write failing JS tests for frame-to-time mapping, 0.35-second snapping, adjacent-node keyboard jumps, and no-node fallback.
- [ ] Write failing Python test that ready preview status exposes timing metadata.
- [ ] Implement the standalone timeline module and metadata propagation.
- [ ] Integrate marker rendering, drag seeking, snapping, video time updates, and keyframe synchronization.
- [ ] Run `node tests/replay_timeline.test.js && pytest -q tests/test_segmented_preview.py`; expect all pass.

### Task 4: Unified polished shell and realtime dashboards

**Files:**
- Create: `segmented_frontend/diagnostics_ui.js`
- Modify: `segmented_frontend/index.html`
- Modify: `segmented_frontend/styles.css`
- Modify: `segmented_frontend/app.js`
- Modify: `segmented_frontend/console_ui.js`
- Create: `tests/diagnostics_ui.test.js`
- Modify: `tests/unified_console.test.js`
- Modify: `tests/segmented_teach_ui.test.js`

**Interfaces:**
- Consumes: `GET /api/console/diagnostics`.
- Produces: tab navigation, update reason card, local SVG charts, robot motion ring buffer, and system-health panel.

- [ ] Write failing tests for explanation labels, chart point filtering/scaling, actor-update interpretation, non-overlapping polling, and bounded robot samples.
- [ ] Run `node tests/diagnostics_ui.test.js`; expect module-not-found.
- [ ] Implement pure formatting/chart/state functions in `diagnostics_ui.js`.
- [ ] Restructure HTML into four accessible views with persistent status bar and action rail.
- [ ] Apply responsive visual system, focus states, status colors, skeleton/empty states, and reduced-motion support.
- [ ] Wire 1 Hz diagnostics polling with overlap guard; render learning, Q, mix, joints, and raw system evidence.
- [ ] Run all frontend Node tests and focused Python API tests; expect all pass.

### Task 5: Integrated verification and deploy-in-place

**Files:**
- Modify only if a verified defect is found in prior task files.

**Interfaces:**
- Consumes all preceding task contracts.
- Produces a restarted 8015 UI service only; does not start RLT or robot services.

- [ ] Run `python -m compileall cobot_console capture_core segmented_capture`.
- [ ] Run the complete backend pytest suite and every `tests/*.test.js` file.
- [ ] Run `git diff --check` and inspect the scoped diff.
- [ ] Restart only the data UI via its existing stop/start scripts; do not start RLT.
- [ ] Verify identity, config, status, diagnostics, static assets, HTML cache versions, and HTTP error degradation with curl.
- [ ] Capture a browser screenshot at desktop and narrow width if an accessible browser surface is available; otherwise validate DOM/CSS through tests and report that visual inspection remains for the user.
