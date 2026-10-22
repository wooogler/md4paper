"""컬렉션 용어집 tool — 어긋난 용어 읽기, 컬렉션 용어집 읽기·쓰기(승인), 다시 번역될 범위 어림.

컬렉션 용어집 파일은 `CONFIG_DIR/glossary/<id>.yaml`이라 테스트마다 CONFIG_DIR을 tmp로 바꾼다
(`glossary.collection_dir`가 부를 때마다 읽으므로 먹힌다). 작업 폴더는 routes의 `workspace=`로 넘긴다.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model, tool_returns  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from md4paper import config, pipeline, projects, workdir  # noqa: E402
from md4paper.ir import GlossaryEntry, GlossaryList  # noqa: E402
from md4paper.llm import FakeProvider  # noqa: E402
from md4paper.translate import glossary  # noqa: E402
from md4paper.ui.agent import retrieval, routes, sessions  # noqa: E402
from md4paper.ui.agent.agent import SYSTEM, make_agent  # noqa: E402
from md4paper.ui.agent.glossary_tools import (  # noqa: E402
    glossary_conflicts,
    read_collection_glossary,
    translation_impact,
    write_collection_glossary,
)
from md4paper.ui.agent.tools import TOOLS, Deps, describe_call, token_of  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "cfg" / "projects.json")
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _e(term: str, korean: str, policy: str = "translate") -> GlossaryEntry:
    return GlossaryEntry(term=term, korean=korean, policy=policy)


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _library(ws):  # noqa: ANN001, ANN202
    """논문 3편이 든 'NIRVANA' 컬렉션 — p0·p1은 'mental model'을 '정신 모형', p2는 '심적 모형'.
    'agent'는 p0·p1이 같은 번역어(어긋나지 않음), p3은 다른 컬렉션."""
    pid = projects.create("NIRVANA")["id"]
    other = projects.create("다른")["id"]
    gl = {
        "p0": [_e("Mental Model", "정신 모형"), _e("agent", "에이전트")],
        "p1": [_e("mental model", "정신 모형"), _e("agent", "에이전트"), _e("prompt", "프롬프트")],
        "p2": [_e("mental model", "심적 모형"), _e("prompt", "프롬프트", "transliterate")],
    }
    wds = []
    for name, entries in gl.items():
        wd = make_wd(ws, name, title=f"Paper {name}")
        glossary.save(GlossaryList(entries=entries), wd)
        workdir.set_projects(wd.root, [pid])
        wds.append(wd)
    p3 = make_wd(ws, "p3", title="Paper p3")
    glossary.save(GlossaryList(entries=[_e("mental model", "멘탈 모델")]), p3)
    workdir.set_projects(p3.root, [other])
    return pid, wds


def _ctx_of(ws) -> SimpleNamespace:  # noqa: ANN001
    reg = Registry()
    deps = Deps(wd_for=reg.wd_for, register_token=reg.register, workspace=ws)
    return SimpleNamespace(deps=deps, tool_call_metadata=None)


def _env(ws, steps, received=None):  # noqa: ANN001, ANN202
    reg = Registry()
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register,
                           build_agent=lambda: make_agent(scripted_model(steps, received=received)),
                           workspace=lambda: ws)
    client = TestClient(app)
    sid = client.post("/agent/sessions").json()["id"]
    return client, sid


def _turn(client, sid, **body) -> list[tuple[str, dict]]:  # noqa: ANN001
    r = client.post(f"/agent/sessions/{sid}/turns", json={"context": {"screen": "home"}, **body})
    assert r.status_code == 200, r.text
    return _sse(r.text)


def test_glossary_conflicts_lists_variants_counts_and_tokens(tmp_path):
    pid, (p0, p1, p2) = _library(tmp_path)
    received: list = []
    client, sid = _env(tmp_path, [[("glossary_conflicts", {"collection": "nirvana"})], "봤어요."],
                       received=received)
    events = _turn(client, sid, message="NIRVANA 용어 어긋난 거 보여 줘")
    assert events[-1][0] == "done"
    out = tool_returns(received[-1])[-1]
    assert out.startswith("컬렉션 'NIRVANA' — 논문 3편(용어집 있는 3편), 어긋난 용어 2개, "
                          "컬렉션 용어집 항목 0개")
    t0, t1, t2 = (token_of(w.root) for w in (p0, p1, p2))
    mm = next(ln for ln in out.splitlines() if ln.startswith("- mental model:"))
    assert "정신 모형(translate) ×2편" in mm and "심적 모형(translate) ×1편" in mm
    assert t0 in mm and t1 in mm and t2 in mm and "멘탈 모델" not in out   # 다른 컬렉션은 빠진다
    assert mm.index("정신 모형") < mm.index("심적 모형")                   # 편수 많은 변이가 먼저
    pr = next(ln for ln in out.splitlines() if ln.startswith("- prompt:"))
    assert "프롬프트(translate) ×1편" in pr and "프롬프트(transliterate) ×1편" in pr
    assert "agent" not in out and "다음: offset" not in out


def test_glossary_conflicts_paging_shared_decided_and_missing(tmp_path):
    pid, _wds = _library(tmp_path)
    ctx = _ctx_of(tmp_path)
    first = asyncio.run(glossary_conflicts(ctx, pid, limit=1))
    assert "- mental model:" in first and "- prompt:" not in first
    assert "다음: offset=1" in first
    second = asyncio.run(glossary_conflicts(ctx, pid, offset=1, limit=1))
    assert "- prompt:" in second and "다음: offset" not in second
    assert "범위 밖" in asyncio.run(glossary_conflicts(ctx, pid, offset=9))

    glossary.save_collection(pid, GlossaryList(entries=[_e("MENTAL MODEL", "정신 모형")]))
    out = asyncio.run(glossary_conflicts(ctx, "NIRVANA"))
    assert "컬렉션 용어집 항목 1개" in out
    mm = next(ln for ln in out.splitlines() if ln.startswith("- mental model:"))
    assert mm.endswith("→ 컬렉션: 정신 모형(translate)")

    common = asyncio.run(glossary_conflicts(ctx, "NIRVANA", shared=True))
    assert "같은 번역어로 쓰는 용어 1개" in common
    line = next(ln for ln in common.splitlines() if ln.startswith("- agent:"))
    assert "에이전트(translate) ×2편" in line and "mental model:" not in common

    assert "list_projects" in asyncio.run(glossary_conflicts(ctx, "없는 컬렉션"))
    empty = projects.create("빈 것")["id"]
    assert "논문이 없습니다" in asyncio.run(glossary_conflicts(ctx, empty))


def test_glossary_conflicts_none(tmp_path):
    pid = projects.create("같음")["id"]
    for name in ("a", "b"):
        wd = make_wd(tmp_path, name)
        glossary.save(GlossaryList(entries=[_e("encoder", "인코더")]), wd)
        workdir.set_projects(wd.root, [pid])
    out = asyncio.run(glossary_conflicts(_ctx_of(tmp_path), pid))
    assert "어긋난 용어 0개" in out and "어긋난 용어가 없습니다" in out


def test_write_collection_glossary_approval_merge_and_refresh(tmp_path):
    pid, _wds = _library(tmp_path)
    glossary.save_collection(pid, GlossaryList(entries=[_e("Mental Model", "멘탈 모델"),
                                                        _e("agent", "에이전트")]))
    entries = [{"term": "mental model", "korean": "정신 모형", "policy": "translate"},
               {"term": "agent", "korean": "에이전트"},
               {"term": "prompt", "korean": "프롬프트", "policy": "transliterate"},
               {"term": "LLM", "korean": "", "policy": "keep"},
               {"term": "bad", "korean": "나쁨", "policy": "weird"},
               {"term": "", "korean": "빈 용어"}]
    received: list = []
    client, sid = _env(tmp_path, [[("write_collection_glossary",
                                    {"collection": "NIRVANA", "entries": entries})], "반영했어요."],
                       received=received)
    events = _turn(client, sid, message="정신 모형으로 통일해 줘")
    assert [n for n, _ in events][-2:] == ["approval_needed", "done"]
    call = events[-2][1]["calls"][0]
    assert call["name"] == "write_collection_glossary"
    assert call["description"] == ("'NIRVANA' 컬렉션 용어집에 4개 쓰기(merge) — 예: "
                                   "mental model→정신 모형, agent→에이전트, prompt→프롬프트 외 1개")
    before = glossary.collection_path(pid).read_text(encoding="utf-8")

    events = _turn(client, sid, approvals={call["id"]: True})
    assert ("ui_action", {"kind": "refresh", "what": "glossary", "token": None,
                          "collection": pid}) in events
    assert glossary.collection_path(pid).read_text(encoding="utf-8") != before
    got = [(e.term, e.korean, e.policy) for e in glossary.load_collection(pid).entries]
    assert got == [("mental model", "정신 모형", "translate"),      # term_key로 덮어씀, 자리 유지
                   ("agent", "에이전트", "translate"),
                   ("prompt", "프롬프트", "transliterate"), ("LLM", "", "keep")]
    out = tool_returns(received[-1])[-1]
    assert out.startswith("컬렉션 'NIRVANA' 용어집에 4개 반영(새로 2, 바뀜 1, 그대로 1)")
    assert "영향: 다시 번역될 섹션이 없습니다" in out   # make_wd 논문엔 번역 캐시가 없다
    assert "건너뛴 항목 2개" in out and "policy 'weird'" in out and "term이 비어" in out
    assert "바뀐 섹션 재번역" in out
    assert "반영했어요" in events[-1][1]["html"]

    # replace는 통째로 — 빠진 항목 수도 알린다
    ctx = _ctx_of(tmp_path)
    out = asyncio.run(write_collection_glossary(ctx, pid, [{"term": "agent", "korean": "행위자"}],
                                                mode="replace"))
    assert "통째로 바꿔 1개 반영(새로 0, 바뀜 1, 그대로 0), 빠짐 3" in out
    assert [e.korean for e in glossary.load_collection(pid).entries] == ["행위자"]
    assert "mode" in asyncio.run(write_collection_glossary(ctx, pid, [], mode="append"))
    assert "바꾸지 않았습니다" in asyncio.run(
        write_collection_glossary(ctx, pid, [{"term": "x", "korean": ""}], mode="replace"))
    assert [e.korean for e in glossary.load_collection(pid).entries] == ["행위자"]
    assert describe_call("write_collection_glossary",
                         {"collection": pid, "entries": [{"term": "agent", "korean": "행위자"}],
                          "mode": "replace"}) == \
        "'NIRVANA' 컬렉션 용어집에 1개 쓰기(통째로 바꾸기) — 예: agent→행위자"


def test_write_collection_glossary_denied_writes_nothing(tmp_path):
    pid, _wds = _library(tmp_path)
    client, sid = _env(tmp_path, [[("write_collection_glossary",
                                    {"collection": pid, "entries": [{"term": "a", "korean": "가"}]})],
                                  "알겠어요."])
    events = _turn(client, sid, message="써 줘")
    cid = events[-2][1]["calls"][0]["id"]
    events = _turn(client, sid, approvals={cid: False})
    assert events[-1][0] == "done" and not any(n == "ui_action" for n, _ in events)
    assert not glossary.collection_path(pid).exists()


def test_read_collection_glossary(tmp_path):
    pid = projects.create("서베이")["id"]
    ctx = _ctx_of(tmp_path)
    assert "비어 있습니다" in asyncio.run(read_collection_glossary(ctx, "서베이"))
    glossary.save_collection(pid, GlossaryList(entries=[_e("encoder", "인코더"),
                                                        _e("BERT", "", "keep")]))
    out = asyncio.run(read_collection_glossary(ctx, pid))
    assert out.startswith("컬렉션 '서베이' 용어집 2개")
    assert "- encoder → 인코더 (translate)" in out and "- BERT → (원어 유지) (keep)" in out
    assert "list_projects" in asyncio.run(read_collection_glossary(ctx, "없음"))


# --- translation_impact: 번역 캐시가 있어야 낡은 섹션이 잡힌다 ------------------------


def _filler(tag: str, n: int = 30) -> str:
    return " ".join(f"This {tag} paragraph sentence number {i} says plain things." for i in range(n))


def _body(user: str) -> str:
    lines = user.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("=== 번역할 본문"):
            return "\n".join(lines[i + 1:])
    return user


def test_translation_impact_reports_stale_sections_and_cost(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    md = ("# A Long Study\n\n## Recurrent Part\n\nWe revisit recurrence and attention here. "
          + _filler("first") + "\n\n## Encoder Part\n\nThe encoder maps inputs. " + _filler("second")
          + "\n\n## Plain Part\n\n" + _filler("third") + "\n")
    (ws / "long.md").write_text(md, encoding="utf-8")
    wd = workdir.WorkDir(ws / "long.md4")
    pipeline.convert(ws / "long.md", wd)
    pid = projects.create("Coll")["id"]
    workdir.set_projects(wd.root, [pid])
    fake = FakeProvider(complete_fn=lambda s, u: _body(u),
                        parse_fn=lambda s, u, sc: GlossaryList(entries=[_e("encoder", "인코더")]),
                        model="gpt-5.6-luna")
    pipeline.run_translate(wd, fake)
    ctx = _ctx_of(ws)
    assert "다시 번역될 섹션이 없습니다" in asyncio.run(translation_impact(ctx, "Coll"))

    out = asyncio.run(write_collection_glossary(ctx, "Coll",
                                                [{"term": "recurrence", "korean": "순환"}]))
    assert "논문 1편의 섹션" in out and "다시 번역됨" in out and "$" in out

    out = asyncio.run(translation_impact(ctx, pid))
    tok = token_of(wd.root)
    assert "다음 번역 실행 때 다시 번역될 곳" in out
    line = next(ln for ln in out.splitlines() if ln.startswith(f"- token {tok}:"))
    assert "낡은 섹션" in line and "유닛 1" in line and "모델 gpt-5.6-luna" in line and "≈ $" in line
    assert "합계: 논문 1편" in out
    assert ctx.deps.wd_for(tok) is not None                      # token이 등록돼 다른 tool로 이어진다
    assert "list_projects" in asyncio.run(translation_impact(ctx, "없음"))


def test_tools_registered_and_system_mentions_them():
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in TOOLS}
    assert {"glossary_conflicts", "read_collection_glossary", "translation_impact",
            "write_collection_glossary"} <= names
    write = next(t for t in TOOLS if getattr(t, "name", "") == "write_collection_glossary")
    assert write.requires_approval
    for name in ("glossary_conflicts", "translation_impact", "write_collection_glossary"):
        assert name in SYSTEM
