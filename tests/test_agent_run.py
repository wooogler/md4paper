"""에이전트 한 턴 — FunctionModel 대본으로 tool 호출·인용 칩·화면 동작·비용·승인 재개를 확인."""

from __future__ import annotations

import asyncio
import re
import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, item, make_wd, scripted_model, tool_returns, write_notes  # noqa: E402
from pydantic_ai import DeferredToolResults, RunContext, UsageLimits  # noqa: E402

from narani import config  # noqa: E402
from narani.llm.base import Usage, cost_usd  # noqa: E402
from narani.ui.agent import retrieval, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent, run_turn  # noqa: E402
from narani.ui.agent.context import ScreenContext  # noqa: E402
from narani.ui.agent.tools import Deps, review_url  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()


def _run(agent, session, text, deps, **kw) -> list[dict]:
    async def go():
        return [ev async for ev in run_turn(agent, session, text, deps, **kw)]
    return asyncio.run(go())


def _names(events: list[dict]) -> list[str]:
    return [e["event"] for e in events]


def _first_row(messages: list, prefix: str = "r") -> int:
    m = re.search(r"\[" + prefix + r"(\d+)\]", "\n".join(tool_returns(messages)))
    assert m, "tool 결과에 행 표기가 없다"
    return int(m.group(1))


def _deps(reg: Registry, tok: str | None, **kw) -> Deps:
    ctx = ScreenContext.model_validate({"screen": "review" if tok else "home",
                                        "paper": {"token": tok} if tok else None})
    return Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register, **kw)


def test_search_then_cite_renders_chip(tmp_path):
    wd = make_wd(tmp_path)
    reg = Registry(wd)
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model([
        [("search_paper", {"query": "how many instructors were interviewed"})],
        lambda msgs: f"18명을 인터뷰했다 [[r{_first_row(msgs)}]]. 지어낸 근거 [[r999]].",
    ]))
    s = sessions.create()
    events = _run(agent, s, "몇 명을 인터뷰했어?", _deps(reg, tok))

    names = _names(events)
    assert names[:2] == ["tool_call", "tool_result"] and names[-1] == "done"
    assert "text" in names and "error" not in names
    call = events[0]["data"]
    assert call["name"] == "search_paper" and call["args"]["query"].startswith("how many")
    assert events[1]["data"]["id"] == call["id"] and "[r" in events[1]["data"]["summary"]
    streamed = "".join(e["data"]["delta"] for e in events if e["event"] == "text")
    assert "[[r999]]" in streamed         # 스트림은 날것, done에서 걸러진 HTML로 교체

    done = events[-1]["data"]
    row = done["citations"][0]["row"]
    assert f'data-kind="row" data-token="{tok}" data-row="{row}" data-n="1"' in done["html"]
    assert "999" not in done["html"]
    assert done["citations"][0]["token"] == tok and "interviewed 18" in done["citations"][0]["en"]
    # 창이 다른 논문의 칩을 눌렀을 때 옮겨 갈 주소 — 토큰만으로는 경로를 못 만든다
    assert done["citations"][0]["url"] == review_url(wd.root)
    assert done["pending"] is False and done["turn_id"]

    back = sessions.load(s.id)
    assert [t["role"] for t in back.turns] == ["user", "assistant"]
    assert back.title == "몇 명을 인터뷰했어?" and back.paper_tokens == [tok]
    assert back.turns[1]["html"] == done["html"] and len(back.conversation.messages) == 4


def test_goto_row_and_open_paper_emit_ui_actions(tmp_path):
    wd, other = make_wd(tmp_path, "p"), make_wd(tmp_path, "q", title="Other")
    reg = Registry(wd, other)
    tok, otok = list(reg.by_token)
    agent = make_agent(scripted_model([
        [("goto_row", {"row": 3}), ("open_paper", {"token": otok}), ("goto_row", {"row": 999})],
        "옮겼어요.",
    ]))
    events = _run(agent, sessions.create(), "인터뷰 문단 보여 줘", _deps(reg, tok))
    actions = [e["data"] for e in events if e["event"] == "ui_action"]
    # 한 응답의 tool들은 동시에 돌 수 있어 순서는 따지지 않는다
    assert sorted(actions, key=lambda a: a["kind"]) == [
        {"kind": "goto_row", "token": tok, "row": 3, "url": review_url(wd.root)},
        {"kind": "open_paper", "token": otok, "url": review_url(other.root)}]
    assert _names(events).index("ui_action") < _names(events).index("done")
    results = [e["data"]["summary"] for e in events if e["event"] == "tool_result"]
    assert any("범위 밖" in r for r in results)     # 잘못된 행은 동작 없이 안내만


def test_usage_becomes_cost_and_limits_count_per_turn(tmp_path):
    wd = make_wd(tmp_path)
    reg = Registry(wd)
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model([[("outline", {})], "목차를 봤어요."],
                                      model_name="gpt-6-luna"))
    s = sessions.create()
    # 턴마다 요청 2번(tool → 답). 상한이 누적으로 세어지면 두 번째 턴이 막힌다.
    limits = UsageLimits(request_limit=2)
    first = _run(agent, s, "목차", _deps(reg, tok), usage_limits=limits)[-1]
    second = _run(agent, s, "다시 목차", _deps(reg, tok), usage_limits=limits)[-1]
    assert first["event"] == second["event"] == "done"

    u = first["data"]["usage"]
    assert u["input"] > 0 and u["output"] > 0
    want = cost_usd("gpt-6-luna", Usage(input_tokens=u["input"], output_tokens=u["output"]))
    assert first["data"]["cost_usd"] == pytest.approx(want, abs=1e-6) and want > 0
    total = first["data"]["cost_usd"] + second["data"]["cost_usd"]
    assert second["data"]["session_cost_usd"] == pytest.approx(total, abs=1e-6)

    back = sessions.load(s.id)
    assert back.conversation.usage.requests == 4          # 세션 누적은 그대로 쌓인다
    assert back.usage["input"] == u["input"] + second["data"]["usage"]["input"]
    assert back.turns[-1]["model"] == "gpt-6-luna"


def test_limit_hit_ends_with_error_and_keeps_question(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model([[("outline", {})], "끝"]))
    s = sessions.create()
    events = _run(agent, s, "목차", _deps(reg, tok), usage_limits=UsageLimits(request_limit=1))
    assert events[-1]["event"] == "error" and "상한" in events[-1]["data"]["message"]
    assert "done" not in _names(events)
    back = sessions.load(s.id)
    assert [t["role"] for t in back.turns] == ["user", "assistant"]
    assert back.turns[1]["error"] and back.conversation.messages == []


def test_home_screen_lists_papers_and_cites_other_paper_with_token(tmp_path):
    wd = make_wd(tmp_path / "ws", "p")
    config.set_section_value("output", "workspace", str(tmp_path / "ws"))
    reg = Registry()                        # 아직 아무 논문도 등록 안 됨 — list_papers가 등록한다
    write_notes(wd, [item("m1", 3, "We interviewed 18 instructors", "표본이 작다")])

    def answer(msgs: list) -> str:
        tok = re.search(r"token ([0-9a-f]{12})", "\n".join(tool_returns(msgs))).group(1)
        row = _first_row(msgs, prefix=f"{tok}:r")
        return f"그 논문은 18명 [[{tok}:r{row}]], 메모도 있다 [[am1]]. 현재 논문 표기 [[r{row}]]"

    agent = make_agent(scripted_model([
        [("list_papers", {})],
        _search_with_token,
        answer,
    ]))
    deps = _deps(reg, None)
    events = _run(agent, sessions.create(), "인터뷰 몇 명 한 논문 있었지?", deps)
    done = events[-1]
    assert done["event"] == "done", events[-1]
    tok = next(iter(reg.by_token))
    assert f'data-token="{tok}"' in done["data"]["html"]
    kinds = [c["kind"] for c in done["data"]["citations"]]
    assert kinds == ["row", "note"]           # 홈에서는 [[rN]]이 어느 논문인지 몰라 버린다
    assert done["data"]["citations"][1]["id"] == "m1"
    assert done["data"]["citations"][1]["token"] == tok


def _search_with_token(msgs: list):  # noqa: ANN202 — 대본 단계: list_papers가 준 token으로 검색
    tok = re.search(r"token ([0-9a-f]{12})", "\n".join(tool_returns(msgs))).group(1)
    return [("search_paper", {"query": "interviewed instructors", "token": tok})]


def test_previous_turn_citations_stay_citable(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    s = sessions.create()
    first = make_agent(scripted_model([
        [("read_rows", {"start": 3})], "18명 [[r3]]"]))
    assert _run(first, s, "r3 읽어 줘", _deps(reg, tok))[-1]["event"] == "done"
    again = make_agent(scripted_model(["아까 말한 대로 [[r3]], 새로 지어낸 [[r4]]"]))
    done = _run(again, s, "다시 말해 줘", _deps(reg, tok))[-1]["data"]
    assert 'data-row="3"' in done["html"] and 'data-row="4"' not in done["html"]


def test_instructions_carry_screen_context_and_memory(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    sessions.memory_path().parent.mkdir(parents=True, exist_ok=True)
    sessions.memory_path().write_text("사용자는 HCI 연구자다.", encoding="utf-8")
    seen: list = []
    agent = make_agent(scripted_model(["네."], seen=seen))
    deps = _deps(reg, tok)
    deps.ctx = ScreenContext.model_validate(
        {"screen": "review", "paper": {"token": tok, "title": "Bespoke Bots"},
         "selection": {"side": "en", "row": 3, "quote": "We interviewed 18 instructors"}})
    _run(agent, sessions.create(), "이 문장 설명해 줘", deps)
    ins = seen[0].instructions
    assert "Narani" in ins and "[[r12]]" in ins            # 고정 지시문
    assert "HCI 연구자" in ins                               # memory.md
    assert "We interviewed 18 instructors" in ins and "현재 화면" in ins
    names = {t.name for t in seen[0].function_tools}
    assert {"search_paper", "read_rows", "outline", "list_note_tabs", "read_notes",
            "read_highlights", "list_papers", "list_projects", "get_bibtex", "goto_row",
            "open_paper"} <= names


def test_unknown_token_and_no_paper_are_soft_errors(tmp_path):
    reg = Registry(make_wd(tmp_path))
    agent = make_agent(scripted_model([
        [("search_paper", {"query": "x"}), ("read_rows", {"start": 0, "token": "ffffffffffff"})],
        "못 찾았어요."]))
    events = _run(agent, sessions.create(), "검색", _deps(reg, None))
    results = [e["data"]["summary"] for e in events if e["event"] == "tool_result"]
    assert any("열린 논문이 없습니다" in r for r in results)
    assert any("알 수 없는 논문 token" in r for r in results)
    assert events[-1]["event"] == "done"


def test_approval_pauses_then_resumes(tmp_path):
    """2단계 대비 — 승인 필요한 tool이 붙으면 run이 승인 대기로 끝나고, 결과를 주면 재개된다."""
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model([[("write_x", {"text": "hi"})], "썼어요."]))

    @agent.tool(requires_approval=True)
    async def write_x(ctx: RunContext[Deps], text: str) -> str:
        """테스트용 쓰기 tool."""
        return f"wrote {text}"

    s = sessions.create()
    events = _run(agent, s, "써 줘", _deps(reg, tok))
    assert _names(events)[-2:] == ["approval_needed", "done"]
    calls = events[-2]["data"]["calls"]
    assert calls[0]["name"] == "write_x" and calls[0]["args"] == {"text": "hi"}
    assert events[-1]["data"]["pending"] is True

    back = sessions.load(s.id)
    assert back.pending["calls"][0]["id"] == calls[0]["id"]
    events = _run(agent, back, None, _deps(reg, tok),
                  deferred_tool_results=DeferredToolResults(approvals={calls[0]["id"]: True}))
    assert events[-1]["event"] == "done" and "썼어요" in events[-1]["data"]["html"]
    assert any(e["event"] == "tool_result" and "wrote hi" in e["data"]["summary"]
               for e in events)
    final = sessions.load(s.id)
    assert final.pending is None
    assert [t["role"] for t in final.turns] == ["user", "assistant"]


def test_list_papers_header_counts_whole_library(tmp_path):
    """목록을 잘라 보여도 머리에 전체·조건·보인 편수와 컬렉션별 편수가 있다 (줄 수로 세지 않게)."""
    from types import SimpleNamespace

    from narani import projects
    from narani.ui.agent.tools import list_papers
    from narani.workdir import set_projects

    ws = tmp_path / "ws"
    wds = [make_wd(ws, f"p{i}", title=f"Paper {i}") for i in range(5)]
    nir = projects.create("NIRVANA")["id"]
    tut = projects.create("튜터")["id"]
    for wd in wds[:3]:
        set_projects(wd.root, [nir])
    set_projects(wds[3].root, [nir, tut])
    reg = Registry()
    deps = _deps(reg, None, workspace=ws)

    def call(**kw) -> str:
        return asyncio.run(list_papers(SimpleNamespace(deps=deps), **kw))

    out = call(limit=2)
    head = out.splitlines()[0]
    assert "전체 5편" in head and "2편을 보입니다" in head
    assert "NIRVANA 4" in out and "튜터 1" in out and "미분류 1" in out
    assert out.count("- token ") == 2 and "3편 더 있음" in out and "query" in out
    assert len(reg.by_token) == 2                 # 보인 논문만 token 등록

    out = call(project="튜터")
    assert "조건(project '튜터')에 맞는 논문 1편" in out and "전체 5편" in out
    assert "더 있음" not in out and out.count("- token ") == 1

    assert call(limit=1000).count("- token ") == 5     # 상한 300으로 묶일 뿐 오류 아님
    assert "전체 5편" in call(query="없는 제목")


def test_switching_paper_rebinds_this_paper(tmp_path):
    """앞 턴과 다른 논문을 열고 '이 논문'을 물으면 지시문·tool이 새 논문을 가리킨다.

    화면 맥락에 제목이 없어도(구버전 클라이언트) 서버가 채워 token과 제목을 둘 다 적는다.
    """
    a = make_wd(tmp_path, "a", title="Bespoke Bots")
    b = make_wd(tmp_path, "b", title="Tutor Survey")
    reg = Registry(a, b)
    tok_a, tok_b = list(reg.by_token)
    s = sessions.create()
    seen: list = []
    first = make_agent(scripted_model([[("outline", {})], "목차예요."], seen=seen))
    assert _run(first, s, "이 논문 목차", _deps(reg, tok_a))[-1]["event"] == "done"
    ins_a = seen[-1].instructions
    assert tok_a in ins_a and "Bespoke Bots" in ins_a and "'이 논문'" in ins_a

    seen.clear()
    second = make_agent(scripted_model([[("search_paper", {"query": "instructors"})], "네."],
                                       seen=seen))
    events = _run(second, s, "이 논문은 몇 명을 인터뷰했어?", _deps(reg, tok_b))
    ins_b = seen[0].instructions
    assert tok_b in ins_b and "Tutor Survey" in ins_b
    assert tok_a not in ins_b and "Bespoke Bots" not in ins_b
    assert "앞 턴에서 열거나 다룬 논문과 혼동하지" in ins_b           # 고정 지시문
    result = next(e["data"]["summary"] for e in events if e["event"] == "tool_result")
    assert result.startswith("논문: Tutor Survey (지금 화면의 논문)") and "[r" in result
