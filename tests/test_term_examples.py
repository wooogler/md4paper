"""용어 예문 — 용어가 쓰인 원문 문장과 짝지은 번역 문장."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from agent_support import make_wd  # noqa: E402

from md4paper.ui import term_examples as te  # noqa: E402
from md4paper.ui.agent import retrieval  # noqa: E402

EN = """# Mental Models in Code

We study how developers build a mental model of code. Tools help a little.

## 2 Method

Participants explained their **mental models** aloud. We said nothing about knowledge-edge cases.

## 3 Results

The [mental model](http://x) gap was large.

## References

[1] A. Author. Mental models of programmers. 1999.
"""

KO = """# 코드의 정신 모형

우리는 개발자가 코드에 대한 정신 모형을 어떻게 구축하는지 연구한다. 도구는 조금 돕는다.

## 2 방법

참가자들은 자신의 **정신모형**을 소리 내어 설명했다. 우리는 지식 경계 사례에 대해 아무 말도 하지 않았다.

## 3 결과

머릿속 그림의 차이가 컸다.

## 참고문헌

[1] A. Author. Mental models of programmers. 1999.
"""


@pytest.fixture(autouse=True)
def _clean():
    retrieval.clear_cache()
    yield
    retrieval.clear_cache()


def test_find_pairs_sentences_and_marks_term_and_rendering(tmp_path):
    wd = make_wd(tmp_path, en=EN, ko=KO)
    ex, rows = te.find(wd, "Mental Model", "정신 모형", limit=5)
    assert rows == 4                      # 제목·서론·방법·결과 (참고문헌은 뺀다)
    by_row = {e.row: e for e in ex}
    intro = next(e for e in ex if "developers build" in e.en)
    assert intro.ko.startswith("우리는 개발자가") and intro.ko_exact
    assert intro.en[slice(*intro.en_spans[0])] == "mental model"
    assert intro.ko[slice(*intro.ko_spans[0])] == "정신 모형"
    method = next(e for e in ex if "aloud" in e.en)
    assert method.en[slice(*method.en_spans[0])] == "mental models"   # 복수형까지 한 덩어리
    assert method.ko_exact and method.ko[slice(*method.ko_spans[0])] == "정신모형"   # 띄어쓰기 무시
    result = next(e for e in ex if "gap was large" in e.en)
    assert not result.ko_exact and result.ko == "머릿속 그림의 차이가 컸다."   # 다른 말로 옮긴 곳
    assert [e.ko_exact for e in ex] == sorted((e.ko_exact for e in ex), reverse=True)   # 찾은 것 먼저
    assert all("Author" not in e.en for e in by_row.values())


def test_find_word_boundary_keep_policy_and_limits(tmp_path):
    wd = make_wd(tmp_path, en=EN, ko=KO)
    assert te.find(wd, "ledge", "x")[1] == 0            # knowledge 안의 글자는 단어 시작이 아니다
    assert te.find(wd, "edge", "경계")[1] == 1          # knowledge-edge의 edge는 하이픈 뒤 — 단어 시작
    ex, _ = te.find(wd, "mental model", "", policy="keep")
    assert ex and not any(e.ko_exact for e in ex)       # keep이면 번역 쪽에서 원어를 찾는다 — 여기엔 없음
    assert len(te.find(wd, "mental model", "정신 모형", limit=2)[0]) == 2
    assert te.find(wd, "", "x") == ([], 0)
    assert te.find(make_wd(tmp_path, "noko", en=EN, ko=None), "mental model")[0][0].ko == ""


def test_clip_and_html(tmp_path):
    long = "word " * 120 + "the mental model appears here " + "tail " * 120
    wd = make_wd(tmp_path, en=f"# T\n\n{long.strip()}.\n", ko=None)
    (e,), _ = te.find(wd, "mental model")
    assert e.en.startswith("…") and e.en.endswith("…") and len(e.en) <= te.CLIP + 4
    assert e.en[slice(*e.en_spans[0])] == "mental model"
    e.en = "<b>mental model</b> & co"
    e.en_spans = [(3, 15)]
    out = te.to_html(e)
    assert "&lt;b&gt;<mark>mental model</mark>&lt;/b&gt; &amp; co" in out


def test_across_papers(tmp_path):
    a = make_wd(tmp_path, "a", en=EN, ko=KO, title="A")
    b = make_wd(tmp_path, "b", en="# B\n\nNothing about it.\n", ko="# B\n\n아무것도.\n", title="B")
    out = te.across([(a.root, "정신 모형", "translate"), (b.root, "x", "translate")], "mental model")
    assert [r["title"] for r in out] == ["A"] and len(out[0]["examples"]) == 1 and out[0]["rows"] == 4


def test_unaligned_translation_is_reported(tmp_path):
    """번역의 헤더 수가 원문과 다르면 행이 안 맞아 번역 문장을 못 짝짓는다 — '번역 없음'과 구별한다."""
    wd = make_wd(tmp_path, en=EN, ko=KO + "\n## 덧붙은 제목\n\n본문.\n")
    ex, _ = te.find(wd, "mental model", "정신 모형")
    assert ex and all(e.ko == "" for e in ex) and te.unaligned(wd)
    assert not te.unaligned(make_wd(tmp_path, "ok", en=EN, ko=KO))
    assert not te.unaligned(make_wd(tmp_path, "none", en=EN, ko=None))
