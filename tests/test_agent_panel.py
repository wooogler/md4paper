"""에이전트 창 화면 — CSS/HTML/JS 문자열 계약 + 셸 설치.

창은 순수 클라이언트라 파이썬 로직이 얇다. 그래서 검증 표면은 (1) 셸에 남는 id·전역 API·이벤트
이름이 서버 쪽(app.py·spa·백엔드 칩 마크업)과 한 이름을 가리킨다는 것, (2) 레이아웃 규칙(본문 밀기·
층 순서), (3) 옛 서랍에서 옮겨 온 '읽던 자리로' 알약의 순서가 곧 동작인 몇 줄이다.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from md4paper.ui import agent_panel  # noqa: E402

CSS, HTML, JS = agent_panel.CSS, agent_panel.HTML, agent_panel.JS


def _js_block(src: str, head: str) -> str:
    """`function foo(`부터 다음 함수 선언까지 — 한 함수 안의 줄 순서를 보려고 자른다."""
    i = src.index(head)
    return src[i:src.index("\n  function ", i + 1)]


def _rule(css: str, selector: str) -> str:
    return css.split(selector + " {", 1)[1].split("}", 1)[0]


# --- 셸 계약 ---------------------------------------------------------------


def test_panel_is_body_level_id_so_spa_keeps_it():
    """id가 있어야 spa.add_body_html이 화면을 갈아 끼울 때 다시 넣지 않는다(대화가 살아남는다)."""
    assert HTML.lstrip().startswith(f'<div id="{agent_panel.PANEL_ID}"')
    assert f"getElementById('{agent_panel.PANEL_ID}')" in JS
    assert "<script" not in HTML           # 스크립트는 따로 실행시킨다(install)
    assert HTML.count("<div") == HTML.count("</div>")


def test_public_js_api():
    """뷰어·홈·툴바가 부르는 이름 — app.py·home.py의 호출과 맞아야 한다."""
    assert "window.__md4Context = ctxNow" in JS
    assert "window.__md4Agent = {" in JS
    for name in ("toggle:", "open:", "close:", "setPaper:", "context:"):
        assert name in JS.split("window.__md4Agent = {", 1)[1]
    assert JS.count("if (window.__md4AgentInit) return;") == 1   # 두 번 돌아도 한 번만 붙는다


def test_init_js_sets_global_before_panel_and_escapes():
    """첫 화면에서는 화면 스크립트가 창보다 먼저 돌 수 있다 → 전역에 남기고 창이 읽는다."""
    js = agent_panel.init_js("abc123def456")
    assert js.index('window.__md4AgentTok = "abc123def456"') < js.index("setPaper(")
    assert "window.__md4Agent &&" in js
    assert agent_panel.init_js(None).startswith("window.__md4AgentTok = null;")
    assert "<" not in agent_panel.init_js("</script>")   # </script> 조기 종료 방지
    assert "var paper = window.__md4AgentTok || null;" in JS


def test_nav_event_name_matches_and_is_guarded(monkeypatch):
    """JS가 emitEvent로 부탁한 이동만 하고, 앱 밖 주소는 받지 않는다."""
    from nicegui import ui

    assert f"emitEvent('{agent_panel.NAV_EVENT}'" in JS
    went: list[str] = []
    monkeypatch.setattr(ui.navigate, "to", went.append)
    agent_panel._nav("/review?wd=%2Fx%2Fp.md4")
    agent_panel._nav("/home")
    agent_panel._nav("https://evil.example/")
    agent_panel._nav("javascript:alert(1)")
    assert went == ["/review?wd=%2Fx%2Fp.md4", "/home"]


def test_citation_chip_selectors_match_backend_markup():
    """칩 마크업은 서버(cites.render_html)가 만든다 — 클래스·data 속성 이름이 어긋나면 칩이 죽는다."""
    from md4paper.ui.agent.cites import render_html

    html = render_html("a [[r3]] b [[aa1]]", [("abc123def456", 3), "aa1"],
                       {"aa1": {"id": "a1", "row": 3, "token": "abc123def456"}},
                       current="abc123def456")
    assert 'class="chat-cite"' in html and 'class="chat-cite chat-cite-note"' in html
    for attr in ("data-kind", "data-token", "data-row", "data-id"):
        assert attr in html and f"getAttribute('{attr}')" in JS
    assert "querySelectorAll('a.chat-cite')" in JS
    assert "a.chat-cite {" in CSS and "a.chat-cite-note {" in CSS


def test_sse_event_names_match_backend():
    """백엔드 run_turn이 내는 이벤트 이름을 모두 받는다."""
    for ev in ("text", "tool_call", "tool_result", "ui_action", "approval_needed", "done", "error"):
        assert f"name === '{ev}'" in JS
    for kind in ("goto_row", "open_paper"):
        assert f"a.kind === '{kind}'" in JS
    assert "j.error || j.detail" in JS        # 스트림 전 거절: 우리 JSON {error} + HTTPException {detail}


def test_routes_used_by_panel_exist():
    """창이 부르는 주소가 백엔드 라우트와 같은 모양이다."""
    from fastapi import FastAPI

    from md4paper.ui.agent import routes

    app = FastAPI()
    routes.register_routes(app, lambda t: None, lambda w: "")
    paths = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    for need in [("GET", "/agent/sessions"), ("POST", "/agent/sessions"),
                 ("GET", "/agent/sessions/{sid}"), ("PATCH", "/agent/sessions/{sid}"),
                 ("DELETE", "/agent/sessions/{sid}"), ("POST", "/agent/sessions/{sid}/turns"),
                 ("GET", "/agent/models"), ("PUT", "/agent/model")]:
        assert need in paths
    assert "'/agent/sessions/' + encodeURIComponent(at) + '/turns'" in JS
    assert "api('GET', '/agent/models')" in JS and "api('PUT', '/agent/model'" in JS


def test_context_snapshot_fields_match_backend_model():
    """__md4Context가 보내는 필드 이름이 ScreenContext 필드와 같다(extra는 조용히 버려지므로)."""
    from md4paper.ui.agent.context import ScreenContext

    body = _js_block(JS, "function ctxNow(")
    sent = set(re.findall(r"^\s+(\w+):", body, re.M))
    assert sent == set(ScreenContext.model_fields)
    assert "closest('.anno-scope')" in JS and ".md4-notetab[data-tab]" in JS
    assert "quote: q.slice(0, 300)" in JS and "out.length < 60" in JS


# --- 레이아웃 ---------------------------------------------------------------


def test_open_panel_pads_shared_body_frame():
    """홈·뷰어 공통 틀(.md4-body)을 밀어야 홈 2단 레이아웃에서도 본문이 덮이지 않는다."""
    assert "body.ag-open .md4-body { padding-right: var(--ag-w); }" in CSS
    assert ".md4-steps" not in CSS
    assert "classList.toggle('ag-open'" in JS


def test_panel_z_matches_memo_drawer():
    """메모 서랍과 같은 층 — 번갈아 열리는 같은 자리다."""
    z = int(re.search(r"z-index:\s*(\d+)", _rule(CSS, "#md4-agent")).group(1))
    assert z == agent_panel.PANEL_Z == 9000
    assert "getElementById('md-anno-panel')" in JS


def test_dark_mode_covers_panel():
    dark = CSS.split("@media (prefers-color-scheme: dark)")[1]
    assert "#md4-agent {" in dark and ".ag-input" in dark and ".ag-menu" in dark


def test_storage_keys():
    """브라우저에 남기는 것은 세션·폭·열림(localStorage)과 다른 논문으로 가는 행(sessionStorage)뿐."""
    assert re.findall(r"var LS_SESSION = '([^']+)', LS_W = '([^']+)', LS_OPEN = '([^']+)'", JS) == [
        ("md4agent.session", "md4agent.w", "md4agent.open")]
    assert "var SS_GOTO = 'md4agent.goto'" in JS
    # '읽던 자리' 핀은 메모리에만 산다 — 리로드하면 뷰어는 맨 위에서 다시 시작하므로 지난 생애의
    # 핀은 도움보다 혼란이다
    jb = JS[JS.index("// ---- '읽던 자리로'"):JS.index("// ---- 모델 고르기")]
    assert "Storage" not in jb and "lsSet" not in jb


# --- 설치 -----------------------------------------------------------------


class _Client:
    def __init__(self, connected: bool) -> None:
        self.has_socket_connection = connected
        self.js: list[str] = []

    def run_javascript(self, code: str) -> None:
        self.js.append(code)


def test_install_puts_css_html_script_once_and_registers_nav(monkeypatch):
    from nicegui import context, ui

    client = _Client(False)
    body: list[str] = []
    heads: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(type(context), "client", property(lambda self: client))
    monkeypatch.setattr(ui, "add_body_html", body.append)
    monkeypatch.setattr(ui, "add_head_html", heads.append)
    monkeypatch.setattr(ui, "on", lambda name, handler: events.append(name))

    agent_panel.install({})
    agent_panel.install({})   # CSS는 같은 창에 두 번 넣지 않는다 (HTML은 id로 브라우저가 거른다)
    assert sum(CSS in h for h in heads) == 1
    assert any(f'<div id="{agent_panel.PANEL_ID}"' in b for b in body)
    assert any(b.startswith("<script>") and "__md4AgentInit" in b for b in body)
    assert events == [agent_panel.NAV_EVENT] * 2


# --- '읽던 자리로' 오버레이 (옛 챗봇 서랍에서 옮김) ---------------------------


def test_jump_back_markup_sits_outside_the_drawer():
    """서랍 안에 두면 서랍을 닫는 순간 함께 사라진다 — 접고 본문만 읽는 사람에게도 복귀는 필요하다."""
    assert HTML.index('class="ag-foot"') < HTML.index('id="md-jump-back"')
    seg = HTML[HTML.index('<div id="md4-agent"'):HTML.index('<div id="md-jump-back"')]
    assert seg.count("<div") == seg.count("</div>")


def test_jump_back_constants_are_single_sourced():
    assert f'id="{agent_panel.JUMP_BACK_ID}"' in HTML
    assert f"#{agent_panel.JUMP_BACK_ID}" in CSS
    assert f"z-index: {agent_panel.JUMP_BACK_Z}" in CSS
    assert JS.count(f"window.{agent_panel.JUMP_BACK_API} =") == 1
    assert f"JB_EDGE = {agent_panel.JUMP_EDGE_PX}" in JS


def test_jump_back_z_index_between_find_bar_and_drawers():
    """알약은 본문 층 위, 서랍·찾기 바 아래여야 한다."""
    from md4paper.ui import find_bar

    find_z = max(int(v) for v in re.findall(r"z-index:\s*(\d+)", find_bar.CSS))
    assert 100 < agent_panel.JUMP_BACK_Z < min(agent_panel.PANEL_Z, find_z)


def test_jump_back_has_dark_mode_override():
    dark = CSS.split("@media (prefers-color-scheme: dark)")[1]
    assert "#md-jump-back" in dark and "background: #262626" in dark and "--mjb-ring" in dark


def test_jump_back_marks_before_every_programmatic_scroll():
    """무장이 스크롤 뒤로 가면 애니메이션 중간값을 집어 앵커가 목적지 쪽으로 끌려간다."""
    for head in ("function gotoRow(", "function gotoNote("):
        piece = _js_block(JS, head)
        assert piece.index("jbMark(") < piece.index("scrollIntoView(")


def test_jump_back_container_lookup_is_gated():
    """단독 `.sbs-grid`는 번역 탭 결과 그리드까지 잡는다 — [data-row]로 뷰어 것만 가려낸다."""
    assert "'.sbs-grid, .vpane'" in JS and "matches('[data-row]')" in JS


def test_jump_back_buttons_are_real_and_labelled():
    seg = HTML[HTML.index('<div id="md-jump-back"'):]
    assert seg.count('type="button"') == 2
    assert seg.count("aria-label=") == 2 and seg.count('aria-hidden="true"') == 1
    assert 'role="status"' in seg


def test_jump_back_thresholds_have_hysteresis_and_grace():
    arm = float(re.search(r"JB_ARM = ([\d.]+)", JS).group(1))
    home = float(re.search(r"JB_HOME = ([\d.]+)", JS).group(1))
    arm_min = int(re.search(r"JB_ARM_MIN = (\d+)", JS).group(1))
    home_min = int(re.search(r"JB_HOME_MIN = (\d+)", JS).group(1))
    assert arm > home and arm_min > home_min
    assert int(re.search(r"JB_GRACE = (\d+)", JS).group(1)) >= 500
    assert "Date.now() - jbJumpAt > JB_GRACE" in JS


def test_jump_back_flash_is_not_the_citation_flash():
    """"여기가 근거다"(파랑)와 "여기로 돌아왔다"(무채색)는 다른 말이다."""
    assert ".mjb-flash" in CSS and "@keyframes mdJumpFlash" in CSS and "'mjb-flash'" in JS
    assert ".chat-cite-flash" in CSS and "'chat-cite-flash'" in JS and "'md-anno-flash'" in JS
    assert "'wheel'" in JS and "{passive: false}" in JS


def test_escape_yields_to_other_overlays_then_pin_then_drawer():
    seg = JS[JS.index("document.addEventListener('keydown'"):]
    assert "#md-img-zoom.open" in seg and "#md-anno-pop.open" in seg and "#md4-find.on" in seg
    assert seg.index("jbDismiss()") < seg.index("panel.classList.remove('open')")
