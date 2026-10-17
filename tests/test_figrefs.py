"""본문 그림·표 참조 미리보기의 맵 짓기 (ui/figrefs.py)."""

from __future__ import annotations

import json

from md4paper.ui import figrefs


def test_labeled_image_and_caption() -> None:
    md = "![Figure 1](/wdimages/t/figure-1.png)\n> **Figure 1:** Overview of the [system](#x).\n"
    idx = figrefs.build_index(md)
    assert idx == {"fig-1": {"src": "/wdimages/t/figure-1.png", "cap": "Overview of the system."}}


def test_unlabeled_image_above_caption_with_noise_line() -> None:
    # Ream Fig 5: 빈 alt 그림 — 사이에 머리글 잔재 줄("Anon.")이 끼어 있다
    md = "Body.\n\n![](/wdimages/t/image-1.png)\n\nAnon.\n\n> **Figure 5:** Two workflows.\n"
    assert figrefs.build_index(md)["fig-5"]["src"] == "/wdimages/t/image-1.png"


def test_markdown_table_below_caption_and_duplicate_caption() -> None:
    md = ("> **Table II:** Weights.\n\n| A | B |\n|---|---|\n| x | 1 |\n\n"
          "> **Table 2:** lists heuristic weights (misread body line).\n")
    idx = figrefs.build_index(md)
    assert set(idx) == {"tab-2"}                     # 로마 숫자 II → 2, 뒤의 오인 캡션은 무시
    assert "<table>" in idx["tab-2"]["html"] and "data-line" not in idx["tab-2"]["html"]
    assert idx["tab-2"]["cap"] == "Weights."


def test_table_image_and_missing_asset() -> None:
    md = ("> **Table 3:** As image.\n\n![Table 3](/wdimages/t/table-3.png)\n\n"
          "> **Figure 9:** Caption with no picture nearby.\n\nJust text.\n")
    idx = figrefs.build_index(md)
    assert idx["tab-3"]["src"] == "/wdimages/t/table-3.png"
    assert "fig-9" not in idx                        # 짝이 없으면 띄울 것도 없다


def test_index_js_ko_uses_own_caption_and_escapes() -> None:
    en = "![Figure 1](/a.png)\n> **Figure 1:** n < 5 [x](#ref-1).\n"
    ko = "![Figure 1](/a.png)\n> **Figure 1:** 그림 설명.\n"
    js = figrefs.index_js(en, ko)
    assert "<" not in js                             # </script> 조기 종료 방지
    payload = json.loads(js.split("=", 1)[1].split(";", 1)[0])
    assert payload["ko"]["fig-1"]["cap"] == "그림 설명."
    assert payload["en"]["fig-1"]["cap"] == "n < 5 x."
