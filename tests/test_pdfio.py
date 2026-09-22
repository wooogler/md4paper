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


def _hand_pdf(content: bytes) -> bytes:
    """Helvetica 하나로 된 한 쪽짜리 PDF를 손으로 조립한다 (xref 오프셋 정확)."""
    import io

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>stream\n" % len(content) + content + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_superscript_marks_reads_raised_small_digits_only(tmp_path):
    """위첨자 각주 마커(작고 올라간 숫자)만 잡는다 — 본문 크기의 '7', 아래첨자 H₂, 큰 수 11,579, 각주 정의 줄은 제외.

    Docling은 위첨자를 평문으로 눕혀 "Cursor 2"와 "7 main categories"를 구분 못 한다. 원본 PDF의
    글자 크기·기준선이 그 구분의 유일한 증거다.
    """
    pytest.importorskip("pypdfium2")
    content = (
        b"BT /F1 9 Tf 20 250 Td (IDE assistants such as GitHub Copilot and Cursor) Tj "
        b"/F1 6.6 Tf 3 Ts (2) Tj 0 Ts /F1 9 Tf ( have evolved beyond autocomplete.) Tj ET\n"
        b"BT /F1 9 Tf 20 230 Td (We found 7 main categories across 11,579 sessions and H) Tj "
        b"/F1 6.6 Tf -2 Ts (2) Tj 0 Ts /F1 9 Tf (O molecules.) Tj ET\n"
        b"BT /F1 5.5 Tf 20 20 Td (2 https://cursor.com/) Tj ET"
    )
    path = tmp_path / "sup.pdf"
    path.write_bytes(_hand_pdf(content))

    marks = pdfio.superscript_marks(path)

    assert [m["num"] for m in marks] == [2]
    assert marks[0]["before"].endswith("Cursor")
    assert marks[0]["page"] == 1


def test_superscript_marks_empty_on_pages_without_text(sample_pdf):
    assert pdfio.superscript_marks(sample_pdf) == []
