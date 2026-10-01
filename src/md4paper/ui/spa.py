"""한 문서 안에서 화면 갈아 끼우기 — 탭 전환 깜빡임 없애기.

탭(홈·논문)을 오갈 때마다 새 페이지를 열면 브라우저가 문서를 통째로 버리고 다시 그린다. 그 사이
첫 페인트는 헤더 탭도 본문도 없는 빈 화면이라, 아무리 미리 칠해도 '반짝'이 남는다. 그래서 셸 페이지
하나를 띄워 두고(헤더는 그대로) 본문만 `ui.sub_pages`로 서버에서 다시 지어 한 번에 바꿔 끼운다 —
옛 화면이 새 화면으로 한 프레임에 넘어가므로 빈 화면이 끼어들 틈이 없다.

그러려면 화면이 페이지에 '한 번만' 하던 일들을 갈아 끼우기에도 맞게 해야 한다:

- **헤더**: `ui.header`는 페이지 최상위에만 만들 수 있어 하위 화면 안에서는 못 만든다 → 셸이 하나
  만들어 두고, 각 화면은 `header()`로 비워 받아 채운다.
- **스크립트**: NiceGUI는 연결된 뒤의 `add_body_html`을 `insertAdjacentHTML`로 넣는데, 그렇게 넣은
  `<script>`는 실행되지 않는다 → `add_body_html()`이 스크립트를 새 요소로 만들어 실행하고, id가
  이미 있는 요소(툴팁·서랍 같은 오버레이)는 다시 넣지 않는다.
- **CSS**: 화면을 지을 때마다 같은 스타일이 쌓이지 않게 한 번만 넣는다.
- **논문별 클라이언트 상태**(하이라이트·채팅·스크롤 기억)는 각 스크립트가 '논문 바꾸기' 훅을 둔다.
"""

from __future__ import annotations

import hashlib
import json

# 갈아 끼울 때 넣는 HTML을 처리하는 함수 — 셸 head에 한 번 싣는다.
HEAD_HTML = """
<script>
window.__md4AddHtml = function(html){
  var t = document.createElement('template'); t.innerHTML = html;
  var app = document.getElementById('app');
  Array.prototype.slice.call(t.content.childNodes).forEach(function(n){
    if (n.nodeType !== 1) return;
    if (n.tagName === 'SCRIPT'){            // innerHTML로 들어간 스크립트는 안 돈다 → 새로 만들어 실행
      var s = document.createElement('script');
      s.textContent = n.textContent;
      document.body.appendChild(s);
      s.remove();
      return;
    }
    if (n.id && document.getElementById(n.id)) return;   // 이미 얹힌 오버레이는 그대로 쓴다
    app.parentNode.insertBefore(n, app);
  });
};
</script>
"""

_HEADER_ATTR = "_md4_header"
_CSS_ATTR = "_md4_css"


def header():  # noqa: ANN201 — ui.header
    """이 화면이 채울 헤더 — 셸이 만들어 둔 것을 비워서 준다(셸 없이 지은 페이지면 새로 만든다)."""
    from nicegui import context, ui

    client = context.client
    hdr = getattr(client, _HEADER_ATTR, None)
    if hdr is None or hdr.is_deleted:
        hdr = ui.header().classes("md4-header items-end no-wrap")
        setattr(client, _HEADER_ATTR, hdr)
    else:
        hdr.clear()
    return hdr


def make_header() -> None:
    """셸 페이지가 부른다 — 화면들이 함께 쓸 헤더를 페이지 최상위에 미리 만든다."""
    from nicegui import context, ui

    setattr(context.client, _HEADER_ATTR, ui.header().classes("md4-header items-end no-wrap"))


def add_css(css: str) -> None:
    """스타일을 넣는다 — 같은 창(클라이언트)에 이미 넣은 것이면 건너뛴다."""
    from nicegui import context, ui

    client = context.client
    seen: set = getattr(client, _CSS_ATTR, None) or set()
    key = hashlib.sha1(css.encode("utf-8")).hexdigest()
    if key in seen:
        return
    seen.add(key)
    setattr(client, _CSS_ATTR, seen)
    ui.add_css(css)


def add_body_html(code: str) -> None:
    """본문 HTML(오버레이·스크립트)을 넣는다 — 처음 그리는 페이지면 그대로, 갈아 끼우는 중이면
    스크립트를 실제로 실행하고 이미 있는 id 요소는 다시 넣지 않는다."""
    from nicegui import context, ui

    client = context.client
    if not client.has_socket_connection:
        ui.add_body_html(code)
        return
    client.run_javascript(f"window.__md4AddHtml && window.__md4AddHtml({json.dumps(code)});")
