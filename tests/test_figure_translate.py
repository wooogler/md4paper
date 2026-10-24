"""그림 속 글자 번역 — LLM은 줄 라벨만(FakeProvider), 그림은 코드가 원본 위에서 그 글자 자리만 고쳐 그린다.

벡터 그림은 손으로 짠 작은 PDF(Helvetica 글자 + 파란 막대)를 그림처럼 잘라 쓴다 — 글자 레이어 판독,
픽셀 좌표 변환, '상자 밖은 한 픽셀도 안 바뀐다', ko.md 참조 연결, 고치기·지우기, 원본이 바뀌면 빠지기.
OCR은 가짜 함수로 끼운다(rapidocr 모델을 내려받지 않게).
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from narani import figure_fix, pdfio
from narani import figure_translate as ft
from narani.llm import FakeProvider
from narani.workdir import WorkDir

pytest.importorskip("pypdfium2")
np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

BBOX = [10.0, 40.0, 290.0, 240.0]  # 그림 자리 (좌상단 원점 pt)


def _font_or_skip() -> str:
    try:
        return ft.find_font()
    except ft.FigureTranslateError:
        pytest.skip("한글 글꼴이 없는 환경")


def _pdf(path: Path, texts: list[tuple[float, float, int, str]]) -> None:
    """글자 레이어가 있는 1쪽 PDF (300×400pt). texts = [(x, 기준선 y(아래 원점), 크기, 글자)] + 파란 막대."""
    body = "0 0 1 rg 200 200 40 120 re f\n" + "".join(
        f"BT /F1 {s} Tf {x} {y} Td ({t}) Tj ET\n" for x, y, s, t in texts)
    content = body.encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 5 0 R >> >>"
            b" /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(out)


@pytest.fixture
def wd(tmp_path, monkeypatch):
    pdf = tmp_path / "s.pdf"
    # 'Accuracy'(제목) · 'Model A'(범례) · '0.5'(눈금) — 위쪽이 그림 안, 'Outside'는 그림 밖
    _pdf(pdf, [(30, 330, 24, "Accuracy"), (30, 250, 16, "Model A"), (30, 200, 12, "0.5"), (30, 20, 16, "Outside")])
    w = WorkDir(tmp_path / "p.md4")
    w.extract_images.mkdir(parents=True)
    w.out_images.mkdir(parents=True)
    w.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    png = pdfio.render_region_png(str(pdf), 0, tuple(BBOX), zoom=4.0, mark_dpi=True)
    (w.extract_images / "img-01.png").write_bytes(png)
    (w.out_images / "figure-3.png").write_bytes(png)  # 조립이 이름만 바꿔 복사한 것
    figure_fix._save(w, {"img-01.png": {"page": 1, "bbox": BBOX}})
    w.en_md.write_text("# T\n\n![Figure 3](images/figure-3.png)\n**Figure 3:** *cap*\n", encoding="utf-8")
    w.ko_md.write_text("# T\n\n![Figure 3](images/figure-3.png)\n**Figure 3:** *캡션*\n", encoding="utf-8")
    monkeypatch.setattr(figure_fix, "_copies", lambda wd_: [wd_.out_images])
    return w


KO = {"Accuracy": "정확도", "Model A": "모델 A"}


def _fake(kind: str = "chart", ko: dict | None = None, calls: list | None = None) -> FakeProvider:
    """줄 목록을 읽고 아는 글자는 번역, 숫자는 유지하는 가짜 라벨러."""
    ko = KO if ko is None else ko

    def parse(system, user, schema):
        if calls is not None:
            calls.append(user)
        items = []
        for row in re.findall(r"^\{.*\}$", user, flags=re.M):
            b = json.loads(row)
            if all(x in ko for x in b["lines"]):
                items.append({"id": b["id"], "action": "translate", "ko_lines": [ko[x] for x in b["lines"]]})
            else:
                items.append({"id": b["id"], "action": "keep", "ko_lines": b["lines"]})
        return schema.model_validate({"kind": kind, "items": items})

    return FakeProvider(parse_fn=parse, model="gpt-6-luna")


def _arr(data: bytes):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)


def test_pdf_lines_reads_text_layer_in_figure_pixels(wd):
    png = (wd.extract_images / "img-01.png").read_bytes()
    W, H = Image.open(io.BytesIO(png)).size
    lines = ft.pdf_lines(wd, "img-01.png", (W, H))
    texts = [x["en"] for x in lines]
    assert texts == ["Accuracy", "Model A", "0.5"]  # 'Outside'는 그림 밖
    acc = lines[0]["box"]
    # 'Accuracy' 기준선 y=330(아래 원점) → 위에서 70pt, 그림 위끝 40pt → 기준선이 그림 안 30pt = 120px(4배).
    # 줄 상자는 글자 윗끝(약 17pt 위)부터 기준선 아래까지
    assert 70 <= acc[0] <= 90 and 40 < acc[1] < 70 and 110 < acc[3] < 150 and acc[2] > acc[0] + 300
    assert all(0 <= v <= max(W, H) for x in lines for v in x["box"])


def test_translate_changes_only_text_pixels_and_links_ko_md(wd):
    _font_or_skip()
    calls: list = []
    res = ft.translate(wd, "figure-3.png", _fake(calls=calls), ocr=lambda png: [])
    assert res["translated"] == 2 and res["ko"] == "figure-3.ko.png" and res["kind"] == "chart"
    assert res["cost_usd"] >= 0
    src = (wd.extract_images / "img-01.png").read_bytes()
    ko = (wd.out_images / "figure-3.ko.png").read_bytes()
    with Image.open(io.BytesIO(ko)) as im:
        assert round(im.info["dpi"][0]) == 288  # 표시 폭 힌트(#mdw=)가 원본과 같게
    a, b = _arr(src), _arr(ko)
    assert a.shape == b.shape
    rec = ft.load(wd)["img-01.png"]
    union = np.zeros(a.shape[:2], bool)
    for x in rec["lines"]:
        if x["action"] == "translate":
            x0, y0, x1, y1 = (int(round(v)) for v in x["box"])
            union[max(0, y0 - 6):y1 + 6, max(0, x0 - 6):x1 + 12] = True
    changed = np.abs(a - b).max(axis=2) > 0
    assert changed.any() and not (changed & ~union).any()  # 상자 밖(막대·눈금 '0.5')은 그대로
    assert {x["en"]: x["action"] for x in rec["lines"]} == {"Accuracy": "translate", "Model A": "translate",
                                                            "0.5": "keep"}
    assert rec["src_digest"] and rec["renderer"] == ft.RENDERER and rec["model"] == "fake/gpt-6-luna"
    # ko.md만 번역 그림으로 — en.md는 그대로
    assert "images/figure-3.ko.png" in wd.ko_md.read_text(encoding="utf-8")
    assert "images/figure-3.ko.png" not in wd.en_md.read_text(encoding="utf-8")
    assert ft.todo(wd) == [] and ft.valid(wd) == {"figure-3.png": "figure-3.ko.png"}
    info = ft.info(wd, "figure-3.ko.png")  # 번역 그림 이름으로 물어도 같은 그림
    assert info["has"] and not info["stale"] and info["name"] == "figure-3.png"


def test_glossary_terms_go_into_the_prompt(wd, monkeypatch):
    _font_or_skip()
    from narani.ir import GlossaryEntry, GlossaryList
    from narani.translate import glossary

    monkeypatch.setattr(glossary, "effective", lambda w, pid=None: (GlossaryList(entries=[
        GlossaryEntry(term="accuracy", korean="정확도"), GlossaryEntry(term="encoder", korean="인코더")]), {}))
    calls: list = []
    ft.translate(wd, "figure-3.png", _fake(calls=calls), ocr=lambda png: [])
    assert "accuracy → 정확도" in calls[0] and "encoder" not in calls[0]  # 그림에 나오는 용어만


def test_edit_rerenders_without_llm_and_remove_restores(wd):
    _font_or_skip()
    ft.translate(wd, "figure-3.png", _fake(), ocr=lambda png: [])
    before = (wd.out_images / "figure-3.ko.png").read_bytes()
    lines = ft.info(wd, "figure-3.png")["lines"]
    acc = next(x for x in lines if x["en"] == "Accuracy")
    leg = next(x for x in lines if x["en"] == "Model A")
    res = ft.edit(wd, "figure-3.png", {str(acc["id"]): "정확률", str(leg["id"]): ""})  # 빈 글 = 원문 그대로
    assert res["translated"] == 1
    assert (wd.out_images / "figure-3.ko.png").read_bytes() != before
    rec = ft.load(wd)["img-01.png"]
    by = {x["en"]: x for x in rec["lines"]}
    assert by["Accuracy"]["ko"] == "정확률" and by["Model A"]["action"] == "keep" and rec["edited"]
    # 모든 줄을 원문으로 되돌리면 번역 그림이 없어지고 ko.md도 원본 그림으로
    ft.edit(wd, "figure-3.png", {str(acc["id"]): ""})
    assert not (wd.out_images / "figure-3.ko.png").exists()
    assert "figure-3.ko.png" not in wd.ko_md.read_text(encoding="utf-8")
    # 지우기 — 기록도 사라져 다시 번역 대상
    ft.translate(wd, "figure-3.png", _fake(), ocr=lambda png: [])
    assert ft.remove(wd, "figure-3.ko.png")
    assert not (wd.out_images / "figure-3.ko.png").exists() and ft.load(wd) == {}
    assert "images/figure-3.png" in wd.ko_md.read_text(encoding="utf-8")
    assert ft.todo(wd) == ["figure-3.png"]


def test_recrop_makes_translation_stale_and_unlinks_it(wd):
    _font_or_skip()
    ft.translate(wd, "figure-3.png", _fake(), ocr=lambda png: [])
    assert "figure-3.ko.png" in wd.ko_md.read_text(encoding="utf-8")
    figure_fix.replace(wd, "img-01.png", 1, [10, 40, 200, 200])  # 영역 다시 지정 → 원본 바이트가 바뀐다
    assert "figure-3.ko.png" not in wd.ko_md.read_text(encoding="utf-8")
    info = ft.info(wd, "figure-3.png")
    assert info["stale"] and not info["has"]
    assert ft.todo(wd) == ["figure-3.png"]
    with pytest.raises(ft.FigureTranslateError):
        ft.edit(wd, "figure-3.png", {"0": "x"})  # 낡은 기록으로 다시 그리지 않는다
    figure_fix.restore(wd, "img-01.png")  # 원래 그림으로 되돌리면 그 번역이 다시 맞는다
    assert "figure-3.ko.png" in wd.ko_md.read_text(encoding="utf-8")


def test_screenshot_keeps_ocr_screen_text_and_translates_vector_callouts(wd):
    _font_or_skip()
    ocr = [{"en": "Settings", "box": [700.0, 600.0, 900.0, 640.0], "src": "ocr"}]
    res = ft.translate(wd, "figure-3.png", _fake(kind="screenshot", ko={**KO, "Settings": "설정"}),
                       ocr=lambda png: [dict(x) for x in ocr])
    by = {x["en"]: x for x in ft.load(wd)["img-01.png"]["lines"]}
    assert by["Settings"]["action"] == "keep" and by["Settings"]["ui"]  # 화면 글자는 그대로
    assert by["Accuracy"]["action"] == "translate" and res["translated"] == 2


def test_ocr_lines_that_overlap_the_text_layer_are_dropped(wd):
    png = (wd.extract_images / "img-01.png").read_bytes()
    W, H = Image.open(io.BytesIO(png)).size
    acc = ft.pdf_lines(wd, "img-01.png", (W, H))[0]["box"]
    lines = ft.find_lines(wd, "img-01.png", png, ocr=lambda p: [
        {"en": "Accuracy", "box": list(acc), "src": "ocr"},           # 같은 글자를 OCR도 읽음 → 버림
        {"en": "Raster note", "box": [600.0, 700.0, 900.0, 740.0], "src": "ocr"}])
    assert [x["en"] for x in lines] == ["Accuracy", "Model A", "0.5", "Raster note"]
    assert [x["src"] for x in lines] == ["pdf", "pdf", "pdf", "ocr"] and [x["id"] for x in lines] == [0, 1, 2, 3]


def test_line_count_mismatch_leaves_that_block_alone(wd):
    _font_or_skip()

    def parse(system, user, schema):
        items = [{"id": json.loads(r)["id"], "action": "translate", "ko_lines": ["가", "나", "다"]}
                 for r in re.findall(r"^\{.*\}$", user, flags=re.M)]
        return schema.model_validate({"kind": "chart", "items": items})

    res = ft.translate(wd, "figure-3.png", FakeProvider(parse_fn=parse, model="gpt-6-luna"), ocr=lambda p: [])
    assert res["translated"] == 0 and res["ko"] is None  # 한 줄 블록에 세 줄 — 안 맞으면 손대지 않는다
    assert ft.todo(wd) == []  # 기록은 남는다 — 일괄 실행이 다시 부르지 않게


def test_no_text_figure_records_empty_without_calling_llm(wd, monkeypatch):
    _font_or_skip()
    monkeypatch.setattr(figure_fix, "where", lambda w, name: None)  # 자리를 모른다 → PDF 글자 없음, OCR도 없음
    res = ft.translate(wd, "figure-3.png", FakeProvider(parse_fn=lambda *a: 1 / 0, model="m"), ocr=lambda p: [])
    assert res["translated"] == 0 and res["cost_usd"] == 0 and ft.todo(wd) == []


def test_translate_many_counts_and_keeps_going(wd, monkeypatch):
    _font_or_skip()
    (wd.out_images / "figure-4.png").write_bytes(b"\x89PNG broken")
    wd.en_md.write_text(wd.en_md.read_text(encoding="utf-8") + "\n![Figure 4](images/figure-4.png)\n"
                        "![](images/formula-01.png)\n", encoding="utf-8")
    assert ft.candidates(wd) == ["figure-3.png", "figure-4.png"]  # 수식 크롭은 빼고, 문서 순서
    seen = []
    res = ft.translate_many(wd, _fake(), on_progress=lambda d, t: seen.append((d, t)), ocr=lambda p: [])
    assert res["done"] == 1 and list(res["failed"]) == ["figure-4.png"] and res["total"] == 2
    assert seen[0] == (0, 2) and seen[-1] == (2, 2)
    assert "images/figure-3.ko.png" in wd.ko_md.read_text(encoding="utf-8")


def test_link_text_handles_md_wiki_html_and_reverts_dead_links(wd, monkeypatch):
    monkeypatch.setattr(ft, "valid", lambda w: {"figure-3.png": "figure-3.ko.png"})
    md = ("![Figure 3](images/figure-3.png)\n![[images/figure-3.png]]\n<img src=\"images/figure-3.png\" alt=\"\">\n"
          "![Figure 4](images/figure-4.ko.png)\n`images/figure-3.png` 글자\n")
    out = ft.link_text(wd, md)
    assert out.count("images/figure-3.ko.png") == 3
    assert "![Figure 4](images/figure-4.png)" in out  # 번역이 사라진 그림은 원본으로
    assert "`images/figure-3.png` 글자" in out           # 이미지 문법이 아닌 글은 그대로
    assert ft.link_text(wd, out) == out                  # 멱등


def test_join_runs_mends_ligature_splits_and_readable_rejects_garbled_fonts():
    runs = [{"en": "verify scienti", "box": [160, 633, 403, 675], "src": "pdf"},
            {"en": "fi", "box": [408, 633, 429, 665], "src": "pdf"},
            {"en": "c QA", "box": [434, 633, 529, 665], "src": "pdf"},
            {"en": "answers by", "box": [161, 686, 385, 727], "src": "pdf"}]
    out = ft._join_runs(runs)
    assert [x["en"] for x in out] == ["verify scientific QA", "answers by"]
    assert out[0]["box"] == [160, 633, 529, 675]
    assert ft._readable("Model A (n=12)") and not ft._readable('% &"#$  $ " $')


def test_group_joins_stacked_lines_but_not_vertical_or_far_ones():
    lines = [{"id": 0, "en": "researcher-", "box": [40, 147, 203, 173], "vertical": False},
             {"id": 1, "en": "written idea", "box": [40, 189, 214, 215], "vertical": False},
             {"id": 2, "en": "Asta Paperfinder", "box": [305, 268, 555, 303], "vertical": False},
             {"id": 3, "en": "Accuracy (%)", "box": [5, 100, 30, 400], "vertical": True}]
    assert sorted(ft.group(lines)) == [[0, 1], [2], [3]]


def test_vertical_axis_title_is_drawn_rotated(wd):
    font = _font_or_skip()
    W, H = 400, 600
    im = Image.new("RGB", (W, H), "white")
    png = io.BytesIO()
    im.save(png, format="PNG")
    line = {"id": 0, "en": "Accuracy (%)", "box": [10.0, 100.0, 40.0, 500.0], "vertical": True,
            "action": "translate", "ko": "정확도 (%)"}
    out = _arr(ft.render(png.getvalue(), [line], font))
    ys, xs = np.where(out.max(axis=2) < 200)  # 그린 글자 픽셀
    assert len(xs) and xs.max() - xs.min() < 40 and ys.max() - ys.min() > 60  # 세로로 길게


def test_apply_writes_ko_md_with_translated_figures(tmp_path, monkeypatch):
    from narani import pipeline
    from narani.translate.apply import run_translate

    w = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(Path(__file__).parent / "corpus" / "sample_arxiv.md", w)
    name = re.search(r"images/([^)\s]+)", w.en_md.read_text(encoding="utf-8")).group(1)
    monkeypatch.setattr(ft, "valid", lambda wd_: {name: ft.ko_name(name)})
    fake = FakeProvider(complete_fn=lambda s, u: u.split("=== 번역할 본문", 1)[-1].split("\n", 1)[-1],
                        parse_fn=lambda s, u, sc: sc(), model="gpt-6-luna")
    run_translate(w, fake, "해라체")
    assert f"images/{ft.ko_name(name)}" in w.ko_md.read_text(encoding="utf-8")


def test_estimate_and_names():
    assert ft.ko_name("figure-3.png") == "figure-3.ko.png" and ft.ko_name("image-1.jpeg") == "image-1.ko.png"
    assert ft.is_candidate("table-2.png") and not ft.is_candidate("formula-01.png")
    assert not ft.is_candidate("figure-3.ko.png") and not ft.is_candidate("../x.png")
    one = ft.estimate_usd("gpt-6-luna", 1)
    assert 0 < one < 0.01 and ft.estimate_usd("gpt-6-luna", 10) == pytest.approx(one * 10)


# --- 확대 뷰어 라우트 (ui/figure_ko.py) ------------------------------------------------


@pytest.fixture
def client(wd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from narani import library
    from narani.ui import figure_ko, translate_job

    monkeypatch.setattr(translate_job, "provider_for", lambda w, choice=None: _fake())
    monkeypatch.setattr(translate_job, "default_model", lambda w: "openai/gpt-6-luna")
    monkeypatch.setattr(library, "auto_export", lambda w, target=None: [])
    monkeypatch.setattr(ft, "ocr_lines", lambda png: [])
    app = FastAPI()
    figure_ko.register_routes(app, lambda tok: wd if tok == "tok" else None)
    return TestClient(app)


def test_routes_run_view_edit_remove(client, wd):
    _font_or_skip()
    st = client.get("/figko/tok/figure-3.png").json()
    assert not st["has"] and not st["record"] and st["next_model"] == "openai/gpt-6-luna" and st["est_usd"] > 0
    assert client.get("/figko/nope/figure-3.png").status_code == 404
    assert client.get("/figko/tok/formula-01.png").status_code == 404  # 수식 크롭은 대상이 아니다
    res = client.post("/figko/tok/figure-3.png/run").json()
    assert res["ok"] and res["ko"] == "figure-3.ko.png" and res["translated"] == 2
    st = client.get("/figko/tok/figure-3.ko.png").json()  # 번역 열에서 연 확대 뷰어
    assert st["has"] and st["name"] == "figure-3.png" and len(st["lines"]) == 3
    acc = next(x for x in st["lines"] if x["en"] == "Accuracy")
    res = client.post("/figko/tok/figure-3.ko.png/edit", json={"edits": {str(acc["id"]): "정확률"}}).json()
    assert res["ok"] and res["translated"] == 2
    assert client.post("/figko/tok/figure-3.png/remove").json()["ok"]
    assert not client.post("/figko/tok/figure-3.png/remove").json()["ok"]  # 두 번째는 지울 게 없다
    assert "figure-3.ko.png" not in wd.ko_md.read_text(encoding="utf-8")


def test_route_reports_errors_instead_of_500(client, monkeypatch):
    from narani.ui import translate_job

    def nokey(w, choice=None):
        raise RuntimeError("openai API 키가 없습니다")

    monkeypatch.setattr(translate_job, "provider_for", nokey)
    res = client.post("/figko/tok/figure-3.png/run").json()
    assert not res["ok"] and "API 키" in res["error"]
    res = client.post("/figko/tok/figure-3.png/edit", json={"edits": {}}).json()
    assert not res["ok"] and "기록" in res["error"]


# --- 번역 작업·에이전트 tool ----------------------------------------------------------------


def test_translate_job_runs_figures_after_text_when_asked(tmp_path, monkeypatch):
    import asyncio

    from narani.ui import controller, translate_job

    class FakeCtrl:
        def __init__(self, w):
            self.wd = w

        def translate(self, provider, style, on_progress):
            return {"cost_usd": 0.01, "kept": 0}

    called = []
    monkeypatch.setattr(controller, "UIController", FakeCtrl)
    monkeypatch.setattr(translate_job.library, "auto_export", lambda w: [])
    monkeypatch.setattr(ft, "translate_many", lambda w, p, on_progress=None: called.append(w) or {
        "done": 3, "empty": 1, "failed": {}, "cost_usd": 0.002, "total": 4})
    translate_job.reset(2)

    async def go(figures):
        job = translate_job.start(WorkDir(tmp_path / f"{figures}.md4"), object(), figures=figures)
        while job.running:
            await asyncio.sleep(0.01)
        return job

    plain = asyncio.run(go(False))
    assert plain.message == "번역 완료 (≈$0.0100)" and not called
    job = asyncio.run(go(True))
    assert job.status == "done" and len(called) == 1
    assert job.message == "번역 완료 (≈$0.0120) · 그림 3개 글자 번역" and job.result["figures"]["done"] == 3
    translate_job.reset()


def test_agent_figure_names_and_approval_card(wd, monkeypatch):
    pytest.importorskip("pydantic_ai")
    from narani.ui.agent import jobtools

    monkeypatch.setattr(jobtools, "translate_choice", lambda w: ("openai", "gpt-6-luna"))
    wd.en_md.write_text(wd.en_md.read_text(encoding="utf-8") + "\n![](images/image-1.png)\n", encoding="utf-8")
    (wd.out_images / "image-1.png").write_bytes(b"\x89PNG x")
    assert jobtools.figure_names(wd, ["Figure 3", "image-1", "figure-3.ko.png"], False) == [
        "figure-3.png", "image-1.png"]
    assert jobtools.figure_names(wd, None, False) == ["figure-3.png", "image-1.png"]
    bad = jobtools.figure_names(wd, ["Figure 9"], False)
    assert isinstance(bad, str) and "Figure 9" in bad and "figure-3" in bad
    card = jobtools.describe_figures({"figures": ["Fig. 3"]}, Path(wd.root))
    assert card.startswith("그림 1장 글자 번역 (LLM 비용 ≈$") and "openai/gpt-6-luna" in card
    assert "translate_figures" in jobtools.DESCRIBE and jobtools.translate_figures in jobtools.TOOLS


def test_translate_into_japanese_uses_language_name_font_and_file(wd):
    """번역 언어가 일본어면 그림 이름·기록·글꼴·프롬프트가 일본어 것 — 한국어 파일은 건드리지 않는다."""
    try:
        font = ft.find_font("ja")
    except ft.FigureTranslateError:
        pytest.skip("일본어 글꼴 없음")
    ko_md = wd.ko_md.read_text(encoding="utf-8")
    wd.set_lang("ja")
    wd.ko_md.write_text(ko_md.replace("캡션", "キャプション"), encoding="utf-8")
    systems: list = []
    fake = _fake(ko={"Accuracy": "精度", "Model A": "モデルA"})
    inner = fake._parse_fn
    fake._parse_fn = lambda system, user, schema: (systems.append(system), inner(system, user, schema))[1]
    res = ft.translate(wd, "figure-3.png", fake, ocr=lambda png: [])
    assert "replace it with Japanese" in systems[0] and "Korean" not in systems[0]
    assert res["ko"] == "figure-3.ja.png" and res["translated"] == 2
    assert (wd.out_images / "figure-3.ja.png").is_file() and not (wd.out_images / "figure-3.ko.png").exists()
    assert (wd.extract / "figures-ja.json").is_file() and not (wd.extract / "figures-ko.json").exists()
    assert "images/figure-3.ja.png" in wd.ko_md.read_text(encoding="utf-8")
    assert {x["ko"] for x in ft.load(wd)["img-01.png"]["lines"] if x["action"] == "translate"} == {"精度", "モデルA"}
    assert ft.info(wd, "figure-3.ja.png")["name"] == "figure-3.png"
    assert "ヒラギノ" in font or "Noto" in font or "Yu" in font or "meiryo" in font
