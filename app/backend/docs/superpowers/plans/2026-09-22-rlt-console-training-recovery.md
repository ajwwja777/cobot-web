# RLT Console and Training Recovery Implementation Plan

Goal: deliver synchronized cameras, detailed diagnostics, allowlisted infrastructure controls, corrected value gates, and one offline-validated online-learning release.

Spec: docs/superpowers/specs/2026-09-22-rlt-console-training-recovery-design.md

Global constraints: no robot motion during implementation; no arbitrary HTTP shell; keep work outside 30Hz control; preserve dirty baseline; test first; never publish without positive autonomous-online gates.

## Task 1: synchronized camera preview
- RED tests for atomic generation, unchanged sequence, stale, desync, malformed source fallback, freeze age, bounded encoding, generation-only browser refresh and warning states.
- Add cache sequence, camera_sync provider, API routes, status badges and synchronized browser refresh.
- Run focused Python and Node tests.

## Task 2: complete diagnostics and charts
- RED tests for multi-release lineage, actor update windows, Q curves, progress, axes/ticks/tooltips/units and empty reasons.
- Implement bounded lineage aggregation, new diagnostic panels and remove motion chart.
- Run observability tests.

## Task 3: allowlisted infrastructure control
- RED tests for allowlists, confirmation nonce, PID ownership, status degradation and bounded logs.
- Add supervisor, fixed routes, status lights, selectors and fixed CAN/camera wrappers without executing actions.
- Run tests and shell syntax checks.

## Task 4: correct evaluator, sampling, metrics and gates
- RED tests proving online autonomous inclusion, UUID isolation, HIL cannot substitute, reversed AUC rejection, actor metric aggregation.
- Implement grouped split, all-online/autonomous/policy-terminal/HIL summaries, stratified sampling and fixed gates.
- Run RLT suite.

## Task 5: fixed replay ablation and release
- Freeze UUID/hash/seed split and baseline.
- Run critic burn-in then equal-budget delta 10/30/100/300 candidates on Cobot GPU.
- Evaluate Q curves, direction, human fit and conditioned safety.
- Publish only a fully passing candidate; otherwise keep pointer unchanged.

## Task 6: integration and delivery
- Run full corrected-environment tests, Node tests, RLT tests, compileall, shell syntax and diff checks.
- Start only 8015 and verify HTTP degradation/state; do not start robot services.
- Fresh whole-change review and one important-finding fix pass.
- Write detailed diagnosis, TODO, release/rollback and onsite frozen-10 then online-5 workflow.
