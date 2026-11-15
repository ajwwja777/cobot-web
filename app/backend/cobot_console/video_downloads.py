"""Read-only original video catalogue and streamed, range-capable downloads."""
from pathlib import Path
import json
import re
from typing import Optional
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import FileResponse
from capture_core.storage import _reject_symlink_components
from .paths import DATA

CAMERAS = ("camera_left", "camera_high", "camera_right")
RECORD_ID = re.compile(r"(?:motion-[0-9a-f]{32}|eval-\d{8}T\d{6}-[0-9a-f]{8})-[0-9a-f]{8}")


class VideoDownloads:
    def __init__(self, resolve, *, default_root=DATA/"videos/deployment", extra_roots=lambda:[]):
        self.resolve=resolve;self.default_root=Path(default_root);self.extra_roots=extra_roots

    def root(self, value=None):
        return self.resolve(str(value or self.default_root))

    def record(self, root, identity):
        root=self.root(root)
        if not RECORD_ID.fullmatch(identity):raise ValueError("invalid_video_record")
        folder=root/identity;_reject_symlink_components(folder)
        manifest=folder/"video.json"
        if not folder.is_dir() or folder.resolve().parent!=root or manifest.is_symlink() or not manifest.is_file():
            raise ValueError("video_not_found")
        if manifest.stat().st_size>65536:raise ValueError("invalid_video_manifest")
        value=json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(value,dict) or (value.get("uncompressed") is not True and value.get("format")!="mp4") or not isinstance(value.get("cameras"),dict):
            raise ValueError("not_an_original_video")
        browser={};sidecar=folder/"browser-video.json"
        if sidecar.is_file() and not sidecar.is_symlink() and sidecar.stat().st_size<=65536:
            try:browser=json.loads(sidecar.read_text()).get("cameras",{})
            except (ValueError,AttributeError):pass
        files=[]
        if value.get("state") in {"saved","error"}:
            for key in CAMERAS:
                camera=value["cameras"].get(key) or {}
                if not isinstance(camera,dict):continue
                name=camera.get("file")
                if name not in {key+suffix for suffix in (".avi",".incomplete.avi",".mp4",".incomplete.mp4")}:continue
                # Old originals remain unchanged. Prefer a verified finalized MP4 copy.
                if name==key+".avi":
                    copy=browser.get(key,{}) if isinstance(browser,dict) else {}
                    source=folder/name
                    if isinstance(copy,dict) and source.is_file() and not source.is_symlink():
                        stat=source.stat()
                        if copy.get("file")==key+".mp4" and copy.get("source_bytes")==stat.st_size and copy.get("source_mtime_ns")==stat.st_mtime_ns and (folder/(key+".mp4")).is_file():name=key+".mp4"
                path=folder/name
                if path.is_symlink() or not path.is_file() or path.resolve().parent!=folder:continue
                size=path.stat().st_size
                if not size:continue
                files.append({"camera":key,"name":name,"bytes":size,"frames":camera.get("frames"),
                    "playable":name.endswith(".mp4"), "incomplete":value.get("state")!="saved" or ".incomplete." in name,
                    "download_url":"/api/videos/file?"+urlencode({"data_root":str(root),"record_id":identity,"camera":key})})
        return {"id":identity,"data_root":str(root),"state":value.get("state"),"error":value.get("error"),
                "saved_at":manifest.stat().st_mtime,"run_id":value.get("run_id"),"files":files,
                "total_bytes":sum(item["bytes"] for item in files)}

    def records(self, data_root=None):
        roots=[self.root(data_root)] if data_root else [self.root()]
        if not data_root:
            for value in self.extra_roots():
                try:
                    candidate=self.root(value)
                    if candidate not in roots:roots.append(candidate)
                except (ValueError,OSError,HTTPException):continue
        rows=[]
        for root in roots:
            if not root.is_dir():continue
            for folder in root.iterdir():
                if not RECORD_ID.fullmatch(folder.name) or folder.is_symlink():continue
                try:rows.append(self.record(root,folder.name))
                except (ValueError,OSError,TypeError):continue
        rows.sort(key=lambda item:(item["saved_at"],item["id"]),reverse=True)
        return {"data_root":str(roots[0]),"roots":[str(root) for root in roots],"records":rows}

    def file(self, root, identity, camera):
        if camera not in CAMERAS:raise ValueError("invalid_camera")
        record=self.record(root,identity)
        item=next((item for item in record["files"] if item["camera"]==camera),None)
        if item is None:raise ValueError("video_not_ready")
        return Path(record["data_root"])/identity/item["name"],item


def install_routes(app, resolve, motion, manager):
    def known_roots():
        videos=[motion.state.get("video"),motion.videos.status(),manager.videos.status()]
        return [str(Path(video["directory"]).parent) for video in videos if isinstance(video,dict) and video.get("directory")]
    downloads=VideoDownloads(resolve,extra_roots=known_roots)
    app.state.video_downloads=downloads

    @app.get("/api/videos/records")
    def records(data_root:Optional[str]=None):
        try:return downloads.records(data_root)
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

    @app.api_route("/api/videos/file",methods=["GET","HEAD"])
    def file(data_root:str,record_id:str,camera:str,download:bool=True):
        try:path,item=downloads.file(data_root,record_id,camera)
        except (ValueError,OSError,TypeError) as exc:raise HTTPException(404,str(exc)) from exc
        suffix=(".incomplete" if item["incomplete"] else "")+path.suffix
        # FileResponse streams from disk and supports HTTP byte ranges; no blob,
        # re-encoding, archive packing or whole-video allocation is involved.
        return FileResponse(path,media_type="video/mp4" if path.suffix==".mp4" else "video/x-msvideo",filename=record_id+"_"+camera+suffix,
                            content_disposition_type="attachment" if download else "inline",headers={"Cache-Control":"private, no-store"})
