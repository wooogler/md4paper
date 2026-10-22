"""모델에 보내는 기록 창과 비용 상한 — 긴 세션에서도 턴이 막히지 않게.

- 기록 창: 최근 N개 질문만, 사용자 질문 경계에서만 자르고, 앞선 턴의 긴 tool 결과는 줄인다.
  세션 파일의 대화는 그대로. 승인 재개·대기 무시 새 질문에서도 tool 호출과 결과가 갈라지지 않는다.
- 턴 상한은 요청 수·tool 호출 수로만 (토큰 합 상한은 없앴다).
- 세션 비용 상한: 넘으면 400 cost_cap, override면 그 턴만 진행.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai import DeferredToolResults, RunContext  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDenied  # noqa: E402

from md4paper import config  # noqa: E402
from md4paper.ui.agent import agent as agent_mod  # noqa: E402
from md4paper.ui.agent import retrieval, routes, sessions  # noqa: E402
from md4paper.ui.agent.agent import (  # noqa: E402
    DEFAULT_LIMITS,
    OLD_RESULT_NOTE,
    make_agent,
    run_turn,
    window_messages,
)
from md4paper.ui.agent.context import ScreenContext  # noqa: E402
from md4paper.ui.agent.tools import Deps  # noqa: E402

BIG = "B" * 2000     # 줄이기 기준(1500자)을 넘는 tool 결과


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _run(agent, session, text, deps, **kw) -> list[dict]:
    async def go():
        return [ev async for ev in run_turn(agent, session, text, deps, **kw)]
    return asyncio.run(go())


def _deps(reg: Registry, tok: str | None) -> Deps:
    ctx = ScreenContext.model_validate({"screen": "review" if tok else "home",
                                        "paper": {"token": tok} if tok else None})
    return Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register)


def _exchange(i: int, result: str = BIG) -> list:
    """질문 하나 = 질문 → tool 호출 → tool 결과 → 답."""
    return [ModelRequest(parts=[UserPromptPart(f"q{i}")]),
            ModelResponse(parts=[ToolCallPart("big", {}, tool_call_id=f"c{i}")]),
            ModelRequest(parts=[ToolReturnPart("big", f"r{i}:" + result, tool_call_id=f"c{i}")]),
            ModelResponse(parts=[TextPart(f"a{i}")])]


def _questions(msgs: list) -> list[str]:
    return [p.content for m in msgs if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, UserPromptPart)]


def _returns(msgs: list) -> dict[str, str]:
    return {p.tool_call_id: str(p.content) for m in msgs if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, ToolReturnPart)}


def _return_list(msgs: list) -> list[str]:
    """tool 결과들 (대본 모델의 호출 id는 턴마다 겹쳐서 dict로는 못 센다)."""
    return [str(p.content) for m in msgs if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, ToolReturnPart)]


def _calls(msgs: list) -> set[str]:
    return {p.tool_call_id for m in msgs if isinstance(m, ModelResponse)
            for p in m.parts if isinstance(p, ToolCallPart)}


def test_window_cuts_at_question_boundary_and_shrinks_old_results():
    msgs = [m for i in range(10) for m in _exchange(i)]
    before = json.dumps([str(m) for m in msgs])
    out = window_messages(msgs, turns=8, keep_full=2)

    assert _questions(out) == [f"q{i}" for i in range(2, 10)]          # 최근 8개 질문
    first = out[0]
    assert isinstance(first, ModelRequest) and isinstance(first.parts[0], UserPromptPart)
    assert _calls(out) == set(_returns(out))                           # 호출·결과가 짝으로 남는다
    rets = _returns(out)
    for i in range(2, 8):                                              # 앞선 턴은 줄인다
        assert rets[f"c{i}"].startswith(f"r{i}:B") and rets[f"c{i}"].endswith(OLD_RESULT_NOTE)
        assert len(rets[f"c{i}"]) < 400
    assert rets["c8"] == "r8:" + BIG and rets["c9"] == "r9:" + BIG     # 최근 2개 질문은 그대로
    assert json.dumps([str(m) for m in msgs]) == before                # 원래 기록은 그대로

    short = window_messages(msgs[:12], turns=8, keep_full=2)           # 질문 3개 — 자르지 않는다
    assert _questions(short) == ["q0", "q1", "q2"] and _returns(short)["c0"].endswith(
        OLD_RESULT_NOTE)
    small = [m for i in range(3) for m in _exchange(i, "짧음")]
    assert window_messages(small, turns=8)[2].parts[0].content == "r0:짧음"  # 짧으면 그대로


def test_window_never_splits_a_denied_call_from_its_result():
    """대기 무시 새 질문 = `[tool 결과(거절), 사용자 질문]` 요청 — 경계가 아니므로 앞 호출과 같이 남는다."""
    msgs = _exchange(0) + [
        ModelRequest(parts=[UserPromptPart("메모 써 줘")]),
        ModelResponse(parts=[ToolCallPart("write_note", {"text": "x"}, tool_call_id="w1")]),
        ModelRequest(parts=[ToolReturnPart("write_note", "거절", tool_call_id="w1"),
                            UserPromptPart("딴 얘기")]),
    ]
    out = window_messages(msgs, turns=1, keep_full=1)
    assert _questions(out) == ["메모 써 줘", "딴 얘기"]
    assert "w1" in _calls(out) and _returns(out)["w1"] == "거절"
    assert sessions.is_turn_start(msgs[0]) and not sessions.is_turn_start(msgs[-1])


def _big_agent(received: list, steps: list | None = None):  # noqa: ANN202
    agent = make_agent(scripted_model(steps or [[("big", {})], "답"], received=received))

    @agent.tool
    async def big(ctx: RunContext[Deps]) -> str:
        """테스트용 — 긴 결과를 돌려준다."""
        return BIG

    return agent


def test_long_session_sends_window_but_saves_everything(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    s = sessions.create()
    received: list = []
    agent = _big_agent(received)
    for i in range(10):
        received.clear()
        assert _run(agent, s, f"질문 {i}", _deps(reg, tok))[-1]["event"] == "done"

    first, second = received                 # 마지막 턴: tool 호출 전 요청, 결과를 본 뒤 요청
    assert _questions(first) == [f"질문 {i}" for i in range(2, 10)]
    rets = _return_list(first)
    assert len(rets) == 7 and sum(r == BIG for r in rets) == 1          # 바로 앞 턴만 그대로
    assert all(r.endswith(OLD_RESULT_NOTE) for r in rets if r != BIG)
    assert _return_list(second)[-2:] == [BIG, BIG]                      # 이번 턴 결과도 그대로

    back = sessions.load(s.id)
    assert len(back.conversation.messages) == 40                        # 파일에는 전부
    assert _return_list(back.conversation.messages) == [BIG] * 10
    assert len([t for t in back.turns if t["role"] == "user"]) == 10


def test_resume_after_approval_keeps_pending_call_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "HISTORY_TURNS", 1)
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    s = sessions.create()
    received: list = []
    agent = _big_agent(received, [[("write_x", {"text": "hi"})], "썼어요."])

    @agent.tool(requires_approval=True)
    async def write_x(ctx: RunContext[Deps], text: str) -> str:
        """테스트용 쓰기 tool."""
        return f"wrote {text}"

    plain = _big_agent([], ["그냥 답"])
    for i in range(3):
        _run(plain, s, f"앞 질문 {i}", _deps(reg, tok))
    events = _run(agent, s, "써 줘", _deps(reg, tok))
    cid = events[-2]["data"]["calls"][0]["id"]

    received.clear()
    events = _run(agent, sessions.load(s.id), None, _deps(reg, tok),
                  deferred_tool_results=DeferredToolResults(approvals={cid: True}))
    assert events[-1]["event"] == "done"
    sent = received[0]
    assert _questions(sent) == ["써 줘"]                                  # 창은 1개 질문
    assert cid in _calls(sent) and _returns(sent)[cid] == "wrote hi"

    # 대기를 두고 새 질문 — 거절 결과와 그 호출이 같이 간다
    s = sessions.load(s.id)
    events = _run(agent, s, "또 써 줘", _deps(reg, tok))
    cid2 = events[-2]["data"]["calls"][0]["id"]
    received.clear()
    deny = DeferredToolResults(approvals={cid2: ToolDenied("넘어감")})
    _run(make_agent(scripted_model(["알겠어요."], received=received)), sessions.load(s.id),
         "딴 얘기", _deps(reg, tok), deferred_tool_results=deny)
    sent = received[0]
    assert _questions(sent) == ["또 써 줘", "딴 얘기"]
    assert cid2 in _calls(sent) and _returns(sent)[cid2] == "넘어감"


def test_turn_limits_count_requests_and_tool_calls_not_tokens():
    assert DEFAULT_LIMITS.total_tokens_limit is None
    assert DEFAULT_LIMITS.input_tokens_limit is None
    assert DEFAULT_LIMITS.request_limit == 12 and DEFAULT_LIMITS.tool_calls_limit == 20


# --- 세션 비용 상한 ---------------------------------------------------------


def test_cost_cap_config_roundtrip():
    assert config.resolve_agent_cost_cap() == 2.0
    config.set_agent_cost_cap(3.5)
    assert config.resolve_agent_cost_cap() == 3.5
    assert "session_cost_cap = 3.5" in config.CONFIG_PATH.read_text(encoding="utf-8")  # 숫자로
    config.set_section_value("agent", "session_cost_cap", "1.25")      # 손으로 고친 문자열
    assert config.resolve_agent_cost_cap() == 1.25
    config.set_section_value("agent", "session_cost_cap", "많이")
    assert config.resolve_agent_cost_cap() == 2.0
    config.set_agent_cost_cap(-1)
    assert config.resolve_agent_cost_cap() == 0.0
    config.set_agent_cost_cap(None)
    assert config.resolve_agent_cost_cap() == 2.0


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_cost_cap_blocks_turn_until_override(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register,
                           build_agent=lambda: make_agent(scripted_model(["네."])))
    client = TestClient(app)
    sid = client.post("/agent/sessions").json()["id"]
    assert client.get(f"/agent/sessions/{sid}").json()["cost_cap"] == 2.0

    s = sessions.load(sid)
    s.usage["cost_usd"] = 2.0
    sessions.save(s)
    body = {"message": "계속 물어볼게", "context": {"screen": "review", "paper": {"token": tok}}}
    r = client.post(f"/agent/sessions/{sid}/turns", json=body)
    assert r.status_code == 400
    err = r.json()
    assert err["code"] == "cost_cap" and err["cost_usd"] == 2.0 and err["cost_cap"] == 2.0
    assert "상한" in err["error"]
    assert not routes._BUSY and sessions.load(sid).turns == []           # 턴을 시작도 안 했다

    r = client.post(f"/agent/sessions/{sid}/turns", json={**body, "override": True})
    assert r.status_code == 200 and _sse(r.text)[-1][0] == "done"
    # override는 그 턴만 — 다음 턴은 다시 막힌다
    assert client.post(f"/agent/sessions/{sid}/turns", json=body).status_code == 400

    config.set_agent_cost_cap(5)                                          # 상한을 올리면 풀린다
    assert client.post(f"/agent/sessions/{sid}/turns", json=body).status_code == 200
    config.set_agent_cost_cap(0)                                          # 0 = 상한 없음
    s = sessions.load(sid)
    s.usage["cost_usd"] = 99.0
    sessions.save(s)
    assert client.post(f"/agent/sessions/{sid}/turns", json=body).status_code == 200
    assert client.get(f"/agent/sessions/{sid}").json()["cost_cap"] == 0.0
