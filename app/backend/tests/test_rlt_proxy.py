"""Loopback RLT proxy and lifecycle registry tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobot_console.rlt_proxy import (
    RltBackendClient,
    RltBackendError,
    RltLifecycleRegistry,
)


class FakeResponse:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.payload = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else payload
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_client_uses_fixed_loopback_and_json_contract():
    opener = FakeOpener(FakeResponse({"phase": "ready", "generation": 7}))
    client = RltBackendClient(opener=opener, timeout_seconds=1.25)

    response = client.request("GET", "/api/session")

    assert response.status == 200
    assert response.payload["phase"] == "ready"
    request, timeout = opener.requests[0]
    assert request.full_url == "http://127.0.0.1:8016/api/session"
    assert timeout == 1.25


def test_client_rejects_non_loopback_base_and_invalid_payload():
    with pytest.raises(ValueError, match="loopback"):
        RltBackendClient(base_url="http://10.7.165.64:8016")
    client = RltBackendClient(
        opener=FakeOpener(FakeResponse(b"<!doctype html>", content_type="text/html"))
    )
    with pytest.raises(RltBackendError, match="not_json"):
        client.request("GET", "/api/session")


def test_client_rejects_absolute_or_unknown_paths():
    client = RltBackendClient(opener=FakeOpener(FakeResponse({})))
    with pytest.raises(ValueError, match="invalid_backend_path"):
        client.request("GET", "http://example.com/")
    with pytest.raises(ValueError, match="invalid_backend_path"):
        client.request("GET", "/not-allowed")


def test_registry_missing_is_offline(tmp_path: Path):
    state = RltLifecycleRegistry(tmp_path / "state.json").read()
    assert state.phase == "offline"
    assert state.generation is None


def test_registry_validates_schema_and_staleness(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "generation": "gen-1",
                "phase": "ready_disarmed",
                "supervisor_pid": 123,
                "step": 4000,
                "mode": "eval",
                "updated_at": "2026-09-16T10:00:00Z",
                "error_code": None,
            }
        ),
        encoding="utf-8",
    )
    state = RltLifecycleRegistry(path).read()
    assert state.phase == "fault"
    assert state.error_code == "supervisor_unavailable"
    assert state.supervisor_pid == 123

    path.write_text('{"phase":"surprise"}', encoding="utf-8")
    with pytest.raises(RltBackendError, match="invalid_lifecycle_state"):
        RltLifecycleRegistry(path).read()


def test_terminal_proxy_has_time_for_data_finalization():
    opener = FakeOpener(FakeResponse({"phase": "replay_committing"}))
    client = RltBackendClient(opener=opener, timeout_seconds=2)
    client.request("POST", "/api/episode/success", {"episode_id": 1, "generation": 3})
    assert opener.requests[0][1] == 60.0
