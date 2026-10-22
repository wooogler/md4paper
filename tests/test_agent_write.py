"""쓰기 tool과 승인 — 메모 쓰기·하이라이트·컬렉션·기억하기.

모델이 쓰기 tool을 부르면 턴이 approval_needed(사람용 description 포함)로 끝나고, 사용자가
승인해야 실제로 파일이 바뀐다. 거절·일부 승인·대기 무시 새 질문, 승인 사이에 화면 논문이 바뀐
경우, 쓴 뒤 뷰어가 다시 읽게 하는 refresh 화면 동작을 확인한다.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model, tool_returns  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from md4paper import library, notes, projects  # noqa: E402
from md4paper.ui import annotations  # noqa: E402
from md4paper.ui.agent import retrieval, routes, sessions  # noqa: E402
from md4paper.ui.agent.agent import make_agent, memory_text  # noqa: E402
from md4paper.ui.agent.context import ScreenContext  # noqa: E402
from md4paper.ui.agent.tools import (  # noqa: E402
    Deps,
    add_highlight,
    cell_text,
    describe_call,
    locate,
    remember,
    set_project_membership,
    write_note,
)


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _ctx(tok: str | None) -> dict:
    return {"screen": "review", "paper": {"token": tok}} if tok else {"screen": "home"}


def _env(tmp_path, steps, received=None, n_papers=1):  # noqa: ANN001, ANN202
    wds = [make_wd(tmp_path, f"p{i}", title=f"Paper {i}") for i in range(n_papers)]
    reg = Registry(*wds)
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register,
                           build_agent=lambda: make_agent(scripted_model(steps,
                                                                         received=received)))
    client = TestClient(app)
    sid = client.post("/agent/sessions").json()["id"]
    return client, sid, wds, list(reg.by_token)


def _turn(client, sid, **body) -> list[tuple[str, dict]]:  # noqa: ANN001
    r = client.post(f"/agent/sessions/{sid}/turns", json=body)
    assert r.status_code == 200, r.text
    return _sse(r.text)


NOTE = "인터뷰 표본: 18명\n세 대학"


def test_write_note_waits_for_approval_then_writes(tmp_path):
    client, sid, (wd,), (tok,) = _env(tmp_path, [
        [("write_note", {"name": "정리", "text": NOTE})], "'정리' 탭에 적었어요."])
    events = _turn(client, sid, message="정리 탭에 요약 적어 줘", context=_ctx(tok))
    names = [n for n, _ in events]
    assert names[-2:] == ["approval_needed", "done"] and "tool_result" not in names
    need = events[-2][1]
    call = need["calls"][0]
    assert call["name"] == "write_note" and call["args"] == {"name": "정리", "text": NOTE}
    assert call["description"] == "'정리' 탭 끝에 2줄 추가"
    assert need["token"] == tok and events[-1][1]["pending"] is True
    assert "정리" not in notes.tab_names(wd)                       # 승인 전에는 아무것도 안 쓴다

    got = client.get(f"/agent/sessions/{sid}").json()
    assert got["pending"] == need

    events = _turn(client, sid, approvals={call["id"]: True}, context=_ctx(tok))
    names = [n for n, _ in events]
    assert "tool_result" in names and names[-1] == "done"
    assert ("ui_action", {"kind": "refresh", "what": "notes", "token": tok, "tab": "정리"}) \
        in events
    done = events[-1][1]
    assert "적었어요" in done["html"] and done["pending"] is False and done["turn_id"]
    assert notes.read(wd, "정리") == NOTE + "\n"

    got = client.get(f"/agent/sessions/{sid}").json()
    assert got["pending"] is None
    assert [t["role"] for t in got["turns"]] == ["user", "assistant"]

    # 다시 쓰면 빈 줄을 두고 덧붙인다
    events = _turn(client, sid, message="하나 더", context=_ctx(tok))
    _turn(client, sid, approvals={events[-2][1]["calls"][0]["id"]: True}, context=_ctx(tok))
    assert notes.read(wd, "정리") == NOTE + "\n\n" + NOTE + "\n"


def test_denied_write_leaves_file_and_tells_model(tmp_path):
    received: list = []
    client, sid, (wd,), (tok,) = _env(tmp_path, [
        [("write_note", {"name": "메모", "text": "지울 수 없는 글"})], "알겠어요, 안 쓸게요."],
        received=received)
    events = _turn(client, sid, message="메모에 써 줘", context=_ctx(tok))
    cid = events[-2][1]["calls"][0]["id"]
    received.clear()
    events = _turn(client, sid, approvals={cid: False}, context=_ctx(tok))
    assert events[-1][0] == "done" and "안 쓸게요" in events[-1][1]["html"]
    assert not any(n == "ui_action" for n, _ in events)
    assert notes.read(wd, "메모") == ""
    assert routes.DENIED in tool_returns(received[0])               # 모델이 거절을 받는다
    assert client.get(f"/agent/sessions/{sid}").json()["pending"] is None


def test_partial_approval_denies_the_rest(tmp_path):
    received: list = []
    client, sid, (wd,), (tok,) = _env(tmp_path, [
        [("write_note", {"name": "메모", "text": "남길 글"}), ("remember", {"text": "HCI 연구자"})],
        "하나만 했어요."], received=received)
    events = _turn(client, sid, message="써 주고 기억해 줘", context=_ctx(tok))
    calls = {c["name"]: c for c in events[-2][1]["calls"]}
    assert calls["remember"]["description"] == '기억해 두기 (memory.md): "HCI 연구자"'
    received.clear()
    # remember는 답을 안 보냈다 → 거절로 닫힌다
    events = _turn(client, sid, approvals={calls["write_note"]["id"]: True}, context=_ctx(tok))
    assert events[-1][0] == "done"
    assert notes.read(wd, "메모") == "남길 글\n"
    assert not sessions.memory_path().exists()
    rets = tool_returns(received[0])
    assert routes.DENIED in rets and any("덧붙였" in r or "썼습니다" in r for r in rets)

    r = client.post(f"/agent/sessions/{sid}/turns", json={"approvals": {"nope": True}})
    assert r.status_code == 400                                   # 대기 없음


def test_unknown_approval_id_is_400(tmp_path):
    client, sid, _wds, (tok,) = _env(tmp_path, [[("remember", {"text": "x"})], "네"])
    events = _turn(client, sid, message="기억해", context=_ctx(tok))
    cid = events[-2][1]["calls"][0]["id"]
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"approvals": {cid: True, "zzz": True}, "context": _ctx(tok)})
    assert r.status_code == 400 and "zzz" in r.json()["error"]
    assert client.get(f"/agent/sessions/{sid}").json()["pending"] is not None


def test_new_question_while_pending_denies_and_continues(tmp_path):
    received: list = []

    def step(msgs: list):  # noqa: ANN202 — 거절을 받았으면 새 질문에 답하고, 아니면 쓰기를 청한다
        if routes.SKIPPED in tool_returns(msgs):
            return "넘어가서 다른 질문에 답해요."
        return [("write_note", {"name": "메모", "text": "안 쓰일 글"})]

    client, sid, (wd,), (tok,) = _env(tmp_path, [step], received=received)
    events = _turn(client, sid, message="메모 써 줘", context=_ctx(tok))
    assert events[-2][0] == "approval_needed"
    received.clear()
    events = _turn(client, sid, message="그건 됐고, 인터뷰 몇 명?", context=_ctx(tok))
    assert events[-1][0] == "done" and "다른 질문" in events[-1][1]["html"]
    assert notes.read(wd, "메모") == ""
    last = received[0][-1]                                        # [거절 결과, 새 질문]
    kinds = [p.part_kind for p in last.parts]
    assert kinds == ["tool-return", "user-prompt"]
    got = client.get(f"/agent/sessions/{sid}").json()
    assert got["pending"] is None
    assert [t["text"] for t in got["turns"] if t["role"] == "user"] == [
        "메모 써 줘", "그건 됐고, 인터뷰 몇 명?"]


def test_write_goes_to_paper_open_when_approval_was_asked(tmp_path):
    """승인 사이에 다른 논문으로 옮겨도 token 없이 부른 쓰기는 요청한 때의 논문에 쓴다."""
    client, sid, (a, b), (tok_a, tok_b) = _env(
        tmp_path, [[("write_note", {"name": "메모", "text": "A에 쓸 글"})], "썼어요."],
        n_papers=2)
    events = _turn(client, sid, message="메모 써 줘", context=_ctx(tok_a))
    cid = events[-2][1]["calls"][0]["id"]
    events = _turn(client, sid, approvals={cid: True}, context=_ctx(tok_b))
    assert notes.read(a, "메모") == "A에 쓸 글\n" and notes.read(b, "메모") == ""
    assert ("ui_action", {"kind": "refresh", "what": "notes", "token": tok_a, "tab": "메모"}) \
        in events


# --- tool 하나씩 (승인 없이 함수로 직접) ---------------------------------------------


def _ctx_of(reg: Registry, tok: str | None, meta: object = None) -> SimpleNamespace:
    ctx = ScreenContext.model_validate(_ctx(tok))
    deps = Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register)
    return SimpleNamespace(deps=deps, tool_call_metadata=meta)


def _actions(ctx: SimpleNamespace) -> list[dict]:
    out = []
    while not ctx.deps.ui_actions.empty():
        out.append(ctx.deps.ui_actions.get_nowait())
    return out


def test_write_note_modes_and_conflict(tmp_path, monkeypatch):
    wd = make_wd(tmp_path)
    reg = Registry(wd)
    tok = next(iter(reg.by_token))
    ctx = _ctx_of(reg, tok)
    run = asyncio.run

    out = run(write_note(ctx, "질문 목록", "- 왜 18명?"))
    assert "새 메모 탭 '질문 목록'" in out and notes.read(wd, "질문 목록") == "- 왜 18명?\n"
    out = run(write_note(ctx, "질문 목록", "통째로 새 글", mode="replace"))
    assert "바꿨습니다" in out and notes.read(wd, "질문 목록") == "통째로 새 글\n"
    assert any((wd.root / "notes" / ".trash").iterdir())             # 덮기 전 내용을 남긴다
    assert "mode" in run(write_note(ctx, "질문 목록", "x", mode="prepend"))
    assert "비어" in run(write_note(ctx, "질문 목록", "   "))
    assert notes.read(wd, "질문 목록") == "통째로 새 글\n"

    real = notes.read_stamped
    monkeypatch.setattr(notes, "read_stamped",                        # 읽은 뒤 바깥에서 바뀜
                        lambda w, n: (real(w, n)[0], "다른 지문"))
    out = run(write_note(ctx, "질문 목록", "덮으면 안 됨"))
    assert "바깥에서 바뀌어" in out and notes.read(wd, "질문 목록") == "통째로 새 글\n"
    monkeypatch.undo()
    acts = _actions(ctx)
    assert len(acts) == 2 and all(a["what"] == "notes" for a in acts)  # 실패한 쓰기는 알리지 않는다

    home = _ctx_of(reg, None)
    assert "열린 논문이 없습니다" in run(write_note(home, "메모", "x"))
    # 승인 재개: metadata의 token(요청한 때 화면)이 지금 화면보다 먼저
    pinned = _ctx_of(reg, None, meta={"token": tok})
    assert "덧붙였" in run(write_note(pinned, "질문 목록", "재개 때 씀"))


def test_cell_text_and_locate():
    plain = cell_text("We **interviewed** 18\ninstructors at $n=3$ sites &amp; more.")
    assert "We interviewed 18" in plain and "**" not in plain and "&amp;" not in plain
    s, e = locate(plain, "interviewed 18 instructors")         # 줄바꿈 = 공백
    assert plain[s:e].split() == ["interviewed", "18", "instructors"]
    assert locate(plain, "없는 문구") is None and locate(plain, "  ") is None


def test_add_highlight_anchor_matches_viewer(tmp_path):
    en = ("# T\n\nIntro line.\n\n## 2 Method\n\n"
          "Before this, we interviewed 18 instructors at three universities in **2024** and "
          "coded the transcripts twice.\n")
    ko = "# T\n\n소개.\n\n## 2 방법\n\n그전에 세 대학의 교수자 18명을 **2024년**에 인터뷰했다.\n"
    wd = make_wd(tmp_path, en=en, ko=ko)
    reg = Registry(wd)
    tok = next(iter(reg.by_token))
    ctx = _ctx_of(reg, tok)
    chunks = retrieval.paper(wd).chunks
    row = next(c.row for c in chunks if "interviewed 18" in c.en)

    out = asyncio.run(add_highlight(ctx, row, "en", "we interviewed 18 instructors",
                                    note="표본이 작다", color="green"))
    assert f"r{row} 원문" in out and "[a" in out
    saved = annotations.load(wd)
    def same(xs: list) -> list:   # normalize는 updated를 지금으로 고친다 — 그것만 빼고 비교
        return [{k: v for k, v in x.items() if k != "updated"} for x in xs]

    assert same(annotations.normalize(saved)) == same(saved) and len(saved) == 1   # 형식 그대로
    it = saved[0]
    assert it["color"] == "green" and it["note"] == "표본이 작다" and it["id"].startswith("a")
    a = it["anchors"][0]
    plain = cell_text(chunks[row].en)
    # 뷰어 resolve()의 첫 검사: 셀 평문의 start에 quote가 그대로 있다
    assert plain[a["start"]:a["end"]] == a["quote"] == "we interviewed 18 instructors"
    assert a["prefix"] == plain[max(0, a["start"] - 40):a["start"]] and a["prefix"].endswith(
        "Before this, ")
    assert a["suffix"] == plain[a["end"]:a["end"] + 40] and len(a["suffix"]) == 40
    # 서버 쪽 다시 찾기(행이 밀렸을 때)도 같은 행으로 간다
    assert retrieval._row_by_quote(chunks, a["quote"]) == row
    fresh = retrieval.paper(wd).by_row()[row]                        # mtime이 바뀌어 다시 읽는다
    assert [n["id"] for n in fresh.notes] == [it["id"]]
    assert _actions(ctx) == [{"kind": "refresh", "what": "highlights", "token": tok,
                              "id": it["id"]}]
    assert it["id"] in ctx.deps.cited_notes                          # 답에서 바로 인용 가능

    # 마크다운 강조가 끼어도 렌더된 글 기준으로 찾는다 (원문 마크다운엔 '**2024**')
    asyncio.run(add_highlight(ctx, row, "ko", "18명을 2024년에 인터뷰했다"))
    b = annotations.load(wd)[1]["anchors"][0]
    assert b["side"] == "ko" and cell_text(chunks[row].ko)[b["start"]:b["end"]] == b["quote"]

    n = len(annotations.load(wd))
    assert "찾지 못했습니다" in asyncio.run(add_highlight(ctx, row, "en", "not in this row"))
    assert "범위 밖" in asyncio.run(add_highlight(ctx, 999, "en", "x"))
    assert "side" in asyncio.run(add_highlight(ctx, row, "fr", "x"))
    assert "color" in asyncio.run(add_highlight(ctx, row, "en", "x", color="red"))
    assert len(annotations.load(wd)) == n


def test_set_project_membership_by_id_or_name(tmp_path):
    wd = make_wd(tmp_path)
    reg = Registry(wd)
    tok = next(iter(reg.by_token))
    ctx = _ctx_of(reg, tok)
    nir = projects.create("NIRVANA")["id"]
    tut = projects.create("튜터")["id"]

    assert "넣었습니다" in asyncio.run(set_project_membership(ctx, tok, "nirvana"))
    assert "넣었습니다" in asyncio.run(set_project_membership(ctx, "", tut))   # 빈 token = 지금 논문
    assert library.projects_of(wd) == [nir, tut]
    assert "이미" in asyncio.run(set_project_membership(ctx, tok, nir))
    assert "뺐습니다" in asyncio.run(set_project_membership(ctx, tok, nir, add=False))
    assert library.projects_of(wd) == [tut]
    assert "컬렉션이 없습니다" in asyncio.run(set_project_membership(ctx, tok, "없는 컬렉션"))
    assert "알 수 없는 논문" in asyncio.run(set_project_membership(ctx, "ffffffffffff", nir))
    assert [a["what"] for a in _actions(ctx)] == ["library"] * 3
    assert describe_call("set_project_membership", {"token": tok, "project_id": nir}) == \
        f"논문을 컬렉션 'NIRVANA'에 넣기 (논문 {tok})"
    assert describe_call("set_project_membership", {"project_id": tut, "add": False}) == \
        "논문을 컬렉션 '튜터'에서 빼기"


def test_remember_appends_dated_line_and_warns_when_long(tmp_path):
    reg = Registry(make_wd(tmp_path))
    ctx = _ctx_of(reg, None)
    assert "기억해 두었습니다" in asyncio.run(remember(ctx, "  HCI\n연구자다 "))
    asyncio.run(remember(ctx, "답은 짧게"))
    today = date.today().isoformat()
    assert sessions.memory_path().read_text(encoding="utf-8") == (
        f"- {today} HCI 연구자다\n- {today} 답은 짧게\n")
    assert "HCI 연구자다" in memory_text()                         # 다음 질문부터 지시문에
    assert "비어" in asyncio.run(remember(ctx, " "))
    sessions.memory_path().write_text("가" * 4100, encoding="utf-8")  # 끝에 줄바꿈 없음
    out = asyncio.run(remember(ctx, "넘침"))
    assert "4000자를 넘어" in out
    assert sessions.memory_path().read_text(encoding="utf-8").endswith(f"가\n- {today} 넘침\n")


def test_memory_text_keeps_recent_tail_at_line_boundary():
    path = sessions.memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"- 2026-10-{i % 28 + 1:02d} 기억 {i:03d} " + "가" * 60 for i in range(80)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert len(path.read_text(encoding="utf-8")) > 4000
    text = memory_text()
    body = text.split("\n", 1)[1]                                 # 머리 줄 다음
    assert body.startswith("…(앞부분 생략)\n- ")                  # 줄 경계에서 자른다
    assert "기억 079" in body and "기억 000" not in body           # 최근(끝) 기억이 남는다
    assert len(body) <= 4000 + len("…(앞부분 생략)\n")
    kept = body.split("\n")[1:]
    assert all(ln in lines for ln in kept)                         # 반쪽 줄이 없다

    path.write_text("짧은 기억\n", encoding="utf-8")
    assert "앞부분 생략" not in memory_text() and memory_text().endswith("짧은 기억")


def test_descriptions_and_tools_require_approval(tmp_path):
    assert describe_call("write_note", {"name": "정리", "text": "a\n\nb\nc", "mode": "replace"}) \
        == "'정리' 탭 내용을 새 글(3줄)로 바꿈"
    assert describe_call("write_note", {"text": "a", "token": "0123456789ab"}) == \
        "'메모' 탭 끝에 1줄 추가 (논문 0123456789ab)"
    assert describe_call("add_highlight", {"row": 12, "side": "en", "quote": "We study x"}) == \
        "r12 원문에 노란 하이라이트: \"We study x\""
    long = describe_call("add_highlight", {"row": 3, "side": "ko", "quote": "가" * 80,
                                           "note": "메모", "color": "blue"})
    assert long.startswith("r3 번역에 파란 하이라이트: \"" + "가" * 50 + "…\"")
    assert long.endswith('+ 메모 "메모"')
    assert describe_call("search_paper", {}) == "search_paper 실행"
    assert describe_call("add_highlight", None) .startswith("r")    # 깨진 인자도 카드는 뜬다

    seen: list = []
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model(["네."], seen=seen))
    ctx = ScreenContext.model_validate(_ctx(tok))

    async def go():  # noqa: ANN202
        from md4paper.ui.agent.agent import run_turn

        deps = Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register)
        return [e async for e in run_turn(agent, sessions.create(), "안녕", deps)]

    asyncio.run(go())
    kinds = {t.name: t.kind for t in seen[0].function_tools}
    for name in ("write_note", "add_highlight", "set_project_membership", "remember"):
        assert kinds[name] == "unapproved"
    assert kinds["search_paper"] == "function"
    assert "write_note" in seen[0].instructions and "승인" in seen[0].instructions
