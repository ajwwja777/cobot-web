"""Bounded camera previews; synchronized evidence and independent console streams."""
from __future__ import annotations

import math
import time
from collections import deque
from threading import RLock
from typing import Any, Callable, Dict, Optional

import cv2
import numpy as np

from capture_core.ros_cache import LatestMessageCache

CAMERA_KEYS = ('camera_high', 'camera_left', 'camera_right')

class CameraFrameUnavailable(RuntimeError):
    pass

JPEG_QUALITY = 82


def _jpeg(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError('jpeg encode failed')
    return bytes(encoded)

class SynchronizedCameraPreview:
    def __init__(self, cache: LatestMessageCache, *, clock: Callable[[], float] = time.monotonic,
                 encoder: Callable[[np.ndarray], bytes] = _jpeg, max_skew_seconds: float = .05,
                 freeze_seconds: float = 1.0, preview_fps: float = 20.0) -> None:
        self._cache = cache
        self._clock = clock
        self._encoder = encoder
        self._max_skew = float(max_skew_seconds)
        self._freeze = float(freeze_seconds)
        if not math.isfinite(preview_fps) or preview_fps <= 0 or preview_fps > 20.0:
            raise ValueError('preview_fps must be in (0, 20]')
        self._minimum_period = 1.0 / float(preview_fps)
        self._lock = RLock()
        self._generation = 0
        self._published_sequences: Optional[tuple] = None
        self._last_advance: Optional[float] = None
        self._images: Dict[str, bytes] = {}
        self._shapes: Dict[str, tuple[int, int]] = {}
        self._generation_times = deque(maxlen=60)
        self._state: Dict[str, Any] = {'status':'unavailable','generation':0,'error_code':'camera_unavailable'}
        self._preview = IndependentCameraPreview(
            cache, clock=clock, encoder=encoder, max_skew_seconds=max_skew_seconds,
            freeze_seconds=freeze_seconds, preview_fps=preview_fps)


    def _base(self, snapshot, now: float) -> Dict[str, Any]:
        sequences = {key: snapshot.sequence(key) for key in CAMERA_KEYS}
        ages = {}
        for key in CAMERA_KEYS:
            arrival = snapshot.arrival_timestamp(key)
            ages[key] = None if not math.isfinite(arrival) else max(0.0, now - arrival)
        return {'generation': self._generation, 'sequences': sequences, 'age_sec': ages,
                'last_advance_age_sec': None if self._last_advance is None else max(0.0, now-self._last_advance)}

    def refresh(self) -> Dict[str, Any]:
        now = float(self._clock())
        snapshot = self._cache.snapshot(now)
        base = self._base(snapshot, now)
        sequences = tuple(base['sequences'][key] for key in CAMERA_KEYS)
        with self._lock:
            times = list(self._generation_times)
            fps = 0.0
            if len(times) >= 2 and times[-1] > times[0]:
                fps = (len(times) - 1) / (times[-1] - times[0])
            base.update(
                preview_fps=round(fps, 1),
                jpeg_quality=JPEG_QUALITY,
                resolution={key:list(value) for key,value in self._shapes.items()},
            )
            base['generation'] = self._generation
            base['last_advance_age_sec'] = None if self._last_advance is None else max(0.0, now-self._last_advance)
            if self._generation and sequences == self._published_sequences and base['last_advance_age_sec'] >= self._freeze:
                state = {**base, 'status':'frozen', 'error_code':'camera_frozen', 'stale_keys':[]}
                self._state = state; return dict(state)
            stale = [key for key in CAMERA_KEYS if not snapshot.is_fresh(key)]
            if stale:
                state = {**base, 'status':'stale', 'error_code':'camera_stale', 'stale_keys':stale}
                self._state = state; return dict(state)
            source = [snapshot.source_timestamp(key) for key in CAMERA_KEYS]
            if all(math.isfinite(value) for value in source):
                timestamps = source; timestamp_clock = 'source'
            else:
                timestamps = [snapshot.arrival_timestamp(key) for key in CAMERA_KEYS]
                timestamp_clock = 'arrival'
            skew = max(timestamps)-min(timestamps)
            base.update(timestamp_clock=timestamp_clock, skew_ms=skew*1000.0, stale_keys=[])
            if not math.isfinite(skew) or skew > self._max_skew:
                state = {**base, 'status':'desynced', 'error_code':'camera_desynced'}
                self._state = state; return dict(state)
            if self._generation and sequences == self._published_sequences:
                state = {**base, 'status':'ready', 'error_code':None}
                self._state = state; return dict(state)
            if self._last_advance is not None and now - self._last_advance < self._minimum_period:
                state = {**base, 'status':'ready', 'error_code':None}
                self._state = state; return dict(state)
            encoded: Dict[str, bytes] = {}
            try:
                for key in CAMERA_KEYS:
                    image = snapshot.get(key)
                    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
                        raise ValueError('invalid camera image')
                    encoded[key] = self._encoder(image)
                    self._shapes[key] = (int(image.shape[1]), int(image.shape[0]))
            except Exception:
                state = {**base, 'status':'unavailable', 'error_code':'camera_encode_failed'}
                self._state = state; return dict(state)
            self._images = encoded
            self._published_sequences = sequences
            self._generation += 1
            self._last_advance = now
            if self._generation_times and now - self._generation_times[-1] > 1.0:
                self._generation_times.clear()
            self._generation_times.append(now)
            times = list(self._generation_times)
            fps = 0.0 if len(times) < 2 or times[-1] <= times[0] else (len(times)-1)/(times[-1]-times[0])
            base.update(preview_fps=round(fps,1), jpeg_quality=JPEG_QUALITY,
                        resolution={key:list(value) for key,value in self._shapes.items()})
            state = {**base, 'generation':self._generation, 'last_advance_age_sec':0.0,
                     'status':'ready', 'error_code':None}
            self._state = state
            return dict(state)

    def state(self) -> Dict[str, Any]:
        return self.refresh()

    def image(self, key: str, *, generation: Optional[int] = None) -> bytes:
        if key not in CAMERA_KEYS:
            raise CameraFrameUnavailable('invalid camera key')
        with self._lock:
            if generation is not None and int(generation) != self._generation:
                raise CameraFrameUnavailable('camera generation is no longer current')
            value = self._images.get(key)
            if value is None:
                raise CameraFrameUnavailable('camera frame unavailable')
            return value


    def preview_state(self) -> Dict[str, Any]:
        return self._preview.state()

    def preview_image(self, key: str, *, generation=None, camera_generation=None) -> bytes:
        return self._preview.image(key, generation=generation, camera_generation=camera_generation)


class IndependentCameraPreview:
    """Each console stream advances independently; never substitutes a stale frame."""
    def __init__(self, cache, *, clock=time.monotonic, encoder=_jpeg,
                 max_skew_seconds=.05, freeze_seconds=1.0, preview_fps=20.0):
        self._cache, self._clock, self._encoder = cache, clock, encoder
        self._max_skew, self._freeze = float(max_skew_seconds), float(freeze_seconds)
        self._period = 1.0 / float(preview_fps)
        self._lock = RLock()
        self._generation = 0
        self._streams = {key: dict(generation=0, sequence=0, last_advance=None,
            last_attempt=None, image=None, shape=None, times=deque(maxlen=60),
            status='unavailable', error_code='camera_unavailable') for key in CAMERA_KEYS}

    def state(self) -> Dict[str, Any]:
        with self._lock:
            now = float(self._clock())
            snapshot = self._cache.snapshot(now)
            cameras, updated = {}, False
            for key, stream in self._streams.items():
                sequence = snapshot.sequence(key)
                arrival = snapshot.arrival_timestamp(key)
                age = None if not math.isfinite(arrival) else max(0.0, now-arrival)
                if sequence == 0:
                    stream.update(status='unavailable', error_code='camera_unavailable')
                elif not snapshot.is_fresh(key):
                    stream.update(status='stale', error_code='camera_stale')
                elif (sequence == stream['sequence'] and stream['last_advance'] is not None
                      and now-stream['last_advance'] >= self._freeze):
                    stream.update(status='frozen', error_code='camera_frozen')
                elif sequence != stream['sequence']:
                    attempt = stream['last_attempt']
                    if attempt is None or now-attempt >= self._period:
                        stream['last_attempt'] = now
                        try:
                            image = snapshot.get(key)
                            if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
                                    or image.ndim != 3 or image.shape[-1] != 3):
                                raise ValueError('invalid camera image')
                            encoded = self._encoder(image)
                        except Exception:
                            stream.update(status='unavailable', error_code='camera_encode_failed')
                        else:
                            stream.update(image=encoded, shape=[int(image.shape[1]), int(image.shape[0])],
                                sequence=sequence, generation=stream['generation']+1,
                                last_advance=now, status='ready', error_code=None)
                            times = stream['times']
                            if times and now-times[-1] > 1.0:
                                times.clear()
                            times.append(now)
                            updated = True
                else:
                    stream.update(status='ready', error_code=None)
                times = stream['times']
                fps = 0.0 if len(times) < 2 or times[-1] <= times[0] else (len(times)-1)/(times[-1]-times[0])
                cameras[key] = dict(status=stream['status'], error_code=stream['error_code'],
                    generation=stream['generation'], sequence=sequence, age_sec=age,
                    preview_fps=round(fps, 1) if stream['status'] == 'ready' else 0.0,
                    resolution=stream['shape'], last_advance_age_sec=None if stream['last_advance'] is None
                    else max(0.0, now-stream['last_advance']))
            if updated:
                self._generation += 1
            ready = [key for key in CAMERA_KEYS if cameras[key]['status'] == 'ready']
            skew, clock = None, None
            if len(ready) == len(CAMERA_KEYS):
                timestamps = [snapshot.source_timestamp(key) for key in CAMERA_KEYS]
                clock = 'source'
                if not all(math.isfinite(value) for value in timestamps):
                    timestamps = [snapshot.arrival_timestamp(key) for key in CAMERA_KEYS]
                    clock = 'arrival'
                skew = max(timestamps)-min(timestamps)
            status = ('ready' if math.isfinite(skew) and skew <= self._max_skew else 'desynced') if skew is not None else ('degraded' if ready else 'unavailable')
            advances = [s['last_advance'] for s in self._streams.values() if s['last_advance'] is not None]
            return dict(status=status, generation=self._generation, cameras=cameras,
                error_code=None if status == 'ready' else {'degraded':'camera_partial',
                    'unavailable':'camera_unavailable','desynced':'camera_desynced'}[status],
                sequences={key:cameras[key]['sequence'] for key in CAMERA_KEYS},
                age_sec={key:cameras[key]['age_sec'] for key in CAMERA_KEYS},
                stale_keys=[key for key in CAMERA_KEYS if key not in ready],
                available_keys=ready, preview_fps=max((cameras[key]['preview_fps'] for key in ready), default=0.0),
                jpeg_quality=JPEG_QUALITY,
                resolution={key:cameras[key]['resolution'] for key in CAMERA_KEYS},
                skew_ms=None if skew is None else skew*1000.0, timestamp_clock=clock,
                last_advance_age_sec=None if not advances else max(0.0, now-max(advances)))

    def image(self, key, *, generation=None, camera_generation=None):
        if key not in CAMERA_KEYS:
            raise CameraFrameUnavailable('invalid camera key')
        with self._lock:
            stream = self._streams[key]
            if generation is not None and int(generation) != self._generation:
                raise CameraFrameUnavailable('camera generation is no longer current')
            if camera_generation is not None and int(camera_generation) != stream['generation']:
                raise CameraFrameUnavailable('camera generation is no longer current')
            if (stream['status'] != 'ready' or stream['image'] is None
                    or not self._cache.snapshot(float(self._clock())).is_fresh(key)):
                raise CameraFrameUnavailable('camera frame unavailable')
            return stream['image']
