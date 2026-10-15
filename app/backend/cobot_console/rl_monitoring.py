"""Read-only collection charts. All training semantics belong to rl-platform."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import runpy
import threading
import time
from .paths import RLT, RUNTIME_ROOT


@lru_cache(maxsize=1)
def contract():
    return runpy.run_path(str(RLT / 'methods/expo_ft/cobot_adapter/monitoring.py'))


def run_directory(model, selected=None):
    root = model.get('online_run_dir')
    if not root or model.get('runtime_adapter') != 'expo_online':
        raise ValueError('This model has no EXPO online history')
    defaults = (model.get('expo_online_options') or {}).get('defaults')
    if defaults and selected:
        from .execution_options import expo_contract
        root = expo_contract()['configured_run']({'run_dir': root, **defaults}, selected)['run_dir']
    return Path(root)


class MonitoringReader:
    def __init__(self, cache_root=None):
        self.cache_root = Path(cache_root or RUNTIME_ROOT / 'rl-monitoring')
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rl-chart-history')
        self.lock = threading.Lock()
        self.jobs = {}

    def close(self):
        self.pool.shutdown(wait=False)

    def scan(self, root, key):
        api = contract()
        cache_path = self.cache_root / (key + '.json')
        cached = api['read_json'](cache_path).get('episodes', {})
        summaries = {}
        for path in sorted((root / 'episodes').glob('*/finished.json')):
            identity = path.parent.name
            stamp = path.stat().st_mtime_ns
            old = cached.get(identity, {})
            summary = old.get('summary') if old.get('stamp') == stamp else api['episode_summary'](path.parent)
            if summary:
                summaries[identity] = dict(stamp=stamp, summary=summary)
        api['write_json'](cache_path, dict(episodes=summaries))
        return api['success_series']([p['summary'] for p in summaries.values()])

    def read(self, model, state, selected=None):
        api = contract()
        root = run_directory(model, selected)
        key = hashlib.sha256(str(root).encode()).hexdigest()[:20]
        with self.lock:
            job = self.jobs.setdefault(key, dict(future=None, next_scan=0, result=None, error=None))
            if job['future'] and job['future'].done():
                try:
                    job['result'] = job['future'].result()
                    job['error'] = None
                except Exception as exc:
                    job['error'] = str(exc)
                job['future'] = None
                job['next_scan'] = time.monotonic() + 5
            if not job['future'] and time.monotonic() >= job['next_scan']:
                job['future'] = self.pool.submit(self.scan, root, key)
            success = job['result'] or dict(points=[], completed=0, excluded=0)
            loading, error = job['result'] is None, job['error']
        training = api['read_json'](root / 'telemetry/learner.json')
        rollout = api['read_json'](root / 'telemetry/rollout.json')
        same_model = state.get('model', {}).get('id') == model['id']
        online = state.get('online') or {} if same_model else {}
        live = same_model and state.get('phase') not in ('offline', 'error', 'loading') and not state.get('status_stale')
        live = bool(live and online.get('phase') in ('ready', 'rollout', 'updating', 'publishing', 'transferring'))
        # A prior episode must never masquerade as the currently running episode.
        active = online.get('active_episode')
        if active and rollout.get('episode_uuid') != active:
            rollout = dict(episode_uuid=active, points=[])
        return dict(schema=1, run_key=key, model_id=model['id'], model_label=model.get('label'),
                    options=selected, live=live, phase=online.get('phase', state.get('phase', 'offline')),
                    actor_version=online.get('served_actor_version', online.get('actor_version')),
                    training=training, success={**success, 'loading': loading, 'error': error},
                    rollout={**rollout, 'active': bool(active and active == rollout.get('episode_uuid'))},
                    sampled_at=time.time())


def install_routes(app, manager):
    from fastapi import HTTPException
    from .deployment import catalog
    reader = MonitoringReader()
    app.add_event_handler('shutdown', reader.close)

    @app.get('/api/collection/rl-monitoring')
    def monitoring(model_id: str, rtc_delay: int = None, edit_scale: float = None):
        state = manager.status()
        current = state.get('model') or {}
        model = current if current.get('id') == model_id else next((m for m in catalog() if m['id'] == model_id), None)
        if not model:
            raise HTTPException(404, 'Unknown model')
        try:
            selected = (model.get('execution_settings') or {}).get('expo')
            if state.get('phase') == 'offline' or current.get('id') != model_id or selected is None:
                selected = (model.get('expo_online_options') or {}).get('defaults')
                if rtc_delay is not None or edit_scale is not None:
                    from .execution_options import expo_contract
                    selected = expo_contract()['normalize'](dict(rtc_delay=rtc_delay, edit_scale=edit_scale))
            return reader.read(model, state, selected)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except (OSError, ImportError) as exc:
            raise HTTPException(503, 'Monitoring unavailable: ' + str(exc))
