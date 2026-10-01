"""figure_upscale — 옛 그림을 PDF에서 찾아 고해상도로 갈아 끼운다 (텍스트는 그대로)."""

import io
import json

import pytest
from PIL import Image, ImageDraw

from md4paper import figure_upscale, pdfio
from md4paper.workdir import WorkDir


@pytest.fixture
def paper(tmp_path):
    """그림 둘(색만 다른 같은 틀) + 아이콘 하나가 있는 1쪽 PDF와, 그걸 144dpi로 잘라 둔 옛 작업 폴더."""
    pytest.importorskip("cv2")
    page = Image.new("RGB", (400, 500), "white")
    d = ImageDraw.Draw(page)
    for top, color in ((40, "red"), (260, "blue")):  # 같은 틀, 선 색만 다르다
        d.rectangle((50, top, 330, top + 180), outline="black", width=2)
        for k in range(6):
            d.line((60, top + 20 + k * 25, 320, top + 150 - k * 20), fill=color, width=3)
    d.ellipse((360, 20, 380, 40), fill="green")  # 아이콘 크기 — 건너뛰어야 한다
    pdf = tmp_path / "p.pdf"
    page.save(pdf, resolution=72)  # 1px = 1pt

    wd = WorkDir(tmp_path / "p.md4")
    wd.extract_images.mkdir(parents=True)
    wd.out_images.mkdir(parents=True)
    rects = {"img-01.png": (50, 260, 331, 441), "img-02.png": (50, 40, 331, 221),
             "img-03.png": (358, 18, 382, 42)}
    for name, rect in rects.items():
        png = pdfio.render_region_png(pdf, 0, rect, zoom=2.0)
        (wd.extract_images / name).write_bytes(png)
    (wd.out_images / "figure-1.png").write_bytes((wd.extract_images / "img-01.png").read_bytes())
    wd.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    return wd, pdf, rects


def test_upscale_finds_each_figure_by_color(paper, monkeypatch):
    from md4paper import library

    monkeypatch.setattr(library, "dir_for", lambda *a, **k: None)  # 실제 저장 위치를 건드리지 않게
    wd, pdf, rects = paper
    got = figure_upscale.upscale(wd, pdf)
    assert got == {"done": 2, "skipped": 1, "already": 0}  # 아이콘은 건너뜀

    for name in ("img-01.png", "img-02.png"):
        im = Image.open(wd.extract_images / name)
        assert round(im.info["dpi"][0]) == 288
        want = Image.open(io.BytesIO(pdfio.render_region_png(pdf, 0, rects[name], zoom=4.0)))
        assert im.size == want.size  # 같은 자리를 4배로 — 색만 다른 이웃 그림이 아니다
        assert list(im.convert("RGB").getdata()) == list(want.convert("RGB").getdata())
    assert Image.open(wd.extract_images / "img-03.png").info.get("dpi") is None
    # 조립 때 복사된 사본도 같이 바뀐다 (바이트가 같던 파일)
    assert (wd.out_images / "figure-1.png").read_bytes() == (wd.extract_images / "img-01.png").read_bytes()

    assert figure_upscale.upscale(wd, pdf) == {"done": 0, "skipped": 1, "already": 2}  # 다시 돌려도 무해
