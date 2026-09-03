"""페이지 안 찾기 바 (Cmd/Ctrl+F) — 앱 창에는 브라우저의 찾기 UI가 없다.

`ui.run(native=True)`로 뜨는 웹뷰(WKWebView·WebView2·WebKitGTK)에는 주소창도 찾기 바도 없어서
Cmd+F를 눌러도 아무 일이 일어나지 않는다. 논문을 읽는 도구에서 본문 검색이 없는 건 곤란하므로
같은 동작을 직접 만든다(브라우저 모드에서도 이 바가 뜬다 — 두 모드의 조작이 갈리지 않게).

**DOM을 건드리지 않는다.** `<mark>`를 심는 흔한 구현은 뷰어의 하이라이트·메모가 붙는 평문
오프셋(§annotations)을 밀어 버린다. 대신 CSS Custom Highlight API(`CSS.highlights`)로 Range에만
색을 얹는다 — 텍스트 노드가 그대로 남아 주석 좌표가 어긋나지 않는다. 이 API가 없는 오래된
웹뷰에서는 브라우저 내장 `window.find()`로 물러선다(전체 개수는 못 세지만 이동은 된다).

숨어 있는 탭 패널(Quasar는 패널을 DOM에 남겨 둔다)의 글자는 건너뛴다 — 보이지 않는 곳으로
스크롤해 봐야 사용자에게는 '아무 일도 안 일어남'이라서.
"""

from __future__ import annotations

CSS = """
#md4-find { position: fixed; top: 8px; right: 14px; z-index: 10050; display: none;
  align-items: center; gap: 6px; padding: 6px 8px; border-radius: 10px;
  background: #fff; color: #222; box-shadow: 0 6px 24px rgba(0,0,0,.22);
  border: 1px solid rgba(0,0,0,.12); font-size: 13px; }
#md4-find.on { display: flex; }
#md4-find input { width: 190px; border: 1px solid #d6d6d6; border-radius: 6px; padding: 4px 7px;
  font-size: 13px; background: #fff; color: #222; outline: none; }
#md4-find input:focus { border-color: #2383e2; }
#md4-find .md4f-count { min-width: 54px; text-align: center; color: #888; font-variant-numeric: tabular-nums; }
#md4-find .md4f-count.none { color: #d33; }
#md4-find button { border: 0; background: transparent; color: #444; cursor: pointer; padding: 2px 5px;
  border-radius: 6px; font-size: 14px; line-height: 1; }
#md4-find button:hover { background: rgba(0,0,0,.08); }
/* Custom Highlight API — DOM에 요소를 넣지 않고 Range에만 색을 얹는다 */
::highlight(md4-find) { background: #ffe9a3; color: #111; }
::highlight(md4-find-cur) { background: #ff9d24; color: #111; }
@media (prefers-color-scheme: dark) {
  #md4-find { background: #2a2a2a; color: #eee; border-color: rgba(255,255,255,.14); }
  #md4-find input { background: #1f1f1f; color: #eee; border-color: #4a4a4a; }
  #md4-find button { color: #ddd; }
  #md4-find button:hover { background: rgba(255,255,255,.12); }
  ::highlight(md4-find) { background: #7a5c00; color: #fff; }
  ::highlight(md4-find-cur) { background: #ff9d24; color: #111; }
}
"""

HTML = """
<div id="md4-find" role="search">
  <input type="search" placeholder="페이지에서 찾기" spellcheck="false" autocomplete="off">
  <span class="md4f-count"></span>
  <button data-act="prev" title="이전 (Shift+Enter)">&#8593;</button>
  <button data-act="next" title="다음 (Enter)">&#8595;</button>
  <button data-act="close" title="닫기 (Esc)">&#10005;</button>
</div>
<script>
(function(){
  if (window.__mdFind) return; window.__mdFind = true;
  var bar = document.getElementById('md4-find');
  if (!bar) return;
  var input = bar.querySelector('input'), countEl = bar.querySelector('.md4f-count');
  var HL = window.Highlight && window.CSS && CSS.highlights;   // 없으면 window.find()로 물러선다
  // 인라인 태그는 문단을 쪼개지 않는다 — <em>으로 끊긴 낱말도 한 낱말로 찾히게.
  var INLINE = {A:1,ABBR:1,B:1,BR:1,CITE:1,CODE:1,EM:1,I:1,KBD:1,LABEL:1,MARK:1,Q:1,S:1,SMALL:1,
                SPAN:1,STRONG:1,SUB:1,SUP:1,TIME:1,U:1,WBR:1};
  var ranges = [], cur = -1, query = '', stale = true;

  function blockOf(el){ while (el && INLINE[el.tagName]) el = el.parentElement; return el; }

  function collect(){                      // 보이는 텍스트 노드 → 이어붙인 평문 + 위치표
    var nodes = [], starts = [], text = '', prevBlock = null;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function(n){
        if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
        var p = n.parentElement;
        // 앱 크롬(헤더 탭·찾기 바)은 건너뛴다 — 늘 붙어 있어 스크롤할 자리가 없고 개수만 부풀린다.
        if (!p || p.closest('#md4-find, header, script, style, noscript, textarea')) return NodeFilter.FILTER_REJECT;
        // 숨은 탭 패널·접힌 패널: display 뿐 아니라 visibility·opacity로 감춘 것도 제외한다.
        var shown = p.checkVisibility
          ? p.checkVisibility({visibilityProperty: true, opacityProperty: true})
          : p.getClientRects().length > 0;
        if (!shown) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    for (var n; (n = walker.nextNode());){
      var block = blockOf(n.parentElement);
      if (prevBlock && block !== prevBlock) text += '\\n';   // 문단 경계를 넘어 붙는 오검출 방지
      prevBlock = block;
      nodes.push(n); starts.push(text.length); text += n.nodeValue;
    }
    return {nodes: nodes, starts: starts, text: text};
  }

  function locate(doc, idx){               // 평문 오프셋 → (텍스트 노드, 노드 안 오프셋)
    var lo = 0, hi = doc.starts.length - 1, at = 0;
    while (lo <= hi){                      // starts는 오름차순 → 이분 탐색
      var mid = (lo + hi) >> 1;
      if (doc.starts[mid] <= idx){ at = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return {node: doc.nodes[at], offset: idx - doc.starts[at]};
  }

  function build(q){
    ranges = []; stale = false;
    if (!q) return;
    var doc = collect(), hay = doc.text.toLowerCase(), needle = q.toLowerCase(), from = 0;
    while (true){
      var i = hay.indexOf(needle, from);
      if (i < 0) break;
      from = i + needle.length;
      var a = locate(doc, i), b = locate(doc, from - 1);
      try {
        var r = document.createRange();
        r.setStart(a.node, Math.min(a.offset, a.node.nodeValue.length));
        r.setEnd(b.node, Math.min(b.offset + 1, b.node.nodeValue.length));
        ranges.push(r);
      } catch (e) { /* 그 사이 DOM이 바뀐 노드는 건너뛴다 */ }
      if (ranges.length >= 2000) break;    // 흔한 낱말('the')에 페이지가 멈추지 않도록
    }
  }

  function paint(){
    if (!HL) return;
    CSS.highlights.set('md4-find', newHighlight(ranges));
    var one = newHighlight(cur >= 0 && ranges[cur] ? [ranges[cur]] : []);
    one.priority = 1;                      // 현재 결과가 전체 결과 색 위로 오도록
    CSS.highlights.set('md4-find-cur', one);
  }
  function newHighlight(list){ var h = new Highlight(); list.forEach(function(r){ h.add(r); }); return h; }

  function clearPaint(){
    if (!HL) return;
    CSS.highlights.delete('md4-find'); CSS.highlights.delete('md4-find-cur');
  }

  function scrollToCur(){
    var r = ranges[cur];
    if (!r) return;
    var rect = r.getBoundingClientRect();
    var el = r.startContainer.parentElement;
    for (var e = el; e && e !== document.body; e = e.parentElement){   // 가장 가까운 스크롤 상자를 중앙으로
      var st = getComputedStyle(e);
      if (!/(auto|scroll|overlay)/.test(st.overflowY) || e.scrollHeight <= e.clientHeight + 1) continue;
      var box = e.getBoundingClientRect();
      e.scrollTop += (rect.top + rect.height / 2) - (box.top + box.height / 2);
      break;
    }
    rect = r.getBoundingClientRect();      // 스크롤 후 위치로 다시 — 창 스크롤이 더 필요한지 본다
    var margin = 90;
    if (rect.top < margin || rect.bottom > innerHeight - 40)
      scrollBy({top: rect.top - innerHeight / 2, behavior: 'smooth'});
  }

  function show(n){
    countEl.classList.toggle('none', !ranges.length);
    countEl.textContent = !query ? '' : (ranges.length ? (n + 1) + ' / ' + ranges.length : '결과 없음');
  }

  function step(dir){
    var q = input.value.trim();
    if (!q){ query = ''; ranges = []; cur = -1; clearPaint(); show(0); return; }
    if (!HL){                              // 물러선 길: 브라우저 내장 찾기 (개수는 못 센다)
      query = q;
      countEl.classList.remove('none');
      countEl.textContent = window.find(q, false, dir < 0, true) ? '' : '결과 없음';
      return;
    }
    var isNew = q !== query;
    if (isNew || stale){                   // 새 검색어이거나 본문이 다시 그려졌으면 다시 훑는다
      query = q; build(q);
      if (isNew) cur = ranges.length ? 0 : -1;
      else if (ranges.length) cur = ((cur < 0 ? 0 : cur) + dir + ranges.length) % ranges.length;
    } else if (ranges.length) cur = (cur + dir + ranges.length) % ranges.length;
    if (cur >= ranges.length) cur = ranges.length ? 0 : -1;
    paint(); show(cur < 0 ? 0 : cur);
    scrollToCur();
  }

  function open(){
    bar.classList.add('on');
    watch(true);
    stale = true;                          // 열린 사이 본문이 바뀌었을 수 있다
    // 헤더가 있는 화면(리뷰)에서는 단계 탭을 가리지 않도록 헤더 아래로 내려 앉는다.
    var head = document.querySelector('header');
    bar.style.top = (head && head.getClientRects().length ? head.getBoundingClientRect().bottom + 8 : 8) + 'px';
    input.focus(); input.select();
    if (input.value.trim()){ stale = true; step(0); }
  }
  function close(){
    bar.classList.remove('on');
    clearTimeout(timer);                   // 예약된 검색이 닫은 뒤에 색을 다시 얹지 않게
    watch(false);
    clearPaint(); ranges = []; cur = -1; query = '';
    countEl.textContent = '';
    if (!HL){                              // 물러선 길에서 남는 브라우저 선택도 지운다
      var sel = window.getSelection();
      if (sel) sel.removeAllRanges();
    }
  }

  var timer = null;
  input.addEventListener('input', function(){
    clearTimeout(timer);
    timer = setTimeout(function(){ stale = true; step(0); }, 180);   // 타이핑 중 매 글자마다 훑지 않게
  });
  input.addEventListener('keydown', function(ev){
    if (ev.key === 'Enter'){ ev.preventDefault(); step(ev.shiftKey ? -1 : 1); }
    else if (ev.key === 'Escape'){ ev.preventDefault(); close(); }
  });
  bar.addEventListener('click', function(ev){
    var b = ev.target.closest('button');
    if (!b) return;
    var act = b.getAttribute('data-act');
    if (act === 'close') close(); else step(act === 'prev' ? -1 : 1);
    if (act !== 'close') input.focus();
  });
  function zoomOpen(){                     // 그림 확대 뷰어가 화면을 차지하고 있으면 그쪽 차례다
    var z = document.getElementById('md-img-zoom');
    return !!z && getComputedStyle(z).display !== 'none';
  }
  document.addEventListener('keydown', function(ev){
    var mod = ev.metaKey || ev.ctrlKey;
    if (mod && (ev.key === 'f' || ev.key === 'F' || ev.key === 'g' || ev.key === 'G')){
      if (zoomOpen()) return;
      ev.preventDefault(); ev.stopPropagation();
      open();
      if (ev.key === 'g' || ev.key === 'G') step(ev.shiftKey ? -1 : 1);
      return;
    }
    // Esc는 여러 곳이 듣는다(확대 뷰어·서랍·팝업) → 찾기 바가 열려 있을 때만 우리가 먹는다
    if (ev.key === 'Escape' && bar.classList.contains('on')){
      ev.stopPropagation();
      close();
    }
  }, true);
  // 탭을 옮기거나 본문이 다시 그려지면 이미 잡아 둔 Range가 낡는다 → 다음 이동 때 다시 훑는다.
  // 찾기 바가 열려 있는 동안만 본다 — 평소에 문서 전체 변화를 계속 지켜볼 이유가 없다.
  var watcher = new MutationObserver(function(muts){
    for (var i = 0; i < muts.length; i++)
      if (!bar.contains(muts[i].target)){ stale = true; return; }
  });
  function watch(on){
    if (on) watcher.observe(document.body,
      {subtree: true, childList: true, characterData: true, attributes: true});
    else watcher.disconnect();
  }
})();
</script>
"""


def install() -> None:
    """현재 페이지에 찾기 바를 얹는다 (CSS + 바 + 키 바인딩)."""
    from nicegui import ui

    ui.add_css(CSS)
    ui.add_body_html(HTML)
