"""추출 단계 — 백엔드 디스패처.

백엔드는 **docling**(MIT, born-digital 논문 실측 최고 품질: 유니코드 무손실·읽기 순서 정확) 단일.
결과 계약: wd.raw_md + wd.extract_images + wd.meta_json.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from md4paper.extract.text_clean import (
    MARKDOWN_SUFFIXES,
    ExtractError,
    clean_extracted,
    repair_garbled_from_pdf,
    repair_mojibake_from_pdf,
    sniff_text_coverage,
)
from md4paper.workdir import WorkDir

BACKENDS = ("docling",)
DEFAULT_BACKEND = "docling"

__all__ = ["BACKENDS", "DEFAULT_BACKEND", "ExtractError", "available_backends", "extract"]


def available_backends() -> list[str]:
    """실제로 설치되어 쓸 수 있는 백엔드 목록."""
    from md4paper.extract import docling_backend

    return ["docling"] if docling_backend.available() else []


def _pre_extracted(source: Path, wd: WorkDir) -> dict:
    """이미 추출된 마크다운을 입력으로 준 경우 — 백엔드 없이 통과 (테스트/재사용 경로)."""
    wd.raw_md.write_text(clean_extracted(source.read_text(encoding="utf-8")), encoding="utf-8")
    sibling = source.parent / "images"
    if sibling.is_dir():
        wd.extract_images.mkdir(parents=True, exist_ok=True)
        for img in sibling.iterdir():
            if img.suffix.lower() in {".jpeg", ".jpg", ".png", ".webp"}:
                shutil.copy2(img, wd.extract_images / img.name)
    return {"backend": "pre-extracted", "source": str(source), "ok": True}


def extract(
    source: Path,
    wd: WorkDir,
    backend: str = DEFAULT_BACKEND,
    ocr: bool = False,
) -> dict:
    """추출 실행. source가 이미 마크다운이면 백엔드를 건너뛴다.

    반환: meta dict (meta.json에도 기록).
    """
    wd.ensure()
    source = Path(source)

    if source.suffix.lower() in MARKDOWN_SUFFIXES:
        meta = _pre_extracted(source, wd)
        wd.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    if backend not in BACKENDS:
        raise ExtractError(f"알 수 없는 백엔드: {backend} (사용 가능: {', '.join(BACKENDS)})")

    from md4paper.extract import docling_backend

    coverage = sniff_text_coverage(source)
    part = docling_backend.extract_to(source, wd, ocr=ocr)

    # 공통 후처리: 깨진 글자 복구(안전망) → 수학 유니코드 정규화 + 엔티티 복구
    raw = wd.raw_md.read_text(encoding="utf-8")
    raw, repaired = repair_garbled_from_pdf(raw, source)
    # 2바이트 코드가 한자처럼 묶인 깨짐(구형 CID 폰트)도 PDF 텍스트 레이어로 되살린다
    raw, moji_fixed, moji_left = repair_mojibake_from_pdf(raw, source)
    wd.raw_md.write_text(clean_extracted(raw), encoding="utf-8")

    final = wd.raw_md.read_text(encoding="utf-8")
    meta = {
        **part,
        "text_coverage": coverage,
        # 남은 깨짐: U+FFFD + 되살리지 못한 묶임 글자 (홈 목록·CLI가 ⚠로 경고)
        "garbled_chars": final.count("�") + moji_left,
        "garbled_repaired": repaired + moji_fixed,
        "source": str(source.resolve()),
        "python": sys.version.split()[0],
        "ok": True,
    }
    wd.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
