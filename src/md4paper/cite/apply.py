"""cite 오케스트레이션 — References 파싱 + 본문 인용 링크 + 참고문헌 재렌더.

paper.en.md를 읽어 References 섹션을 찾고, 구조화 파싱한 뒤, 본문의 [n] 마커를 선택된 스타일로
링크하고, 참고문헌 목록을 DOI/arXiv 링크 붙은 형태로 교체해 다시 en.md에 쓴다.
어떤 단계든 실패하면 문서를 그대로 두는 graceful degradation.
"""

from __future__ import annotations

import json

from md4paper.cite import link, parse, render
from md4paper.llm.base import Provider
from md4paper.regions import region_for_ids, section_ids_by_text
from md4paper.review import manifest as manifest_io
from md4paper.workdir import WorkDir

_REF_KEYWORDS = {"references", "reference", "bibliography"}


class CiteError(RuntimeError):
    pass


def find_references_region(manifest, section_map: list[dict], n_lines: int) -> tuple[int, int] | None:
    """참고문헌 섹션의 (헤딩 out_line, 본문 끝 out_line) 반환. 없으면 None."""
    ids = section_ids_by_text(manifest, _REF_KEYWORDS)
    return region_for_ids(section_map, ids, n_lines)


def load_cached_refs(wd: WorkDir) -> list:
    """이전에 파싱해 둔 references.json의 accepted 항목 (LLM 호출 없음)."""
    if not wd.references_json.exists():
        return []
    from md4paper.ir import RefEntry

    data = json.loads(wd.references_json.read_text(encoding="utf-8"))
    return [RefEntry(**d) for d in data.get("accepted", [])]


def ref_urls(wd: WorkDir) -> dict[str, str]:
    """참고문헌 번호 → DOI/arXiv URL (내부 앵커를 못 쓰는 Notion 등에서 외부 링크로). 없으면 빈 dict."""
    return {r.label: r.url() for r in load_cached_refs(wd) if r.url()}


def apply_links(wd: WorkDir, refs: list, parts: list[str], reference_links: bool = True) -> dict:
    """이미 파싱된 참고문헌으로 본문 인용 링크 + 참고문헌 목록을 다시 적용 (결정론적·무료).

    표기 설정(citation_parts 등)만 바꿨을 때 재파싱 없이 즉시 반영하는 경로.
    """
    if not refs or not wd.en_md.exists() or not wd.sections_map.exists():
        return {"linked": 0, "skipped": True}
    manifest = manifest_io.load(wd)
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    lines = wd.en_md.read_text(encoding="utf-8").splitlines()
    region = find_references_region(manifest, section_map, len(lines))
    if region is None:
        return {"linked": 0, "skipped": True}
    start, end = region

    rewritten, stats = link.rewrite_lines(lines, refs, parts, skip_range=range(start + 1, end))
    rendered = render.render_reference_list(refs, reference_links)
    new_lines = rewritten[: start + 1] + [""] + rendered + [""] + rewritten[end:]
    wd.en_md.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    wd.cite.mkdir(parents=True, exist_ok=True)
    wd.links_json.write_text(
        json.dumps(
            {"parts": parts, "rewritten": stats.rewritten,
             "skipped_out_of_range": stats.skipped_out_of_range, "matches": stats.matches},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return {"linked": stats.rewritten, "skipped_range": stats.skipped_out_of_range}


def run_cite(
    wd: WorkDir, provider: Provider, parts: list[str] | None = None, reference_links: bool = True
) -> dict:
    """cite 적용. parts=본문 인용 표시 요소 조합. 반환: 요약 dict."""
    parts = parts or ["number"]
    if not wd.en_md.exists():
        raise CiteError(f"paper.en.md 없음: {wd.en_md}. 먼저 convert를 실행하세요.")

    manifest = manifest_io.load(wd)
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    lines = wd.en_md.read_text(encoding="utf-8").splitlines()

    region = find_references_region(manifest, section_map, len(lines))
    if region is None:
        raise CiteError("References 섹션을 찾지 못했습니다. 인용 처리를 건너뜁니다.")
    start, end = region
    ref_body = "\n".join(lines[start + 1 : end]).strip()
    if not ref_body:
        raise CiteError("References 본문이 비어 있습니다.")

    accepted, rejected = parse.parse_references(ref_body, provider)
    if not accepted:
        raise CiteError("유효한 참고문헌 항목을 추출하지 못했습니다 (전부 검증 실패).")

    wd.cite.mkdir(parents=True, exist_ok=True)
    wd.references_json.write_text(
        json.dumps(
            {"accepted": [r.model_dump() for r in accepted], "rejected": [r.model_dump() for r in rejected]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 본문 인용 치환 (참고문헌 영역 제외) 후 참고문헌 목록 교체
    rewritten, stats = link.rewrite_lines(lines, accepted, parts, skip_range=range(start + 1, end))
    rendered = render.render_reference_list(accepted, reference_links)
    new_lines = rewritten[: start + 1] + [""] + rendered + [""] + rewritten[end:]
    wd.en_md.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    wd.links_json.write_text(
        json.dumps(
            {"parts": parts, "rewritten": stats.rewritten, "skipped_out_of_range": stats.skipped_out_of_range, "matches": stats.matches},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "parsed": len(accepted),
        "rejected": len(rejected),
        "linked": stats.rewritten,
        "skipped_range": stats.skipped_out_of_range,
        "cost_usd": provider.cost(),
    }
