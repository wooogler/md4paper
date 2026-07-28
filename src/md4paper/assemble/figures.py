"""그림/표 렌더링 — 안정적 파일명 리네임 + 뷰어별 임베딩 플레이버.

marker의 `![](_page_2_Figure_1.jpeg)`(베어 파일명, 캡션 별도 줄)를
`images/fig-01.jpeg`로 리네임하고 캡션을 붙여, 선택된 뷰어 문법으로 emit한다.
재실행해도 문서 등장 순서 기반이라 파일명/diff가 안정적이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from md4paper.ir import Flavor
from md4paper.structure import captions

@dataclass
class FigurePlan:
    """렌더 계획: 이미지 줄 → 블록, 버릴 캡션 줄, 파일명 리네임 맵."""

    image_blocks: dict[int, list[str]] = field(default_factory=dict)
    drop_lines: set[int] = field(default_factory=set)
    rename: dict[str, str] = field(default_factory=dict)  # 원본 basename → 새 basename
    replace_lines: dict[int, str] = field(default_factory=dict)  # 줄 인덱스 → 재스타일한 캡션(표 등)


def _caption_text(label: str | None, caption: str | None) -> str:
    if label and caption:
        return f"{label}: {caption}"
    return label or caption or ""


def _caption_line(label: str | None, caption: str | None, style: str) -> str | None:
    """마크다운 텍스트 플레이버(STANDARD·OBSIDIAN)의 캡션 한 줄을 스타일에 맞게 포맷.

    본문과 구별되도록 bold-italic(라벨만 굵게+설명 이탤릭) / blockquote(인용구) / italic(전체 이탤릭).
    """
    if not label and not caption:
        return None
    if style == "blockquote":
        if label and caption:
            return f"> **{label}:** {caption}"
        return f"> **{label or caption}**"
    if style == "italic":
        return f"*{_caption_text(label, caption)}*"
    # bold-italic (기본): 라벨만 굵게, 설명은 이탤릭
    if label and caption:
        return f"**{label}:** *{caption}*"
    if label:
        return f"**{label}**"
    return f"*{caption}*"


def _alt_safe(text: str) -> str:
    """마크다운 alt 텍스트에서 대괄호를 제거 — ![...] 구문이 깨지지 않도록."""
    return text.replace("[", "(").replace("]", ")")


def _subcaption_line(subcaptions: list[str]) -> str | None:
    """서브피규어 라벨들을 이탤릭 한 줄로 (예: '*(a) …*  ·  *(b) …*'). 없으면 None."""
    items = [s.strip() for s in subcaptions if s.strip()]
    return "  ·  ".join(f"*{s}*" for s in items) if items else None


def _render(
    rel_path: str, label: str | None, caption: str | None, flavor: Flavor, caption_style: str,
    subcaptions: list[str] | None = None,
) -> list[str]:
    subcap = _subcaption_line(subcaptions or [])
    if flavor is Flavor.OBSIDIAN:
        block = [f"![[{rel_path}]]"]
        if subcap:
            block += ["", subcap]
        line = _caption_line(label, caption, caption_style)
        if line:
            block.append(line)
        return block
    if flavor is Flavor.NOTION:
        # Notion은 import 시 alt를 캡션으로 취급 → 전체 캡션을 alt에 (스타일 무관)
        block = [f"![{_alt_safe(_caption_text(label, caption))}]({rel_path})"]
        if subcap:
            block += ["", subcap]
        return block
    if flavor is Flavor.HTML:
        alt = label or ""
        out = ["<figure>", f'<img src="{rel_path}" alt="{alt}">']
        if subcap:
            out.append(f"<figcaption>{_subcaption_line(subcaptions or []) or ''}</figcaption>")
        cap = _caption_text(label, caption)
        if cap:
            out.append(f"<figcaption>{cap}</figcaption>")
        out.append("</figure>")
        return out
    # STANDARD: 이미지(alt=라벨) + 서브캡션 이탤릭 줄 + 스타일 적용된 메인 캡션
    block = [f"![{label or ''}]({rel_path})"]
    if subcap:
        block += ["", subcap]
    line = _caption_line(label, caption, caption_style)
    if line:
        block.append(line)
    return block


def build_plan(lines: list[str], flavor: Flavor, caption_style: str = "bold-italic") -> FigurePlan:
    """raw.md 줄 목록 → FigurePlan. caption_style은 그림·표 캡션 공통 표기."""
    plan = FigurePlan()
    pairs = captions.find_pairs(lines)
    used: set[str] = set()
    seq = 0
    for pair in pairs:
        if pair.image_path is None or pair.image_line is None:
            continue
        old_base = os.path.basename(pair.image_path)
        ext = os.path.splitext(old_base)[1] or ".jpeg"
        kind = "table" if pair.kind == "table" else "figure"
        # 캡션의 실제 번호를 파일명에 반영: "Figure 3" → figure-3, "Table 2" → table-2.
        # 캡션이 없어 번호를 모르면 figure-N과 헷갈리지 않게 image-N(순번)으로.
        num = pair.label.split()[-1] if pair.label else ""
        if num:
            stem = f"{kind}-{num}"
        else:
            seq += 1
            stem = f"image-{seq}"
        new_base = f"{stem}{ext}"
        dup = 2
        while new_base in used:  # 같은 번호가 둘이면 -2, -3 … (캡션 페어링 오차 대비)
            new_base = f"{stem}-{dup}{ext}"
            dup += 1
        used.add(new_base)
        plan.rename[old_base] = new_base

        rel_path = f"images/{new_base}"
        plan.image_blocks[pair.image_line] = _render(
            rel_path, pair.label, pair.caption, flavor, caption_style, pair.subcaptions)
        if pair.caption_line is not None:
            plan.drop_lines.add(pair.caption_line)
        plan.drop_lines.update(pair.consumed_lines)  # 서브캡션 원위치 줄 제거 (텍스트는 블록으로 이동)

    # 이미지가 아닌 캡션(마크다운 표 그리드·헤더에서 벗긴 표/그림 캡션)도 같은 caption_style로.
    # → Figure 캡션 표기 설정이 Table에도 그대로 반영된다.
    for idx, (label, caption) in captions.find_table_captions(lines, plan.drop_lines).items():
        styled = _caption_line(label, caption, caption_style)
        if styled:
            plan.replace_lines[idx] = styled
    return plan
