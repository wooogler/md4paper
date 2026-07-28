"""구조 단계 — raw.md에서 헤더를 파싱하고 번호 기반으로 재레벨링해 manifest를 만든다.

marker JSON은 이후 페이지 번호·TOC 교차검증으로 얹는다. M0는 raw.md를 척추로 삼는다
(이 경로가 곧 degrade 경로이기도 하다).
"""

from __future__ import annotations

import json
import re

from md4paper import prefs
from md4paper.ir import Flavor, Manifest, ManifestSection, Scheme
from md4paper.structure import captions, numbering, runin
from md4paper.workdir import WorkDir

_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")
# marker가 헤더에 심는 앵커/HTML 태그 (예: <span id="page-2-0"></span>) — 번호 감지·표시를 방해
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_heading_text(text: str) -> str:
    """헤더 텍스트에서 HTML 태그(marker 페이지 앵커 등)를 제거하고 공백 정리."""
    return _HTML_TAG_RE.sub("", text).strip()


def parse_headings(raw_md: str) -> list[tuple[int, int, str]]:
    """ATX 헤더 파싱 (펜스 코드 블록 내부는 무시).

    반환: (line_idx, marker_level, text) 목록.
    """
    out: list[tuple[int, int, str]] = []
    in_fence = False
    for i, line in enumerate(raw_md.splitlines()):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ATX_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), clean_heading_text(m.group(2))))
    return out


def _context_snippet(lines: list[str], heading_line: int, words: int = 10) -> str | None:
    """헤더 다음의 첫 본문 줄에서 앞 몇 단어 (블라인드 편집 방지용)."""
    in_fence = False
    for k in range(heading_line + 1, len(lines)):
        s = lines[k].strip()
        if _FENCE_RE.match(s):
            in_fence = not in_fence
            continue
        if in_fence or not s or _ATX_RE.match(lines[k]) or s.startswith("!["):
            continue
        toks = s.split()
        snippet = " ".join(toks[:words])
        return snippet + ("…" if len(toks) > words else "")
    return None


def _find_runins(lines: list[str], atx_lines: set[int]) -> list[tuple[int, str, str]]:
    """본문 줄에서 run-in 헤더 탐색. 반환: (line, 라벨, 나머지 본문)."""
    out: list[tuple[int, str, str]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or i in atx_lines:
            continue
        det = runin.detect(line)
        if det:
            num, title, body, _ = det
            out.append((i, f"{num} {title}" if num else title, body))
    return out


def _heading_pages(wd: WorkDir) -> dict[str, int]:
    """추출기가 준 헤더별 PDF 페이지 (정규화된 텍스트 → 페이지)."""
    if not wd.headings_json.exists():
        return {}
    try:
        items = json.loads(wd.headings_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {re.sub(r"\s+", " ", it["text"]).strip().lower(): it["page"] for it in items if it.get("text")}


def _llm_runin_decisions(cands: list, releveled: list, provider) -> dict[int, tuple[bool, int]] | None:  # noqa: ANN001
    """무번호 run-in 후보의 (is_heading, level)을 LLM으로 판정. 판정 대상/키 없으면 None(규칙 폴백)."""
    has_candidate = any(c[3] and r[2] for c, r in zip(cands, releveled))  # is_runin & needs_review
    if not has_candidate or provider is None:
        return None
    from md4paper.structure import section_layout

    items: list[dict] = []
    for i, ((line, mlvl, text, is_runin, body), (_det, lvl, nr)) in enumerate(zip(cands, releveled)):
        if is_runin and nr:  # 판정 대상 — 무번호 run-in
            items.append({"index": i, "text": text, "level": None, "context": body or ""})
        else:  # 확정 헤더(번호형·ATX) — 레벨을 보여줘 문맥·형제 판단 근거로
            items.append({"index": i, "text": text, "level": (mlvl if nr else lvl)})
    return section_layout.classify(provider, items)


def build(raw_md: str, wd: WorkDir, flavor: Flavor = Flavor.STANDARD, provider=None) -> Manifest:  # noqa: ANN001
    """raw.md → manifest(sections.yaml) + blocks.json. Manifest 반환.

    ATX 헤더와 본문 내 run-in 헤더를 함께 트리에 넣는다(사용자가 둘 다 조정 가능).
    provider가 있으면 애매한 무번호 run-in의 역할·레벨을 LLM이 판정(라벨→코드 조립), 없으면 규칙 폴백.
    """
    from md4paper import config

    lines = raw_md.splitlines()
    atx = parse_headings(raw_md)  # (line, marker_level, text)
    atx_lines = {ln for ln, _, _ in atx}
    runins = _find_runins(lines, atx_lines)
    runin_mode = config.resolve_runin_mode()

    # ATX + run-in 을 문서 순서로 병합
    cands = [(ln, lvl, txt, False, None) for ln, lvl, txt in atx]
    cands += [(ln, 3, label, True, body) for ln, label, body in runins]
    cands.sort(key=lambda c: c[0])

    releveled = numbering.relevel([c[2] for c in cands])
    pages = _heading_pages(wd)
    # 구조 단계 LLM 하이브리드: 애매한 무번호 run-in 후보의 역할·레벨 판정 (없으면 규칙 폴백)
    runin_decisions = _llm_runin_decisions(cands, releveled, provider)

    sections: list[ManifestSection] = []
    last_header_level = 1  # 마지막 '실제 헤더'(ATX·번호형) 레벨 — 무번호 run-in의 부모 기준
    for idx, ((line, marker_level, text, is_runin, body), (detected, level, needs_review)) in enumerate(
        zip(cands, releveled)
    ):
        unnumbered_runin = False
        if is_runin:
            unnumbered = needs_review  # relevel이 깊이를 못 준 무번호 run-in
            needs_review = False       # run-in은 기본 동작이 정해져 경고 대상 아님
            if not unnumbered:         # 번호형 run-in → 결정적 깊이
                computed, is_head = level, True
            else:
                unnumbered_runin = True
                dec = runin_decisions.get(idx) if runin_decisions else None
                if dec is not None:    # LLM 판정 (진짜 소제목? + 레벨)
                    is_head, computed = dec
                else:                  # 규칙 폴백: '직전 실제 헤더'보다 한 단계 아래(형제는 같은 레벨)
                    is_head, computed = True, min(last_header_level + 1, 6)
            # 소제목이면 runin_mode에 따라 표기(승격/이탤릭/유지), 아니면(본문·리스트·캡션) skip
            final_level = ({"header": computed, "italic": "italic"}.get(runin_mode, "skip")
                           if is_head else "skip")
        else:
            final_level = marker_level if needs_review else level
        # 이전 논문에서 이 헤더 이름에 대해 사용자가 정한 선택이 있으면 그걸 적용
        auto_level = final_level
        remembered = prefs.lookup(text)
        if remembered is not None:
            final_level = remembered
            needs_review = False  # 사용자가 이미 판단한 항목
        if isinstance(final_level, int) and not unnumbered_runin:
            # 무번호 run-in은 부모 기준을 밀지 않는다(형제 run-in이 같은 레벨을 쓰게)
            last_header_level = final_level

        sections.append(
            ManifestSection(
                id=f"h_{idx:04d}",
                text=text,
                line=line,
                page=pages.get(re.sub(r"\s+", " ", text).strip().lower()),
                detected=detected,
                marker_level=marker_level,
                level=final_level,
                auto_level=auto_level,
                context=(body[:60] if is_runin and body else _context_snippet(lines, line)),
                needs_review=needs_review,
                runin=is_runin,
                runin_body=body,
            )
        )

    title = _guess_title(lines, sections)
    # 문서 제목을 별도 취급 — h1 고정, 확인/기억 대상에서 제외
    for s in sections:
        if s.text == title and s.detected is not None and s.detected.scheme is Scheme.NONE:
            s.is_title = True
            s.level = 1
            s.auto_level = 1
            s.needs_review = False
            break

    # 문서 수준 설정은 config 기본값으로 시드 (이후 manifest가 권위 소스; UI/에디터로 편집)
    from md4paper import config

    manifest = Manifest(
        title=title,
        flavor=flavor,
        citation_parts=config.resolve_citation_parts(),
        reference_links=config.resolve_reference_links(),
        runin_headings=config.resolve_runin_mode(),
        caption_style=config.resolve_caption_style(),
        korean_style=config.resolve_korean_style(),
        translate_headers=config.resolve_translate_headers(),
        translate_references=config.resolve_translate_references(),
        author_parts=config.resolve_author_parts(),
        sections=sections,
    )

    # blocks.json — 이후 단계용 (헤더 라인 맵 + 그림/캡션 짝)
    pairs = captions.find_pairs(lines)
    blocks = {
        "raw_md_hash": None,
        "n_lines": len(lines),
        "headings": [
            {"id": s.id, "line": s.line, "marker_level": s.marker_level, "text": s.text}
            for s in sections
        ],
        "figures": [p.model_dump() for p in pairs],
    }
    wd.structure.mkdir(parents=True, exist_ok=True)
    wd.blocks_json.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


_NONTITLE_KW = {"abstract", "references", "keywords", "ccs concepts", "acm reference format"}


def _looks_like_title(text: str) -> bool:
    """제목다운지 — 각주·문장(마침표로 끝나거나 너무 긴 것)을 배제."""
    t = text.strip()
    if not t or len(t) > 140:
        return False
    if t.rstrip().endswith((".", ";")):  # 각주·문장은 마침표로 끝난다
        return False
    return re.sub(r"[:\-–—]+$", "", t).strip().lower() not in _NONTITLE_KW


def _guess_title(lines: list[str], sections: list[ManifestSection]) -> str:
    """문서 제목 추정: 제목다운 첫 최상위 헤더 (각주·긴 문장 제외)."""
    tops = [s.text for s in sections if s.marker_level == 1]
    for t in tops:
        if _looks_like_title(t):
            return t
    # 제목다운 h1이 없으면: 가장 얕은 레벨의 제목다운 첫 헤더
    for s in sections:
        if _looks_like_title(s.text):
            return s.text
    return tops[0] if tops else (sections[0].text if sections else "")
