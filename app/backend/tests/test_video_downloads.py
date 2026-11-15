import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cobot_console.video_downloads import install_routes, CAMERAS
from capture_core.storage import _reject_symlink_components

IDENTITY="motion-"+"a"*32+"-"+"b"*8

@pytest.fixture
def archive(tmp_path):
    root=tmp_path/"videos";folder=root/IDENTITY;folder.mkdir(parents=True)
    manifest={"state":"saved","uncompressed":False,"format":"mp4","cameras":{}}
    for key in CAMERAS:
        (folder/(key+".mp4")).write_bytes(bytes(range(256)))
        manifest["cameras"][key]={"file":key+".mp4","frames":30}
    (folder/"video.json").write_text(json.dumps(manifest))
    def resolve(value):
        path=Path(value);_reject_symlink_components(path)
        if not path.is_absolute() or root!=path.resolve():raise ValueError("outside")
        return path.resolve()
    app=FastAPI();fake=SimpleNamespace(state={},videos=SimpleNamespace(status=lambda:None))
    install_routes(app,resolve,fake,fake);app.state.video_downloads.default_root=root
    with TestClient(app) as client:yield client,root,folder,manifest

def params(root,**kw):return dict(data_root=str(root),record_id=IDENTITY,camera="camera_left",**kw)

def test_catalogue_preview_attachment_and_exact_range(archive):
    client,root,folder,_=archive
    value=client.get("/api/videos/records").json()["records"][0]
    assert len(value["files"])==3 and value["total_bytes"]==768
    assert all(item["playable"] for item in value["files"])
    response=client.get("/api/videos/file",params=params(root),headers={"Range":"bytes=0-15"})
    assert response.status_code==206 and response.content==bytes(range(16))
    assert response.headers["content-range"]=="bytes 0-15/256"
    assert response.headers["content-type"]=="video/mp4"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["accept-ranges"]=="bytes"
    assert client.head("/api/videos/file",params=params(root,download="false")).headers["content-disposition"].startswith("inline;")

def test_five_gib_stream_is_not_buffered_and_can_resume(archive):
    client,root,folder,_=archive
    with (folder/"camera_left.mp4").open("wb") as stream:stream.seek(5*1024**3);stream.write(b"end")
    response=client.head("/api/videos/file",params=params(root))
    assert response.status_code==200 and not response.content
    assert int(response.headers["content-length"])==5*1024**3+3
    response=client.get("/api/videos/file",params=params(root),headers={"Range":"bytes=-3"})
    assert response.status_code==206 and response.content==b"end"
    assert client.get("/api/videos/file",params=params(root),headers={"Range":"bytes=99999999999-"}).status_code==416

def test_paths_symlinks_manifest_names_and_recording_rejected(archive,tmp_path):
    client,root,folder,manifest=archive
    assert client.get("/api/videos/records",params={"data_root":str(tmp_path)}).status_code==422
    for fields in [{"record_id":"../secret"},{"camera":"../secret"}]:
        q=params(root);q.update(fields);assert client.get("/api/videos/file",params=q).status_code==404
    path=folder/"camera_left.mp4";path.unlink();path.symlink_to(folder/"camera_high.mp4")
    assert client.get("/api/videos/file",params=params(root)).status_code==404
    path.unlink();path.write_bytes(b"test")
    manifest["cameras"]["camera_left"]["file"]="../../secret"
    (folder/"video.json").write_text(json.dumps(manifest))
    assert client.get("/api/videos/file",params=params(root)).status_code==404
    manifest["state"]="recording";(folder/"video.json").write_text(json.dumps(manifest))
    assert client.get("/api/videos/records").json()["records"][0]["files"]==[]

def test_legacy_copy_preferred_only_when_source_matches_and_partial_explicit(archive):
    client,root,folder,manifest=archive
    manifest.update(uncompressed=True);manifest.pop("format")
    for key in CAMERAS:
        path=folder/(key+".avi");path.write_bytes(b"original")
        manifest["cameras"][key]["file"]=path.name
    (folder/"video.json").write_text(json.dumps(manifest));source=folder/"camera_left.avi"
    copy={"file":"camera_left.mp4","source_bytes":source.stat().st_size,"source_mtime_ns":source.stat().st_mtime_ns}
    (folder/"browser-video.json").write_text(json.dumps({"cameras":{"camera_left":copy}}))
    assert client.head("/api/videos/file",params=params(root)).headers["content-type"]=="video/mp4"
    source.write_bytes(b"changed")
    assert client.head("/api/videos/file",params=params(root)).headers["content-type"]=="video/x-msvideo"
    manifest.update(state="error",uncompressed=False,format="mp4")
    manifest["cameras"]["camera_left"]["file"]="camera_left.incomplete.mp4"
    (folder/"camera_left.incomplete.mp4").write_bytes(b"partial")
    (folder/"video.json").write_text(json.dumps(manifest))
    row=client.get("/api/videos/records").json()["records"][0]
    assert row["files"][0]["incomplete"]
