"""ToUnicode 복구 — 글리프 이름은 멀쩡한데 매핑만 U+FFFF로 깨진 글꼴 (macOS 미리보기로 다시 저장한 PDF)."""

import pytest

from narani import pdfio
from narani.extract import tounicode
from narani.extract.tounicode import emit_cmap, glyph_text, parse_cmap

# 실제 사례(CHI '27 투고본)의 ToUnicode 그대로: 합자 네 개가 <ffff>, 마지막만 정상
BROKEN_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<00><FF>
endcodespacerange
5 beginbfrange
<21><21><ffff>
<22><22><ffff>
<23><23><ffff>
<24><24><ffff>
<25><25><011b>
endbfrange
endcmap
"""


def test_glyph_names_decode_to_text():
    assert glyph_text("/f_f_i") == "ffi"
    assert glyph_text("f_t") == "ft"
    assert glyph_text("fi") == "fi"  # AGL의 표현형(ﬁ)이 아니라 풀어 쓴 글자
    assert glyph_text("o.sc") == "o"  # 스몰캡
    assert glyph_text("hyphen.sc") == "-"
    assert glyph_text("uni00E9") == "é"
    assert glyph_text("u1D461") == "\U0001d461"
    assert glyph_text("glyph1234") is None  # 모르는 이름은 건드리지 않는다
    assert glyph_text(".notdef") is None


def test_cmap_parse_and_emit_roundtrip():
    mapping, space, nbytes = parse_cmap(BROKEN_CMAP)
    assert mapping == {0x21: "￿", 0x22: "￿", 0x23: "￿", 0x24: "￿", 0x25: "ě"}
    assert nbytes == 1
    mapping[0x22] = "fi"
    again, _, _ = parse_cmap(emit_cmap(mapping, space, nbytes))
    assert again == mapping


def test_cmap_ranges_increment_and_arrays():
    data = b"""1 begincodespacerange <0000><FFFF> endcodespacerange
2 beginbfrange
<0041><0043><0061>
<0050><0051>[<0066006C> <0078>]
endbfrange
1 beginbfchar
<0060> <FFFF>
endbfchar"""
    mapping, _, nbytes = parse_cmap(data)
    assert nbytes == 2
    assert mapping == {0x41: "a", 0x42: "b", 0x43: "c", 0x50: "fl", 0x51: "x", 0x60: "￿"}


def _broken_pdf(path):
    """Quartz가 만드는 모양 그대로의 글꼴: /Differences에 합자 이름, ToUnicode는 <ffff>."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                               NumberObject)

    w = PdfWriter()
    page = w.add_blank_page(400, 200)
    cmap = DecodedStreamObject()
    cmap.set_data(BROKEN_CMAP)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): DictionaryObject({
            NameObject("/Type"): NameObject("/Encoding"),
            NameObject("/Differences"): ArrayObject(
                [NumberObject(33), NameObject("/f_f"), NameObject("/f_i"), NameObject("/f_f_i"),
                 NameObject("/f_l"), NameObject("/ecaron")]),
        }),
        NameObject("/ToUnicode"): w._add_object(cmap),
    })
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 14 Tf 20 100 Td (we de"ne di!erent work$ows) Tj ET')
    page.replace_contents(content)
    with open(path, "wb") as fh:
        w.write(fh)


def test_repair_restores_ligatures_for_every_extractor(tmp_path):
    src, dst = tmp_path / "broken.pdf", tmp_path / "fixed.pdf"
    _broken_pdf(src)
    assert "define" not in pdfio.full_text(src)  # 원본은 pdfium도 못 읽는다
    assert tounicode.repair(src, dst) == 4  # 깨진 네 코드 (정상 ě는 그대로)
    assert "we define different workflows" in pdfio.full_text(dst)
    assert pdfio.page_count(dst) == 1


def test_clean_pdf_is_left_alone(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    src = tmp_path / "clean.pdf"
    doc.save(str(src))
    doc.close()
    assert tounicode.repair(src, tmp_path / "out.pdf") == 0
    assert not (tmp_path / "out.pdf").exists()  # 고칠 게 없으면 사본도 안 만든다


def test_unreadable_pdf_is_not_fatal(tmp_path):
    src = tmp_path / "junk.pdf"
    src.write_bytes(b"not a pdf")
    assert tounicode.repair(src, tmp_path / "out.pdf") == 0


# 실제 사례(미리보기로 다시 저장한 ACM 원고)의 수식 글꼴: ToUnicode가 U+FFFF가 아니라 엉뚱한 글자다
# — 𝑡→'C', ℓ→'✓', 𝐻→U+0000. 이름은 멀쩡하다.
GARBLED_MATH_CMAP = b"""1 begincodespacerange <00><FF> endcodespacerange
4 beginbfrange
<21><21><0043>
<22><22><0000>
<23><23><2713>
<24><24><0039>
endbfrange
"""


def _font(cmap: bytes, names: list[str]):
    pytest.importorskip("pypdf")
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                               NumberObject)

    stream = DecodedStreamObject()
    stream.set_data(cmap)
    return DictionaryObject({
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/Encoding"): DictionaryObject({NameObject("/Differences"): ArrayObject(
            [NumberObject(33)] + [NameObject(n) for n in names])}),
        NameObject("/ToUnicode"): stream,
    })


def test_garbled_math_font_trusts_glyph_names():
    font = _font(GARBLED_MATH_CMAP, ["/u1D461", "/u1D43B", "/uni2113", "/u1D457"])
    assert tounicode.fix_font(font) == 4
    mapping, _, _ = parse_cmap(font["/ToUnicode"].get_data())
    assert mapping == {0x21: "\U0001d461", 0x22: "\U0001d43b", 0x23: "ℓ", 0x24: "\U0001d457"}


def test_one_odd_entry_in_a_sane_font_is_kept():
    # 과반이 이름과 맞는 글꼴은 믿는다 — 하나 어긋난 항목은 글꼴 제작자의 의도일 수 있다
    cmap = b"""1 begincodespacerange <00><FF> endcodespacerange
3 beginbfchar
<21> <0061>
<22> <0062>
<23> <007E>
endbfchar"""
    font = _font(cmap, ["/a", "/b", "/similar"])
    assert tounicode.fix_font(font) == 0
