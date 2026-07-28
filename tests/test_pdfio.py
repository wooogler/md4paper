"""pdfio — pdfium 접근 직렬화 (동시 렌더 회귀 방지)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from md4paper import pdfio


@pytest.fixture
def sample_pdf(tmp_path):
    """페이지 6장짜리 최소 PDF를 만들어 경로 반환."""
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    for _ in range(6):
        doc.new_page(200, 260)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_page_count_and_render(sample_pdf):
    assert pdfio.page_count(sample_pdf) == 6
    png = pdfio.render_page_png(sample_pdf, 0, zoom=1.0)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert pdfio.render_page_png(sample_pdf, 99) is None  # 범위 밖


def test_page_count_on_broken_pdf(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    assert pdfio.page_count(broken) == 0


def test_document_access_never_overlaps(sample_pdf):
    """두 스레드가 동시에 pdfium 문서 안에 들어가면 안 된다.

    pdfium은 스레드 안전하지 않다. 락이 없으면 실제 논문 PDF에서
    `PdfiumError: Failed to load page`가 나고 웹 UI 서버가 멈춘다(15페이지 논문에서 실측).
    합성 PDF로는 경합이 재현되지 않으므로, 대신 '동시 진입 0회' 보장을 직접 검증한다.
    """
    state = {"inside": 0, "max_inside": 0}
    guard = threading.Lock()

    def visit(_i):
        with pdfio.open_document(sample_pdf) as doc:
            with guard:
                state["inside"] += 1
                state["max_inside"] = max(state["max_inside"], state["inside"])
            time.sleep(0.005)  # 겹칠 틈을 준다 — 락이 없으면 여기서 겹친다
            with guard:
                state["inside"] -= 1
            return len(doc)

    with ThreadPoolExecutor(max_workers=8) as ex:
        counts = list(ex.map(visit, range(16)))

    assert counts == [6] * 16
    assert state["max_inside"] == 1, "pdfium 문서에 동시 진입이 발생했다 — 직렬화가 깨졌다"


def test_concurrent_render_all_succeed(sample_pdf):
    """동시에 렌더를 요청해도 전부 정상 PNG를 돌려준다 (웹 UI의 PDF 썸네일 동시 로딩)."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        pngs = list(ex.map(lambda i: pdfio.render_page_png(sample_pdf, i % 6, zoom=1.0), range(24)))

    assert len(pngs) == 24
    assert all(p is not None and p[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)
