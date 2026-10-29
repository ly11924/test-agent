"""E2E 测试：HTTP 接口 -> 解析 -> 执行 -> 记录落盘。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.constants import BAD_VOLUME_INSTRUCTION, STRICT_INSTRUCTION, SUCCESS_INSTRUCTION

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CHEM_DATA_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROBOT_FAULT_RATE", "0")
    monkeypatch.setenv("ROBOT_SEED", "2")
    monkeypatch.setenv("TOOL_TIMEOUT_S", "0.2")
    return tmp_path


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_success_end_to_end(isolated_env):
    resp = client.post("/api/experiments", json={"instruction": SUCCESS_INSTRUCTION})
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["status"] == "success"
    assert data["record"]["rounds_executed"] == 2
    assert data["record"]["final_yield_rate"] == 0.65
    assert data["record"]["yield_history"] == [0.55, 0.65]

    run_id = data["run_id"]
    assert (isolated_env / "runs" / f"{run_id}.json").exists()

    fetched = client.get(f"/api/experiments/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["run_id"] == run_id


def test_max_rounds_end_to_end():
    resp = client.post("/api/experiments", json={"instruction": STRICT_INSTRUCTION})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "max_rounds_reached"
    assert data["record"]["rounds_executed"] == 2


def test_invalid_param_rejected_before_tool():
    resp = client.post("/api/experiments", json={"instruction": BAD_VOLUME_INSTRUCTION})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "invalid_param"
    assert "volume_ml" in body["message"]


def test_invalid_instruction_rejected():
    resp = client.post("/api/experiments", json={"instruction": "帮我做一个实验"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_instruction"


def test_options_override_max_rounds():
    resp = client.post(
        "/api/experiments",
        json={"instruction": SUCCESS_INSTRUCTION, "options": {"max_rounds": 1}},
    )
    data = resp.json()["data"]
    assert data["status"] == "max_rounds_reached"
    assert data["round"] == 1


def test_unknown_run_returns_404():
    assert client.get("/api/experiments/not-exists").status_code == 404
