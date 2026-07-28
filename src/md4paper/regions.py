"""섹션 영역 찾기 — manifest + sections.map.json으로 특정 섹션의 출력 라인 범위를 구한다.

cite(References)와 translate(Abstract)가 공유한다.
"""

from __future__ import annotations

import re

from md4paper.ir import Manifest


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def section_ids_by_text(manifest: Manifest, keywords: set[str]) -> set[str]:
    """텍스트(정규화)가 keywords에 속하는 헤더들의 id."""
    return {s.id for s in manifest.sections if _norm(s.text) in keywords}


def region_for_ids(section_map: list[dict], ids: set[str], n_lines: int) -> tuple[int, int] | None:
    """id 집합 중 처음 등장하는 섹션의 (헤딩 out_line, 다음 헤딩 out_line) 반환."""
    if not ids:
        return None
    entries = sorted(section_map, key=lambda e: e["out_line"])
    for i, e in enumerate(entries):
        if e["id"] in ids:
            start = e["out_line"]
            end = entries[i + 1]["out_line"] if i + 1 < len(entries) else n_lines
            return start, end
    return None
