"""PDF 읽기 — pypdfium2 접근을 한 곳으로 모으고 직렬화한다.

pdfium은 **스레드 안전하지 않다**. 웹 UI의 PDF 대조 뷰는 페이지 썸네일을 한꺼번에 요청하는데
(`/pdfpage/...` × 페이지 수), FastAPI가 이를 스레드풀에서 동시에 처리하면
`PdfiumError: Failed to load page`가 나고 서버가 그대로 멈춘다 — 15페이지 논문에서 실측했다.
그래서 문서 열기~닫기 전 구간을 전역 락으로 감싼다. 페이지 렌더는 수십 ms라 직렬화해도
체감 차이가 없고, 뷰어는 어차피 순차적으로 그린다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

# RLock: 같은 스레드에서 중첩 호출해도 데드락 나지 않도록.
_LOCK = threading.RLock()


@contextmanager
def open_document(src) -> Iterator:  # noqa: ANN001 — 경로 | bytes (pypdfium2가 둘 다 받는다)
    """pdfium 문서를 열고 닫는 컨텍스트 매니저. pypdfium2 미설치면 ImportError를 그대로 올린다."""
    import pypdfium2 as pdfium

    with _LOCK:
        doc = pdfium.PdfDocument(src)
        try:
            yield doc
        finally:
            doc.close()


def page_count(src) -> int:  # noqa: ANN001
    """페이지 수. 열기 실패(손상 PDF 등)면 0."""
    try:
        with open_document(src) as doc:
            return len(doc)
    except Exception:  # noqa: BLE001
        return 0


def render_page_png(src, page: int, zoom: float = 1.5) -> bytes | None:  # noqa: ANN001
    """페이지 한 장을 PNG 바이트로. 범위 밖이거나 실패하면 None."""
    import io

    with open_document(src) as doc:
        if not (0 <= page < len(doc)):
            return None
        buf = io.BytesIO()
        # scale=zoom → 72dpi 기준 배율 (zoom=2.0이면 144dpi)
        doc[page].render(scale=zoom).to_pil().save(buf, format="PNG")
        return buf.getvalue()


def full_text(src) -> str:  # noqa: ANN001
    """전체 페이지 텍스트를 공백으로 이어 붙인다 (깨진 글자 복구 대조용)."""
    with open_document(src) as doc:
        return " ".join(doc[p].get_textpage().get_text_range() for p in range(len(doc)))


def first_page_text(src) -> str:  # noqa: ANN001
    """1페이지 텍스트 (저널 머리말·저작권 줄 — 연도·venue의 가장 확실한 출처)."""
    with open_document(src) as doc:
        return doc[0].get_textpage().get_text_range() if len(doc) else ""


def text_page_ratio(src) -> float:  # noqa: ANN001
    """텍스트 레이어가 있는 페이지 비율 (born-digital 판별)."""
    with open_document(src) as doc:
        pages = len(doc) or 1
        return sum(1 for pg in doc if pg.get_textpage().get_text_range().strip()) / pages


def metadata(src) -> dict:  # noqa: ANN001
    """PDF 내장 메타데이터 (Title/Author/CreationDate/ModDate 등)."""
    with open_document(src) as doc:
        return doc.get_metadata_dict() or {}
