# Baitbot runtime

PowerShell에서 실행합니다.

```powershell
Set-Location C:\Users\SSAFY\Desktop\VoicePhishing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\baitbot_runtime\requirements.txt
python -m uvicorn backend.baitbot_runtime.app:app --reload
```

브라우저에서 `http://127.0.0.1:8000/`을 엽니다. 서버는
`C:\Users\SSAFY\Desktop\.env` 또는 프로젝트 루트 `.env`의 `google_ai_studio` (또는 `GEMINI_API_KEY`, `GOOGLE_API_KEY`)를 `python-dotenv`로 읽고, 기본 모델은
`gemini-2.0-flash-lite`, 추론 수준은 `low`입니다. 기본값은
`GEMINI_MODEL`, `GEMINI_REASONING_EFFORT`로 바꿀 수 있습니다. (`gemini-2.5-flash-lite` 입력 시 `gemini-2.0-flash-lite`로 자동 매핑 지원) Gemini 요청에는 추론 설정을
전달하지 않으며, 기존 UI/API 필드는 호환성을 위해 유지합니다.

서버 실행마다 `backend\baitbot_runtime\logs\runtime_<UTC timestamp>_<run id>.jsonl`에
JSONL 이벤트 로그가 생성됩니다. 로그에는 API 키, Authorization 헤더, 자격증명 값을
넣지 마세요. 요청 본문은 미들웨어가 기록하지 않습니다.

실제 API 호출 없이 최소 점검을 실행하려면 다음을 사용합니다.

```powershell
python backend\baitbot_runtime\event_log.py
python backend\baitbot_runtime\test_runtime.py
python backend\baitbot_runtime\test_scenario_rag.py
```

Scenario RAG는 `scenario_corpus.json`의 `VERIFIED` 문서만 기본 검색하며, 커뮤니티·재연·소셜 자료는 `CANDIDATE`로 격리합니다. 검색 결과는 `/api/chat` 응답의 `scenario_rag`와 JSONL의 `scenario_rag.completed` 이벤트에서 확인할 수 있습니다.
