# Baitbot runtime

PowerShell에서 실행합니다.

```powershell
Set-Location C:\Users\SSAFY\Desktop\VoicePhishing
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\baitbot_runtime\requirements.txt
python -m uvicorn backend.baitbot_runtime.app:app --reload --host 127.0.0.1
```

브라우저에서 `http://127.0.0.1:8000/`을 엽니다. LLM은 로컬 Codex CLI의
ChatGPT OAuth 세션을 사용하며 모델은 `gpt-5.5`, 추론 수준은 `low`로
고정됩니다. 화면의 `GPT 로그인` 버튼으로 브라우저 인증을 시작할 수 있습니다.
버튼을 쓸 수 없으면 최초 실행 전 같은 사용자 계정에서 직접 로그인합니다.

```powershell
codex login
codex login status
```

백엔드는 `codex login status`의 로그인 여부만 확인하고 OAuth 토큰, 이메일,
CLI 원문을 응답·로그에 넣지 않습니다. `auth.json`을 직접 읽거나 복사·업로드하지
마세요. `/api/auth/login`은 `127.0.0.1`/`::1`에서만 `X-Baitbot-Local: 1`
헤더와 함께 호출할 수 있으며, Vercel 등 공개 런타임에서는 OAuth 로그인을
거부합니다. 따라서 저장소의 기존 Vercel 주소는 정적 화면 확인용이며, 이 OAuth
LLM 데모의 실제 실행 대상은 로컬 서버입니다.

서버 실행마다 `backend\baitbot_runtime\logs\runtime_<UTC timestamp>_<run id>.jsonl`에
JSONL 이벤트 로그가 생성됩니다. 로그에는 인증 토큰, 이메일, Authorization 값,
CLI stdout/stderr 원문을 기록하지 않습니다.

실제 API 호출 없이 최소 점검을 실행하려면 다음을 사용합니다.

```powershell
python backend\baitbot_runtime\event_log.py
python backend\baitbot_runtime\test_runtime.py
python backend\baitbot_runtime\test_scenario_rag.py
python backend\baitbot_runtime\test_scenario4.py
python backend\baitbot_runtime\test_api_status.py
python backend\baitbot_runtime\test_event_privacy.py
python validation\architecture_invariants.py
```

Scenario RAG는 `scenario_corpus.json`의 `VERIFIED` 문서만 기본 검색하며,
커뮤니티·재연·소셜 자료는 `CANDIDATE`로 격리합니다. 검색 결과는 `/api/chat`
응답의 `scenario_rag`와 JSONL의 `scenario_rag.completed` 이벤트에서 확인할 수
있습니다.
