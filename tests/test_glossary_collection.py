"""컬렉션 용어집 — 저장·덮어쓰기(effective)·충돌 집계·컬렉션 삭제 정리.

컬렉션 용어집은 `CONFIG_DIR/glossary/<id>.yaml`이고 번역 때 논문 용어집 위에 주 컬렉션의 것을
덮어쓴다(같은 term_key면 컬렉션이 이긴다). CONFIG_DIR은 테스트 세션 내내 같은 임시 폴더라 파일이
테스트 사이로 새지 않게 매번 tmp로 바꾼다(`collection_dir`가 부를 때마다 읽으므로 먹힌다).
"""

from pathlib import Path

import pytest

from md4paper import config, projects, workdir
from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.translate import glossary
from md4paper.workdir import WorkDir


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "cfg" / "projects.json")


def _e(term: str, korean: str, policy: str = "translate") -> GlossaryEntry:
    return GlossaryEntry(term=term, korean=korean, policy=policy)


def _paper(tmp_path: Path, name: str, entries: list[GlossaryEntry] | None, pids: list[str] = ()) -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / f"{name}.md4")
    wd.ensure()
    wd.sections_yaml.write_text(f"title: {name}\nsections: []\n", encoding="utf-8")  # 목록에 잡히려면 필요
    if entries is not None:
        glossary.save(GlossaryList(entries=entries), wd)
    if pids:
        workdir.set_projects(wd.root, list(pids))
    return wd


def test_save_load_roundtrip_and_delete(tmp_path):
    assert glossary.collection_path("p1") == tmp_path / "cfg" / "glossary" / "p1.yaml"
    assert glossary.load_collection("p1").entries == []  # 아직 없는 컬렉션
    glossary.save_collection("p1", GlossaryList(entries=[_e("Mental Model", "정신 모형"),
                                                          _e("mental model", "멘탈 모델")]))
    text = glossary.collection_path("p1").read_text(encoding="utf-8")
    assert text.startswith("# 컬렉션 용어집 — 이 컬렉션의 모든 논문 번역에 논문 용어집보다 우선 적용됩니다.")
    assert "# policy:" in text
    assert [(e.term, e.korean) for e in glossary.load_collection("p1").entries] == [("Mental Model", "정신 모형")]
    assert glossary.delete_collection("p1") is True
    assert glossary.delete_collection("p1") is False  # 없으면 조용히
    assert glossary.load_collection("p1").entries == []


def test_paper_save_load_unchanged_on_path_helpers(tmp_path):
    wd = _paper(tmp_path, "a", [_e("encoder", "인코더")])
    assert wd.glossary_yaml.read_text(encoding="utf-8").startswith("# 용어집 — 번역 전에 검토/수정하세요")
    assert glossary.load_path(wd.glossary_yaml).entries == glossary.load(wd).entries


def test_effective_collection_wins_in_paper_position(tmp_path):
    wd = _paper(tmp_path, "a", [_e("attention", "어텐션", "transliterate"), _e("Encoder", "인코더"),
                                _e("decoder", "디코더")])
    glossary.save_collection("p1", GlossaryList(entries=[_e("new term", "새 용어"), _e("ENCODER", "부호기")]))
    eff, origin = glossary.effective(wd, "p1")
    assert [(e.term, e.korean) for e in eff.entries] == [
        ("attention", "어텐션"), ("ENCODER", "부호기"), ("decoder", "디코더"), ("new term", "새 용어")]
    assert origin == {"attention": "paper", "encoder": "collection", "decoder": "paper",
                      "new term": "collection"}


def test_effective_uses_primary_collection_and_handles_missing(tmp_path):
    p1 = projects.create("첫째")["id"]
    p2 = projects.create("둘째")["id"]
    glossary.save_collection(p1, GlossaryList(entries=[_e("encoder", "부호기")]))
    glossary.save_collection(p2, GlossaryList(entries=[_e("encoder", "엔코더")]))
    wd = _paper(tmp_path, "a", [_e("encoder", "인코더")], pids=[p2, p1])
    assert glossary.effective(wd)[0].entries[0].korean == "엔코더"  # 주 컬렉션(첫 소속)만

    alone = _paper(tmp_path, "b", [_e("encoder", "인코더")])  # 미분류 — 컬렉션 없음
    eff, origin = glossary.effective(alone)
    assert [e.korean for e in eff.entries] == ["인코더"] and origin == {"encoder": "paper"}

    no_file = _paper(tmp_path, "c", None, pids=[p1])  # 논문 용어집 파일이 아직 없음
    eff, origin = glossary.effective(no_file)
    assert [e.korean for e in eff.entries] == ["부호기"] and origin == {"encoder": "collection"}
    assert glossary.effective(_paper(tmp_path, "d", None))[0].entries == []


def test_conflicts_group_case_insensitively_and_sort_by_papers():
    by_paper = {
        "t1": [_e("Mental Model", "정신 모형"), _e("prompt", "프롬프트", "transliterate")],
        "t2": [_e("mental model", "정신 모형"), _e("prompt", "프롬프트", "병기-first-use")],
        "t3": [_e("mental model", "심적 모형"), _e("Prompt", "프롬프트", "transliterate"),
               _e("agent", "에이전트")],
        "t4": [_e("MENTAL MODEL", "멘탈 모델"), _e("agent", "에이전트")],
        "t5": [_e("mental  model ", " 정신 모형 "), _e("solo", "단독")],
    }
    out = glossary.conflicts(by_paper)
    assert [c.key for c in out] == ["mental model", "prompt"]  # 관련 논문 5편 > 3편
    mm = out[0]
    assert mm.term == "mental model" and mm.paper_count == 5  # 가장 많이 쓰인 표기
    assert [(v.korean, v.papers) for v in mm.variants] == [
        ("정신 모형", ["t1", "t2", "t5"]), ("심적 모형", ["t3"]), ("멘탈 모델", ["t4"])]
    pr = out[1]  # 번역어는 같고 policy만 달라도 충돌
    assert [(v.policy, v.papers) for v in pr.variants] == [
        ("transliterate", ["t1", "t3"]), ("병기-first-use", ["t2"])]

    assert glossary.shared(by_paper) == [("agent", "에이전트", "translate", ["t3", "t4"])]
    assert glossary.shared(by_paper, min_papers=1)[-1][0] == "solo"


def test_collection_entries_by_paper_and_projects_delete_cleans_file(tmp_path):
    pid = projects.create("서베이")["id"]
    other = projects.create("다른")["id"]
    a = _paper(tmp_path, "a", [_e("encoder", "인코더")], pids=[pid])
    _paper(tmp_path, "b", None, pids=[pid])  # 용어집 없음 → 건너뜀
    c = _paper(tmp_path, "c", [_e("encoder", "부호기")], pids=[other, pid])  # 주 컬렉션이 아니어도 소속
    _paper(tmp_path, "d", [_e("encoder", "엔코더")], pids=[other])
    got = glossary.collection_entries_by_paper(pid, tmp_path / "ws")
    assert set(got) == {a.root, c.root}
    assert [c.key for c in glossary.conflicts(got)] == ["encoder"]

    glossary.save_collection(pid, GlossaryList(entries=[_e("encoder", "인코더")]))
    assert projects.delete(pid, tmp_path / "ws")
    assert not glossary.collection_path(pid).exists()
    assert projects.delete("p-없는-것") is False  # 파일이 없어도 조용히
