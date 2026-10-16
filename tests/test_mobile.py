"""모바일 HTML — 고른 논문들을 한 장으로 (원문·번역 행, 그림 한 번만, 앵커, CLI)."""

from __future__ import annotations

import collections
import json
import re

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from md4paper.ui import mobile, sentpair  # noqa: E402
from md4paper.workdir import WorkDir  # noqa: E402

EN = """# Bespoke Bots

We study how instructors customize chatbots [1](#ref-1). It matters.<sup class="md-fn"><a href="#fn-1">1</a></sup>

![Figure 1](images/figure-1.png)
> **Figure 1:** The interface.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.

<a id="fn-1"></a>**1.** Footnote text.
"""

KO = """# 맞춤형 봇

우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다 [1](#ref-1). 이는 중요하다.<sup class="md-fn"><a href="#fn-1">1</a></sup>

![Figure 1](images/figure-1.png)
> **Figure 1:** 인터페이스.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.

<a id="fn-1"></a>**1.** 각주 내용.
"""


def _wd(tmp_path, name: str, en: str = EN, ko: str | None = KO) -> WorkDir:
    from PIL import Image

    wd = WorkDir(tmp_path / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), (200, 30, 30)).save(wd.out_images / "figure-1.png")
    wd.references_json.parent.mkdir(parents=True, exist_ok=True)
    wd.references_json.write_text(json.dumps({"accepted": [
        {"label": "1", "authors": ["Mina Lee"], "year": 2022, "title": "CoAuthor", "venue": "CHI",
         "doi": "10.1145/3491102.3502030"}]}), encoding="utf-8")
    wd.footnotes_json.write_text(json.dumps({"1": "Footnote text."}), encoding="utf-8")
    return wd


def _data(html: str) -> dict:
    return json.loads(re.search(r"var DATA = (\{.*?\});\n", html, re.S).group(1))


def test_build_rows_images_and_anchors(tmp_path):
    a = _wd(tmp_path, "a")
    b = _wd(tmp_path, "b", ko=None)
    html = mobile.build([a.root, b.root])

    # 논문 목록 + 두 편 + CSS로만 바꾸는 보기 모드
    assert html.count('data-open="p') == 2
    assert 'id="m-ko"' in html and 'id="m-en"' in html and 'id="m-both"' in html
    assert 'class="paper has-ko" id="p1"' in html
    assert 'class="paper noalign" id="p2"' in html  # 번역 없음 → 원문만, 대조 불가

    # 정렬 행: 번역 셀과 원문 셀이 짝으로
    p1 = html.split('id="p1"', 1)[1].split('id="p2"', 1)[0]
    assert '<div class="cell ko" lang="ko">' in p1 and '<div class="cell en" lang="en">' in p1
    assert "교수자가 챗봇을" in p1 and "instructors customize" in p1

    # 그림 줄은 원문·번역 공통 셀로 한 번만 — 크기도 적혀 자리가 미리 잡힌다
    assert p1.count("<img ") == 1
    assert re.search(r'<img [^>]*src="data:image/png;base64,[^"]+" width="40" height="20"', p1)

    # id가 논문·언어별로 갈려 겹치지 않고, 내부 링크는 전부 있는 곳을 가리킨다
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert not [k for k, v in collections.Counter(ids).items() if v > 1]
    assert {h for h in re.findall(r'href="#([^"]+)"', html)} <= set(ids)
    # 번역 셀의 인용은 번역 쪽 참고문헌으로
    assert 'href="#p1-ko-ref-1"' in p1 and 'href="#p1-en-ref-1"' in p1


def test_untranslated_references_reachable_from_both_sides(tmp_path):
    """참고문헌이 원문·번역에서 같으면 공통 셀 하나 — 양쪽 인용이 모두 닿도록 별칭 앵커를 둔다."""
    html = mobile.build([_wd(tmp_path, "a").root])
    assert 'id="p1-ko-ref-1"' in html and 'id="p1-en-ref-1"' in html
    assert html.count("[1] Lee, M. 2022. CoAuthor. CHI.") == 1


def test_cite_and_footnote_data_for_tap_sheet(tmp_path):
    html = mobile.build([_wd(tmp_path, "a").root])
    d = _data(html)["p1"]
    assert d["cites"]["1"]["url"] == "https://doi.org/10.1145/3491102.3502030"
    assert "CoAuthor" in d["cites"]["1"]["text"]
    assert d["fns"] == {"1": "Footnote text."}
    assert d["title"] == "맞춤형 봇"  # 번역 제목을 앞에


def test_script_embeds_shared_sentence_pairing():
    """문장 짝 규칙은 뷰어와 같은 코드 — 두 화면이 갈라지지 않게 같은 문자열을 끼운다."""
    from md4paper.ui.app import _ANNO_HTML

    assert sentpair.JS in mobile._JS
    assert sentpair.JS in _ANNO_HTML
    assert "function pairUp" in sentpair.JS and "function sentsOf" in sentpair.JS


def test_noalign_when_section_counts_differ(tmp_path):
    ko = KO.replace("## References", "## 참고문헌\n\n## 부록")
    html = mobile.build([_wd(tmp_path, "a", ko=ko).root])
    assert 'class="paper has-ko noalign"' in html
    assert "문단 대조·문장 짝을 쓸 수 없습니다" in html


def test_split_shared_images():
    shared, en, ko = mobile._split_shared_images("![F](a.png)\n> **F:** cap", "![F](a.png)\n> **F:** 캡션")
    assert shared == "![F](a.png)" and en == "> **F:** cap" and ko == "> **F:** 캡션"
    assert mobile._split_shared_images("text", "글")[0] == ""


def test_large_image_is_shrunk(tmp_path):
    from PIL import Image

    p = tmp_path / "big.png"
    Image.effect_noise((2400, 1200), 80).convert("RGB").save(p)
    data, mime, size = mobile._image_bytes(p)
    assert size == (mobile.MAX_IMG_W, 600)
    assert len(data) < p.stat().st_size and mime in ("image/png", "image/webp", "image/jpeg")


def test_cli_mobile_accepts_paper_folder(tmp_path):
    from click.testing import CliRunner

    from md4paper.cli import cli

    a = _wd(tmp_path / "A", "a")  # tmp/A/a.md4 — 논문 폴더(tmp/A)를 넘겨도 찾는다
    b = _wd(tmp_path, "b", ko=None)
    out = tmp_path / "m.html"
    result = CliRunner().invoke(cli, ["mobile", str(tmp_path / "A"), str(b.root), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "2편" in result.output
    html = out.read_text(encoding="utf-8")
    assert a.root.stem in html and 'id="p2"' in html

    empty = CliRunner().invoke(cli, ["mobile"])
    assert empty.exit_code != 0 and "논문을 고르세요" in empty.output
