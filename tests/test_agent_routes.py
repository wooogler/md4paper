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

from narani import config  # noqa: E402
from narani.ui.agent import retrieval, routes, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent  # noqa: E402


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


def _listed_token(msgs: list) -> str:
    m = re.search(r"token ([0-9a-f]{12}):", "\n".join(tool_returns(msgs)))
    return m.group(1) if m else "notfound0000"


def test_restart_reregisters_tokens_from_session(tmp_path):
    """서버 재시작(빈 등록부) 뒤에도 앞 턴에서 받은 token으로 승인 재개·읽기가 된다."""
    from narani import notes

    wd = make_wd(tmp_path / "ws")
    steps = [[("list_papers", {})],
             lambda msgs: [("write_note", {"name": "메모", "text": "재시작 뒤 글",
                                           "token": _listed_token(msgs)})],
             lambda msgs: [("search_paper", {"query": "interviewed",
                                             "token": _listed_token(msgs)})],
             "썼어요."]

    def server():  # noqa: ANN202 — 새로 켠 서버: 등록부가 비어 있다
        reg = Registry()
        app = FastAPI()
        routes.register_routes(app, reg.wd_for, reg.register,
                               build_agent=lambda: make_agent(scripted_model(steps)),
                               workspace=lambda: tmp_path / "ws")
        return TestClient(app), reg

    client, _reg = server()
    sid = client.post("/agent/sessions").json()["id"]
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"message": "메모 써 줘", "context": {"screen": "home"}})
    events = _sse(r.text)
    need = [d for n, d in events if n == "approval_needed"][0]
    tok = need["calls"][0]["args"]["token"]
    assert tok != "notfound0000"
    assert sessions.load(sid).papers == {tok: str(wd.root.resolve())}

    client, reg = server()                                  # 재시작
    assert reg.by_token == {}
    r = client.post(f"/agent/sessions/{sid}/turns",
                    json={"approvals": {need["calls"][0]["id"]: True},
                          "context": {"screen": "home"}})
    events = _sse(r.text)
    results = [d["summary"] for n, d in events if n == "tool_result"]
    assert len(results) == 2 and not any("알 수 없는" in x for x in results), results
    assert events[-1][0] == "done" and "썼어요" in events[-1][1]["html"]
    assert notes.read(wd, "메모") == "재시작 뒤 글\n"
    assert set(reg.by_token) == {tok}


def test_restart_skips_folders_that_are_gone(tmp_path):
    wd = make_wd(tmp_path)
    gone = make_wd(tmp_path, "gone")
    tok = Registry(wd).register(wd)
    s = sessions.create()
    sessions.remember_papers(s, {"aaaaaaaaaaaa": gone.root, tok: wd.root})
    sessions.save(s)
    shutil.rmtree(gone.root)
    reg = Registry()
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register, build_agent=_agent)
    # 화면 token 검사(404)보다 먼저 재등록하므로 재시작 직후의 화면 논문도 통과한다
    r = TestClient(app).post(f"/agent/sessions/{s.id}/turns",
                             json={"message": "몇 명?", "context": _ctx(tok)})
    assert r.status_code == 200 and _sse(r.text)[-1][0] == "done"
    assert set(reg.by_token) == {tok}
    assert sessions.load(s.id).papers[tok] == str(wd.root.resolve())


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


# --- 모델 고르기 (설정 [chat] — 옛 뷰어 챗봇 테스트에서 옮김) ------------------------------


@pytest.fixture
def cfg_snapshot():
    """설정 파일을 되돌린다 — 키·기본 제공사가 뒤 테스트로 새지 않게."""
    path = config.CONFIG_PATH
    before = path.read_bytes() if path.exists() else None
    yield
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)


def test_chat_choice_falls_back_to_global_default(cfg_snapshot):
    """[chat]을 안 정했으면 전역 기본을 그대로 따른다."""
    config.set_chat_choice(None)
    config.set_default("default_provider", "openai")
    assert config.resolve_chat_choice() == ("openai", config.DEFAULT_MODELS["openai"])
    config.set_default("default_provider", "anthropic")
    assert config.resolve_chat_choice() == ("anthropic", config.DEFAULT_MODELS["anthropic"])


def test_chat_choice_overrides_global_and_clears(cfg_snapshot):
    """에이전트만 다른 모델로 — 전역 기본(번역 등)은 건드리지 않는다."""
    config.set_default("default_provider", "openai")
    config.set_chat_choice("anthropic", "claude-opus-5")
    assert config.resolve_chat_choice() == ("anthropic", "claude-opus-5")
    assert config.resolve_provider() == "openai"          # 전역은 그대로

    config.set_chat_choice("gemini")                      # 모델 생략 → 그 제공사 기본
    assert config.resolve_chat_choice() == ("gemini", config.DEFAULT_MODELS["gemini"])

    config.set_chat_choice(None)                          # 해제 → 다시 전역
    assert config.resolve_chat_choice() == ("openai", config.DEFAULT_MODELS["openai"])


def test_chat_choice_rejects_unknown_provider():
    with pytest.raises(ValueError):
        config.set_chat_choice("mistral", "whatever")


def test_model_options_marks_providers_without_key(cfg_snapshot):
    """키 없는 제공사도 목록에는 남긴다 — 왜 못 고르는지 보여주려고."""
    config.set_key("openai", "sk-test")
    opts = routes.model_options()
    assert {o["model"] for o in opts} >= set(config.MODEL_TIERS["anthropic"])
    by_prov = {o["provider"]: o["has_key"] for o in opts}
    assert by_prov["openai"] is True and by_prov["anthropic"] is False
