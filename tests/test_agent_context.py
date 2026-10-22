"""화면 맥락 스냅숏 — 형태 검증·상한·빈 값·프롬프트 블록."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from md4paper.ui.agent.context import PROMPT_MAX, QUOTE_MAX, ScreenContext, to_prompt  # noqa: E402


def test_empty_context_gives_empty_prompt():
    assert to_prompt(None) == ""
    assert to_prompt(ScreenContext()) == ""
    assert to_prompt(ScreenContext.model_validate({})) == ""
    assert ScreenContext().token is None


def test_full_context_renders_korean_block():
    ctx = ScreenContext.model_validate({
        "screen": "review", "paper": {"token": "0123456789ab", "title": "Bespoke Bots"},
        "lang": "both", "visible_rows": [14, 12, 13, 20], "top_row": 12,
        "selection": {"side": "en", "row": 13, "quote": "We interviewed  18\ninstructors"},
        "note_tabs": {"open": ["notes", "정리"], "focus": "정리"},
        "left_panel": "toc", "project": {"id": "p1", "name": "튜터 서베이"},
        "unknown_field": 1,
    })
    assert ctx.token == "0123456789ab"
    text = to_prompt(ctx)
    assert text.startswith("## 현재 화면")
    assert "논문 뷰어" in text and "Bespoke Bots" in text and "0123456789ab" in text
    assert "r12–r14, r20" in text and "맨 위 r12" in text
    assert '"We interviewed 18 instructors"' in text and "원문 r13" in text
    assert "정리" in text and "튜터 서베이" in text and "toc" in text


def test_selection_quote_is_capped():
    ctx = ScreenContext.model_validate({"selection": {"quote": "가" * 1000}})
    assert len(ctx.selection.quote) <= QUOTE_MAX + 1


def test_prompt_is_capped():
    ctx = ScreenContext.model_validate({
        "paper": {"token": "t", "title": "T" * 500},
        "visible_rows": list(range(0, 400, 2)),
        "selection": {"quote": "q" * 1000},
        "note_tabs": {"open": ["탭" * 40] * 30},
    })
    assert len(ctx.visible_rows) <= 60
    assert len(to_prompt(ctx)) <= PROMPT_MAX + 1
