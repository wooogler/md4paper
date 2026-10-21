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


def test_view_is_remembered_per_paper_without_touching_tabs(tmp_path):
    wd = _wd(tmp_path)
    notes.save_view(wd, {"panes": ["메모"], "focus": 0, "ratio": 50})
    assert not (wd.root / "notes").exists()          # 메모를 안 쓰는 논문엔 폴더를 만들지 않는다
    q = notes.add(wd, "질문")
    notes.save_view(wd, {"panes": [q], "focus": 0, "ratio": 50})
    assert notes.load_view(wd)["panes"] == ["질문"]
    notes.add(wd, "아이디어")                          # 탭 목록을 고쳐도 보던 자리는 남는다
    assert notes.load_view(wd)["panes"] == ["질문"]
    assert notes.tab_names(wd) == ["메모", "질문", "아이디어"]


# ---------------------------------------------------------------- 조용히 잃지 않기

def _trash(wd):
    d = wd.root / "notes" / ".trash"
    return sorted(d.glob("*.md")) if d.exists() else []


def test_write_refuses_when_file_changed_since_stamp(tmp_path):
    wd = _wd(tmp_path)
    notes.write(wd, "메모", "처음 내용\n")
    text, st = notes.read_stamped(wd, "메모")
    assert text == "처음 내용\n" and st == notes.stamp(wd, "메모")
    wd.notes_md.write_text("다른 창에서 고친 4.7KB 메모\n", encoding="utf-8")   # 바깥에서 바뀜
    import pytest
    with pytest.raises(notes.Conflict):
        notes.write(wd, "메모", "#", expect=st)
    assert wd.notes_md.read_text(encoding="utf-8") == "다른 창에서 고친 4.7KB 메모\n"  # 덮지 않았다
    # 지문이 맞으면 쓰고 새 지문을 돌려준다 — 다음 쓰기는 그 지문으로
    st2 = notes.stamp(wd, "메모")
    st3 = notes.write(wd, "메모", "이어 쓴 내용\n", expect=st2)
    assert st3 == notes.stamp(wd, "메모") and notes.write(wd, "메모", "또\n", expect=st3)


def test_stamp_of_missing_file_is_none_and_guards_creation(tmp_path):
    wd = _wd(tmp_path)
    assert notes.read_stamped(wd, "메모") == ("", None)
    notes.write(wd, "메모", "새 메모", expect=None)              # 없던 파일 — 여전히 없으면 쓴다
    import pytest
    with pytest.raises(notes.Conflict):
        notes.write(wd, "메모", "다른 편집기", expect=None)      # 그 사이 누가 만들었다


def test_emptying_or_big_shrink_keeps_old_copy_in_trash(tmp_path):
    wd = _wd(tmp_path)
    long = "긴 메모 한 줄\n" * 50
    notes.write(wd, "메모", long)
    notes.write(wd, "메모", long + "조금 더\n")                   # 늘리기 — 남기지 않는다
    assert _trash(wd) == []
    notes.write(wd, "메모", "#")                                   # 20% 미만으로 줄었다
    [t] = _trash(wd)
    assert t.read_text(encoding="utf-8") == long + "조금 더\n"
    notes.write(wd, "메모", "")                                    # 비우기 — 파일은 지우되 직전 것을 남긴다
    assert not wd.notes_md.exists()
    assert [f.read_text(encoding="utf-8") for f in _trash(wd)].count("#") == 1
    assert notes.tab_names(wd) == ["메모"]                         # .trash는 탭이 아니다
    assert notes.combined(wd) == ""


def test_keep_old_and_keep_and_remove_go_to_trash(tmp_path):
    wd = _wd(tmp_path)
    notes.write(wd, "메모", "바깥 내용")
    notes.write(wd, "메모", "내 내용으로 덮기", keep_old=True)
    assert [f.read_text(encoding="utf-8") for f in _trash(wd)] == ["바깥 내용"]
    notes.keep(wd, "메모", "버리는 편집기 내용")
    q = notes.add(wd, "질문")
    notes.write(wd, q, "지울 탭의 내용")
    notes.remove(wd, q)
    got = {f.read_text(encoding="utf-8") for f in _trash(wd)}
    assert got == {"바깥 내용", "버리는 편집기 내용", "지울 탭의 내용"}


def test_trash_is_capped(tmp_path, monkeypatch):
    wd = _wd(tmp_path)
    monkeypatch.setattr(notes, "_TRASH_KEEP", 3)
    for i in range(6):
        notes.keep(wd, "메모", f"판 {i}")
    assert len(_trash(wd)) == 3
