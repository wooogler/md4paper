"""에이전트 라우트 — 세션 CRUD, 턴 SSE 스트림, 404·400·409, 모델 선택."""

from __future__ import annotations

import json
import re
import shutil
import time

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model, tool_returns  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from md4paper import config  # noqa: E402
from md4paper.ui.agent import retrieval, routes, sessions  # noqa: E402
from md4paper.ui.agent.agent import make_agent  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _cite_answer(msgs: list) -> str:
    m = re.search(r"\[r(\d+)\]", "\n".join(tool_returns(msgs)))
    return f"18명을 인터뷰했다 [[r{m.group(1)}]]." if m else "열린 논문이 없어요."


def _agent():  # noqa: ANN202
    return make_agent(scripted_model([
        [("search_paper", {"query": "interviewed instructors"})], _cite_answer]))


@pytest.fixture
def env(tmp_path):
    wd = make_wd(tmp_path)
    reg = Registry(wd)
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register, build_agent=_agent)
    return TestClient(app), next(iter(reg.by_token))


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _ctx(tok: str) -> dict:
    return {"screen": "review", "paper": {"token": tok, "title": "Bespoke Bots"},
            "visible_rows": [2, 3]}


def test_session_crud(env):
    client, _tok = env
    created = client.post("/agent/sessions")              # 본문 없이도 만든다
    assert created.status_code == 200
    sid = created.json()["id"]
    assert created.json()["turns"] == [] and created.json()["pending"] is None
    titled = client.post("/agent/sessions", json={"title": "논문 비교"}).json()
    assert titled["title"] == "논문 비교"

    listed = client.get("/agent/sessions").json()["sessions"]
    assert {m["id"] for m in listed} == {sid, titled["id"]}

    r = client.patch(f"/agent/sessions/{sid}", json={"title": "새 이름"})
    assert r.status_code == 200 and r.json()["title"] == "새 이름"
    assert client.get(f"/agent/sessions/{sid}").json()["title"] == "새 이름"

    assert client.delete(f"/agent/sessions/{sid}").json() == {"ok": True}
    assert client.get(f"/agent/sessions/{sid}").status_code == 404
    assert [m["id"] for m in client.get("/agent/sessions").json()["sessions"]] == [titled["id"]]


def test_turn_streams_sse_and_saves(env):
    client, tok = env
    sid = client.post("/agent/sessions").json()["id"]
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"message": "몇 명을 인터뷰했어?", "context": _ctx(tok)})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _sse(r.text)
    names = [n for n, _ in events]
    assert names[:2] == ["tool_call", "tool_result"] and names[-1] == "done"
    assert "text" in names
    done = events[-1][1]
    assert f'data-token="{tok}"' in done["html"] and done["citations"][0]["token"] == tok
    assert set(done) >= {"turn_id", "html", "citations", "usage", "cost_usd",
                         "session_cost_usd", "pending"}
    assert not routes._BUSY                               # 스트림이 끝나면 풀린다

    got = client.get(f"/agent/sessions/{sid}").json()
    assert [t["role"] for t in got["turns"]] == ["user", "assistant"]
    assert got["pending"] is None and got["paper_tokens"] == [tok]
    assert got["usage"]["input"] > 0
    meta = client.get("/agent/sessions").json()["sessions"][0]
    assert meta["turns"] == 1 and meta["title"] == "몇 명을 인터뷰했어?"
    assert meta["last_paper"] == tok


def test_turn_from_home_without_paper(env):
    client, _tok = env
    sid = client.post("/agent/sessions").json()["id"]
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"message": "안녕", "context": {"screen": "home"}})
    events = _sse(r.text)
    assert events[-1][0] == "done"            # 열린 논문이 없어도 tool이 안내를 돌려줄 뿐 끝까지 돈다
    assert any(n == "tool_result" and "열린 논문이 없습니다" in d["summary"] for n, d in events)


def test_not_found_cases(env):
    client, tok = env
    assert client.get("/agent/sessions/s0000000").status_code == 404
    assert client.patch("/agent/sessions/s0000000", json={"title": "x"}).status_code == 404
    assert client.delete("/agent/sessions/s0000000").status_code == 404
    assert client.post("/agent/sessions/s0000000/turns", json={"message": "q"}).status_code == 404
    assert client.get("/agent/sessions/..%2Fx").status_code == 404
    sid = client.post("/agent/sessions").json()["id"]
    # 모르는 논문 token — current_wd로 폴백하지 않고 스트림 시작 전에 404
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"message": "q", "context": _ctx("ffffffffffff")})
    assert r.status_code == 404
    assert client.get(f"/agent/sessions/{sid}").json()["turns"] == []


def test_bad_requests(env, tmp_path):
    client, tok = env
    sid = client.post("/agent/sessions").json()["id"]
    r = client.post(f"/agent/sessions/{sid}/turns", json={"message": "  ", "context": _ctx(tok)})
    assert r.status_code == 400 and "비어" in r.json()["error"]
    r = client.post(f"/agent/sessions/{sid}/turns", json={"approvals": {"x": True}})
    assert r.status_code == 400 and "승인" in r.json()["error"]

    def no_key():  # noqa: ANN202
        raise RuntimeError("openai API 키가 없습니다.")

    app = FastAPI()
    reg = Registry(make_wd(tmp_path, "z"))
    routes.register_routes(app, reg.wd_for, reg.register, build_agent=no_key)
    c2 = TestClient(app)
    sid2 = c2.post("/agent/sessions").json()["id"]
    r = c2.post(f"/agent/sessions/{sid2}/turns", json={"message": "q"})
    assert r.status_code == 400 and "키가 없습니다" in r.json()["error"]
    m = c2.get("/agent/models").json()
    assert m["ready"] is False and "키가 없습니다" in m["reason"]


def test_concurrent_turn_on_same_session_is_409(env):
    client, tok = env
    sid = client.post("/agent/sessions").json()["id"]
    routes._BUSY[sid] = time.time()           # 다른 창에서 턴이 도는 중
    r = client.post(f"/agent/sessions/{sid}/turns", json={"message": "q", "context": _ctx(tok)})
    assert r.status_code == 409
    routes._BUSY[sid] = time.time() - routes.BUSY_TTL - 1    # 오래된 표시는 풀린 것으로 본다
    r = client.post(f"/agent/sessions/{sid}/turns", json={"message": "q", "context": _ctx(tok)})
    assert r.status_code == 200 and _sse(r.text)[-1][0] == "done"


def test_models_list_and_switch(env):
    client, _tok = env
    m = client.get("/agent/models").json()
    assert m["ready"] is True and m["provider"] in config.PROVIDERS
    assert {"provider", "model", "price", "has_key"} <= set(m["models"][0])
    r = client.put("/agent/model", json={"provider": "anthropic", "model": "claude-sonnet-5"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["provider"] == "anthropic" and r.json()["model"] == "claude-sonnet-5"
    assert client.get("/agent/models").json()["model"] == "claude-sonnet-5"
    assert client.put("/agent/model", json={"provider": "nope"}).status_code == 400


def test_default_factory_reports_missing_key(tmp_path):
    """주입 없이 등록하면 설정의 [chat] 모델 + 키로 만든다 — 키가 없으면 준비 안 됨."""
    app = FastAPI()
    reg = Registry(make_wd(tmp_path))
    routes.register_routes(app, reg.wd_for, reg.register)
    m = TestClient(app).get("/agent/models").json()
    assert m["ready"] is False and "API 키가 없습니다" in m["reason"]
