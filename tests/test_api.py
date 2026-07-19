from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def configure_test_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("environment", "test")
    monkeypatch.setenv("data_dir", str(tmp_path))
    monkeypatch.setenv("OPERATOR_TOKEN", "test-operator-token")
    for name in (
        "APIFY_API_TOKEN",
        "HiddenLayer_API_ClientID",
        "HiddenLayer_API_ClientSecret",
        "HIDDENLAYER_CLIENT_ID",
        "HIDDENLAYER_CLIENT_SECRET",
        "NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY",
        "NVIDIA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def operator_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/operator/login", json={"token": "test-operator-token"}
    )
    assert response.status_code == 200
    return {"x-csrf-token": response.json()["csrf_token"]}


def test_operator_session_and_fixture_pipeline(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/demo/fixtures/clean-ai-tool/inject").status_code == 401
        headers = operator_headers(client)
        response = client.post(
            "/api/demo/fixtures/clean-ai-tool/inject", headers=headers
        )
        assert response.status_code == 200

        scene = client.get("/api/scene").json()
        assert scene["trust_state"] == "NORMAL"
        assert scene["cursor"] > 0

        events = client.get("/api/events").json()
        boundaries = {
            event["payload"].get("boundary")
            for event in events
            if event["type"] == "scan_completed"
        }
        assert boundaries == {
            "ingest",
            "prompt",
            "response",
            "tool_arguments",
            "tool_result",
        }
        assert any(event["type"] == "tool_completed" for event in events)


def test_restricted_approval_executes_once(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        headers = operator_headers(client)
        response = client.post(
            "/api/demo/fixtures/restricted-injection/inject", headers=headers
        )
        assert response.status_code == 200
        approvals = client.get("/api/approvals").json()
        assert len(approvals) == 1
        approval_id = approvals[0]["id"]

        approved = client.post(
            f"/api/approvals/{approval_id}/approve", headers=headers
        )
        assert approved.status_code == 200
        repeated = client.post(
            f"/api/approvals/{approval_id}/approve", headers=headers
        )
        assert repeated.status_code == 404
        assert len(client.get("/api/briefs").json()) == 1
        evidence = client.get(
            f"/api/evidence/{approvals[0]['source_item_id']}"
        ).json()
        assert evidence["source"]["processing_status"] == "completed"


def test_csrf_and_session_tampering_are_rejected(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        operator_headers(client)
        assert client.post("/api/demo/reset").status_code == 403
        client.cookies.set("hidden_tower_operator", "tampered.value")
        assert client.get("/api/operator/session").status_code == 401


def test_resume_while_normal_does_not_emit_state_change(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        headers = operator_headers(client)
        before = client.get("/api/events").json()

        response = client.post("/api/state/resume", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"trust_state": "NORMAL"}
        after = client.get("/api/events").json()
        assert [
            event
            for event in after[len(before) :]
            if event["type"] == "state_changed"
        ] == []


def test_malicious_tool_argument_is_blocked(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        headers = operator_headers(client)
        response = client.post(
            "/api/demo/fixtures/malicious-tool-arguments/inject", headers=headers
        )
        assert response.status_code == 200
        events = client.get("/api/events").json()
        assert any(
            event["type"] == "scan_completed"
            and event["payload"].get("boundary") == "tool_arguments"
            and event["payload"].get("action") == "Block"
            for event in events
        )
        assert any(event["type"] == "tool_blocked" for event in events)
        source_id = next(
            event["source_item_id"]
            for event in events
            if event["type"] == "content_received"
        )
        evidence = client.get(f"/api/evidence/{source_id}").json()
        assert evidence["source"]["processing_status"] == "blocked"


def test_malicious_tool_result_is_blocked(monkeypatch, tmp_path: Path) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        headers = operator_headers(client)
        response = client.post(
            "/api/demo/fixtures/malicious-tool-result/inject", headers=headers
        )
        assert response.status_code == 200
        events = client.get("/api/events").json()
        assert any(
            event["type"] == "scan_completed"
            and event["payload"].get("boundary") == "tool_result"
            and event["payload"].get("action") == "Block"
            for event in events
        )
        assert any(
            event["type"] == "tool_blocked"
            and event["payload"].get("reason") == "result_scan"
            for event in events
        )
        source_id = next(
            event["source_item_id"]
            for event in events
            if event["type"] == "content_received"
        )
        evidence = client.get(f"/api/evidence/{source_id}").json()
        assert evidence["source"]["processing_status"] == "blocked"


def test_websocket_replay_resumes_after_cursor_without_duplicates(
    monkeypatch, tmp_path: Path
) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    with TestClient(app) as client:
        headers = operator_headers(client)
        client.post("/api/demo/fixtures/clean-ai-tool/inject", headers=headers)
        events = client.get("/api/events?limit=500").json()
        cursor = events[-3]["id"]
        expected = [event["id"] for event in events if event["id"] > cursor][:2]
        with client.websocket_connect(f"/ws/events?after_id={cursor}") as websocket:
            received = [websocket.receive_json()["id"] for _ in range(2)]
        assert received == expected
        assert len(received) == len(set(received))
