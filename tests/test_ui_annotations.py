"""뷰어 하이라이트·메모 저장 계층 — 정리(normalize) 규칙과 마크다운 내보내기.

한 표시는 문장 단위이고 원문·번역 양쪽 앵커를 가진다.
"""

from __future__ import annotations

import json

from md4paper.ui import annotations
from md4paper.workdir import WorkDir


def _anchor(side: str = "en", **over) -> dict:
    base = {"side": side, "row": 2, "start": 10, "end": 15,
            "quote": "hello" if side == "en" else "안녕", "prefix": "say ", "suffix": " there"}
    base.update(over)
    return base


def _item(**over) -> dict:
    base = {"id": "a1", "color": "green", "note": "",
            "anchors": [_anchor("en"), _anchor("ko")]}
    base.update(over)
    return base


def test_normalize_fills_defaults():
    (a,) = annotations.normalize([{"anchors": [{"quote": "hi"}]}])
    assert a["color"] == "yellow" and a["note"] == "" and a["id"]
    (an,) = a["anchors"]
    assert an["side"] == "en" and an["row"] == 0
    assert an["start"] == 0 and an["end"] == 2  # end는 quote 길이에서 유도


def test_normalize_keeps_both_sides():
    (a,) = annotations.normalize([_item()])
    assert [x["side"] for x in a["anchors"]] == ["en", "ko"]
    assert a["anchors"][1]["quote"] == "안녕"


def test_normalize_drops_unusable_and_duplicate_items():
    items = annotations.normalize([
        {"anchors": [{"quote": "   "}]},        # 다시 찾을 단서가 없음
        {"anchors": []},                        # 앵커가 없음
        "쓰레기",                                 # dict가 아님
        _item(id="dup"),
        _item(id="dup", color="blue"),
    ])
    assert [a["id"] for a in items] == ["dup"]
    assert items[0]["color"] == "green"  # 먼저 온 것이 남는다


def test_normalize_keeps_one_anchor_per_side():
    (a,) = annotations.normalize([_item(anchors=[
        _anchor("en", quote="first"), _anchor("en", quote="second"), _anchor("ko"),
    ])])
    assert [x["quote"] for x in a["anchors"]] == ["first", "안녕"]


def test_normalize_clamps_unknown_values():
    (a,) = annotations.normalize([_item(color="neon", note="n" * (annotations.MAX_NOTE + 50),
                                        anchors=[_anchor("pdf", row=-3, start="x")])])
    assert a["color"] == "yellow" and len(a["note"]) == annotations.MAX_NOTE
    assert a["anchors"][0]["side"] == "en" and a["anchors"][0]["row"] == 0
    assert a["anchors"][0]["start"] == 0


def test_normalize_accepts_v1_flat_item():
    """앵커 개념이 없던 v1 파일도 앵커 하나로 읽어들인다 (사용자가 쓴 걸 버리지 않는다)."""
    (a,) = annotations.normalize([
        {"id": "old", "side": "ko", "row": 4, "start": 3, "end": 8,
         "quote": "옛날 표시", "color": "pink", "note": "메모"},
    ])
    assert a["id"] == "old" and a["color"] == "pink" and a["note"] == "메모"
    assert a["anchors"] == [{"side": "ko", "row": 4, "start": 3, "end": 8,
                             "quote": "옛날 표시", "prefix": "", "suffix": ""}]


def test_save_load_roundtrip(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    annotations.save(wd, [_item(note="여기 중요")])
    assert json.loads(wd.annotations_json.read_text(encoding="utf-8"))["version"] == annotations.VERSION
    (a,) = annotations.load(wd)
    assert a["note"] == "여기 중요" and len(a["anchors"]) == 2


def test_guess_and_orig_survive_normalize():
    (a,) = annotations.normalize([_item(anchors=[
        _anchor("ko", guess=1, orig={"quote": "예전 번역", "prefix": "앞", "suffix": "뒤", "x": 1}),
        _anchor("en", orig={"quote": "  "}),     # 단서 없는 orig는 버린다
    ])])
    ko, en = a["anchors"]
    assert ko["guess"] is True and ko["orig"] == {"quote": "예전 번역", "prefix": "앞", "suffix": "뒤"}
    assert "guess" not in en and "orig" not in en


def test_repair_save_backs_up_original_once(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    annotations.save(wd, [_item(note="원본")])
    original = wd.annotations_json.read_bytes()
    annotations.save(wd, [_item(note="보통 저장")])           # 수리가 아닌 저장은 백업하지 않는다
    assert not annotations.backup_path(wd).exists()
    original = wd.annotations_json.read_bytes()
    annotations.save(wd, [_item(note="1차 수리")], backup=True)
    assert annotations.backup_path(wd).read_bytes() == original
    annotations.save(wd, [_item(note="2차 수리")], backup=True)  # 두 번째 수리가 수리 전 원본을 덮지 않는다
    assert annotations.backup_path(wd).read_bytes() == original
    assert annotations.load(wd)[0]["note"] == "2차 수리"


def test_save_empty_removes_file(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    annotations.save(wd, [_item()])
    assert wd.annotations_json.exists()
    assert annotations.save(wd, []) == []
    assert not wd.annotations_json.exists()  # 빈 껍데기를 남기지 않는다


def test_load_survives_broken_file(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.annotations_json.write_text("{not json", encoding="utf-8")
    assert annotations.load(wd) == []  # 뷰어가 열리지 못하게 하지 않는다


def test_to_markdown_pairs_source_and_translation_in_document_order():
    md = annotations.to_markdown(annotations.normalize([
        _item(id="late", anchors=[_anchor("en", row=5, quote="second")]),
        _item(id="early", note="의역됨", anchors=[
            _anchor("en", row=1, quote="first"), _anchor("ko", row=1, quote="첫 문장"),
        ]),
    ]), title="논문")
    assert md.startswith("# 논문 — 하이라이트 · 메모")
    assert md.index("> first") < md.index("> second")   # 행 순서
    assert "> first\n>\n> *첫 문장*" in md               # 원문·번역이 한 인용구 안에
    assert "의역됨" in md


def test_to_markdown_handles_translation_only_item():
    md = annotations.to_markdown(annotations.normalize([_item(anchors=[_anchor("ko")])]))
    assert "> *안녕*" in md


def test_export_bytes_sanitizes_filename(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    name, data = annotations.export_bytes(wd, "A/B: 논문")
    assert "/" not in name and ":" not in name and name.endswith(".md")
    assert "아직 표시한 내용이 없습니다" in data.decode("utf-8")


# --- 자유 메모(notes.md) 병합 ---
def test_to_markdown_puts_free_notes_on_top():
    items = annotations.normalize([_item(note="문장 메모", anchors=[_anchor("en", quote="first")])])
    md = annotations.to_markdown(items, "T", notes="# 전체 인상\n\n방법이 약하다.\n")
    assert md.startswith("# T — 하이라이트 · 메모\n\n## 메모\n\n# 전체 인상\n\n방법이 약하다.\n\n## 하이라이트\n\n> first")
    assert "문장 메모" in md


def test_to_markdown_unchanged_without_free_notes():
    items = annotations.normalize([_item(anchors=[_anchor("en", quote="first")])])
    assert annotations.to_markdown(items, "T") == annotations.to_markdown(items, "T", notes="")
    assert "## 하이라이트" not in annotations.to_markdown(items, "T")


def test_export_with_notes_only(tmp_path):
    """하이라이트가 하나도 없어도 자유 메모만으로 내려받을 수 있다."""
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    assert annotations.has_any(wd) is False
    wd.notes_md.write_text("전체적으로 좋음\n", encoding="utf-8")
    assert annotations.has_any(wd) is True
    name, data = annotations.export_bytes(wd, "제목")
    assert name == "제목 - 메모.md"
    assert data.decode("utf-8") == "# 제목 — 하이라이트 · 메모\n\n## 메모\n\n전체적으로 좋음\n"
    assert "_아직 표시한 내용이 없습니다._" not in data.decode("utf-8")


def test_export_merges_notes_and_highlights(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.notes_md.write_text("요약 메모", encoding="utf-8")
    annotations.save(wd, [_item(note="여기 중요")])
    text = annotations.export_bytes(wd)[1].decode("utf-8")
    assert text.index("## 메모") < text.index("요약 메모") < text.index("## 하이라이트") < text.index("여기 중요")

