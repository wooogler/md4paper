"""문서 컨텍스트 추출 — 제목 + Abstract.

Abstract는 논문 주제·기여·핵심 용어를 압축한 절이므로, 모든 섹션 번역의 시스템 프롬프트에
문서 컨텍스트로 주입해 용어·톤 일관성을 확보한다(Abstract 기반 contextual 번역).
"""

from __future__ import annotations

import json
import re

from md4paper.ir import Manifest
from md4paper.regions import region_for_ids, section_ids_by_text
from md4paper.workdir import WorkDir

_ABSTRACT_KEYWORDS = {"abstract"}
# 용어집 소스 상한 (토큰·비용 방어). 초록+서론+섹션 첫 문단이면 대개 이 안에 들어온다.
_GLOSSARY_SOURCE_CAP = 16000
_NUM_PREFIX = re.compile(r"^(?:\d+(?:\.\d+)*|[ivxlc]+)[.)]?\s+")  # "1 " · "i. " · "3.2 " 접두


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _is_dense_section(title: str) -> bool:
    """초록/서론처럼 전문(全文)을 넣을 정보 밀도 높은 섹션인지 (번호 접두 무시)."""
    t = _NUM_PREFIX.sub("", _norm(title))
    return t in ("abstract", "introduction", "intro")


def extract_context(wd: WorkDir, manifest: Manifest) -> tuple[str, str]:
    """(제목, 초록 텍스트) 반환 + translate/context.md 기록.

    초록이 없으면 첫 본문 섹션 요약 대신 첫 몇 문단을 컨텍스트로 쓴다(LLM 호출 없이).
    """
    lines = wd.en_md.read_text(encoding="utf-8").splitlines()
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])

    abstract = ""
    ids = section_ids_by_text(manifest, _ABSTRACT_KEYWORDS)
    region = region_for_ids(section_map, ids, len(lines))
    if region:
        start, end = region
        abstract = "\n".join(lines[start + 1 : end]).strip()
    if not abstract:
        # 폴백: 첫 헤딩 이후 앞부분 (최대 ~1000자)
        body = "\n".join(lines).strip()
        abstract = body[:1000]

    title = manifest.title
    wd.translate.mkdir(parents=True, exist_ok=True)
    wd.context_md.write_text(f"# {title}\n\n## Abstract\n\n{abstract}\n", encoding="utf-8")
    return title, abstract


def extract_glossary_source(wd: WorkDir, manifest: Manifest) -> str:
    """용어집 생성용 발췌 — 초록·서론은 전문(全文), 나머지 섹션은 제목만.

    Abstract만으로는 본문에서 처음 정의되는 용어가 빠지므로 정보 밀도 높은 초록·서론은 통째로,
    나머지 섹션은 제목으로 주제 키워드만 준다(가볍게). 본문 깊은 용어는 '선택 섹션에서 용어 추가'로.
    """
    lines = wd.en_md.read_text(encoding="utf-8").splitlines()
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    ordered = sorted(section_map, key=lambda e: e["out_line"])
    if not ordered:  # 섹션 정보 없으면 앞부분으로 폴백
        return "\n".join(lines).strip()[:_GLOSSARY_SOURCE_CAP]

    parts: list[str] = []
    for idx, sec in enumerate(ordered):
        start = sec["out_line"]
        end = ordered[idx + 1]["out_line"] if idx + 1 < len(ordered) else len(lines)
        title = re.sub(r"^#+\s*", "", lines[start]).strip() if start < len(lines) else sec.get("text", "")
        if _is_dense_section(title):
            parts.append(f"## {title}\n" + "\n".join(lines[start + 1 : end]).strip())
        else:
            parts.append(f"## {title}")  # 제목만
    return "\n\n".join(parts).strip()[:_GLOSSARY_SOURCE_CAP]
