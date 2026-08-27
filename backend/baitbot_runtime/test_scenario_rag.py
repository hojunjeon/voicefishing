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
    assert not any(item["is_benign"] for item in candidate_results)
    no_match = rag.retrieve("xqzv981273")
    assert no_match and all(item["is_benign"] and item["score"] == 0 for item in no_match)
    assert not rag.retrieve("xqzv981273", include_benign=False)

    scam_cases = (
        (
            "card_delivery",
            "scn_card_delivery_001",
            "신청하지 않은 카드가 배송된다고 배송기사가 전화했습니다. 명의도용과 대포통장 연루라고 하며 가짜 카드사 고객센터로 다시 전화하라고 하고 검사에게 연결한다며 성명과 생년월일을 물었습니다.",
        ),
        (
            "remote_control_malware",
            "scn_card_delivery_002",
            "카드 배송 오류와 명의도용을 확인한다며 휴대전화 보안점검이 필요하다고 했습니다. 원격제어앱 APK 설치 후 인증번호를 말하고 안전계좌로 자산검수 이체하라고 즉시 지시했습니다.",
        ),
        (
            "institution_impersonation",
            "scn_institution_001",
            "검찰 수사관이라며 제 명의가 범죄에 연루돼 무혐의를 입증하려면 가족에게 비밀로 하고 조용한 곳으로 이동하라고 했습니다. 새 휴대전화를 개통하고 자산을 안전계좌로 옮겨 정시 보고하라고 합니다.",
        ),
        (
            "loan_offer",
            "scn_loan_001",
            "정부지원 저금리 대환대출을 해 준다며 카카오톡 친구추가를 하라고 했습니다. 신청서 APK를 설치하고 기초정보와 기존 대출을 확인해 주면 즉시 심사한다고 합니다.",
        ),
        (
            "loan_transfer",
            "scn_loan_002",
            "기존 대출 계약을 위반했다며 오늘 안에 위약금을 변제해야 한다고 합니다. 예금을 해지해 현금으로 인출하고 수거책에게 전달하라고 하며 은행 문진에는 거짓말하라고 지시했습니다.",
        ),
        (
            "family_kidnap",
            "scn_child_kidnap_001",
            "아이 이름을 부르며 울음소리를 들려주고 아이를 차량에 감금했다고 합니다. 위치를 공개하겠다며 지금 합의금을 지정 계좌로 송금하고 통화를 끊지 말라고 합니다.",
        ),
        (
            "policy_security",
            "scn_policy_issue_001",
            "정부 소비쿠폰 지급을 안내한다며 문자 URL을 눌러 사고접수를 하라고 했습니다. 유심 해킹 보안점검이라며 개인정보를 입력하고 원격제어앱 설치와 통화 가로채기를 허용하라고 합니다.",
        ),
        (
            "public_procurement",
            "scn_public_noshow_001",
            "시청 계약 담당자라며 대규모 납품 계약과 소방 점검이 급하다고 공문과 명함을 보냈습니다. 취급하지 않는 장비를 허위 공급업체에서 구매해 지정 계좌로 선입금하라고 합니다.",
        ),
    )
    for name, expected_id, scam_query in scam_cases:
        assessment = rag.assess(scam_query)
        assert set(assessment) == {"suspicion_percent", "scam_state", "handoff_available", "results"}
        assert assessment["results"][0]["id"] == expected_id, (name, assessment)
        assert 0 <= assessment["suspicion_percent"] <= 100, (name, assessment)
        assert assessment["suspicion_percent"] >= 80, (name, assessment)
        assert assessment["scam_state"] == "PHISHING_CONFIRMED", (name, assessment)
        assert assessment["handoff_available"] is True, (name, assessment)

    normal_cases = (
        ("ordinary_delivery", "주문한 택배가 오늘 도착한다는데 주문번호로 확인했고 배송 기사님께 수령 가능 시간을 알려드렸어요."),
        ("requested_bank_callback", "제가 은행 앱에서 상담을 신청해서 고객센터에서 콜백이 왔고 송금이나 앱 설치 요청은 없었습니다."),
        ("personal_family_chat", "엄마에게 저녁 먹고 집에 들어간다고 전화했어요."),
        ("legitimate_business_purchase", "제가 운영하는 카페에 손님이 직접 전화해 주문한 커피와 빵을 내일 매장에서 결제하고 받아 가기로 했습니다. 지정 업체나 선입금 요청은 없었습니다."),
        ("unrelated", "xqzv981273"),
    )
    for name, normal_query in normal_cases:
        assessment = rag.assess(normal_query)
        assert 0 <= assessment["suspicion_percent"] <= 100, (name, assessment)
        assert assessment["suspicion_percent"] < 80, (name, assessment)
        assert assessment["scam_state"] != "PHISHING_CONFIRMED", (name, assessment)
        assert assessment["handoff_available"] is False, (name, assessment)

    intermediate = rag.assess("카드배송 문제라면서 은행 고객센터와 연결해 준다고 합니다. 개인정보를 알려 달래요.")
    assert 40 <= intermediate["suspicion_percent"] < 80
    assert intermediate["scam_state"] == "SUSPECTED"
    assert intermediate["handoff_available"] is False
    for percent, expected in (
        (39, ("NORMAL", False)),
        (40, ("SUSPECTED", False)),
        (79, ("SUSPECTED", False)),
        (80, ("PHISHING_CONFIRMED", True)),
    ):
        assert ScenarioRAG._state_for_suspicion(percent) == expected
    assert "openrouter_api" not in json.dumps(results).lower()
    if BaitbotRuntime is not None:
        asyncio.run(check_runtime_integration())
        print("test_scenario_rag: PASS")
    else:
        print("test_scenario_rag: PASS (retrieval; runtime integration blocked by missing python-dotenv)")


if __name__ == "__main__":
    main()
