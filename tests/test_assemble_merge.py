"""재조립 때 직접 고친 en.md 살리기 — 3-way merge (§assemble/merge)."""

from md4paper.assemble.merge import line_map, merge3

BASE = """# Title

## 2 Background

Sentence one. Sentence two [1](#ref-1).

## 3 Method

Method body.
"""


def test_level_change_and_body_edit_both_kept():
    mine = BASE.replace("Sentence one.", "Sentence one, fixed.")
    theirs = BASE.replace("## 3 Method", "### 3 Method")
    r = merge3(BASE, mine, theirs)
    assert "Sentence one, fixed." in r.text and "### 3 Method" in r.text
    assert (r.conflicts, r.dropped) == (0, 0)


def test_added_paragraph_survives():
    mine = BASE.replace("Method body.\n", "Method body.\n\n사용자가 덧붙인 문단.\n")
    theirs = BASE.replace("## 2 Background", "### 2 Background")
    r = merge3(BASE, mine, theirs)
    assert r.text.endswith("사용자가 덧붙인 문단.\n") and "### 2 Background" in r.text


def test_same_line_merges_word_by_word():
    """인용 표기를 바꾸면 theirs가 문단 줄을 고친다 — 같은 줄의 다른 낱말을 고친 사용자 편집도 산다."""
    mine = BASE.replace("Sentence one.", "First sentence.")
    theirs = BASE.replace("[1](#ref-1)", "[Vaswani 2017](#ref-1)")
    r = merge3(BASE, mine, theirs)
    assert "First sentence. Sentence two [Vaswani 2017](#ref-1)." in r.text
    assert r.conflicts == 0


def test_edited_heading_takes_new_level_with_user_title():
    mine = BASE.replace("## 3 Method", "## 3 Our Method")
    theirs = BASE.replace("## 3 Method", "### 3 Method")
    r = merge3(BASE, mine, theirs)
    assert "### 3 Our Method" in r.text and "## 3 Method" not in r.text
    assert r.conflicts == 0


def test_edited_heading_follows_italic():
    mine = BASE.replace("## 3 Method", "## 3 Our Method")
    theirs = BASE.replace("## 3 Method", "*3 Method*")
    assert "*3 Our Method*" in merge3(BASE, mine, theirs).text


def test_dropped_section_takes_its_edits_with_it():
    mine = BASE.replace("Method body.", "Method body, edited.")
    theirs = BASE.replace("## 3 Method\n\nMethod body.\n", "")
    r = merge3(BASE, mine, theirs)
    assert "Method" not in r.text and r.dropped == 1


def test_true_conflict_keeps_user_text():
    mine = BASE.replace("Method body.", "User wrote this.")
    theirs = BASE.replace("Method body.", "Pipeline wrote that.")
    r = merge3(BASE, mine, theirs)
    assert "User wrote this." in r.text and r.conflicts == 1


def test_no_structure_change_returns_user_text():
    mine = BASE.replace("Method body.", "Changed.")
    assert merge3(BASE, mine, BASE).text == mine


def test_line_map_follows_inserted_lines():
    old = ["a", "b", "c"]
    new = ["a", "x", "y", "b", "c"]
    assert line_map(old, new) == [0, 3, 4]
