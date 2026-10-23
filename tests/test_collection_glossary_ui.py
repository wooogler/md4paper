"""컬렉션 용어집의 화면·CLI — 대화상자 데이터 준비, 뷰어 용어집 시트·에이전트 refresh 계약, `collection glossary`.

백엔드(저장·effective·conflicts·impact)는 test_glossary_collection.py가 본다. 여기서는 그 위에 얹은 것:
대화상자의 행 합치기·통일·충돌 표 데이터, 뷰어가 컬렉션 행을 논문 용어집 목록에 섞지 않는지,
에이전트 창의 refresh(glossary) 경로, CLI 출력·--set·--conflicts·--impact.
"""

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner

from md4paper import config, projects, workdir
from md4paper.cli import cli
from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.translate import glossary
from md4paper.ui import agent_panel, collection_glossary_dialog as cgd
from md4paper.workdir import WorkDir


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "cfg" / "projects.json")
    config.set_section_value("output", "workspace", str(tmp_path / "ws"))  # config.toml은 conftest가 치운다


def _e(term: str, korean: str, policy: str = "translate") -> GlossaryEntry:
    return GlossaryEntry(term=term, korean=korean, policy=policy)


def _paper(tmp_path: Path, name: str, entries: list[GlossaryEntry], pids: list[str]) -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / f"{name}.md4")
    wd.ensure()
    wd.sections_yaml.write_text(f"title: {name}\nsections: []\n", encoding="utf-8")  # 목록에 잡히려면 필요
    glossary.save(GlossaryList(entries=entries), wd)
    workdir.set_projects(wd.root, list(pids))
    return wd


# --- 대화상자 데이터 준비 ---

def test_merge_dupes_and_merge_entry():
    rows = [{"term": "Encoder", "korean": "", "policy": "translate"},
            {"term": "", "korean": "", "policy": "translate"},  # 방금 '행 추가'한 빈 행은 그대로
            {"term": "encoder", "korean": "부호기", "policy": "keep"}]
    kept, removed = cgd.merge_dupes(rows)
    assert removed == 1 and [(d["term"], d["korean"], d["policy"]) for d in kept] == [
        ("Encoder", "부호기", "keep"), ("", "", "translate")]
    cgd.merge_entry(kept, "ENCODER", "인코더", "transliterate")  # 같은 용어 — 그 자리에서 바꾼다
    cgd.merge_entry(kept, "decoder", "디코더", "translate")
    assert [(d["term"], d["korean"]) for d in kept] == [("Encoder", "인코더"), ("", ""), ("decoder", "디코더")]


def test_conflict_rows_marks_current_value():
    by_paper = {"a": [_e("mental model", "정신 모형")], "b": [_e("Mental Model", "멘탈 모델", "병기-first-use")],
                "c": [_e("mental model", "정신 모형")]}
    data = cgd.conflict_rows(glossary.conflicts(by_paper), [{"term": "mental model", "korean": "정신 모형",
                                                            "policy": "translate"}], {"a": "논문 A"})
    assert len(data) == 1
    row = data[0]
    assert row["current"] == {"korean": "정신 모형", "policy": "translate"} and row["papers"] == 3
    assert [(v["korean"], v["n"], v["chosen"]) for v in row["variants"]] == [("정신 모형", 2, True), ("멘탈 모델", 1, False)]
    assert row["variants"][0]["titles"] == ["논문 A", "c"]
    assert cgd.conflict_rows(glossary.conflicts(by_paper), [])[0]["current"] is None


def test_load_overview_and_impact_summary(tmp_path):
    pid = projects.create("NIRVANA")["id"]
    a = _paper(tmp_path, "a", [_e("encoder", "인코더"), _e("tutor", "튜터")], [pid])
    _paper(tmp_path, "b", [_e("Encoder", "부호기"), _e("tutor", "튜터")], [pid])
    _paper(tmp_path, "c", [_e("encoder", "엔코더")], [])  # 다른 컬렉션(미분류)은 안 센다
    other = projects.create("다른")["id"]
    _paper(tmp_path, "d", [_e("encoder", "엔진")], [other, pid])  # 비주 소속 — 빼고 편수만 알린다
    ov = cgd.load_overview(pid, tmp_path / "ws")
    assert ov["papers"] == 2 and [c.key for c in ov["conflicts"]] == ["encoder"]
    assert ov["secondary"] == 1 and len(ov["conflicts"][0].variants) == 2
    assert [t[0] for t in ov["shared"]] == ["tutor"] and ov["titles"][a.root] == "a"
    assert cgd.impact_summary([]).startswith("낡은 번역 없음")
    line = cgd.impact_summary([{"stale": ["s1", "s2"], "est_cost_usd": 0.5}, {"stale": ["s9"], "est_cost_usd": 0.25}])
    assert line == "다음 번역 때 다시 번역될 곳: 논문 2편 · 섹션 3개 · ≈ $0.75"
    line = cgd.impact_summary([{"stale": ["s1", "s2"], "est_cost_usd": 0.5, "full": True},
                               {"stale": ["s9"], "est_cost_usd": 0.25, "full": False}])
    assert line == "다음 번역 때 다시 번역될 곳: 논문 2편 · 섹션 3개 · ≈ $0.75 · 그중 1편은 전체 재번역(캐시 못 씀)"


def test_dialog_entry_points_exist():
    from md4paper.ui import batch_translate_dialog, home

    assert callable(cgd.open_collection_glossary) and callable(batch_translate_dialog.open_batch_translate)
    src = inspect.getsource(home.build_home)
    assert 'ui.menu_item(t("용어집…")' in src and "open_collection_glossary(p)" in src  # 사이드바 ⋯ 메뉴
    assert "toggle_select_all" in src and 't("번역 ({n}편)", n=n)' in src  # 전체 선택 · 일괄 번역
    assert 'j.detail.get("queued")' in src and "translate_job.ahead(j)" in src  # 대기 중 작업 카드


# --- 뷰어 용어집 시트 · 에이전트 refresh ---

def test_viewer_keeps_collection_rows_out_of_paper_glossary():
    """컬렉션 행은 읽기 전용 구역에만 — gloss_entries(= glossary.yaml로 통째로 저장)에 섞이면 첫 blur에 새어 든다."""
    from md4paper.ui import app

    src = inspect.getsource(app.build)
    assert 'coll_view: dict = {"pid": ctrl.collection_id(), "entries": ctrl.collection_glossary()}' in src
    assert "gloss_entries.append" not in src.split("def coll_rows(", 1)[1].split("def on_glossary_refresh(", 1)[0]
    assert '"컬렉션 용어집이 우선합니다"' in src and "text-decoration:line-through" in src
    assert 't("용어집 검토 ({n} + 컬렉션 {n_coll})"' in src
    assert "ui.on(agent_panel.GLOSSARY_REFRESH_EVENT, on_glossary_refresh)" in src
    body = src.split("def on_glossary_refresh(", 1)[1].split("with ui.dialog() as gloss_dialog", 1)[0]
    assert "gloss_dialog.is_deleted" in body and 'args.get("collection")' in body and "ctrl.collection_id()" in body
    after = src.split("def after_collection_change(", 1)[1].split("coll_host =", 1)[0]
    assert "refresh_staleness()" in after and "coll_rows.refresh()" in after


def test_agent_refresh_glossary_branch():
    js = agent_panel.PANEL_JS if hasattr(agent_panel, "PANEL_JS") else inspect.getsource(agent_panel)
    rv = js.split("function refreshView(", 1)[1].split("function onEvent(", 1)[0]
    assert "what === 'glossary'" in rv
    assert rv.index("what === 'glossary'") < rv.index("tok !== paper")  # token이 없어도 닿는다
    assert f"emitEvent('{agent_panel.GLOSSARY_REFRESH_EVENT}', {{collection: coll || ''}})" in rv
    assert "refreshView(a.what, a.token, a.collection)" in js


# --- CLI ---

def test_cli_show_set_and_conflicts(tmp_path):
    pid = projects.create("NIRVANA")["id"]
    _paper(tmp_path, "a", [_e("mental model", "정신 모형"), _e("tutor", "튜터")], [pid])
    _paper(tmp_path, "b", [_e("Mental Model", "멘탈 모델"), _e("tutor", "튜터")], [pid])
    run = CliRunner().invoke

    res = run(cli, ["collection", "glossary", "NIRVANA"])
    assert res.exit_code == 0, res.output
    assert "NIRVANA 용어집 (0개)" in res.output and "(비어 있음)" in res.output

    res = run(cli, ["collection", "glossary", pid, "--set", "mental model=정신 모형",
                    "--set", "time: a reading=시간: 읽기:keep", "--set", "ratio=1:2"])
    assert res.exit_code == 0, res.output
    assert "추가 3 · 바꿈 0" in res.output
    got = [(e.term, e.korean, e.policy) for e in glossary.load_collection(pid).entries]
    assert got == [("mental model", "정신 모형", "translate"), ("time: a reading", "시간: 읽기", "keep"),
                   ("ratio", "1:2", "translate")]  # 끝 ':' 뒤가 처리 방식 이름일 때만 뗀다

    res = run(cli, ["collection", "glossary", "nirv", "--set", "Mental Model=멘탈 모델:병기-first-use"])
    assert "추가 0 · 바꿈 1" in res.output
    assert glossary.load_collection(pid).entries[0].policy == "병기-first-use"

    res = run(cli, ["collection", "glossary", "NIRVANA"])
    assert "Mental Model → 멘탈 모델 (병기-first-use)" in res.output and "ratio → 1:2 (translate)" in res.output

    res = run(cli, ["collection", "glossary", "NIRVANA", "--conflicts", "--shared"])
    assert res.exit_code == 0, res.output
    assert "용어집이 있는 논문 2편 · 어긋난 용어 1개" in res.output
    head = next(ln for ln in res.output.splitlines() if "[2편]" in ln)  # 표기는 더 많이 쓰인 쪽(동률이면 먼저 본 것)
    assert head.casefold().startswith("mental model  [2편]") and "✓ 멘탈 모델 (병기-first-use)" in head
    assert "    정신 모형 (translate) · 1편" in res.output and "    멘탈 모델 (translate) · 1편" in res.output
    assert "tutor → 튜터 (translate) · 2편" in res.output

    res = run(cli, ["collection", "glossary", "NIRVANA", "--impact"])
    assert res.exit_code == 0 and "낡은 번역 없음" in res.output  # 번역한 논문이 없다

    assert run(cli, ["collection", "glossary", "NIRVANA", "--set", "no-equals"]).exit_code != 0


def test_cli_edit_roundtrip(tmp_path, monkeypatch):
    pid = projects.create("SCALE")["id"]
    glossary.save_collection(pid, GlossaryList(entries=[_e("encoder", "인코더")]))
    seen: list[str] = []

    def fake_edit(text, extension=".yaml"):
        seen.append(text)
        return text.replace("인코더", "부호기") + "- term: decoder\n  korean: 디코더\n  policy: keep\n"

    monkeypatch.setattr("click.edit", fake_edit)
    res = CliRunner().invoke(cli, ["collection", "glossary", "SCALE", "--edit"])
    assert res.exit_code == 0, res.output
    assert seen[0].startswith("# 컬렉션 용어집")
    assert [(e.term, e.korean, e.policy) for e in glossary.load_collection(pid).entries] == [
        ("encoder", "부호기", "translate"), ("decoder", "디코더", "keep")]
