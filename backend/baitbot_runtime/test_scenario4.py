from __future__ import annotations

import importlib
import json

from fastapi.testclient import TestClient


try:
    app_module = importlib.import_module("app")
except ModuleNotFoundError:
    app_module = importlib.import_module("backend.baitbot_runtime.app")


class FakeScenarioRag:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def assess(self, query: str) -> dict:
        self.queries.append(query)
        suspicious = "안전계좌" in query or "원격제어앱" in query
        return {
            "suspicion_percent": 100 if suspicious else 20,
            "scam_state": "PHISHING_CONFIRMED" if suspicious else "NORMAL",
            "handoff_available": suspicious,
            "results": [{"id": "fake_scam" if suspicious else "fake_normal", "is_benign": not suspicious}],
        }

    def retrieve(self, query: str, *, top_k: int, include_benign: bool) -> list[dict]:
        del query, top_k, include_benign
        return []

    def health(self) -> dict:
        return {"document_count": 1, "retrievable_count": 1, "candidate_count": 0, "benign_count": 1}


class FakeClient:
    def __init__(self, *failures: str) -> None:
        self.failures = set(failures)
        self.requests: list[dict] = []

    async def complete(self, *, operation, messages, model, reasoning, json_output):
        self.requests.append(
            {
                "client_id": id(self),
                "operation": operation,
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "model": model,
                "reasoning": reasoning,
                "json_output": json_output,
            }
        )
        if operation in self.failures:
            raise app_module.ProviderError(f"{operation}_failed")
        if operation == "caller":
            prompt = messages[0]["content"]
            if "평범한 모르는 발신자" in prompt:
                return "안녕하세요, 배송 가능 시간을 확인하려고 전화드렸습니다."
            return "검찰 보안확인 건입니다. 안전계좌와 원격제어앱을 지금 비밀로 확인해 주세요."
        if operation == "responder":
            return {"text": "네, 조금 더 천천히 말씀해 주세요.", "intent": "CLARIFY", "end_call": False}
        if operation == "extractor":
            return {"patches": []}
        raise AssertionError(f"unexpected operation: {operation}")


def _start(client: TestClient, *, mode: str = "SCAMMER") -> dict:
    response = client.post(
        "/api/scenario4/caller",
        json={"mode": mode, "conversation": [], "model": "test/model", "reasoning": "high"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body.get("scenario_id"), str) and body["scenario_id"]
    return body


def _handoff_payload(body: dict, turn: int) -> dict:
    payload = {
        "scenario_id": body["scenario_id"],
        "mode": body["mode"],
        "conversation": body["conversation"],
        "baitbot_turn": turn,
        "model": body["model"],
        "reasoning": body["reasoning"],
    }
    if "session_snapshot" in body:
        payload["session_snapshot"] = body["session_snapshot"]
    return payload


def _new_runtime(fake_client: FakeClient) -> None:
    app_module.runtime = app_module.BaitbotRuntime(client=fake_client, scenario_rag=FakeScenarioRag())


def _check_sequence_and_mirrors() -> None:
    fake_client = FakeClient()
    _new_runtime(fake_client)
    with TestClient(app_module.app) as client:
        assert client.get("/api/config").status_code == 200
        assert client.post("/api/scenario4/caller", json={"mode": "INVALID"}).status_code == 422
        assert client.post(
            "/api/scenario4/caller",
            json={"mode": "SCAMMER", "conversation": [{"speaker": "SCAMMER", "text": "잘못된 역할"}]},
        ).status_code == 422

        start = _start(client)
        scenario_id = start["scenario_id"]
        assert [entry["speaker"] for entry in start["conversation"]] == ["CALLER"]
        assert start["caller_message"] == start["conversation"][-1]["text"]
        assert start["handoff_available"] is True

        optimistic_conversation = start["conversation"] + [{"speaker": "USER", "text": "무슨 확인인가요?"}]
        reply = client.post(
            "/api/scenario4/caller",
            json={
                "scenario_id": scenario_id,
                "mode": "SCAMMER",
                "conversation": optimistic_conversation,
                "message": "무슨 확인인가요?",
                "model": "test/model",
                "reasoning": "high",
            },
        )
        assert reply.status_code == 200, reply.text
        current = reply.json()
        assert current["scenario_id"] == scenario_id
        assert [entry["speaker"] for entry in current["conversation"]] == ["CALLER", "USER", "CALLER"]

        caller_payload = {
            "scenario_id": scenario_id,
            "mode": "SCAMMER",
            "conversation": current["conversation"],
            "message": "계속 말씀해 주세요.",
            "model": "test/model",
            "reasoning": "high",
        }
        assert client.post("/api/scenario4/caller", json={key: value for key, value in caller_payload.items() if key != "scenario_id"}).status_code == 409
        for key, value in (("mode", "NORMAL"), ("model", "other/model"), ("reasoning", "low")):
            changed = dict(caller_payload)
            changed[key] = value
            assert client.post("/api/scenario4/caller", json=changed).status_code == 409
        stale_caller = dict(caller_payload)
        stale_caller["conversation"] = start["conversation"]
        assert client.post("/api/scenario4/caller", json=stale_caller).status_code == 409

        first_payload = _handoff_payload(current, 1)
        assert client.post("/api/scenario4/handoff", json=_handoff_payload(current, 2)).status_code == 409
        wrong_id = dict(first_payload)
        wrong_id["scenario_id"] = "scenario_wrong"
        assert client.post("/api/scenario4/handoff", json=wrong_id).status_code == 409
        for key, value in (("mode", "NORMAL"), ("model", "other/model"), ("reasoning", "low")):
            changed = dict(first_payload)
            changed[key] = value
            assert client.post("/api/scenario4/handoff", json=changed).status_code == 409

        before_handoff = len(fake_client.requests)
        first = client.post("/api/scenario4/handoff", json=first_payload)
        assert first.status_code == 200, first.text
        current = first.json()
        handoff_requests = fake_client.requests[before_handoff:]
        assert {request["operation"] for request in handoff_requests} == {"caller", "responder", "extractor"}
        assert {request["client_id"] for request in fake_client.requests} == {id(fake_client)}
        assert {request["model"] for request in handoff_requests} == {"test/model"}
        assert {request["reasoning"] for request in handoff_requests} == {"high"}
        for request in handoff_requests:
            if request["operation"] in {"responder", "extractor"}:
                assert "무슨 확인인가요?" in json.dumps(request["messages"], ensure_ascii=False)
        assert current["session_snapshot"]["conversation"]
        assert current["ended"] is False and current["baitbot_turn"] == 1

        for _ in range(2):
            assert client.post("/api/scenario4/handoff", json=first_payload).status_code == 409
        assert client.post("/api/scenario4/caller", json=caller_payload).status_code == 409

        tampered_snapshot = json.loads(json.dumps(current["session_snapshot"]))
        tampered_snapshot["session_id"] = "session_000000000000"
        tampered = _handoff_payload(current, 2)
        tampered["session_snapshot"] = tampered_snapshot
        assert client.post("/api/scenario4/handoff", json=tampered).status_code == 409
        stale_handoff = _handoff_payload(current, 2)
        stale_handoff["conversation"] = current["conversation"][:-1]
        assert client.post("/api/scenario4/handoff", json=stale_handoff).status_code == 409
        assert client.post("/api/scenario4/handoff", json=_handoff_payload(current, 3)).status_code == 409

        for turn in range(2, 6):
            response = client.post("/api/scenario4/handoff", json=_handoff_payload(current, turn))
            assert response.status_code == 200, response.text
            current = response.json()
            assert current["scenario_id"] == scenario_id and current["baitbot_turn"] == turn
            assert current["ended"] is (turn == 5)
            if turn < 5:
                assert current["caller_message"]

        assert current["call_state"] == "ENDED"
        assert "caller_message" not in current
        assert "통화는 여기서 마치겠습니다" in current["baitbot_message"]
        final_requests = fake_client.requests[before_handoff:]
        final_responder = [request for request in final_requests if request["operation"] == "responder"][-1]
        assert '"end_call":true' in final_responder["messages"][0]["content"]
        assert client.post("/api/scenario4/handoff", json=_handoff_payload(current, 5)).status_code == 409
        assert client.post("/api/scenario4/handoff", json=_handoff_payload(current, 6)).status_code == 409
        assert client.post(
            "/api/scenario4/caller",
            json={
                "scenario_id": scenario_id,
                "mode": "SCAMMER",
                "conversation": current["conversation"],
                "message": "다시 시작할게요.",
                "model": "test/model",
                "reasoning": "high",
            },
        ).status_code == 409

        assert client.post("/api/reset").status_code == 200
        normal = _start(client, mode="NORMAL")
        assert normal["handoff_available"] is False
        assert client.post("/api/scenario4/handoff", json=_handoff_payload(normal, 1)).status_code == 409


def _check_conversation_budget() -> None:
    fake_client = FakeClient()
    _new_runtime(fake_client)
    huge_conversation = [
        {"speaker": "CALLER" if index % 2 == 0 else "USER", "text": "x" * 4000}
        for index in range(200)
    ]
    with TestClient(app_module.app) as client:
        before = len(fake_client.requests)
        assert client.post(
            "/api/scenario4/caller",
            json={"mode": "SCAMMER", "conversation": huge_conversation, "model": "test/model", "reasoning": "high"},
        ).status_code == 422
        assert len(fake_client.requests) == before

        start = _start(client)
        before = len(fake_client.requests)
        caller_body = {
            "scenario_id": start["scenario_id"],
            "mode": "SCAMMER",
            "conversation": huge_conversation,
            "message": "x" * 4000,
            "model": "test/model",
            "reasoning": "high",
        }
        assert client.post("/api/scenario4/caller", json=caller_body).status_code == 422
        handoff_body = _handoff_payload(start, 1)
        handoff_body["conversation"] = huge_conversation
        assert client.post("/api/scenario4/handoff", json=handoff_body).status_code == 422
        assert len(fake_client.requests) == before


def _check_partial_handoffs() -> None:
    for failures, expected_status in (("extractor", 207), (("responder", "extractor"), 502)):
        failure_names = (failures,) if isinstance(failures, str) else failures
        fake_client = FakeClient(*failure_names)
        _new_runtime(fake_client)
        with TestClient(app_module.app) as client:
            start = _start(client)
            payload = _handoff_payload(start, 1)
            response = client.post("/api/scenario4/handoff", json=payload)
            assert response.status_code == expected_status, response.text
            body = response.json()
            assert body["scenario_id"] == start["scenario_id"]
            assert body["session_snapshot"] and body["conversation"]
            assert len(body["errors"]) == len(failure_names)
            assert client.post("/api/scenario4/handoff", json=payload).status_code == 409


def main() -> None:
    original_runtime = app_module.runtime
    try:
        _check_sequence_and_mirrors()
        _check_conversation_budget()
        _check_partial_handoffs()
    finally:
        app_module.runtime = original_runtime
    print("test_scenario4: PASS")


if __name__ == "__main__":
    main()
