"""추출 단계 — 백엔드 디스패처.

백엔드는 **docling**(MIT, born-digital 논문 실측 최고 품질: 유니코드 무손실·읽기 순서 정확) 단일.
결과 계약: wd.raw_md + wd.extract_images + wd.meta_json.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from md4paper.extract.text_clean import (
    MARKDOWN_SUFFIXES,
    ExtractError,
    clean_extracted,
    repair_garbled_from_pdf,
    repair_mojibake_from_pdf,
    sniff_text_coverage,
)
from md4paper.extract import emphasis, inline_math, tounicode
from md4paper.workdir import WorkDir

# 추출 결과를 바꾸는 개선을 넣을 때마다 올린다 — 기존 논문을 다시 변환하면 추출부터 새로 한다.
# 2: 수식 글꼴 ToUnicode 복구 · 인라인 수식 $LaTeX$ · 깊은 여백의 러닝 푸터
# 3: 본문 굵게·기울임을 PDF 글꼴로 되살림 (**…** / *…*)
# 4: docling 2.140 + 원본에서 맞닿은 글리프 사이에 Docling이 끼운 공백 제거 ("Points ." → "Points.")
# 5: 선 없는 표에서 한 행으로 뭉친 본문 행을 PDF 기준선으로 다시 나눔
# 6: 표 셀로 들어간 투고본 여백 줄 번호 제거 · headings.json에서도 줄 번호 제거
EXTRACT_VERSION = 6

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
    with tempfile.TemporaryDirectory() as tmp:
        # 글리프 이름은 있는데 ToUnicode만 깨진 글꼴(macOS 미리보기로 다시 저장한 PDF의 합자 등)을
        # 고친 사본으로 추출한다. 고칠 게 없으면 원본 그대로.
        fixed_pdf = Path(tmp) / source.name
        glyphs = tounicode.repair(source, fixed_pdf)
        pdf = fixed_pdf if glyphs else source
        part = docling_backend.extract_to(pdf, wd, ocr=ocr)

        # 공통 후처리: 깨진 글자 복구(안전망) → 수학 유니코드 정규화 + 엔티티 복구
        raw = wd.raw_md.read_text(encoding="utf-8")
        raw, repaired = repair_garbled_from_pdf(raw, pdf)
        # 2바이트 코드가 한자처럼 묶인 깨짐(구형 CID 폰트)도 PDF 텍스트 레이어로 되살린다
        raw, moji_fixed, moji_left = repair_mojibake_from_pdf(raw, pdf)
        # 납작해진 인라인 수식("x ( k ) i ∈ [ 0 , 1 ] d k")을 PDF 글꼴·기준선으로 $LaTeX$로.
        # 정리(&lt; → <, 𝑡 → t)를 먼저 해야 PDF 글자와 맞춰 볼 수 있다.
        raw, inline = inline_math.restore(clean_extracted(raw), pdf)
        # 본문 굵게·기울임 — Docling이 버리는 글꼴 정보를 PDF에서 읽어 **…** / *…* 로.
        # 인라인 수식 뒤에 해야 $…$ 안을 건드리지 않는다.
        raw, emph = emphasis.restore(raw, pdf)
    wd.raw_md.write_text(clean_extracted(raw), encoding="utf-8")

    final = wd.raw_md.read_text(encoding="utf-8")
    meta = {
        **part,
        "text_coverage": coverage,
        # 남은 깨짐: U+FFFD + 되살리지 못한 묶임 글자 (홈 목록·CLI가 ⚠로 경고)
        "garbled_chars": final.count("�") + moji_left,
        "garbled_repaired": repaired + moji_fixed,
        "glyphs_restored": glyphs,  # ToUnicode를 글리프 이름으로 고친 코드 수
        "inline_math": inline,  # PDF 좌표로 되살린 인라인 수식 수
        "emphasis": emph,  # PDF 글꼴로 되살린 굵게·기울임 구간 수
        "source": str(source.resolve()),
        "python": sys.version.split()[0],
        "ok": True,
    }
    wd.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
