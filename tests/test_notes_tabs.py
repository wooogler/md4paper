"""메모 탭 — 첫 탭은 notes.md, 더 만든 탭은 notes/<이름>.md, 순서·이름은 notes/_tabs.json."""

from __future__ import annotations

from md4paper import library, notes
from md4paper.ui import annotations
from md4paper.workdir import WorkDir


def _wd(tmp_path) -> WorkDir:
    wd = WorkDir(tmp_path / "2017_Attention_Vaswani.md4")
    wd.root.mkdir(parents=True)
    return wd


def test_default_is_single_tab_backed_by_notes_md(tmp_path):
    wd = _wd(tmp_path)
    assert notes.tab_names(wd) == ["메모"]
    notes.write(wd, "메모", "첫 메모\n")
    assert wd.notes_md.read_text(encoding="utf-8") == "첫 메모\n"   # 예전 자리 그대로
    assert not (wd.root / "notes").exists()                         # 탭을 만들기 전엔 폴더도 없다


def test_add_rename_remove(tmp_path):
    wd = _wd(tmp_path)
    notes.write(wd, "메모", "요약 내용")
    q = notes.add(wd, "질문")
    assert notes.tab_names(wd) == ["메모", "질문"]
    notes.write(wd, q, "왜 이 데이터셋인가?")
    assert (wd.root / "notes" / "질문.md").read_text(encoding="utf-8") == "왜 이 데이터셋인가?"

    assert notes.add(wd, "질문") == "질문 2"                      # 이름이 겹치면 번호
    assert notes.add(wd, "") == "메모 2"                          # 비면 기본 이름

    new = notes.rename(wd, "질문", "열린 질문")
    assert new == "열린 질문"
    assert notes.read(wd, "열린 질문") == "왜 이 데이터셋인가?"     # 파일도 함께 옮겨졌다
    assert not (wd.root / "notes" / "질문.md").exists()
    assert notes.rename(wd, "메모", "요약") == "요약"
    assert wd.notes_md.exists()                                     # 첫 탭 파일은 이름이 바뀌어도 notes.md

    left = notes.remove(wd, "열린 질문")
    assert "열린 질문" not in left
    assert not (wd.root / "notes" / "열린 질문.md").exists()


def test_last_tab_cannot_be_removed_and_names_are_cleaned(tmp_path):
    wd = _wd(tmp_path)
    assert notes.remove(wd, "메모") == ["메모"]
    assert notes.add(wd, '../a/b:c*') == "..abc".lstrip(".")       # 경로·금지 글자는 빠진다
    assert all((wd.root / "notes").resolve() in p.resolve().parents
               for p in (wd.root / "notes").glob("*.md"))


def test_empty_tab_keeps_its_place_but_no_file(tmp_path):
    wd = _wd(tmp_path)
    t = notes.add(wd, "아이디어")
    notes.write(wd, t, "x")
    notes.write(wd, t, "  \n")
    assert t in notes.tab_names(wd)
    assert not (wd.root / "notes" / "아이디어.md").exists()


def test_combined_export_keeps_old_shape_for_one_tab_and_sections_for_many(tmp_path, monkeypatch):
    wd = _wd(tmp_path)
    notes.write(wd, "메모", "전체적으로 좋음\n")
    assert notes.combined(wd) == "전체적으로 좋음\n"
    q = notes.add(wd, "질문")
    notes.write(wd, q, "표본이 작다")
    text = notes.combined(wd)
    assert text.index("### 메모") < text.index("전체적으로 좋음") < text.index("### 질문") < text.index("표본이 작다")
    assert annotations.load_notes(wd) == text

    monkeypatch.setattr(library, "notes_dir", lambda project=None: tmp_path / "EN")
    out = library.export_notes(wd)
    assert out.read_text(encoding="utf-8") == text
