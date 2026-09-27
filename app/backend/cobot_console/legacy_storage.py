"""Pure storage selection/history contract for the new camera cohort."""
import json,os
from pathlib import Path
from cobot_console.paths import RLT as ROOT, LEGACY_DATA
RUN=ROOT/'runs/plug_v2'
ALLOWED=LEGACY_DATA
BASE=ALLOWED/'rlt/plug_v2'
SETTINGS=RUN/'backend/storage.json'


def validate_root(value):
    p=Path(str(value))
    if not p.is_absolute():raise ValueError('录制目录必须是绝对路径')
    resolved=p.resolve();resolved.relative_to(ALLOWED.resolve())
    if resolved==ALLOWED.resolve():raise ValueError('请选择data下面的子目录')
    for candidate in [p]+list(p.parents):
        if candidate==ALLOWED:break
        if candidate.is_symlink():raise ValueError('录制目录不能经过符号链接')
    try:resolved.relative_to(BASE/'demonstrations')
    except ValueError:pass
    else:raise ValueError('RLT rollout不可写入本版专家demonstrations目录')
    for meta in resolved.glob('episode_*.rlt.json'):
        item=json.loads(meta.read_text())
        if item.get('cohort')!='plug_v2':raise ValueError('目录含其他相机版本RLT数据，请选择独立目录')
    return resolved


def settings():
    return json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {'current':{},'history':{}}


def selected_root(phase):
    if phase not in ('warmup','online'):raise ValueError('invalid rollout phase')
    return validate_root(settings().get('current',{}).get(phase,str(BASE/phase)))


def roots_for_phase(phase):
    values=[str(BASE/phase),str(selected_root(phase))]+settings().get('history',{}).get(phase,[])
    return list(dict.fromkeys(validate_root(v) for v in values))


def history(root):
    items=[]
    for p in Path(root).glob('episode_*.rlt.json'):
        try:
            d=json.loads(p.read_text())
            if d.get('cohort')!='plug_v2' or d.get('outcome') not in ('success','failure'):continue
            if not d.get('recording_enabled',False) or d.get('shadow',False):continue
            items.append(d)
        except (OSError,ValueError):continue
    return sorted(items,key=lambda d:int(d['episode_index']))


def releases_for_phase(phase):
    found={}
    for root in roots_for_phase(phase):
        for p in (root/'lerobot').glob('*/conversion.json'):
            d=json.loads(p.read_text());meta=d.get('metadata',{})
            if d.get('cohort')!='plug_v2':continue
            if meta.get('data_phase',phase)!=phase:continue
            found[d['source_uuid']]=p
    return sorted(found.values())
