"""읽던 자리 기억 — 논문·단계별 스크롤 위치를 창(세션) 안에 저장하고 되돌린다.

헤더 탭으로 다른 논문에 갔다 오면 페이지가 새로 그려진다(탭 = 페이지 이동). 단계 탭
(변환/번역/뷰어)도 패널을 갈아 끼우므로 그때마다 스크롤이 맨 위로 튄다. 논문을 오가며 읽는
도구에서 "읽던 자리"가 사라지는 건 곧 흐름이 끊긴다는 뜻이라, 스크롤 상자마다 위치를 적어 둔다.

- 저장 위치는 `sessionStorage`다: 창마다 따로 기억하고(창을 나란히 띄워도 서로 안 섞인다),
  앱을 닫으면 사라진다. 서버(status.json)에 적지 않는 이유 — 읽던 자리는 그 창의 사정이다.
- 열쇠는 `논문 토큰 → 단계 → 스크롤 상자`다. 상자는 클래스 셀렉터 + 같은 셀렉터 안 순번으로
  가리킨다(뷰어의 목차·본문·PDF는 각자 다른 상자다).
- 우리가 되돌리는 동안의 scroll 이벤트는 저장하지 않는다 — 복원값이 0을 덮어써 버리지 않게.
"""

from __future__ import annotations

import json

# 위치를 기억할 스크롤 상자들 (클래스로 안정적으로 가리켜지는 것만)
SELECTORS = (
    ".conv-md",     # 변환 탭 마크다운
    ".sbs-grid",    # 뷰어 정렬 그리드 (원문·번역)
    ".vpane",       # 뷰어 단일 패널 (정렬 불가 시)
    ".vtoc",        # 뷰어 목차
    ".vpdf",        # 뷰어 PDF
    ".md4-scroll",  # 그 외 우리가 표시해 둔 스크롤 열 (설정 열·논문 목록 등)
)

_JS = """
(function(){
  if (window.__mdScrollMem) return; window.__mdScrollMem = true;
  var PAPER = %s, SELS = %s;
  var PREFIX = 'md4:pos:' + PAPER + ':';
  var quiet = 0, timer = null, tries = 0, moved = false;

  function stepKey(){                        // 지금 단계 탭 (없으면 홈처럼 단계가 없는 화면)
    var t = document.querySelector('.q-tab--active');
    return t ? (t.innerText || '').trim().split('\\n').pop() : '-';
  }
  function boxes(){                          // [{key, el}] — 셀렉터 + 같은 셀렉터 안 순번
    var out = [];
    SELS.forEach(function(sel){
      var list = document.querySelectorAll(sel);
      for (var i = 0; i < list.length; i++) out.push({key: sel + '#' + i, el: list[i]});
    });
    return out;
  }
  function save(){
    if (Date.now() < quiet) return;          // 복원 직후의 scroll 이벤트는 우리 것이다
    var pos = {w: window.scrollY || 0};
    boxes().forEach(function(b){ if (b.el.scrollTop > 0) pos[b.key] = b.el.scrollTop; });
    try { sessionStorage.setItem(PREFIX + stepKey(), JSON.stringify(pos)); } catch (e) {}
  }
  function restore(){
    var raw = null;
    try { raw = sessionStorage.getItem(PREFIX + stepKey()); } catch (e) {}
    if (!raw) return false;
    var pos; try { pos = JSON.parse(raw); } catch (e) { return false; }
    var hit = false;
    quiet = Date.now() + 500;
    boxes().forEach(function(b){
      var want = pos[b.key];
      if (!want) return;
      var max = b.el.scrollHeight - b.el.clientHeight;
      if (max <= 0) return;                  // 아직 내용이 안 실렸다 → 다음 시도에
      b.el.scrollTop = Math.min(want, max);
      hit = true;
    });
    if (pos.w) window.scrollTo(0, pos.w);
    return hit;
  }
  function restoreSoon(){                    // 렌더·이미지 로딩으로 높이가 커지므로 몇 번 더 두드린다
    tries = 0; moved = false;
    (function attempt(){
      if (moved) return;                     // 사용자가 이미 움직였다 → 그 자리를 존중한다
      restore();
      if (++tries < 6) setTimeout(attempt, 120 * tries);
    })();
  }

  // 사용자가 직접 움직이면 복원 재시도를 멈추고 저장도 막지 않는다 (자기가 고른 자리가 이긴다)
  ['wheel', 'touchmove', 'keydown', 'mousedown'].forEach(function(ev){
    window.addEventListener(ev, function(){ moved = true; quiet = 0; }, true);
  });
  window.addEventListener('scroll', function(){
    clearTimeout(timer); timer = setTimeout(save, 300);
  }, true);                                  // 캡처 — 내부 스크롤 상자의 스크롤까지 잡는다
  window.addEventListener('pagehide', save);
  document.addEventListener('visibilitychange', function(){ if (document.hidden) save(); });
  // 단계 탭을 누르면 패널이 갈아 끼워진다 → 누르기 전 자리 저장, 새 패널이 붙은 뒤 그 단계 자리 복원
  document.addEventListener('click', function(ev){
    if (ev.target.closest && ev.target.closest('.q-tab')) { save(); setTimeout(restoreSoon, 60); }
  }, true);
  if (document.readyState === 'complete') restoreSoon();
  else window.addEventListener('load', restoreSoon);
})();
"""


def init_js(paper_key: str) -> str:
    """이 논문(또는 화면)의 스크롤 기억 스크립트."""
    return _JS % (json.dumps(paper_key), json.dumps(list(SELECTORS)))


def install(paper_key: str) -> None:
    """현재 페이지에 스크롤 기억을 얹는다."""
    from nicegui import ui

    ui.add_body_html(f"<script>{init_js(paper_key)}</script>")
