"""에이전트 창 화면 — CSS/HTML/JS 문자열 계약 + 셸 설치.

창은 순수 클라이언트라 파이썬 로직이 얇다. 그래서 검증 표면은 (1) 셸에 남는 id·전역 API·이벤트
이름이 서버 쪽(app.py·spa·백엔드 칩 마크업)과 한 이름을 가리킨다는 것, (2) 레이아웃 규칙(본문 밀기·
층 순서), (3) 옛 서랍에서 옮겨 온 '읽던 자리로' 알약의 순서가 곧 동작인 몇 줄이다.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    assert "var paper = SA ? (SA.paper || null) : (window.__md4AgentTok || null);" in JS


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


def test_narrow_window_rules_exist_and_override_push():
    """좁은 창 — 덮기(ag-overlay)는 밀기 규칙 **뒤**에 와야 이긴다. 홈 열 접기·서랍 머리줄 줄이기도
    agent_panel CSS 안에 있다(home.py는 뷰포트만 보는 미디어 쿼리라 서랍이 민 폭을 모른다)."""
    push = CSS.index("body.ag-open .md4-body { padding-right: var(--ag-w); }")
    over = CSS.index("body.ag-open.ag-overlay .md4-body { padding-right: 0; }")
    assert push < over
    assert "body.ag-open.ag-overlay #md4-agent { box-shadow:" in CSS
    assert "body.ag-open.ag-narrow .md4-hprev { display: none; }" in CSS
    assert "ag-tight" not in CSS and "AG_TIGHT" not in JS   # 닿지 않던 사이드바 접기 규칙은 지웠다
    assert "#md4-agent.ag-slim .ag-cost { display: none; }" in CSS
    dark = CSS.split("@media (prefers-color-scheme: dark)")[1]
    assert "ag-overlay #md4-agent" in dark
    for cls in ("ag-overlay", "ag-narrow"):
        assert f"cl.toggle('{cls}'" in JS
    assert "panel.classList.toggle('ag-slim'" in JS


def test_narrow_window_constants_are_single_sourced():
    """경계값은 파이썬 상수 하나가 출처다 — JS의 var와 어긋나면 테스트가 잡는다."""
    ap = agent_panel
    assert (f"var AG_MIN = {ap.AG_MIN_PX}, AG_MAX = {ap.AG_MAX_PX}, "
            f"AG_KEEP = {ap.AG_KEEP_PX};") in JS
    assert (f"var AG_OVERLAY_BELOW = {ap.AG_OVERLAY_BELOW}, AG_NARROW_BELOW = {ap.AG_NARROW_BELOW}, "
            f"AG_SLIM_BELOW = {ap.AG_SLIM_BELOW};") in JS
    # 숫자는 그 한 줄에만 — 다른 곳에 경계값을 날것으로 다시 쓰지 않는다
    lay = _js_block(JS, "function syncLayout(")
    assert "AG_OVERLAY_BELOW" in lay and str(ap.AG_OVERLAY_BELOW) not in lay
    assert ap.AG_OVERLAY_BELOW < ap.AG_NARROW_BELOW and ap.AG_MIN_PX <= ap.AG_MAX_PX
    assert not hasattr(ap, "AG_TIGHT_BELOW")


def test_width_clamps_to_viewport_on_open_resize_and_drag():
    """기억한 폭을 그대로 쓰면 좁은 창에서 서랍이 본문을 덮는다 — 열 때(syncBody)·창 크기(resize)·
    드래그(setWidth) 모두 syncLayout을 거치고, localStorage엔 드래그 값만 쓴다."""
    assert "syncLayout();" in _js_block(JS, "function syncBody(")
    assert "window.addEventListener('resize', function(){ syncTop(); syncLayout(); });" in JS
    sw = _js_block(JS, "function setWidth(")
    assert "agCap()" in sw and "syncLayout();" in sw and "lsSet(LS_W" in sw
    assert "lsSet(" not in _js_block(JS, "function syncLayout(")
    assert "window.innerWidth - AG_KEEP" in _js_block(JS, "function agCap(")


def test_jump_back_pill_avoids_overlay_drawer():
    place = _js_block(JS, "function jbPlace(")
    assert "classList.contains('ag-overlay')" in place
    assert "panel.getBoundingClientRect().left" in place


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


# --- 승인 카드 (쓰기 tool) ---------------------------------------------------


def test_approval_card_markup_and_buttons():
    """호출마다 아이콘·설명·[승인]/[거절]·접힌 인자, 여럿이면 [모두 승인]/[모두 거절]. 답하면 결과로 잠긴다."""
    node = _js_block(JS, "function approvalNode(")
    assert "c.description ||" in _js_block(JS, "function apprText(")   # 없으면 이름+인자 요약
    for s in ("'ag-appr' + (open ? '' : ' done')", "material-icons ag-appr-i", "'ag-appr-d'",
              "['승인', true, 'ag-appr-ok'], ['거절', false, 'ag-appr-no']", "'ag-appr-args'",
              "row.classList.toggle('open')", "calls.length > 1", "['모두 승인', true], ['모두 거절', false]",
              "'승인됨'", "'거절됨'", "'건너뜀'"):
        assert s in node, s
    assert ".ag-appr-args { display: none;" in CSS and ".ag-appr-c.open .ag-appr-args { display: block; }" in CSS
    for cls in (".ag-appr.done", ".ag-appr-s.ok", ".ag-appr-s.no", ".ag-appr-s.skip", ".ag-appr-all"):
        assert cls + " {" in CSS, cls
    dark = CSS.split("@media (prefers-color-scheme: dark)")[1]
    for cls in (".ag-appr.done", ".ag-appr-s.ok", ".ag-appr-args", ".ag-cap"):
        assert cls in dark, cls
    # 쓰기 tool 넷은 사람 말 이름과 아이콘이 있다
    for name in ("write_note", "add_highlight", "set_project_membership", "remember"):
        assert f"{name}: '" in JS.split("var TOOL_KO = {", 1)[1].split("};", 1)[0]
        assert f"{name}: '" in JS.split("var TOOL_ICON = {", 1)[1].split("};", 1)[0]


def test_approvals_are_posted_once_every_call_has_an_answer():
    """서버는 대기 호출 전부의 답을 한 번에 받는다 — 다 모이면 같은 turns 주소로 approvals를 보낸다."""
    dec = _js_block(JS, "function decide(")
    assert "calls.every(function(x){ return x.id in decisions; })" in dec
    assert "send('', ap)" in dec
    snd = _js_block(JS, "function send(")
    assert "body = {message: q, context: ctxNow()};" in snd
    assert "if (approvals) body.approvals = approvals;" in snd
    assert "'/agent/sessions/' + encodeURIComponent(at) + '/turns'" in snd


def test_pending_card_locks_into_history_and_send_skips_it():
    """보내면 카드는 마지막 턴 뒤에 잠긴 기록으로 남고(그냥 새 질문 = 건너뜀), 스트림 전 거절이면 되살린다."""
    snd = _js_block(JS, "function send(")
    assert "skipped: !approvals" in snd and "apprByTurn[key]" in snd
    assert snd.index("pending = null; decisions = {};") < snd.index("fetch(")
    assert "pending = was;" in snd                       # 409·키 없음·비용 상한 → 카드는 아직 기다린다
    assert "apprDone(wrap, t.id);" in _js_block(JS, "function turnNode(")
    # 승인 재개가 다시 대기로 끝나면 그 사이 tool 줄을 질문 턴에 남긴다(같은 카드가 또 뜬 까닭)
    assert "var resumed = (!L.q && L.tools.length) ? L.tools : null;" in snd
    assert "toolsByTurn['u:' + t.id]" in _js_block(JS, "function turnNode(")


def test_pending_keeps_input_open_with_wait_placeholder():
    rend = _js_block(JS, "function render(")
    assert "input.disabled = busy || !modelInfo.ready;" in rend      # 대기 중에도 그냥 보낼 수 있다
    assert "input.placeholder = pending ? PH_WAIT : PH_IDLE;" in rend
    assert "var PH_WAIT = '승인을 기다리는 중 — 그냥 보내면 건너뜁니다';" in JS
    # 진행 중 말풍선 아래에, 그리고 세션을 다시 열면 서버의 pending으로 카드가 되살아난다
    assert rend.index("liveNode()") < rend.index("approvalNode(pending, true)")
    assert "pending = d.pending || null;" in _js_block(JS, "function setSession(")
    on = _js_block(JS, "function onEvent(")
    assert on.index("name === 'approval_needed'") < on.index("render();", on.index("approval_needed"))


def test_approval_flow_matches_backend_contract():
    """세션 상세는 pending을 싣고, turns는 approvals 키를 받는다(백엔드와 한 이름)."""
    import inspect

    from md4paper.ui.agent import routes

    src = inspect.getsource(routes)
    assert '"pending"' in src and 'body.get("approvals")' in src


# --- 쓰기 뒤 화면 갱신 (ui_action refresh) --------------------------------------


def test_refresh_action_branches():
    assert "a.kind === 'refresh'" in _js_block(JS, "function doAction(")
    rv = _js_block(JS, "function refreshView(")
    for what in ("library", "notes", "highlights"):
        assert f"what === '{what}'" in rv
    # 메모·하이라이트는 지금 뷰어의 논문일 때만 (다른 논문이면 열 때 새로 읽는다)
    assert "if (!tok || tok !== paper || !onReview()) return;" in rv
    assert rv.index("what === 'library'") < rv.index("tok !== paper")
    assert "emitEvent('md4-agent-nav', {url: '/home', refresh: true})" in rv


def test_refresh_hooks_match_app_names():
    """창이 부르는 이름이 뷰어(app.py)가 내놓는 이름과 같다 — 어긋나면 갱신이 조용히 죽는다."""
    from pathlib import Path

    app_src = Path(agent_panel.__file__).with_name("app.py").read_text(encoding="utf-8")
    rv = _js_block(JS, "function refreshView(")
    assert f"emitEvent('{agent_panel.NOTES_REFRESH_EVENT}', {{token: tok}})" in rv
    assert "ui.on(agent_panel.NOTES_REFRESH_EVENT, on_agent_refresh)" in app_src
    assert f"window.{agent_panel.ANNO_RELOAD_API}(tok, j.items || [])" in rv
    assert "'/annotations/' + encodeURIComponent(tok)" in rv
    assert f"window.{agent_panel.ANNO_RELOAD_API} = function(tok, list){{" in app_src
    # 다시 칠하기는 flush하지 않는다 — 화면의 옛 목록이 방금 더한 항목을 파일에서 지우지 않게
    hook = app_src.split(f"window.{agent_panel.ANNO_RELOAD_API} = function", 1)[1].split("};", 1)[0]
    assert "flush()" not in hook and "tok !== window.__mdAnnoTok" in hook


def test_notes_refresh_handler_guards_and_defers_to_conflict_path():
    """옛 화면의 핸들러가 쌓여도 지금 논문·살아 있는 편집기에만, 그리고 capture()로 기존 충돌 안내를 탄다."""
    import inspect

    from md4paper.ui import app

    src = inspect.getsource(app.build)
    body = src.split("async def on_agent_refresh(", 1)[1].split("ui.on(agent_panel.NOTES_REFRESH_EVENT", 1)[0]
    assert 'args.get("token") != tok' in body and "box.is_deleted" in body
    assert body.index("await capture()") < body.index("mount(i)")
    assert 'notes_state["conflicts"]' in body and "asking" in body
    assert "notes_io.stamp(ctrl.wd" in body and "note_tabs.refresh()" in body


# --- 비용 상한 ---------------------------------------------------------------


def test_cost_cap_warning_and_override_retry():
    snd = _js_block(JS, "function send(")
    assert "er.code = j.code || ''" in snd and "sess.cost_cap = e.cap" in snd
    assert "e.code === 'cost_cap'" in snd and "capRetry = {body: body, msg: m};" in snd
    assert "body.override = true;" in snd                      # 다시 보낼 때 같은 본문 + override
    cap = _js_block(JS, "function capNode(")
    assert "'그래도 계속'" in cap and "send(null, null, r.body)" in cap
    assert "if (capRetry) list.appendChild(capNode());" in _js_block(JS, "function render(")
    head = _js_block(JS, "function renderHead(")
    assert "sess.cost_cap" in head and "' / $' + cap.toFixed(2)" in head
    assert ".ag-cost.over {" in CSS


def test_home_refresh_rebuilds_sub_pages_only_when_home_is_shown(monkeypatch):
    """같은 주소 이동은 sub_pages가 무시한다 — refresh는 지금 홈을 보여 주는 SubPages만 다시 짓는다."""
    from nicegui import context, ui
    from nicegui.elements.sub_pages import SubPages

    class _SP(SubPages):
        def __init__(self, path: str) -> None:  # noqa: D107 — 진짜 요소를 짓지 않는 대역
            self._rendered_path = path
            self.n = 0

        def refresh(self) -> None:
            self.n += 1

    home, review = _SP("/home"), _SP("/review")

    class _C:
        elements = {1: home, 2: review}

    monkeypatch.setattr(type(context), "client", property(lambda self: _C()))
    went: list[str] = []
    monkeypatch.setattr(ui.navigate, "to", went.append)
    agent_panel._nav("/home", refresh=True)
    agent_panel._nav("https://evil.example/", refresh=True)
    assert (home.n, review.n, went) == (1, 0, [])


# --- 비용은 머리 한 곳에만 ----------------------------------------------------


def test_money_shows_only_in_head_session_total():
    """쓴 돈은 머리의 세션 누적(상한 있으면 '/ 상한') 한 곳만 — 모델 단가·턴별 비용·작업 비용·토큰은 없다."""
    assert "m.price" not in JS                         # 드롭다운에 단가를 붙이지 않는다
    assert "(m.has_key ? '' : '  (키 없음)')" in JS     # 키 없음 표시는 남는다
    assert "money(" not in JS                          # 작업 완료 줄의 ', $0.0016'
    turn = _js_block(JS, "function turnNode(")
    assert "cost_usd" not in turn and "toFixed" not in turn
    assert "t.model !== modelLabel()" in turn           # 모델 이름은 지금 모델과 다를 때만
    job = _js_block(JS, "function jobNode(")
    assert "j.message" not in job and "j.label" in job                      # jobtools 문구엔 '(≈$…)'가 붙어 있다
    for word in ("usage.input", "usage.output", "tokens"):
        assert word not in JS
    # 남은 한 곳 — 머리의 세션 누적
    assert JS.count("toFixed(4)") == 1 and "costEl.textContent = '$' + c.toFixed(4)" in JS


# --- 새 창으로 (단독 창 /agent) ------------------------------------------------


_APP_SRC = (Path(agent_panel.__file__).with_name("app.py")).read_text(encoding="utf-8")


def test_standalone_page_is_registered_apart_from_shell():
    """단독 창은 셸(shell)과 다른 페이지 함수여야 새 문서로 뜬다 — 셸 경로 묶음에 끼면 헤더·화면이 붙는다."""
    assert agent_panel.STANDALONE_PATH == "/agent"
    page = _APP_SRC[_APP_SRC.index("@ui.page(agent_panel.STANDALONE_PATH"):]
    page = page[:page.index("\n\n")]
    assert "def agent_window(session: str = \"\", paper: str = \"\")" in page
    assert "agent_panel.standalone_page(state, session, paper, native=desktop.active())" in page
    shell = _APP_SRC[_APP_SRC.index('@ui.page("/")'):_APP_SRC.index("def shell()")]
    assert "/agent" not in shell and "STANDALONE_PATH" not in shell


def test_popout_event_and_path_match_js_and_app():
    """JS가 내보내는 이벤트·주소가 install의 ui.on·app.py의 형제 창 경로와 한 이름이다."""
    assert f"emitEvent('{agent_panel.POPOUT_EVENT}'" in JS
    assert f"POPOUT_PATH = '{agent_panel.STANDALONE_PATH}'" in JS
    assert f"SA_POLL_MS = {agent_panel.SA_POLL_MS}" in JS
    assert f'href="{agent_panel.STANDALONE_PATH}" target="_blank"' in HTML   # 브라우저 모드는 진짜 링크
    assert "open_in_new" in HTML
    # popHref()와 같은 꼴
    assert agent_panel.popout_path("s1a2b3c", "tok9") == "/agent?session=s1a2b3c&paper=tok9"
    assert agent_panel.popout_path("s1a2b3c") == "/agent?session=s1a2b3c"
    assert agent_panel.popout_path() == "/agent"
    ph = _js_block(JS, "function popHref(")
    assert "'session=' + encodeURIComponent(sid)" in ph and "'paper=' + encodeURIComponent(paper)" in ph
    # 앱 창 모드 — app.py가 형제 웹뷰를 띄우는 함수를 넘긴다(새 창 단추와 같은 _spawn_path)
    assert "agent_panel.install(state, popout=" in _APP_SRC and "if desktop.active() else None" in _APP_SRC
    assert "await _spawn_path(_review_url(root), state, title" in _APP_SRC


def test_popout_hands_over_and_closes_this_drawer():
    """같은 세션을 두 창이 함께 쓰면 마지막 저장이 이긴다 → 새 창을 열면 이 창의 서랍은 닫는다."""
    i = JS.index("if (popBtn) popBtn.addEventListener('click'")
    block = JS[i:JS.index("// ---- 단독 창", i)]
    assert "popBtn.setAttribute('href', popHref());" in block       # 클릭 순간 지금 세션으로
    assert "ev.preventDefault();" in block and "window.__md4AgentNative" in block   # 앱 창만 서버로
    assert "setTimeout(closePanel, 0);" in block
    # 다시 열면 세션을 새로 읽는다 — 단독 창에서 쌓인 턴
    op = _js_block(JS, "function openPanel(")
    assert "if (!busy) refreshSession();" in op


def test_install_registers_popout_only_in_app_window_mode(monkeypatch):
    from nicegui import context, ui

    client = _Client(False)
    events: dict = {}
    body: list[str] = []
    monkeypatch.setattr(type(context), "client", property(lambda self: client))
    monkeypatch.setattr(ui, "add_body_html", body.append)
    monkeypatch.setattr(ui, "add_head_html", lambda *_: None)
    monkeypatch.setattr(ui, "on", lambda name, handler: events.setdefault(name, handler))

    agent_panel.install({})
    assert agent_panel.POPOUT_EVENT not in events
    assert any("window.__md4AgentNative = false;" in b for b in body)

    paths: list[str] = []
    events.clear()
    agent_panel.install({}, popout=paths.append)
    assert set(events) == {agent_panel.NAV_EVENT, agent_panel.POPOUT_EVENT}
    assert any("window.__md4AgentNative = true;" in b for b in body)

    class _E:
        args = {"session": "s0a1b2c3", "paper": "../x"}   # 형식에 안 맞는 값은 싣지 않는다
    events[agent_panel.POPOUT_EVENT](_E())
    assert paths == ["/agent?session=s0a1b2c3"]


def test_standalone_mode_css_hides_drawer_chrome():
    """단독 창 — 서랍을 화면 전체로 펴고 닫기·너비 드래그·새 창 단추·'읽던 자리로' 알약은 숨긴다."""
    assert agent_panel.STANDALONE_CLASS == "ag-standalone"
    assert "document.body.classList.add('ag-standalone')" in JS
    r = _rule(CSS, "body.ag-standalone #md4-agent")
    assert "top: 0" in r and "left: 0" in r and "width: auto" in r
    hide = CSS.split("body.ag-standalone .ag-handle,", 1)[1].split("}", 1)[0]
    for sel in (".ag-x", ".ag-pop", "#md-jump-back"):
        assert f"body.ag-standalone {sel}" in hide
    assert "display: none !important" in hide
    assert "--ag-top: 0px" in _rule(CSS, "body.ag-standalone")


def test_standalone_js_guards_shared_state_and_navigation():
    """단독 창이 메인 창의 localStorage(열림)를 건드리지 않고, Esc·✕로 비지 않고, 화면 이동은 안내로."""
    assert "if (!SA) lsSet(LS_OPEN" in _js_block(JS, "function syncBody(")
    assert "if (!SA) panel.classList.remove('open')" in JS                     # closePanel
    assert "if (!SA && panel.classList.contains('open')) panel.classList.remove('open');" in JS  # Esc
    nv = _js_block(JS, "function nav(")
    assert nv.index("if (SA){") < nv.index("emitEvent('md4-agent-nav'")
    assert "notice = {url: url}" in nv
    # 새 탭은 칩 클릭(사용자 제스처)에서만 — 답이 흐르다 온 open_paper·goto_row는 안내 줄
    assert "(gesture && !window.__md4AgentNative) ? window.open(url, '_blank') : null" in nv
    assert "c.url, true)" in _js_block(JS, "function wireChips(")
    da = _js_block(JS, "function doAction(")
    assert "gotoElsewhere(a.token, a.row, null, a.url);" in da and "nav(a.url);" in da
    assert "메인 창에서 열어 주세요" in _js_block(JS, "function noticeNode(")
    assert "if (SA) return;" in _js_block(JS, "function refreshView(")
    ctx = _js_block(JS, "function ctxNow(")
    assert "screen: 'window'" in ctx
    assert "(SA && SA.session)" in _js_block(JS, "function ensureSession(")


def test_standalone_poll_redraws_only_on_change():
    poll = JS[JS.index("function saPoll("):JS.index("var wasOpen")]
    assert "busy || live" in poll and "document.hidden" in poll
    assert "sessSig(d.turns, d.pending, d.title, d.jobs)" in poll and "setSession(d)" in poll
    assert "setInterval(saPoll, SA_POLL_MS)" in JS


def test_standalone_page_drops_unknown_paper_and_bad_session(monkeypatch, tmp_path):
    from nicegui import ui

    body: list[str] = []
    titles: list[str] = []
    monkeypatch.setattr(ui, "add_body_html", body.append)
    monkeypatch.setattr(ui, "add_css", lambda *_: None)
    monkeypatch.setattr(ui, "page_title", titles.append)

    agent_panel.standalone_page({"wd_by_token": {}}, "s0a1b2c3", "nope")
    flags = next(b for b in body if "__md4AgentStandalone" in b)
    assert '"session": "s0a1b2c3"' in flags and '"paper": null' in flags
    assert titles == [agent_panel.WINDOW_TITLE]

    class _WD:
        root = tmp_path / "paper.md4"
    (tmp_path / "paper.md4" / "structure").mkdir(parents=True)
    (tmp_path / "paper.md4" / "structure" / "sections.yaml").write_text("title: A </script> Paper\n")
    body.clear()
    titles.clear()
    agent_panel.standalone_page({"wd_by_token": {"tokA": _WD()}}, "bad id!", "tokA")
    flags = next(b for b in body if "__md4AgentStandalone" in b)
    assert '"session": ""' in flags and '"paper": "tokA"' in flags
    assert "</script> Paper" not in flags                     # 제목이 스크립트를 닫지 못한다
    assert titles and titles[0].startswith(agent_panel.WINDOW_TITLE + " — A")
    assert any(b.startswith("<script>") and "__md4AgentInit" in b for b in body)
    assert body.index(flags) < next(i for i, b in enumerate(body) if "__md4AgentInit" in b)


def test_answer_markdown_restores_list_markers_and_heading_weight():
    """앱의 Tailwind 리셋(list-style:none, h* font-weight:inherit)을 답 안에서 되살린다 —
    없으면 섹션 제목이 본문처럼, 목록이 기호 없는 들여쓴 문단처럼 보인다."""
    css = agent_panel.CSS
    assert ".ag-a ul { list-style: disc; }" in css
    assert ".ag-a ol { list-style: decimal; }" in css
    assert re.search(r"\.ag-a h1, \.ag-a h2, \.ag-a h3[^{]*\{[^}]*font-weight: 700", css)
