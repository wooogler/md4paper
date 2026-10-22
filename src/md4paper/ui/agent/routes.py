"""에이전트 창 HTTP 라우트 — 세션 CRUD, 턴(SSE 스트림), 모델 선택.

서버에는 클라이언트별 상태가 없다. 창은 매 요청에 세션 id와 화면 맥락을 실어 보내고, 서버는 세션
파일을 읽어 턴을 돌린 뒤 다시 저장한다. 그래서 화면을 옮겨도·앱을 다시 켜도 대화가 이어진다.

논문은 요청의 맥락에 든 token으로만 찾는다. 모르는 token은 404 — 뷰어 자산 라우트처럼
`state["current_wd"]`로 폴백하지 않는다(창이 여럿이면 엉뚱한 논문을 읽게 된다). 그러니 app.py는
`wd_for`로 **폴백 없는** 조회(`state["wd_by_token"].get`)를 넘겨야 한다.

승인: 쓰기 tool을 부른 턴은 `approval_needed`로 끝나고 세션에 대기 호출이 남는다. 창은 같은 턴
라우트에 `approvals: {call_id: true|false}`를 보내 재개한다(빠진 id는 거절). 대기 중인데 approvals
없이 새 질문이 오면 대기 호출을 모두 거절로 닫고 그 질문을 잇는다 — 사용자가 카드를 무시하고
넘어간 것이다(닫지 않으면 Pydantic AI가 새 질문을 받지 않는다).

비용: 세션 누적 비용이 `config.resolve_agent_cost_cap()`(기본 $2)에 닿으면 턴을 시작하지 않고
400 `{"code": "cost_cap"}`. 창이 확인을 받아 `override: true`로 다시 보내면 그 턴만 허용한다.

`build_agent`는 인자 없는 공장 함수다(기본: 설정의 [chat] 모델 + 그 키). 테스트는 FunctionModel
에이전트를 돌려주는 람다를 끼운다 — 옛 `chat_panel.register_routes`의 build_provider 주입과 같은 꼴.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from md4paper import config
from md4paper.ui.agent import sessions
from md4paper.ui.agent.agent import default_agent, run_turn
from md4paper.ui.agent.context import ScreenContext
from md4paper.ui.agent.tools import Deps
from md4paper.workdir import WorkDir

MAX_MESSAGE = 4000     # 질문 길이 상한
DENIED = "사용자가 거절했습니다."
SKIPPED = "사용자가 승인하지 않고 새 질문으로 넘어갔습니다. 이 작업은 실행되지 않았습니다."

# 지금 턴이 도는 세션 → 시작 시각. 한 세션에 턴 둘이 겹치면 대화 기록이 갈라지므로 두 번째는 409.
# 스트림이 시작도 못 하고 끊기면(finally가 안 돎) 표시가 남으므로 BUSY_TTL이 지나면 풀린 것으로 본다.
_BUSY: dict[str, float] = {}
_busy_lock = threading.Lock()
BUSY_TTL = 600.0


def model_options() -> list[dict]:
    """창 드롭다운에 채울 모델 목록 — 제공사별 저렴→비쌈, 키 없는 건 표시만 하고 못 고른다."""
    from md4paper.llm.base import PRICING

    keys = config.key_status()
    out: list[dict] = []
    for prov in config.PROVIDERS:
        for mid in config.MODEL_TIERS.get(prov, ()):
            pr = PRICING.get(mid)
            out.append({"provider": prov, "model": mid,
                        "price": (f"${pr[0]:g}/${pr[1]:g}" if pr else ""),
                        "has_key": bool(keys.get(prov))})
    return out


def readiness(make: Callable[[], object] | None = None) -> tuple[bool, str]:
    """에이전트를 만들 수 있는지 (준비됐나?, 안내 메시지). 네트워크는 건드리지 않는다."""
    try:
        (make or default_agent)()
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — 설정 파일 손상 등도 안내로 보여준다
        return False, str(e) or e.__class__.__name__
    return True, ""


def _detail(s: sessions.Session) -> dict:
    """세션 하나를 창에 그릴 형태로."""
    return {**s.meta(), "paper_tokens": s.paper_tokens, "turns": s.turns,
            "pending": s.pending, "usage": s.usage, "cost_cap": config.resolve_agent_cost_cap()}


def _sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"


def register_routes(fastapi_app, wd_for: Callable[[str], WorkDir | None],  # noqa: ANN001
                    register_token: Callable[[WorkDir], str],
                    build_agent: Callable[[], object] | None = None,
                    workspace: Callable[[], Path] | None = None) -> None:
    """`/agent/...` 라우트 등록.

    wd_for: 토큰 → WorkDir (모르면 None; 폴백 금지).
    register_token: WorkDir → 토큰 (list_papers가 찾은 논문을 wd_for로 다시 찾을 수 있게 등록).
    build_agent: 인자 없는 에이전트 공장 (기본: `agent.default_agent`).
    workspace: list_papers가 훑을 작업 폴더를 돌려주는 함수 (기본: config 작업 폴더).
    """
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import ValidationError
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.tools import ToolDenied
    from starlette.exceptions import HTTPException

    make = build_agent or default_agent

    def _results(ids: list[str], ok: dict[str, bool], pending: dict,
                 why: str = DENIED) -> DeferredToolResults:
        """승인/거절 → 재개 입력. token 없이 부른 쓰기가 승인을 요청한 때의 논문에 쓰도록
        그 논문을 호출별 metadata로 싣는다 (tool은 ctx.tool_call_metadata["token"]으로 받는다)."""
        meta = {"token": pending.get("token")}
        return DeferredToolResults(
            approvals={i: True if ok.get(i) else ToolDenied(why) for i in ids},
            metadata={i: dict(meta) for i in ids})

    def _models_payload() -> dict:
        ok, reason = readiness(make)
        provider, model = config.resolve_chat_choice()
        return {"provider": provider, "model": model, "models": model_options(),
                "ready": ok, "reason": reason}

    def _load_or_404(sid: str) -> sessions.Session:
        s = sessions.load(sid)
        if s is None:
            raise HTTPException(status_code=404)
        return s

    @fastapi_app.get("/agent/sessions")
    def _list():  # noqa: ANN202 — 세션 목록 (최근순)
        return {"sessions": sessions.list_sessions()}

    @fastapi_app.post("/agent/sessions")
    def _create(body: dict | None = None):  # noqa: ANN202 — 새 세션
        title = str((body or {}).get("title") or "").strip() or None
        return _detail(sessions.create(title=title))

    @fastapi_app.get("/agent/sessions/{sid}")
    def _get(sid: str):  # noqa: ANN202 — 세션 본문 (턴·승인 대기·사용량)
        return _detail(_load_or_404(sid))

    @fastapi_app.patch("/agent/sessions/{sid}")
    def _rename(sid: str, body: dict):  # noqa: ANN202 — 이름 바꾸기
        s = sessions.rename(sid, str((body or {}).get("title") or ""))
        if s is None:
            raise HTTPException(status_code=404)
        return s.meta()

    @fastapi_app.delete("/agent/sessions/{sid}")
    def _delete(sid: str):  # noqa: ANN202 — 세션 지우기
        if not sessions.delete(sid):
            raise HTTPException(status_code=404)
        return {"ok": True}

    @fastapi_app.post("/agent/sessions/{sid}/turns")
    async def _turn(sid: str, body: dict):  # noqa: ANN202 — 질문(또는 승인) → SSE 스트림
        s = await asyncio.to_thread(sessions.load, sid)
        if s is None:
            raise HTTPException(status_code=404)
        body = body or {}
        try:
            ctx = ScreenContext.model_validate(body.get("context") or {})
        except ValidationError as e:
            return JSONResponse({"error": f"화면 맥락 형식 오류: {e.errors()[:1]}"},
                                status_code=400)
        tok = ctx.token
        if tok and wd_for(tok) is None:
            raise HTTPException(status_code=404, detail="알 수 없는 논문 token")

        approvals = body.get("approvals")
        deferred = None
        message = str(body.get("message") or "").strip()[:MAX_MESSAGE]
        pending = s.pending
        if approvals:
            if pending is None or not isinstance(approvals, dict):
                return JSONResponse({"error": "승인 대기 중인 작업이 없습니다."}, status_code=400)
            want = [c["id"] for c in pending["calls"]]
            unknown = set(map(str, approvals)) - set(want)
            if unknown:
                return JSONResponse({"error": f"대기 중이 아닌 작업입니다: {sorted(unknown)}"},
                                    status_code=400)
            got = {str(k): v for k, v in approvals.items()}
            # 일부만 와도 된다 — 답이 없는 호출은 거절로 닫는다(Pydantic AI는 전부 답해야 재개한다)
            deferred = _results(want, {i: got.get(i) is True for i in want}, pending)
        elif pending is not None and message:
            # 승인 카드를 두고 새 질문 — 대기 호출을 모두 거절로 닫고 질문을 잇는다
            want = [c["id"] for c in pending["calls"]]
            deferred = _results(want, dict.fromkeys(want, False), pending, why=SKIPPED)
        elif not message:
            return JSONResponse({"error": "질문이 비어 있습니다."}, status_code=400)

        cap = config.resolve_agent_cost_cap()
        spent = float(s.usage.get("cost_usd") or 0.0)
        if cap > 0 and spent >= cap and body.get("override") is not True:
            return JSONResponse(
                {"error": (f"이 세션의 누적 비용(${spent:.4f})이 상한 ${cap:g}에 닿았습니다. "
                           "계속하면 이번 질문만 상한을 넘겨 진행합니다."),
                 "code": "cost_cap", "cost_usd": round(spent, 6), "cost_cap": cap},
                status_code=400)

        try:
            agent = make()
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        with _busy_lock:
            if time.time() - _BUSY.get(sid, 0.0) < BUSY_TTL:
                return JSONResponse({"error": "이 세션에서 이미 답을 만드는 중입니다."},
                                    status_code=409)
            _BUSY[sid] = time.time()

        deps = Deps(wd_for=wd_for, current_token=tok, ctx=ctx, register_token=register_token,
                    workspace=workspace() if workspace else None)

        async def stream():  # noqa: ANN202
            try:
                # 승인 재개도 질문과 함께 올 수 있다 (approvals + message, 또는 새 질문이 대기를 닫을 때)
                async for ev in run_turn(agent, s, message or None, deps,
                                         deferred_tool_results=deferred):
                    yield _sse(ev)
            except Exception as e:  # noqa: BLE001 — run_turn 밖 실패도 스트림 안에서 알린다
                yield _sse({"event": "error", "data": {"message": str(e)[:200]}})
            finally:
                with _busy_lock:
                    _BUSY.pop(sid, None)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @fastapi_app.get("/agent/models")
    def _models():  # noqa: ANN202 — 모델 목록 + 지금 선택 + 키 준비 상태
        return _models_payload()

    @fastapi_app.put("/agent/model")
    def _put_model(body: dict):  # noqa: ANN202 — 에이전트가 쓸 모델 고르기 ([chat] 설정 공유)
        prov = str((body or {}).get("provider") or "").strip()
        mid = str((body or {}).get("model") or "").strip()
        try:
            config.set_chat_choice(prov or None, mid or None)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True, **_models_payload()}
