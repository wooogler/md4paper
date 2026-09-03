"""뷰어 하이라이트·메모 저장 계층 — 정리(normalize) 규칙과 마크다운 내보내기."""

from __future__ import annotations

import json

from md4paper.ui import annotations
from md4paper.workdir import WorkDir


def _item(**over) -> dict:
    base = {"id": "a1", "side": "en", "row": 2, "start": 10, "end": 15,
            "quote": "hello", "prefix": "say ", "suffix": " there", "color": "green", "note": ""}
    base.update(over)
    return base


def test_normalize_fills_defaults_and_keeps_anchor():
    (a,) = annotations.normalize([{"quote": "hi"}])
    assert a["side"] == "en" and a["row"] == 0 and a["color"] == "yellow"
    assert a["start"] == 0 and a["end"] == 2  # end는 quote 길이에서 유도
    assert a["id"]  # 없으면 만들어 준다


def test_normalize_drops_unusable_and_duplicate_items():
    items = annotations.normalize([
        {"quote": "   "},          # 다시 찾을 단서가 없음
        "쓰레기",                    # dict가 아님
        _item(id="dup"),
        _item(id="dup", quote="other"),
    ])
    assert [a["id"] for a in items] == ["dup"]
    assert items[0]["quote"] == "hello"  # 먼저 온 것이 남는다


def test_normalize_clamps_unknown_values():
    (a,) = annotations.normalize([_item(side="pdf", color="neon", row=-3, start="x",
                                        note="n" * (annotations.MAX_NOTE + 50))])
    assert a["side"] == "en" and a["color"] == "yellow" and a["row"] == 0 and a["start"] == 0
    assert len(a["note"]) == annotations.MAX_NOTE


def test_save_load_roundtrip(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    annotations.save(wd, [_item(note="여기 중요")])
    assert json.loads(wd.annotations_json.read_text(encoding="utf-8"))["version"] == annotations.VERSION
    (a,) = annotations.load(wd)
    assert a["quote"] == "hello" and a["note"] == "여기 중요" and a["row"] == 2


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


def test_to_markdown_groups_by_side_in_document_order():
    md = annotations.to_markdown(annotations.normalize([
        _item(id="b", side="ko", row=1, quote="번역문", note="의역됨"),
        _item(id="a", side="en", row=5, quote="second"),
        _item(id="c", side="en", row=1, quote="first"),
    ]), title="논문")
    assert md.startswith("# 논문 — 하이라이트 · 메모")
    assert md.index("## 원문") < md.index("## 번역")
    assert md.index("> first") < md.index("> second")  # 행 순서
    assert "의역됨" in md


def test_export_bytes_sanitizes_filename(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    name, data = annotations.export_bytes(wd, "A/B: 논문")
    assert "/" not in name and ":" not in name and name.endswith(".md")
    assert "아직 표시한 내용이 없습니다" in data.decode("utf-8")
