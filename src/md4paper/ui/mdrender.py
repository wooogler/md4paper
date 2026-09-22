"""변환 탭 프리뷰 렌더러 — markdown-it-py로 HTML을 만들고 블록마다 소스 줄 번호를 심는다.

**왜 ui.markdown(markdown2)이 아닌가.** 분할 뷰는 에디터의 소스 줄과 프리뷰의 화면 위치를
맞춰야 하는데, 그러려면 렌더된 각 블록이 "원문 몇 번째 줄에서 왔는지"를 알아야 한다.
markdown2는 그 정보를 주지 않지만 markdown-it-py는 블록 토큰마다 `token.map = [시작줄, 끝줄]`을
준다. VS Code의 마크다운 프리뷰가 스크롤 싱크에 쓰는 것과 같은 메커니즘이고, 여기서도 그걸
`data-line` 속성으로 내보내 `_md_sync_js`가 읽는다.

**덤으로 고쳐지는 것들** (코퍼스 실측 — markdown2가 지금 프로덕션에서 만들어 내던 오염):
  - `snake_case_name` → `snake<em>case</em>name` (194건, 10편). CommonMark는 단어 안 `_`를
    강조로 보지 않는다.
  - `$0.08 per Find, $1.41` → 통째로 MathML이 되어 `$` 기호와 문장이 사라짐 (12편).
  - `<T2>`, `<LM_INPUT>` 같은 의사 태그가 진짜 HTML 태그로 해석돼 본문이 화면에서 사라짐 (1편).

**수식.** markdown2의 latex extra는 `$`가 두 번 나오면 무조건 수식으로 봐서 금액을 파괴했다.
여기서는 dollarmath를 `allow_digits=False`로 쓴다 — `$` 바로 옆이 숫자면 수식이 아니다.
코퍼스 실측: 금액이 든 문장 24개 중 오인이 24개 → 3개로 줄고, 진짜 수식은 그대로 잡힌다.
남은 3건은 프롬프트 예시(`$WHOLE$`)와 표 헤더(`Input Cost ($)`)라 본질적으로 애매한 것들이다.
렌더는 latex2mathml에 맡긴다(markdown2가 쓰던 것과 같은 라이브러리라 보이는 결과가 이어진다).

**뷰어 탭은 건드리지 않는다.** 하이라이트·메모 앵커가 렌더된 HTML이 아니라 평문 오프셋이라
(annotations.py) 렌더러를 바꾸면 기존 메모가 밀린다. 이 모듈은 변환 탭 전용이다.
"""

from __future__ import annotations

import re
from functools import lru_cache

# 링크 주소 안의 공백 — PDF 추출이 `https://doi.org/10. 1145/...` 처럼 URL을 끊어 놓는다.
# CommonMark는 이스케이프 안 된 공백이 든 주소를 링크로 인정하지 않아 그대로 글자로 나온다.
# (markdown2는 링크로 만들지만 브라우저가 공백을 %20으로 바꿔 없는 DOI를 가리키므로 어차피 깨진 링크다.)
# 주소 안의 공백만 지우면 양쪽 다 고쳐진다 — 코퍼스 27편에서 실제로 동작하는 링크 94개가 살아났다.
_LINK_DEST_SPACE_RE = re.compile(r"(\]\()(\s*(?:https?://|#|\.{0,2}/)[^)\n]*?)(\))")


def _tighten_link_dests(md: str) -> str:
    return _LINK_DEST_SPACE_RE.sub(
        lambda m: m.group(1) + re.sub(r"\s+", "", m.group(2)) + m.group(3), md)


def _math_renderer(content: str, _opts) -> str:  # noqa: ANN001 — mdit 플러그인 시그니처
    """dollarmath가 잡아낸 LaTeX를 MathML로. 변환 실패는 원문을 그대로 보여준다."""
    try:
        from latex2mathml.converter import convert

        return convert(content)
    except Exception:  # noqa: BLE001 — 수식 하나 때문에 문서 전체가 안 뜨면 안 된다
        return f'<code class="md-math-raw">{content}</code>'


def _source_map_plugin(md) -> None:  # noqa: ANN001 — MarkdownIt 인스턴스
    """블록 토큰에 `data-line`(0-based 시작 줄)을 심는다 — VS Code의 pluginSourceMap과 같은 규칙.

    inline 토큰은 제외한다(부모 블록이 이미 위치를 갖고 있고, 인라인에 붙이면 태그가 겹친다).
    fence는 markdown-it이 속성을 안쪽 `<code>`에 붙이므로 렌더 후 바깥 `<pre>`로 올려 준다.
    html_block은 원문을 그대로 뱉어 속성이 증발하므로 `<div data-line>`으로 감싼다.
    """

    def rule(state) -> None:  # noqa: ANN001
        for tok in state.tokens:
            if tok.map and tok.type != "inline" and not tok.hidden:
                tok.attrSet("data-line", str(tok.map[0]))
                tok.attrJoin("class", "code-line")

    md.core.ruler.push("md4_source_map", rule)

    default_fence = md.renderer.rules.get("fence")
    default_html_block = md.renderer.rules.get("html_block")

    # markdown-it-py의 렌더 룰은 바운드 메서드라 (tokens, idx, options, env) 넷만 받는다.
    def fence(tokens, idx, options, env):  # noqa: ANN001
        out = (default_fence(tokens, idx, options, env) if default_fence
               else md.renderer.renderToken(tokens, idx, options))
        tok = tokens[idx]
        if tok.map and out.startswith("<pre"):
            out = out.replace("<pre", f'<pre data-line="{tok.map[0]}" class="code-line"', 1)
        return out

    def html_block(tokens, idx, options, env):  # noqa: ANN001
        out = (default_html_block(tokens, idx, options, env) if default_html_block
               else tokens[idx].content)
        tok = tokens[idx]
        if tok.map:
            return f'<div data-line="{tok.map[0]}" class="code-line">{out}</div>'
        return out

    md.renderer.rules["fence"] = fence
    md.renderer.rules["html_block"] = html_block


@lru_cache(maxsize=1)
def _md():  # noqa: ANN202 — MarkdownIt
    from markdown_it import MarkdownIt

    # html=True 는 필수다 — 이 문서들은 `<a id="ref-N"></a>` 4,878개와 `<sup class="md-fn">`로
    # 짜여 있어서, 끄면 인용·각주 인프라가 통째로 이스케이프돼 글자로 보인다.
    # (chat.py는 html=False 인스턴스를 쓰므로 여기서 따로 만든다.)
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    try:
        from mdit_py_plugins.dollarmath import dollarmath_plugin

        md.use(dollarmath_plugin, allow_digits=False, allow_space=False, renderer=_math_renderer)
    except ImportError:  # 수식 없이도 문서는 떠야 한다
        pass
    _source_map_plugin(md)
    return md


def render(md_text: str) -> str:
    """마크다운 → 프리뷰 HTML (블록마다 data-line 포함)."""
    return _md().render(_tighten_link_dests(md_text))


def blocks(md_text: str) -> list[tuple[int, str]]:
    """최상위 블록을 (시작 줄, HTML) 목록으로 — 라이브 갱신이 바뀐 블록만 갈아 끼울 때 쓴다.

    전체를 다시 렌더하는 대신 이 목록을 이전 판과 비교해 달라진 항목만 클라이언트로 보낸다
    (실측: 전체 재렌더 274KB·222~338ms vs 블록 패치 400B·35~92ms).
    """
    mdi = _md()
    src = _tighten_link_dests(md_text)
    tokens = mdi.parse(src)
    out: list[tuple[int, str]] = []
    depth = 0
    start = 0
    for i, tok in enumerate(tokens):
        if depth == 0:
            start = i
        depth += tok.nesting
        if depth == 0:
            line = tokens[start].map[0] if tokens[start].map else (out[-1][0] if out else 0)
            html = mdi.renderer.render(tokens[start:i + 1], mdi.options, {})
            out.append((line, html))
    return out
