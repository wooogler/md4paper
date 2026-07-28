"""단계들을 잇는 오케스트레이션 — 각 단계는 재개 가능한 순수 함수.

status.json 해시로 변경 없는 단계는 건너뛴다. 어느 단계가 실패해도 그 전까지의 산출물은 유효하다.
"""

from __future__ import annotations

from pathlib import Path

from md4paper.assemble import render
from md4paper import extract as extract_stage
from md4paper.ir import Flavor
from md4paper.review import manifest as manifest_io
from md4paper.structure import build
from md4paper.workdir import WorkDir, hash_text


def run_extract(
    source: Path, wd: WorkDir,
    backend: str = extract_stage.DEFAULT_BACKEND, ocr: bool = False,
    force: bool = False,
) -> dict:
    src_hash = hash_text(f"{source}:{backend}:ocr={ocr}")
    if not force and wd.is_fresh("extract", src_hash) and wd.raw_md.exists():
        return {"skipped": True}
    meta = extract_stage.extract(source, wd, backend=backend, ocr=ocr)
    wd.mark_done("extract", src_hash, backend=meta.get("backend"))
    return meta


def run_frontmatter(wd: WorkDir, provider=None, force: bool = False) -> dict:
    """앞부분(front matter) 정규화 — 흩어진 저자/저작권 조각 정리 후 raw.md 갱신.

    provider가 있으면 LLM 라벨+코드 재조립(포맷에 강함), 없거나 실패하면 규칙 폴백.
    raw.md를 직접 바꾸므로 이후 structure/assemble가 raw.md 해시 변화로 자연히 재실행된다.
    한 번 정규화된 raw.md는 출력 해시로 캐시해 재호출(LLM)을 막는다.
    """
    import json

    from md4paper import config
    from md4paper.extract import front_matter

    if not wd.raw_md.exists():
        return {"skipped": True}
    raw = wd.raw_md.read_text(encoding="utf-8")
    cur_hash = hash_text(raw)
    entry = wd.load_status().get("frontmatter")
    if not force and entry and entry.get("output_hash") == cur_hash:
        return {"skipped": True}  # 이미 정규화됨

    new, authors = front_matter.normalize_authors(provider, raw, config.resolve_author_parts())
    if new != raw:
        wd.raw_md.write_text(new, encoding="utf-8")
    # 구조화 저자 저장 → 나중에 표기(author_parts) 변경 시 LLM 재호출 없이 재렌더
    if authors:
        wd.authors_json.write_text(
            json.dumps([a.model_dump() for a in authors], ensure_ascii=False, indent=2), encoding="utf-8")
    wd.mark_done("frontmatter", cur_hash, output_hash=hash_text(new), changed=new != raw)
    return {"changed": new != raw}


def run_structure(wd: WorkDir, flavor: Flavor = Flavor.STANDARD, force: bool = False, provider=None):
    raw = wd.raw_md.read_text(encoding="utf-8")
    model = getattr(provider, "model", "") if provider is not None else ""
    h = hash_text(raw + f":flavor={flavor.value}:m={model}")
    if not force and wd.is_fresh("structure", h) and wd.sections_yaml.exists():
        return manifest_io.load(wd)
    manifest = build.build(raw, wd, flavor=flavor, provider=provider)
    manifest_io.save(manifest, wd)
    wd.mark_done("structure", h, n_headings=len(manifest.sections))
    return manifest


def run_assemble(wd: WorkDir, force: bool = False) -> None:
    raw = wd.raw_md.read_text(encoding="utf-8")
    manifest = manifest_io.load(wd)  # 승인/편집된 파일에서 로드
    h = hash_text(raw + wd.sections_yaml.read_text(encoding="utf-8"))
    if not force and wd.is_fresh("assemble", h) and wd.en_md.exists():
        return
    render.assemble(raw, manifest, wd)

    # 재조립은 en.md를 새로 만들므로 인용 링크가 사라진다.
    # 이미 파싱해 둔 참고문헌이 있으면 LLM 호출 없이 즉시 다시 적용 (표기 설정 변경이 프리뷰에 바로 반영).
    from md4paper.cite.apply import apply_links, load_cached_refs

    refs = load_cached_refs(wd)
    if refs:
        apply_links(wd, refs, manifest.citation_parts, manifest.reference_links)

    wd.mark_done("assemble", h)


def run_cite(wd, provider, parts=None, reference_links=None, force: bool = False):
    """Stage 5: References 파싱 + 본문 인용 링크 + 참고문헌 재렌더. 요약 dict 반환.

    parts/reference_links가 None이면 manifest 값을 쓴다(manifest = 권위 소스). CLI가 오버라이드.
    """
    from md4paper.cite.apply import run_cite as _apply

    manifest = manifest_io.load(wd)
    eff_parts = parts or manifest.citation_parts or ["number"]
    eff_links = manifest.reference_links if reference_links is None else reference_links

    h = hash_text(f"{','.join(eff_parts)}:{eff_links}:{provider.model}")
    if not force and wd.is_fresh("cite", h) and wd.references_json.exists():
        return {"skipped": True}
    summary = _apply(wd, provider, parts=eff_parts, reference_links=eff_links)
    wd.mark_done("cite", h, parts=eff_parts)
    return summary


def run_glossary(wd, provider, regenerate: bool = True) -> int:
    """용어집만 생성/갱신 (번역 전 검토용). 항목 수 반환."""
    from md4paper.translate.apply import run_glossary as _run

    return _run(wd, provider, regenerate=regenerate)


def run_translate(wd, provider, korean_style=None, on_progress=None, force: bool = False) -> dict:
    """Stage 6: 한국어 번역 → paper.ko.md. korean_style None이면 manifest 값(권위 소스)."""
    from md4paper.translate.apply import run_translate as _apply

    manifest = manifest_io.load(wd)
    style = korean_style or manifest.korean_style
    summary = _apply(wd, provider, style, on_progress=on_progress)
    wd.mark_done("translate", hash_text(f"{style}:{provider.model}"), style=style)
    return summary


def convert(
    source: Path,
    wd: WorkDir,
    backend: str = extract_stage.DEFAULT_BACKEND,
    ocr: bool = False,
    flavor: Flavor = Flavor.STANDARD,
    provider=None,
) -> WorkDir:
    """Stage 1-4 (리뷰 제외): extract → (front matter 정규화) → structure → assemble → paper.en.md.

    provider가 있으면 앞부분 정규화에 LLM(라벨+재조립)을 쓴다. 없으면 규칙 폴백.
    """
    run_extract(source, wd, backend=backend, ocr=ocr)
    run_frontmatter(wd, provider=provider)
    run_structure(wd, flavor=flavor, provider=provider)
    run_assemble(wd)
    return wd
