from fastapi.testclient import TestClient
from segmented_capture.api import create_app
from tests.test_segmented_capture_http import FakeService, FakeBridge

def test_browse_prefix_directories_and_create_missing(project_tmp):
    root=project_tmp/"data";root.mkdir()
    (root/"plug").mkdir();(root/"plug_v2").mkdir();(root/"test").mkdir()
    (root/"file.txt").write_text("not a directory");(root/".segments").mkdir()
    with TestClient(create_app(service=FakeService(project_tmp),bridge=FakeBridge(),allowed_data_root=root)) as c:
        url="/api/segmented-teach/storage/directories"
        p=c.get(url,params={"path":str(root)+"/"})
        assert p.status_code==200 and p.json()["directories"]==[str(root/x) for x in ("plug","plug_v2","test")]
        assert c.get(url,params={"path":str(root)+"/pl"}).json()["directories"]==[str(root/"plug"),str(root/"plug_v2")]
        new=root/"plug_v3/demonstrations"
        assert c.get(url,params={"path":str(new)+"/"}).json()["exists"] is False
        assert not new.exists()
        response=c.post("/api/segmented-teach/storage/prepare",json={"data_root":str(new),"storage_layout":"flat"})
        assert response.status_code==200 and new.is_dir()
        assert response.json()["episode_directory"]==str(new)
        assert not (new/"plug").exists()

def test_browse_confined_no_symlink_or_file_leak(project_tmp):
    root=project_tmp/"data";root.mkdir()
    outside=project_tmp/"private";outside.mkdir();(outside/"secret").mkdir()
    (root/"escape").symlink_to(outside,target_is_directory=True)
    (root/"inside").mkdir();(root/"alias").symlink_to(root/"inside",target_is_directory=True)
    (root/"file").write_text("file")
    with TestClient(create_app(service=FakeService(project_tmp),bridge=FakeBridge(),allowed_data_root=root)) as c:
        url="/api/segmented-teach/storage/directories"
        assert c.get(url,params={"path":str(root)+"/"}).json()["directories"]==[str(root/"inside")]
        for path in (str(outside)+"/",str(root)+"/../private/",str(root/"escape")+"/","relative/",str(root)+"-other/"):
            assert c.get(url,params={"path":path}).status_code==422
        assert c.get(url,params={"path":str(root/"file")+"/"}).status_code==422


def test_registered_legacy_root_stays_accessible_without_allowing_other_paths(project_tmp, monkeypatch):
    import json
    primary=project_tmp/"new";legacy=project_tmp/"legacy";private=project_tmp/"private"
    for path in (primary,legacy,private):path.mkdir()
    (legacy/"episode-set").mkdir()
    monkeypatch.setenv("COBOT_ADDITIONAL_DATA_ROOTS",json.dumps([str(legacy)]))
    with TestClient(create_app(service=FakeService(project_tmp),bridge=FakeBridge(),allowed_data_root=primary)) as client:
        url="/api/segmented-teach/storage/directories"
        result=client.get(url,params={"path":str(legacy)+"/"})
        assert result.status_code==200 and result.json()["directories"]==[str(legacy/"episode-set")]
        assert client.get(url,params={"path":str(private)+"/"}).status_code==422
        (legacy/"escape").symlink_to(private,target_is_directory=True)
        assert client.get(url,params={"path":str(legacy/"escape")+"/"}).status_code==422
