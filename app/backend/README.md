# Task5 Human-in-the-Loop Rollout Recorder v1

This is an independent, read-only companion for the existing Task2 deployment.
It subscribes to Task2/camera topics, records complete rollouts, derives per-arm
human-intervention intervals, generates bounded-memory three-camera replays,
and writes JSON label sidecars. It does
not import Cobot Station, publish robot commands, or call control services.

## Start

After Task2 arms, cameras, middle camera and the model deployment are running:

The launcher deliberately uses the existing Cobot Station Python 3.8
environment and sources the Piper ROS Noetic workspace before starting the
read-only subscriber API.

```bash
cd /home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl
bash v1/scripts/start_task5_v1.sh
```

Open `http://10.7.165.64:8015/`. Starting a rollout only starts recording; it
does not start or move the robot. Use the existing Task2 teach buttons for
left/right/bilateral intervention. Stop from the page when the rollout ends,
review the cached replay, then choose only `success` or `failure`. This final
decision automatically unlocks and increments the next episode.

```bash
bash v1/scripts/check_task5_v1.sh
bash v1/scripts/stop_task5_v1.sh
```

Runtime files are isolated under `v1/runtime/` and `v1/logs/`. Final episodes
are under `data/raw_rollouts/<task>/<model>/<round>/episode_XXXXXX.hdf5`; labels
are adjacent `episode_XXXXXX.labels.json` files. Updating labels never opens the
HDF5 in write mode. Replays are derived caches under `.previews/<episode_uuid>/`
and may be regenerated without changing the episode.

The collapsed history panel can select any finalized episode, replay it, change
its success/failure result, or permanently delete it. Permanent deletion has an
explicit filename-and-size confirmation, removes the HDF5/label/replay directly,
and cannot be recovered. Existing files are never renumbered. Allocation uses
`max(current episode indices) + 1`; therefore deleting the trailing episode
allows that trailing index to be reused, while deleting a middle episode leaves
the gap in place. An empty series starts at index 1. The identity-only deletion
ledger is audit history and does not reserve indices.

The original Cobot Station backend/frontend, legacy data collector/converter,
Task2 coordinator and existing deployment scripts remain unchanged.
