"""Read-only collection index across segmented and rollout recordings."""
from .labels import LabelStore

def combined_history(root, segmented, *, limit=50, offset=0):
    rows = {}
    for item in LabelStore(root).list_episodes():
        if str(item.get("episode_outcome", "")).lower() != "aborted":
            rows[item["episode_uuid"]] = dict(item, history_format="rollout")
    # A normal recording also has an HDF5 label entry. Prefer its richer node sidecar.
    for item in segmented.list_episodes(data_root=root):
        if str(item.get("episode_outcome", "")).lower() != "aborted":
            rows[item["episode_uuid"]] = dict(item, history_format="segmented")
    ordered = sorted(rows.values(), key=lambda row: (int(row.get("episode_index") or 0), str(row["episode_uuid"])), reverse=True)
    return {"episodes": ordered[offset:offset + limit], "total": len(ordered)}
