"""문구가 바뀐 메모 다시 찾기 (ui/reanchor.JS) — node로 실제 JS를 돌린다. node가 없으면 건너뛴다."""

import json
import shutil
import subprocess

import pytest

from md4paper.ui import reanchor, sentpair

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 없음")

# DOM 없이 돌리려고 셀과 sentsOf를 흉내 낸다 (문장 = 마침표까지)
HARNESS = sentpair.JS + """
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


def test_retranslated_sentence_is_placed_as_a_guess():
    cells = ["사용자는 구체적인 결과를 보고 무엇이 마음에 드는지 판단할 수 있다. "
             "더 나은 결과를 얻기 위해 에이전트가 수행해야 할 행동의 순서를 지정하는 것보다, "
             "사용자에게는 결과를 평가하는 편이 더 쉽다. 이에 따라 사용자는 대안에 대한 선호를 표현한다."]
    quote = ("사용자는 더 나은 결과에 도달하기 위해 에이전트가 실행해야 하는 행동 순서를 지정하는 것보다 "
             "결과를 평가하는 일을 더 쉽게 해낼 수 있다.")
    f = _find(cells, quote)
    assert f["text"].startswith("더 나은 결과를 얻기 위해") and f["text"].endswith("더 쉽다.")
    assert f["sure"] is False  # 확실하지 않음 → 뷰어가 추정(guess)으로 저장하고 원래 quote는 orig에 남긴다


def test_unrelated_or_ambiguous_text_is_left_unplaced():
    assert _find(["완전히 다른 내용의 문장이다. 또 다른 이야기."], "사용자는 결과를 평가하는 편이 더 쉽다.") is None
    twins = ["The agent explores roots under the posterior.", "The agent explores roots under the posterior."]
    assert _find(twins, "The agent explore roots under its posterior.") is None  # 똑같이 비슷한 두 자리


def test_search_stays_near_the_original_row():
    far = ["filler."] * 40 + ["Users can more easily evaluate outcomes than specify actions."]
    assert _find(far, "Users can more easily evaluate outcome than specify the actions.", row=0) is None
    assert _find(far, "Users can more easily evaluate outcome than specify the actions.", row=35) is not None


# 두 쪽 격자 — 셀마다 side·row를 준다. 짝 앵커가 붙은 자리에서 반대쪽을 다시 고르는 길(siblingFind)과
# 행 창·문서 전체 탐색(fuzzyFind center/wide)을 함께 본다.
GRID = sentpair.JS + """
function sentsOf(c){ var t = c.textContent, out = [], re = /[^.!?]+[.!?]*\\s*/g, m;
  while ((m = re.exec(t))){ if (!m[0].trim()) continue;
    out.push({s: m.index, e: m.index + m[0].replace(/\\s+$/, '').length}); }
  return out; }
""" + reanchor.JS + """
var d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
var cells = d.cells.map(function(c){ return {textContent: c.t,
  classList: {contains: function(k){ return k === 'sbs-cell'; }},
  getAttribute: function(k){ return k === 'data-row' ? String(c.row) : c.side; }}; });
function cell(side, row){ return cells.filter(function(c){
  return c.getAttribute('data-side') === side && +c.getAttribute('data-row') === row; })[0]; }
var f;
if (d.sib){
  var sc = cell(d.sib.side, d.sib.row), at = sc.textContent.indexOf(d.sib.quote);
  f = siblingFind(d.anchor, cell(d.anchor.side, d.sib.row), sc, at, at + d.sib.quote.length);
} else {
  f = fuzzyFind(d.anchor, cells.filter(function(c){ return c.getAttribute('data-side') === d.anchor.side; }),
                d.center, d.wide);
}
console.log(JSON.stringify(f && {row: f.row, text: f.sc.textContent.slice(f.s, f.e), sure: f.sure}));
"""


def _grid(cells, anchor, **kw):
    out = subprocess.run([NODE, "-e", GRID], input=json.dumps({"cells": cells, "anchor": anchor, **kw}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


EN = ("Users can only evaluate a few outcomes. The space of outcomes is large. "
      "As a result, the feedback is sparse.")
KO_NEW = "사용자는 소수의 결과만 평가할 수 있다. 결과의 공간은 훨씬 크다. 따라서 이용 가능한 피드백은 본질적으로 희소하다."


def _paper(shift):
    """재변환으로 문단이 잘게 나뉘어 행이 shift만큼 밀린 격자 (같은 행에 원문·번역)."""
    cells = []
    for r in range(shift + 3):
        en, ko = (EN, KO_NEW) if r == shift + 1 else (f"Filler paragraph {r}.", f"채움 문단 {r}번이다.")
        cells += [{"side": "en", "row": r, "t": en}, {"side": "ko", "row": r, "t": ko}]
    return cells


def test_row_shifted_and_retranslated_is_repaired_from_the_sibling_row():
    # 원문 쪽은 이미 새 행(41)에서 찾았고, 번역 쪽은 옛 행(1)·옛 번역 문구만 들고 있다
    old_ko = {"side": "ko", "row": 1, "quote": "결과의 공간은 크다."}
    f = _grid(_paper(40), old_ko, sib={"side": "en", "row": 41, "quote": "The space of outcomes is large."})
    assert f["row"] == 41 and f["text"] == "결과의 공간은 훨씬 크다."
    # 같은 행에서 짝을 골라도 전혀 다른 문장이면 붙이지 않는다
    wrong = {"side": "ko", "row": 1, "quote": "완전히 엉뚱한 내용으로 된 예전 메모 문장이었다."}
    assert _grid(_paper(40), wrong, sib={"side": "en", "row": 41, "quote": "The space of outcomes is large."}) is None


def test_far_row_is_found_only_by_the_whole_document_pass():
    old_ko = {"side": "ko", "row": 1, "quote": "따라서 이용할 수 있는 피드백은 본질적으로 희소하다.",
              "prefix": "결과의 공간은 크다. ", "suffix": ""}
    assert _grid(_paper(40), old_ko) is None                       # 행 창(±25) 밖
    f = _grid(_paper(40), old_ko, wide=True)                      # "다시 찾기"
    assert f["row"] == 41 and f["text"].startswith("따라서 이용 가능한 피드백은")


def test_hyphen_and_unicode_variants_count_as_the_same_text():
    cells = [{"side": "en", "row": 0, "t": "We use a pre-\u00adde\ufb01ned feature space \u2014 fixed."}]
    f = _grid(cells, {"side": "en", "row": 0, "quote": "a predefined feature space - fixed"})
    assert f["sure"] and f["text"] == "a pre-\u00adde\ufb01ned feature space \u2014 fixed"


def test_korean_quote_does_not_land_on_an_english_reference_entry():
    cells = [{"side": "ko", "row": 0, "t": "선호 기반 최적화(PBO)는 제한된 비교로 선호를 배운다."},
             {"side": "ko", "row": 1, "t": "Preferential bayesian optimization."}]
    quote = "선호 기반 베이지안 최적화(Preferential Bayesian optimization)."
    f = _grid(cells, {"side": "ko", "row": 0, "quote": quote}, wide=True)
    assert f is None or f["row"] == 0


def test_context_breaks_a_tie_between_twin_sentences():
    twin = "The agent explores roots under the posterior."
    cells = [{"side": "en", "row": 0, "t": "Setup comes first. " + twin + " Then we stop."},
             {"side": "en", "row": 1, "t": "Results differ here. " + twin + " Users liked it."}]
    a = {"side": "en", "row": 0, "quote": "The agent explore roots under its posterior.",
         "prefix": "Results differ here. ", "suffix": " Users liked it."}
    f = _grid(cells, a)
    assert f is not None and f["row"] == 1
