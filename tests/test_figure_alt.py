"""그림 설명 — 태그 PDF의 대체 텍스트를 읽고, 정리하고, 본문 그림에 짝짓는다."""

import json

import pytest

from md4paper import figure_alt
from md4paper.figure_alt import Alt, Fig, clean, pair
from md4paper.workdir import WorkDir

pdfium = pytest.importorskip("pypdfium2")

DESC = "A bar chart comparing three conditions; the rightmost bar is the tallest."


def tagged_pdf(figs: list[tuple]) -> bytes:
    """한 쪽(300×400)짜리 태그 PDF. figs = [(alt | None, (x, y, w, h) PDF 좌표, 캡션 | None)].

    Figure 요소마다 MCID 하나(채운 사각형), 캡션이 있으면 바로 뒤 형제 Caption 요소(글자).
    """
    content, elems, mcid = [], [], 0
    for alt, (x, y, w, h), cap in figs:
        content.append(f"/Figure <</MCID {mcid}>> BDC 0 0 1 rg {x} {y} {w} {h} re f EMC")
        fm, cm = mcid, None
        mcid += 1
        if cap:
            content.append(f"/P <</MCID {mcid}>> BDC BT /F1 9 Tf {x} {y - 12} Td ({cap}) Tj ET EMC")
            cm = mcid
            mcid += 1
        elems.append((alt, fm, cm))
    stream = "\n".join(content).encode()
    root_id = 6 + 2 * len(figs)
    sect_id, pt_id = root_id + 1, root_id + 2
    objs, kids, refs, nid = {}, [], [], 6
    for alt, fm, cm in elems:
        alt_s = f"/Alt ({alt})" if alt is not None else ""
        objs[nid] = f"<< /Type /StructElem /S /Figure /P {sect_id} 0 R /Pg 3 0 R /K {fm} {alt_s} >>"
        kids.append(nid)
        refs.append((fm, nid))
        if cm is not None:
            objs[nid + 1] = f"<< /Type /StructElem /S /Caption /P {sect_id} 0 R /Pg 3 0 R /K {cm} >>"
            kids.append(nid + 1)
            refs.append((cm, nid + 1))
        else:
            objs[nid + 1] = "<< >>"
        nid += 2
    objs[root_id] = f"<< /Type /StructTreeRoot /K {sect_id} 0 R /ParentTree {pt_id} 0 R >>"
    objs[sect_id] = f"<< /Type /StructElem /S /Sect /P {root_id} 0 R /K [{' '.join(f'{k} 0 R' for k in kids)}] >>"
    objs[pt_id] = f"<< /Nums [0 [{' '.join(f'{r} 0 R' for _, r in sorted(refs))}]] >>"
    objs[1] = f"<< /Type /Catalog /Pages 2 0 R /MarkInfo << /Marked true >> /StructTreeRoot {root_id} 0 R >>"
    objs[2] = "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> >> /StructParents 0 >>")
    objs[5] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    buf, offs = bytearray(b"%PDF-1.7\n"), {}
    for i in sorted([*objs, 4]):
        offs[i] = len(buf)
        if i == 4:
            buf += f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream\nendobj\n"
        else:
            buf += f"{i} 0 obj\n{objs[i]}\nendobj\n".encode()
    size, xref = max(offs) + 1, len(buf)
    buf += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    buf += "".join(f"{offs[i]:010d} 00000 n \n" for i in range(1, size)).encode()
    buf += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf)


# --- 정리 ---------------------------------------------------------------


def test_clean_strips_label_and_keeps_number():
    assert clean(f"Figure 3: {DESC}") == (DESC, "3")
    assert clean(f"Figure 7-a: {DESC}") == (DESC, "7")
    assert clean(f"Figure C.3: {DESC}") == (DESC, "C.3")
    assert clean(f"Figure 1\nFigure 1 shows {DESC}") == (f"Figure 1 shows {DESC}", "1")


def test_clean_keeps_sentence_that_starts_with_label():
    text, num = clean(f"Figure 2 illustrates the concept. {DESC}")
    assert text.startswith("Figure 2 illustrates") and num == "2"


def test_clean_unquotes_and_drops_latex_residue():
    assert clean(f'Figure 1: "{DESC}"') == (DESC, "1")
    assert clean(r"The overview of \sys{} interface with two panels side by side.")[0] == \
        "The overview of interface with two panels side by side."


@pytest.mark.parametrize("junk", ["", "image 1", "Figure 3", "system-overview-01.png", "CC-BY logo",
                                  "Orcid Logo", "CHI 2020 Paper", "Paper 656", "email logo",
                                  "Your use of the JSTOR archive indicates your acceptance"])
def test_clean_drops_non_descriptions(junk):
    assert clean(junk)[0] is None


# --- 짝짓기 -------------------------------------------------------------


def _alt(text, page=1, bbox=None, num=None):
    return Alt(page=page, bbox=bbox, text=text, num=num)


def test_pair_by_place_beats_caption_number():
    # docling이 캡션을 잘못 붙인 그림(Figure 14가 table-5.png) — 번호보다 자리가 맞다
    alts = [_alt("desc 14", bbox=(10, 10, 100, 100), num="14")]
    figs = [Fig("figure-14.png", "14", 2, (10, 10, 100, 100)), Fig("table-5.png", None, 1, (12, 12, 98, 98))]
    got = pair(alts, figs)
    assert list(got) == ["table-5.png"] and got["table-5.png"][1] == "bbox"


def test_pair_split_figure_gets_description_on_every_piece():
    alts = [_alt("whole", bbox=(0, 0, 200, 100))]
    figs = [Fig("image-1.png", None, 1, (0, 0, 95, 100)), Fig("image-2.png", None, 1, (105, 0, 200, 100))]
    assert set(pair(alts, figs)) == {"image-1.png", "image-2.png"}


def test_pair_subfigures_join_in_reading_order():
    alts = [_alt("bottom part", bbox=(0, 60, 100, 100)), _alt("top part", bbox=(0, 0, 100, 50))]
    got = pair(alts, [Fig("figure-2.png", "2", 1, (0, 0, 100, 100))])
    assert figure_alt.join(got["figure-2.png"][0]) == "top part\n\nbottom part"


def test_pair_overlapping_hits_prefer_same_number():
    # 요소 bbox가 두 그림에 걸칠 때 캡션 번호가 같은 쪽으로
    alts = [_alt("three", bbox=(0, 0, 100, 200), num="3")]
    figs = [Fig("figure-2.png", "2", 1, (0, 0, 100, 100)), Fig("figure-3.png", "3", 1, (0, 100, 100, 200))]
    assert list(pair(alts, figs)) == ["figure-3.png"]


def test_pair_falls_back_to_caption_number_without_places():
    alts = [_alt("one", num="1"), _alt("nine", num="9"), _alt("nothing")]
    got = pair(alts, [Fig("figure-1.png", "1"), Fig("figure-2.png", "2")])
    assert {k: v[1] for k, v in got.items()} == {"figure-1.png": "caption"}


def test_pair_number_does_not_pile_onto_placed_figure():
    alts = [_alt("by place", bbox=(0, 0, 50, 50), num="1"), _alt("by number", page=3, num="1")]
    got = pair(alts, [Fig("figure-1.png", "1", 1, (0, 0, 50, 50))])
    assert [a.text for a in got["figure-1.png"][0]] == ["by place"]


# --- 읽기 (pdfium) --------------------------------------------------------


def test_read_alts_from_tagged_pdf(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(tagged_pdf([(DESC, (50, 250, 200, 100), "Figure 2: Things."),
                                ("CC-BY logo", (10, 10, 20, 10), None)]))
    got = figure_alt.read_alts(pdf)
    assert len(got) == 1  # 로고는 설명이 아니다
    a = got[0]
    assert (a.page, a.text, a.num) == (1, DESC, "2")  # 번호는 형제 Caption 글자에서
    assert a.bbox == pytest.approx((50, 50, 250, 150))  # 좌상단 원점


def test_read_alts_untagged_pdf_is_empty(tmp_path):
    doc = pdfium.PdfDocument.new()
    doc.new_page(300, 400)
    pdf = tmp_path / "u.pdf"
    doc.save(str(pdf))
    doc.close()
    assert figure_alt.read_alts(pdf) == []


# --- 기록 ---------------------------------------------------------------


@pytest.fixture
def wd(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(tagged_pdf([(f"Figure 1: {DESC}", (50, 250, 200, 100), None),
                                ("A flow diagram with four boxes connected left to right.", (50, 50, 200, 100),
                                 "Figure 2: Flow.")]))
    w = WorkDir(tmp_path / "p.md4")
    w.extract_images.mkdir(parents=True)
    w.out_images.mkdir(parents=True)
    w.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    for i, data in ((1, b"\x89PNG one"), (2, b"\x89PNG two")):
        (w.extract_images / f"img-0{i}.png").write_bytes(data)
        (w.out_images / f"figure-{i}.png").write_bytes(data)
    (w.out_images / "formula-01.png").write_bytes(b"\x89PNG formula")
    return w


def test_build_pairs_by_number_and_place(wd):
    # figure-2는 자리 기록이 있다(쪽 아래쪽 = 두 번째 요소), figure-1은 번호로
    wd.figures_json.write_text(json.dumps({"img-02.png": {"page": 1, "bbox": [50, 250, 250, 350]}}))
    data = figure_alt.build(wd)
    assert data["figures"]["figure-1.png"] == {"alt": DESC, "via": "caption"}
    assert data["figures"]["figure-2.png"]["via"] == "bbox"
    assert data["figures"]["figure-2.png"]["alt"].startswith("A flow diagram")
    assert data["unpaired"] == 0
    assert figure_alt.alts(wd)["figure-1.png"] == DESC


def test_ensure_reuses_record_until_inputs_change(wd, monkeypatch):
    first = figure_alt.ensure(wd)
    calls = []
    monkeypatch.setattr(figure_alt, "read_alts", lambda pdf: calls.append(pdf) or [])
    assert figure_alt.ensure(wd) == first and not calls  # 그대로
    (wd.out_images / "figure-1.png").write_bytes(b"\x89PNG recropped")  # 영역 다시 지정 → 바이트가 바뀜
    assert figure_alt.ensure(wd)["figures"] == {} and calls


def test_needs_locate_only_once_with_leftovers():
    assert figure_alt.needs_locate({"unpaired": 2, "located": False})
    assert not figure_alt.needs_locate({"unpaired": 2, "located": True})
    assert not figure_alt.needs_locate({"unpaired": 0, "located": False})
    assert not figure_alt.needs_locate({})


# --- 쓰는 곳 ------------------------------------------------------------


def test_agent_rows_carry_figure_descriptions():
    from md4paper.ui.agent import retrieval

    chunks = retrieval.build_chunks("# A\n\n![Figure 1](images/figure-1.png)\n\nBody text.\n")
    retrieval.attach_figures(chunks, {"figure-1.png": "Two bar charts\nside by side."})
    row = next(c for c in chunks if c.figures)
    assert row.figures == ["Figure 1: Two bar charts side by side."]
    assert "Two bar charts" in retrieval.doc_text(row)


def test_fig_alts_js_escapes_script_end():
    from md4paper.ui.app import fig_alts_js

    js = fig_alts_js({"figure-1.png": "a </script> b"})
    assert "</script>" not in js and js.startswith("window.__mdFigAlts = ")
