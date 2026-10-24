"""편집기 라이브 프리뷰 (§ui/live_preview.py) — 서버 쪽 수식 변환과 JS 조립."""

import shutil
import subprocess

import pytest

from narani.ui.live_preview import MAX_TEX, live_preview_js, mathml


def test_mathml_converts_and_skips_bad_input():
    out = mathml(["x^2", 3, "", "  "])
    assert list(out) == ["x^2"]
    assert out["x^2"].startswith("<math")


def test_mathml_rejects_non_list_and_caps_count():
    assert mathml("x^2") == {}
    assert mathml(None) == {}
    assert len(mathml([f"x_{i}" for i in range(MAX_TEX + 50)])) == MAX_TEX


def test_js_carries_editor_id_and_token():
    js = live_preview_js(42, 'to"k', on=False)
    assert js.rstrip().endswith('(42, "to\\"k", false);')
    assert "nicegui-codemirror" in js  # 편집기와 같은 CodeMirror 인스턴스를 쓴다


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_js_is_valid_syntax(tmp_path):
    f = tmp_path / "lp.mjs"
    f.write_text(live_preview_js(1, "t"), encoding="utf-8")
    subprocess.run(["node", "--check", str(f)], check=True)
