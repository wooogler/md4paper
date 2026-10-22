"""비용 tool — 섹션 재번역·용어집을 서버 수명 작업으로 띄우고, 끝나면 세션이 알린다.

번역은 실제 `run_translate`를 FakeProvider로 돌린다(네트워크 없음). 왕복: 승인 카드(비용·모델 표시)
→ 승인 → job 시작(watch_job 화면 동작) → 끝남 → GET …/jobs에 결과·세션 비용 → 다음 턴 지시문에
요약 한 번 → notified. 같은 논문이 이미 번역 중이면 거절, 키가 없으면 안내 문자열.

`with TestClient(app)`로 감싸야 한다 — 감싸지 않으면 요청마다 이벤트 루프가 새로 떴다 사라져서
tool이 띄운 작업 태스크가 SSE 응답이 끝날 때 함께 죽는다.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, scripted_model  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from md4paper import pipeline  # noqa: E402
from md4paper.ir import GlossaryEntry, GlossaryList  # noqa: E402
from md4paper.llm import FakeProvider  # noqa: E402
from md4paper.translate.apply import run_translate  # noqa: E402
from md4paper.ui import agent_panel, jobs  # noqa: E402
from md4paper.ui.agent import jobtools, retrieval, routes, sessions  # noqa: E402
from md4paper.ui.agent.agent import job_notice, make_agent  # noqa: E402
from md4paper.ui.agent.tools import Deps, outline  # noqa: E402
from md4paper.workdir import WorkDir  # noqa: E402

GLOSS = GlossaryList(entries=[GlossaryEntry(term="attention", korean="어텐션", policy="transliterate"),
                              GlossaryEntry(term="encoder", korean="인코더", policy="translate")])


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _body(user: str) -> str:
    """번역 요청에서 '번역할 본문'만 (test_translate_e2e와 같은 가짜 번역가)."""
    lines = user.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("=== 번역할 본문"):
            return "\n".join(lines[i + 1:])
    return user


def fake(calls: list | None = None) -> FakeProvider:
    """구조를 그대로 돌려주는 가짜 번역 + 고정 용어집. calls를 주면 번역 요청 본문을 쌓는다."""
    def complete(system: str, user: str) -> str:
        if calls is not None:
            calls.append(_body(user))
        return _body(user)
    return FakeProvider(complete_fn=complete, parse_fn=lambda s, u, schema: GLOSS,
                        model="gpt-5.6-luna")


def _para(word: str) -> str:
    return " ".join(f"The {word} encoder uses attention in sentence {i}." for i in range(40))


@pytest.fixture
def paper(tmp_path) -> WorkDir:
    """섹션 셋이 각각 1500자를 넘어 번역 유닛이 섹션 하나씩인 논문 — 한 번 번역해 캐시가 있다."""
    src = tmp_path / "src.md"
    src.write_text("# Attention Study\n\n## 1 Introduction\n\n" + _para("intro")
                   + "\n\n## 2 Method\n\n" + _para("method")
                   + "\n\n## 3 Results\n\n" + _para("results") + "\n", encoding="utf-8")
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(src, wd)
    pipeline.run_translate(wd, fake())        # status.json에 모델이 남는다 (뷰어 드롭다운 기본값)
    return wd


def _sid_of(wd: WorkDir, text: str) -> str:
    smap = json.loads(wd.sections_map.read_text(encoding="utf-8"))["sections"]
    return next(e["id"] for e in smap if e["text"] == text)


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _turn(client, sid, **body) -> list[tuple[str, dict]]:  # noqa: ANN001
    r = client.post(f"/agent/sessions/{sid}/turns", json=body)
    assert r.status_code == 200, r.text
    return _sse(r.text)


def _ctx(tok: str) -> dict:
    return {"screen": "review", "paper": {"token": tok}}


def _app(wd: WorkDir, steps, seen=None, provider=None, received=None):  # noqa: ANN001, ANN202
    reg = Registry(wd)
    app = FastAPI()
    routes.register_routes(
        app, reg.wd_for, reg.register,
        build_agent=lambda: make_agent(scripted_model(steps, seen=seen, received=received)),
        build_job_provider=provider or (lambda purpose, w: fake()))
    return app, next(iter(reg.by_token))


def _wait_jobs(client, sid, timeout: float = 15.0) -> dict:  # noqa: ANN001
    """GET …/jobs를 미완료가 없을 때까지 (창의 3초 폴링 대신 짧게)."""
    end = time.time() + timeout
    while True:
        got = client.get(f"/agent/sessions/{sid}/jobs").json()
        if all(j["done"] for j in got["jobs"]) or time.time() > end:
            return got
        time.sleep(0.05)


# --- 섹션 id · 캐시 무효화 ----------------------------------------------------


def test_outline_shows_section_ids(paper):
    reg = Registry(paper)
    tok = next(iter(reg.by_token))
    deps = Deps(wd_for=reg.wd_for, current_token=tok, register_token=reg.register)
    out = asyncio.run(outline(SimpleNamespace(deps=deps)))
    method = _sid_of(paper, "2 Method")
    assert f"] {method} 2 Method" in out and "섹션 id" in out


def test_plan_and_invalidate_retranslate_only_that_section(paper):
    method = _sid_of(paper, "2 Method")
    plan = jobtools.plan_retranslate(paper, [method])
    assert plan["affected"] == [method] and plan["names"] == ["2 Method"]
    assert jobtools.invalidate_cache(paper, plan["hashes"]) >= 1
    calls: list = []
    summary = run_translate(paper, fake(calls), "해라체")
    assert summary["ok"] == 1 and summary["cached"] == summary["chunks"] - 1
    assert len(calls) == 1 and "method encoder" in calls[0]


def test_invalidate_merged_unit_retranslates_it(tmp_path):
    """짧은 섹션들은 한 유닛으로 묶여 섹션 조각 해시로도 캐시된다 — 조각 해시까지 지워야 다시 번역된다.
    (apply.unit_hashes 접합부가 바뀌어도 '그 유닛만 다시'가 끝까지 되는지 지키는 테스트.)"""
    wd = WorkDir(tmp_path / "short.md4")
    pipeline.convert(Path(__file__).parent / "corpus" / "sample_cite.md", wd)
    run_translate(wd, fake(), "해라체")
    method = _sid_of(wd, "2 Method")
    plan = jobtools.plan_retranslate(wd, [method])
    assert len(plan["affected"]) > 1                      # 이웃과 묶였다
    jobtools.invalidate_cache(wd, plan["hashes"])
    summary = run_translate(wd, fake(), "해라체")
    assert summary["ok"] >= 1


def test_plan_refuses_unknown_unticked_and_untranslated(paper, tmp_path):
    assert "없는 섹션 id" in jobtools.plan_retranslate(paper, ["h_9999"])
    from md4paper.review import manifest as manifest_io

    man = manifest_io.load(paper)
    method = _sid_of(paper, "2 Method")
    for s in man.sections:
        if s.id == method:
            s.translate = False
    manifest_io.save(man, paper)
    assert "번역 대상에서 빠진" in jobtools.plan_retranslate(paper, [method])
    paper.ko_md.unlink()
    assert "아직 번역이 없습니다" in jobtools.plan_retranslate(paper, [method])


def test_describe_counts_stale_sections_that_ride_along(paper):
    intro, method = _sid_of(paper, "1 Introduction"), _sid_of(paper, "2 Method")
    en = paper.en_md.read_text(encoding="utf-8")
    paper.en_md.write_text(en.replace("intro encoder uses", "intro encoder now uses"), encoding="utf-8")
    text = jobtools.describe_retranslate({"section_ids": [method]}, Path(paper.root))
    # 제목 섹션은 짧아 서론과 한 유닛으로 묶인다 — 그 유닛의 섹션이 모두 낡음으로 잡힌다
    assert text.endswith("원문이 바뀐 섹션 2개도 함께") and intro not in text


# --- 왕복 ------------------------------------------------------------------


def test_retranslate_round_trip(paper):
    method = _sid_of(paper, "2 Method")
    seen: list = []

    def step(msgs: list):  # noqa: ANN202 — 질문마다: 재번역 요청 → 승인 뒤 '시작' → 다음 질문엔 답만
        from agent_support import tool_returns

        users = sum(1 for m in msgs if getattr(m, "kind", "") == "request"
                    and any(p.part_kind == "user-prompt" for p in m.parts))
        if users >= 2:
            return "네, 번역이 끝났어요."
        if any("번역 작업을 시작했습니다" in r for r in tool_returns(msgs)):
            return "번역 작업을 시작했어요."
        return [("retranslate_sections", {"section_ids": [method]})]

    seen_msgs: list = []
    app, tok = _app(paper, [step], seen=seen, received=seen_msgs)
    with TestClient(app) as client:
        sid = client.post("/agent/sessions").json()["id"]
        events = _turn(client, sid, message="2번째 섹션 다시 번역해 줘", context=_ctx(tok))
        call = events[-2][1]["calls"][0]
        assert events[-2][0] == "approval_needed" and call["name"] == "retranslate_sections"
        assert call["description"] == ("'2 Method' 섹션 재번역 (LLM 비용 발생, "
                                       "모델 fake/gpt-5.6-luna)")
        assert not jobs.running()                                  # 승인 전에는 아무것도 안 돈다

        events = _turn(client, sid, approvals={call["id"]: True}, context=_ctx(tok))
        assert ("ui_action", {"kind": "watch_job", "job": "translate", "token": tok}) in events
        done = events[-1][1]
        assert done["pending"] is False and len(done["jobs"]) == 1
        rec = done["jobs"][0]
        assert rec["kind"] == "translate" and rec["sections"] == [method]
        assert rec["turn_id"] == done["turn_id"] and rec["label"] == "번역"   # 뷰어와 같은 라벨

        got = _wait_jobs(client, sid)
        rec = got["jobs"][0]
        assert rec["done"] and rec["status"] == "done", rec
        assert rec["result"]["sections"] == 1 and rec["result"]["translated"] == 1
        cost = rec["result"]["cost_usd"]
        assert cost > 0
        assert got["usage"]["cost_usd"] == pytest.approx(cost)     # 대본 모델은 공짜 — 작업 비용만
        job = jobs.get(paper.root, "translate")                    # 뷰어와 같은 종류로 돌았다
        assert job is not None and job.status == "done" and "재번역 완료" in job.message
        assert not job.seen                                       # 알림은 뷰어·헤더 몫으로 남긴다

        # 다음 턴 — 지시문에 끝난 작업이 한 번 실리고 notified가 된다
        seen.clear()
        seen_msgs.clear()
        _turn(client, sid, message="번역 끝났어?", context=_ctx(tok))
        ins = seen[0].instructions
        assert "## 백그라운드 작업" in ins and "끝남: 번역" in ins and "1개 섹션 다시 번역" in ins
        asked = seen_msgs[-1][-1].parts[0].content            # 질문 메시지 앞에도 한 번 실린다
        assert asked[0].startswith("[앱 알림") and "끝남: 번역" in asked[0]
        assert asked[1] == "번역 끝났어?"
        assert client.get(f"/agent/sessions/{sid}").json()["turns"][-2]["text"] == "번역 끝났어?"
        detail = client.get(f"/agent/sessions/{sid}").json()
        assert detail["jobs"][0]["notified"] is True
        assert detail["usage"]["cost_usd"] == pytest.approx(cost)  # 다시 더하지 않는다
        seen.clear()
        _turn(client, sid, message="고마워", context=_ctx(tok))
        ins = seen[0].instructions
        assert "새로 끝난 작업" not in ins                          # '알려 주라'는 한 번만
        assert "이미 알린 작업" in ins and "끝남: 번역" in ins       # 물으면 답할 수 있게 짧게 남는다


def test_glossary_round_trip(paper):
    def step(msgs: list):  # noqa: ANN202
        from agent_support import tool_returns

        if any("용어집 작업을 시작했습니다" in r for r in tool_returns(msgs)):
            return "용어집을 만들고 있어요."
        return [("run_glossary", {})]

    app, tok = _app(paper, [step])
    with TestClient(app) as client:
        sid = client.post("/agent/sessions").json()["id"]
        events = _turn(client, sid, message="용어집 다시 만들어 줘", context=_ctx(tok))
        call = events[-2][1]["calls"][0]
        assert call["description"].startswith("용어집 다시 생성 (LLM 비용 발생, 모델 ")
        assert "다음 번역은 전체를 다시 번역함" in call["description"]
        events = _turn(client, sid, approvals={call["id"]: True}, context=_ctx(tok))
        assert ("ui_action", {"kind": "watch_job", "job": "terms", "token": tok}) in events
        rec = _wait_jobs(client, sid)["jobs"][0]
        assert rec["kind"] == "glossary" and rec["job_kind"] == "terms" and rec["status"] == "done"
        assert rec["result"]["entries"] == 2


def test_refuses_while_translating(paper):
    method = _sid_of(paper, "2 Method")
    gate = asyncio.Event()

    async def hold() -> None:
        jobs.start(paper.root, "translate", "번역", lambda j: time.sleep(0.5))
        gate.set()

    def step(msgs: list):  # noqa: ANN202
        from agent_support import tool_returns

        rets = tool_returns(msgs)
        return rets[-1] if rets else [("retranslate_sections", {"section_ids": [method]})]

    app, tok = _app(paper, [step])
    with TestClient(app) as client:
        client.portal.call(hold)                                  # 서버 루프에서 '뷰어의 번역'을 띄운다
        sid = client.post("/agent/sessions").json()["id"]
        call = _turn(client, sid, message="다시 번역해 줘", context=_ctx(tok))[-2][1]["calls"][0]
        events = _turn(client, sid, approvals={call["id"]: True}, context=_ctx(tok))
        assert "이미 번역 중" in events[-1][1]["html"]
        assert events[-1][1]["jobs"] == []
        assert not any(n == "ui_action" for n, _ in events)


def test_missing_key_returns_guidance(paper):
    method = _sid_of(paper, "2 Method")

    def step(msgs: list):  # noqa: ANN202
        from agent_support import tool_returns

        rets = tool_returns(msgs)
        return rets[-1] if rets else [("retranslate_sections", {"section_ids": [method]})]

    def no_key(purpose: str, wd: WorkDir):  # noqa: ANN202
        raise RuntimeError("openai API 키가 없습니다.")

    app, tok = _app(paper, [step], provider=no_key)
    with TestClient(app) as client:
        sid = client.post("/agent/sessions").json()["id"]
        call = _turn(client, sid, message="다시 번역해 줘", context=_ctx(tok))[-2][1]["calls"][0]
        events = _turn(client, sid, approvals={call["id"]: True}, context=_ctx(tok))
        assert "번역을 시작하지 못했습니다" in events[-1][1]["html"] and "키가 없습니다" in events[-1][1]["html"]
        assert not jobs.running() and events[-1][1]["jobs"] == []


def test_default_provider_uses_viewer_translate_model(paper, monkeypatch):
    """대화 모델([chat])이 아니라 이 논문을 지난번에 번역한 모델 — 전역 기본이 달라도 캐시를 지킨다."""
    from md4paper import config

    got = {}

    def build(p, m):  # noqa: ANN001, ANN202
        got["t"] = (p, m)
        return fake()

    monkeypatch.setattr(config, "build_provider", build)
    jobtools.default_job_provider("translate", paper)
    assert got["t"] == ("fake", "gpt-5.6-luna")
    jobtools.default_job_provider("glossary", paper)
    assert got["t"] == jobtools.glossary_choice()


# --- 세션 기록 ----------------------------------------------------------------


def test_sync_marks_lost_jobs_and_adds_cost_once(tmp_path):
    s = sessions.create()
    root = str(Path(tmp_path).resolve())
    s.jobs = [{"id": "j1", "kind": "translate", "job_kind": "translate", "root": root,
               "started": 1.0, "done": False}]
    assert [r["id"] for r in sessions.sync_jobs(s)] == ["j1"]
    assert s.jobs[0]["status"] == "failed" and s.jobs[0]["error"] == sessions.LOST_JOB

    job = jobs.Job(root=root, kind="translate", label="번역", status="done",
                   result={"ok": 2, "passthrough": 0, "cost_usd": 0.5, "sections": 2})
    jobs._JOBS[(root, "translate")] = job
    s.jobs = [{"id": "j2", "kind": "translate", "job_kind": "translate", "root": root,
               "started": job.started, "done": False}]
    s.usage["cost_usd"] = 0.25
    sessions.sync_jobs(s)
    sessions.sync_jobs(s)
    assert s.jobs[0]["result"] == {"sections": 2, "translated": 2, "failed": 0, "cost_usd": 0.5}
    assert s.usage["cost_usd"] == pytest.approx(0.75)
    sessions.save(s)
    assert sessions.load(s.id).jobs[0]["result"]["cost_usd"] == 0.5     # 파일 왕복


def test_job_cost_counts_toward_session_cap(paper, monkeypatch):
    from md4paper import config

    monkeypatch.setattr(config, "resolve_agent_cost_cap", lambda: 0.4)
    s = sessions.create()
    root = str(Path(paper.root).resolve())
    job = jobs.Job(root=root, kind="translate", label="번역", status="done",
                   result={"ok": 1, "cost_usd": 0.5, "sections": 1})
    jobs._JOBS[(root, "translate")] = job
    s.jobs = [{"id": "j3", "kind": "translate", "job_kind": "translate", "root": root,
               "started": job.started, "done": False}]
    sessions.save(s)
    app, tok = _app(paper, ["네."])
    with TestClient(app) as client:
        r = client.post(f"/agent/sessions/{s.id}/turns",
                        json={"message": "안녕", "context": _ctx(tok)})
        assert r.status_code == 400 and r.json()["code"] == "cost_cap"
        assert r.json()["cost_usd"] == pytest.approx(0.5)


def test_job_notice_lists_running_every_time_and_finished_once():
    s = sessions.Session(id="s0000001")
    s.jobs = [{"id": "a", "kind": "translate", "label": "번역", "detail": "'2 Method' 섹션",
               "token": "t", "title": "P", "done": False, "progress": [1, 3]},
              {"id": "b", "kind": "glossary", "label": "용어집 생성", "detail": "다시 생성",
               "token": "t", "title": "P", "done": True, "status": "done",
               "result": {"entries": 7, "cost_usd": 0.01}, "notified": False}]
    first = job_notice(s)
    assert "진행 중: 번역 ('2 Method' 섹션)" in first and "진행 1/3" in first
    assert "끝남: 용어집 생성" in first and "용어 7개" in first
    again = job_notice(s)
    assert "새로 끝난" not in again and "진행 중: 번역" in again
    assert "이미 알린 작업" in again and "끝남: 용어집 생성" in again


# --- 창(JS) 계약 ----------------------------------------------------------------


JS = agent_panel.JS


def test_panel_polls_jobs_only_while_unfinished():
    assert f"var JOB_POLL_MS = {agent_panel.JOB_POLL_MS};" in JS
    assert "'/agent/sessions/' + encodeURIComponent(at) + '/jobs'" in JS
    set_jobs = JS[JS.index("function setJobs("):JS.index("function pollJobs(")]
    assert "if (jobsPending())" in set_jobs and "clearTimeout(jobTimer)" in set_jobs
    assert "if (L.done.jobs) setJobs(L.done.jobs);" in JS        # 턴이 띄운 작업 → 폴링 시작
    assert "setJobs(d.jobs || []);" in JS                        # 세션을 다시 열어도


def test_panel_job_lines():
    text = JS[JS.index("function jobText("):JS.index("function jobNode(")]
    for s in ("'⏳ '", "'번역 중 — '", "'✅ 번역 완료: '", "'개 섹션'", "'✅ 용어집 완료: '",
              "' 실패: '"):
        assert s in text
    assert "cost_usd" not in text          # 작업 비용은 머리의 세션 누적에만 (§test_agent_panel)
    assert "jobsAfter(t.id, used)" in JS                        # 작업을 띄운 턴 뒤에 그린다


def test_watch_job_reaches_viewer_and_panel_does_not_toast():
    assert "a.kind === 'watch_job'" in JS
    assert f"emitEvent('{agent_panel.WATCH_JOB_EVENT}'" in JS
    assert "Notify" not in JS          # 완료 알림은 뷰어(attach_*)·헤더 탭 줄이 이미 띄운다 — 겹치지 않게
    src = Path(agent_panel.__file__).with_name("app.py").read_text(encoding="utf-8")
    assert "ui.on(agent_panel.WATCH_JOB_EVENT, on_agent_job)" in src
    handler = src[src.index("def on_agent_job("):src.index("ui.on(agent_panel.WATCH_JOB_EVENT")]
    assert "attach_translate(job)" in handler and "attach_terms(job)" in handler
    assert 'args.get("token") != tok' in handler


def test_cost_tools_have_names_icons_and_are_in_agent():
    for name in ("retranslate_sections", "run_glossary"):
        assert f"{name}: '" in JS                                 # TOOL_KO · TOOL_ICON
    agent = make_agent(scripted_model(["네."]))
    tools = agent._function_toolset.tools  # noqa: SLF001
    assert tools["retranslate_sections"].requires_approval
    assert tools["run_glossary"].requires_approval
