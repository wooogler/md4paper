"""문구가 바뀐 메모 다시 찾기 (ui/reanchor.JS) — node로 실제 JS를 돌린다. node가 없으면 건너뛴다."""

import json
import shutil
import subprocess

import pytest

from md4paper.ui import reanchor

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 없음")

# DOM 없이 돌리려고 셀과 sentsOf를 흉내 낸다 (문장 = 마침표까지)
HARNESS = """
function sentsOf(c){ var t = c.textContent, out = [], re = /[^.!?]+[.!?]*\\s*/g, m;
  while ((m = re.exec(t))){ if (!m[0].trim()) continue;
    out.push({s: m.index, e: m.index + m[0].replace(/\\s+$/, '').length}); }
  return out; }
""" + reanchor.JS + """
var d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
var cells = d.cells.map(function(t, i){ return {textContent: t,
  classList: {contains: function(c){ return c === 'sbs-cell'; }},
  getAttribute: function(k){ return k === 'data-row' ? String(i) : 'ko'; }}; });
var f = fuzzyFind(d.anchor, cells);
console.log(JSON.stringify(f && {row: f.row, text: f.sc.textContent.slice(f.s, f.e), sure: f.sure}));
"""


def _find(cells, quote, row=0):
    out = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(
        {"cells": cells, "anchor": {"quote": quote, "row": row}}), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_spacing_and_quote_style_changes_are_found_and_saved():
    cells = ["Intro text.", "As P7 described, 'I could just say which options I liked.' Then more."]
    f = _find(cells, 'As P7 described, "I could just say which options I liked."')
    assert f == {"row": 1, "text": "As P7 described, 'I could just say which options I liked.'", "sure": True}
    f = _find(["methods ( 0 . 607 ), compared"], "methods (0.607)")
    assert f["text"] == "methods ( 0 . 607 )" and f["sure"]


def test_retranslated_sentence_is_shown_but_not_saved():
    cells = ["사용자는 구체적인 결과를 보고 무엇이 마음에 드는지 판단할 수 있다. "
             "더 나은 결과를 얻기 위해 에이전트가 수행해야 할 행동의 순서를 지정하는 것보다, "
             "사용자에게는 결과를 평가하는 편이 더 쉽다. 이에 따라 사용자는 대안에 대한 선호를 표현한다."]
    quote = ("사용자는 더 나은 결과에 도달하기 위해 에이전트가 실행해야 하는 행동 순서를 지정하는 것보다 "
             "결과를 평가하는 일을 더 쉽게 해낼 수 있다.")
    f = _find(cells, quote)
    assert f["text"].startswith("더 나은 결과를 얻기 위해") and f["text"].endswith("더 쉽다.")
    assert f["sure"] is False  # 짐작으로 원래 quote를 덮어쓰지 않는다


def test_unrelated_or_ambiguous_text_is_left_unplaced():
    assert _find(["완전히 다른 내용의 문장이다. 또 다른 이야기."], "사용자는 결과를 평가하는 편이 더 쉽다.") is None
    twins = ["The agent explores roots under the posterior.", "The agent explores roots under the posterior."]
    assert _find(twins, "The agent explore roots under its posterior.") is None  # 똑같이 비슷한 두 자리


def test_search_stays_near_the_original_row():
    far = ["filler."] * 40 + ["Users can more easily evaluate outcomes than specify actions."]
    assert _find(far, "Users can more easily evaluate outcome than specify the actions.", row=0) is None
    assert _find(far, "Users can more easily evaluate outcome than specify the actions.", row=35) is not None
