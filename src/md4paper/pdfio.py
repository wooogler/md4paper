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


def render_region_png(  # noqa: ANN001
    src, page: int, rect: tuple[float, float, float, float], zoom: float = 3.0,
) -> bytes | None:
    """페이지의 한 영역만 PNG로 잘라낸다 (좌상단 원점 pt). 범위 밖이면 None.

    pdfium의 `crop`은 좌표가 아니라 **각 변에서 잘라낼 여백**(left, bottom, right, top)이다.
    수식은 원문에서도 작아서 zoom을 페이지 렌더(1.5)보다 높게 잡는다 — 첨자·위첨자가
    LLM 입력에서 뭉개지면 그게 곧 오독이 된다.
    """
    import io

    left, top, right, bottom = rect
    with open_document(src) as doc:
        if not (0 <= page < len(doc)):
            return None
        pg = doc[page]
        width, height = pg.get_size()
        crop = (max(0.0, left), max(0.0, height - bottom), max(0.0, width - right), max(0.0, top))
        if width - crop[0] - crop[2] <= 1 or height - crop[1] - crop[3] <= 1:
            return None  # 빈 영역 — pdfium이 여기서 에러를 낸다
        buf = io.BytesIO()
        pg.render(scale=zoom, crop=crop).to_pil().save(buf, format="PNG")
        return buf.getvalue()


# --- 위첨자 각주 마커 — 글자 크기·기준선으로 PDF에서 직접 읽는다 ------------------------
# Docling은 위첨자를 평문으로 눕혀 버려서 "Cursor 2"의 2가 각주인지 그냥 숫자인지 텍스트만으론
# 알 수 없다. 원본 PDF엔 증거가 남아 있다: 각주 마커는 본문보다 작은 글자로(보통 0.6~0.8배),
# 앞 글자에 붙어(공백 없이), 기준선이 몇 pt 올라가 있다. 그 셋을 함께 만족하는 숫자만 마커다.
_SUP_MIN_RATIO = 0.5  # 이보다 작으면 숨은 텍스트·DOI 워터마크 같은 잡음(이 논문엔 1pt 글자가 있었다)
_SUP_MAX_RATIO = 0.85
_SUP_MIN_RAISE = 1.0  # pt — 아래첨자(음수)·같은 줄 작은 글자(0)와 구분
_SUP_MAX_RAISE_RATIO = 0.8  # 본문 크기의 이 배수보다 더 올라갔으면 다른 줄(각주 정의·러닝 헤드)


def superscript_marks(src) -> list[dict]:  # noqa: ANN001
    """본문 속 위첨자 숫자 마커를 [{num, before, page}] 로 돌려준다 (문서 순서).

    `before`는 마커 바로 앞 같은 줄의 텍스트 끝 ~40자 — 본문 마크다운에서 그 자리를 찾는 열쇠다.
    pypdfium2가 없거나 텍스트 레이어가 없으면 빈 목록(호출자는 텍스트 휴리스틱으로 물러난다).
    """
    import statistics

    try:
        import pypdfium2.raw as C
    except ImportError:
        return []
    marks: list[dict] = []
    with open_document(src) as doc:
        for pi in range(len(doc)):
            tp = doc[pi].get_textpage()
            n = tp.count_chars()
            chars: list[tuple[str, float, float]] = []  # (글자, 크기, 아래쪽 y)
            for i in range(n):
                u = C.FPDFText_GetUnicode(tp, i)
                ch = chr(u) if u else ""
                size = float(C.FPDFText_GetFontSize(tp, i))
                bottom = tp.get_charbox(i, loose=False)[1] if ch.strip() else 0.0
                chars.append((ch, size, bottom))
            sizes = [s for c, s, _ in chars if c.strip() and s > 0]
            if len(sizes) < 40:
                continue
            body = statistics.median(sizes)
            i = 0
            while i < n:
                ch, size, bottom = chars[i]
                if not (ch.isdigit() and _SUP_MIN_RATIO * body <= size <= _SUP_MAX_RATIO * body and i > 0):
                    i += 1
                    continue
                j = i
                while j < n and chars[j][0].isdigit() and abs(chars[j][1] - size) < 0.01:
                    j += 1
                pch, psize, pbottom = chars[i - 1]
                raise_pt = bottom - pbottom
                ok = (
                    pch.strip() and not pch.isdigit() and pch not in ".,-–—/"  # 한 수의 일부가 아니다
                    and psize >= 0.9 * body  # 본문 글자에 붙어 있다
                    and _SUP_MIN_RAISE <= raise_pt <= _SUP_MAX_RAISE_RATIO * body
                    and j - i <= 3
                )
                if ok:
                    before = "".join(c for c, _, _ in chars[max(0, i - 40):i])
                    before = before.replace("\r", " ").replace("\n", " ").split("\n")[-1]
                    marks.append({"num": int("".join(c for c, _, _ in chars[i:j])),
                                  "before": before, "page": pi + 1})
                i = j
    return marks
