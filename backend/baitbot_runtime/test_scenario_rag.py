from __future__ import annotations

import asyncio
import json

try:
    from .scenario_rag import ScenarioRAG
except ImportError:
    from scenario_rag import ScenarioRAG

try:
    from .runtime import BaitbotRuntime
except ImportError:
    try:
        from runtime import BaitbotRuntime
    except ModuleNotFoundError as error:
        if getattr(error, "name", None) != "dotenv":
            raise
        BaitbotRuntime = None


class FakeClient:
    async def complete(self, *, operation, messages, model, reasoning, json_output):
        del messages, model, reasoning, json_output
        if operation == "responder":
            return {"text": "네, 천천히 다시 말씀해 주세요.", "intent": "CONTINUE", "end_call": False}
        return {"turn_id": "turn_0001", "patches": []}


async def check_runtime_integration() -> None:
    records: list[dict] = []
    runtime = BaitbotRuntime(
        client=FakeClient(),
        event_logger=lambda event, **fields: records.append({"event": event, **fields}),
    )
    result = await runtime.process("검찰이라고 하면서 안전계좌로 보내라고 합니다.")
    assert result["scenario_rag"]["status"] == "SEARCHED"
    assert result["scenario_rag"]["results"]
    assert any(record["event"] == "scenario_rag.completed" for record in records)


def main() -> None:
    rag = ScenarioRAG()
    health = rag.health()
    assert health["document_count"] == 13
    assert health["retrievable_count"] == 10
    assert health["candidate_count"] == 3

    results = rag.retrieve("검찰 명의도용 안전계좌 원격제어 앱 설치", top_k=5)
    assert results
    assert all(item["review_status"] == "VERIFIED" for item in results)
    assert results[0]["is_benign"] is False
    assert any(item["is_benign"] for item in results)
    assert "scn_card_delivery_002" in {item["id"] for item in results}

    candidate_rag = ScenarioRAG(include_candidate=True)
    candidate_results = candidate_rag.retrieve(
        "법원등기 가짜 사이트 주민번호 입력", top_k=20, include_benign=False
    )
    assert "candidate_community_court_delivery_001" in {
        item["id"] for item in candidate_results
    }
    assert "openrouter_api" not in json.dumps(results).lower()
    if BaitbotRuntime is not None:
        asyncio.run(check_runtime_integration())
        print("test_scenario_rag: PASS")
    else:
        print("test_scenario_rag: PASS (retrieval; runtime integration blocked by missing python-dotenv)")


if __name__ == "__main__":
    main()
