"""그림 영역 다시 지정 — 원천·사본을 함께 바꾸고, 되돌리고, 지금 자리를 찾는다."""

import json

import pytest

from narani import figure_fix
from narani.workdir import WorkDir

pdfium = pytest.importorskip("pypdfium2")


@pytest.fixture
def wd(tmp_path, monkeypatch):
    doc = pdfium.PdfDocument.new()
    doc.new_page(300, 400)
    doc.new_page(300, 400)
    pdf = tmp_path / "s.pdf"
    doc.save(str(pdf))
    doc.close()
    w = WorkDir(tmp_path / "p.md4")
    w.extract_images.mkdir(parents=True)
    w.out_images.mkdir(parents=True)
    w.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    old = b"\x89PNG old figure"
    (w.extract_images / "img-01.png").write_bytes(old)
    (w.extract_images / "img-02.png").write_bytes(b"\x89PNG other")
    (w.extract_images / "formula-01.png").write_bytes(b"\x89PNG formula")
    (w.out_images / "figure-3.png").write_bytes(old)  # 조립이 이름만 바꿔 복사한 것
    (w.out_images / "figure-4.png").write_bytes(b"\x89PNG other")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "figure-3.png").write_bytes(old)  # 저장 위치 사본
    monkeypatch.setattr(figure_fix, "_copies", lambda wd_: [wd_.out_images, lib])
    w.lib = lib
    return w


def test_extract_name_by_content(wd):
    assert figure_fix.extract_name(wd, "figure-3.png") == "img-01.png"
    assert figure_fix.extract_name(wd, "figure-4.png") == "img-02.png"
    assert figure_fix.extract_name(wd, "formula-01.png") is None  # 수식 크롭은 그림이 아니다
    assert figure_fix.extract_name(wd, "../x.png") is None


def test_replace_swaps_all_copies_and_backs_up(wd):
    figure_fix._save(wd, {"img-01.png": {"page": 1, "bbox": [10, 10, 50, 50]}})
    res = figure_fix.replace(wd, "img-01.png", 2, [200, 30, 20, 130])
    new = (wd.extract_images / "img-01.png").read_bytes()
    assert new.startswith(b"\x89PNG\r\n") and res["copies"] == 2
    assert (wd.out_images / "figure-3.png").read_bytes() == new
    assert (wd.lib / "figure-3.png").read_bytes() == new
    assert (wd.out_images / "figure-4.png").read_bytes() == b"\x89PNG other"  # 남의 그림은 그대로
    assert (wd.figure_backup / "img-01.png").read_bytes() == b"\x89PNG old figure"
    rec = figure_fix.load(wd)["img-01.png"]
    assert rec["page"] == 2 and rec["bbox"] == [20.0, 30.0, 200.0, 130.0] and rec["manual"]
    assert rec["auto"] == {"page": 1, "bbox": [10, 10, 50, 50]}  # 변환 때 자리는 남긴다
    assert figure_fix.extract_name(wd, "figure-3.png") == "img-01.png"  # 바꾼 뒤에도 이어진다
    assert figure_fix.where(wd, "img-01.png") == {"page": 2, "bbox": [20.0, 30.0, 200.0, 130.0]}

    # 두 번째로 바꿔도 백업은 처음 그림 그대로
    figure_fix.replace(wd, "img-01.png", 1, [0, 0, 100, 100])
    assert (wd.figure_backup / "img-01.png").read_bytes() == b"\x89PNG old figure"
    assert figure_fix.load(wd)["img-01.png"]["auto"] == {"page": 1, "bbox": [10, 10, 50, 50]}


def test_restore_puts_original_back(wd):
    figure_fix._save(wd, {"img-01.png": {"page": 1, "bbox": [10, 10, 50, 50]}})
    figure_fix.replace(wd, "img-01.png", 2, [20, 30, 200, 130])
    assert figure_fix.has_backup(wd, "img-01.png")
    figure_fix.restore(wd, "img-01.png")
    for p in (wd.extract_images / "img-01.png", wd.out_images / "figure-3.png", wd.lib / "figure-3.png"):
        assert p.read_bytes() == b"\x89PNG old figure"
    assert not figure_fix.has_backup(wd, "img-01.png")
    assert figure_fix.load(wd)["img-01.png"] == {"page": 1, "bbox": [10, 10, 50, 50]}
    with pytest.raises(ValueError):
        figure_fix.restore(wd, "img-01.png")  # 되돌릴 게 없다


def test_replace_rejects_bad_region(wd):
    with pytest.raises(ValueError):
        figure_fix.replace(wd, "img-01.png", 1, [10, 10, 11, 11])  # 너무 작다
    with pytest.raises(ValueError):
        figure_fix.replace(wd, "formula-01.png", 1, [0, 0, 100, 100])  # 수식 크롭은 여기서 안 다룬다
    assert (wd.extract_images / "img-01.png").read_bytes() == b"\x89PNG old figure"


def test_where_falls_back_to_template_matching(wd, monkeypatch):
    calls = []

    def fake_locate(pdf, path):
        calls.append(path.name)
        return (1, 0.97, (12.0, 20.0, 112.0, 80.0), 0.5)

    monkeypatch.setattr(figure_fix, "_locate", fake_locate)
    assert figure_fix.where(wd, "img-02.png") == {"page": 2, "bbox": [12.0, 20.0, 112.0, 80.0]}
    assert figure_fix.load(wd)["img-02.png"]["located"]  # 확실하면 기록해 둔다
    figure_fix.where(wd, "img-02.png")
    assert calls == ["img-02.png"]  # 두 번째는 기록을 쓴다

    monkeypatch.setattr(figure_fix, "_locate", lambda pdf, path: (0, 0.6, (1.0, 2.0, 3.0, 4.0), 0.01))
    got = figure_fix.where(wd, "img-01.png")
    assert got["guess"] and got["page"] == 1  # 자신 없으면 짐작으로만 보여 주고 적지 않는다
    assert "img-01.png" not in figure_fix.load(wd)


def test_picture_rect_from_docling_prov():
    from types import SimpleNamespace as NS

    from narani.extract.docling_backend import _picture_rect

    box = NS(l=50.0, r=250.0, t=700.0, b=500.0, coord_origin="BOTTOMLEFT")
    pic = NS(prov=[NS(page_no=3, bbox=box)])
    doc = NS(pages={3: NS(size=NS(width=612.0, height=792.0))})
    assert _picture_rect(pic, doc) == (3, (50.0, 92.0, 250.0, 292.0))
    assert _picture_rect(NS(prov=[]), doc) is None
