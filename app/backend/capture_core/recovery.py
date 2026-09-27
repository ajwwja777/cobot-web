"""Quarantine closed, explicitly failed native files; never salvage them for training."""
import json,os,re,hashlib
from pathlib import Path
from uuid import UUID
import h5py
from .labels import LabelValidationError
RE=re.compile(r"^episode_([0-9]{6})\.hdf5\.incomplete$")

def quarantine_failed_files(directory,identity):
    directory=Path(directory).resolve(strict=True)
    archived=[]
    for path in sorted(directory.glob('episode_*.hdf5.incomplete')):
        match=RE.fullmatch(path.name)
        if not match or path.is_symlink() or path.resolve()!=path:continue
        try:
            # Native file lock rejects a live writer; never bypass HDF locking.
            with h5py.File(path,'r+') as h:
                if str(h.attrs.get('completion_state',''))!='error':continue
                expected=(identity.task_id,identity.model_id,identity.dataset_round)
                actual=tuple(str(h.attrs.get(k,'')) for k in ('task_id','model_id','dataset_round'))
                if actual!=expected:continue
                uuid=str(UUID(str(h.attrs['episode_uuid'])))
                index=int(h.attrs['episode_index'])
                if index!=int(match[1]):raise LabelValidationError('failed_episode_identity_mismatch')
                reason=str(h.attrs.get('failure_reason','recorder_error'))
        except OSError:
            # Still-open/corrupt files remain blocked by normal inspection.
            continue
        folder=directory/'.failed'/uuid
        for parent in (directory/'.failed',folder):
            if parent.is_symlink():raise LabelValidationError('failed_archive_symlink')
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/path.name
        if target.exists():raise LabelValidationError('failed_archive_collision')
        receipt=directory/('episode_%06d.failed.json'%index)
        if receipt.is_symlink():raise LabelValidationError('failed_receipt_symlink')
        if receipt.exists():
            old=json.loads(receipt.read_text())
            if old.get('episode_uuid')!=uuid:raise LabelValidationError('failed_receipt_identity_mismatch')
        item={'status':'quarantining','episode_uuid':uuid,'episode_index':index,
              'source':str(path),'archive':str(target),'failure_reason':reason,'keep_for_training':False,
              'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        def write():
            tmp=receipt.with_suffix('.json.tmp')
            with tmp.open('w') as f:json.dump(item,f,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(tmp,receipt)
        write();path.rename(target);item['status']='quarantined';write()
        archived.append(item)
    return archived
