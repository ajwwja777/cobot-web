# Cobot Magic dashboard preview

Served by the existing 8015 FastAPI static mount at `/dashboard-preview/`. The production console remains at `/`. This preview is read-only: JavaScript performs only `GET` requests to `/api/console/devices`, `/api/console/status`, and `/api/console/cameras` plus synchronized JPEG reads. Its navigation opens the existing controls.

## Components

- `vendor/tabler.min.css`: `@tabler/core` 1.5.1, MIT. License in `vendor/TABLER-LICENSE.txt`. No bundled ApexCharts or other third-party Tabler plugins are used.
- `dashboard.css`: local Cobot theme on Tabler cards, forms, buttons, and badges.
- `dashboard.js`: independent read-only polling, stale detection, event feed, and component focus.
- `#scene-root`: spatial view boundary. The present clickable SVG is a schematic, not a measured digital twin. Every target has a stable `data-part` ID corresponding to the five arms, two front grippers, and three cameras. A future glTF scene can replace this view using Three.js `GLTFLoader` and `Raycaster` while retaining the same IDs and focus/status APIs. No 3D asset, joint transform, or motion control is claimed in this preview.

The dashboard deliberately separates *node running* from *fresh CAN feedback*. A running ROS launch cannot turn an arm green when the passive CAN probe reports missing data. If API data ages out, the safety strip becomes unconfirmed and cameras show a stale overlay.

## Verification

Run `node --check dashboard.js`, open `/dashboard-preview/` at 8015, inspect all three images and the safety strip, click a component, then open the existing console through its link. This preview does not alter Session, model selection, CAN, homing, or recording state.
