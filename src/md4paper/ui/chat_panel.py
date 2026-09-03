"""뷰어 챗봇 서랍 — CSS/HTML/JS 문자열 + HTTP 라우트 (app.py에는 훅만 남긴다).

메모 서랍(`#md-anno-panel`)과 같은 시각 언어를 쓰고, 두 서랍은 서로 배타적으로 열린다.
답변 HTML은 서버에서 만들어(raw HTML 비활성 마크다운) 넣으므로 클라이언트는 삽입만 한다.
근거 문단 텍스트는 렌더하지 않고 textContent로만 넣는다.
"""

from __future__ import annotations

import json
from typing import Callable

from md4paper import config
from md4paper.ui import annotations, chat

CSS = """
/* 오른쪽 챗봇 컬럼 — 메모 서랍과 같은 자리·같은 시각 언어 (동시에 열리지 않는다).
   본문을 덮지 않고 **오른쪽을 차지한다**: 열리면 단계 패널이 그만큼 좁아져(body.mc-open)
   PDF 패널처럼 나란히 놓인다. 위치는 fixed로 두는데, 서랍이 body에 붙어 있어야 뷰어가
   다시 그려져도 대화 상태가 살아남기 때문이다 — 자리는 padding으로 비운다. */
:root { --mc-w: 380px; }
#md-chat-panel { position: fixed; top: 50px; right: 0; bottom: 0; width: var(--mc-w); z-index: 9000;
  display: none; flex-direction: column; background: #fff; border-left: 1px solid #e6e4e0;
  color: #37352f; }
#md-chat-panel.open { display: flex; }
body.mc-open .md4-steps { padding-right: var(--mc-w); }
/* 너비 드래그 핸들 — 목차·컬럼 핸들과 같은 감각 */
.mc-handle { position: absolute; left: 0; top: 0; bottom: 0; width: 9px; margin-left: -4px;
  cursor: col-resize; z-index: 1; }
.mc-handle::after { content: ''; position: absolute; left: 4px; top: 0; bottom: 0; width: 1px;
  background: transparent; }
.mc-handle:hover::after { background: #2383e2; width: 2px; }
.mc-head { display: flex; align-items: center; gap: 7px; padding: 10px 12px;
  border-bottom: 1px solid #ecebe8; font-size: 13px; }
.mc-model { font-size: 10.5px; padding: 1px 7px; border-radius: 9px; background: #f0efec;
  color: #6b6862; max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* '메모 포함' 토글 — 내 하이라이트·메모를 근거에 넣을지 */
.mc-tog { display: inline-flex; align-items: center; gap: 3px; font-size: 10.5px;
  color: #6b6862; cursor: pointer; white-space: nowrap; }
.mc-tog input { margin: 0; width: 12px; height: 12px; accent-color: #d9a406; }
.mc-tog b { color: #b58900; font-weight: 600; }
.mc-sp { flex: 1 1 auto; }
.mc-btn { border: none; background: none; font-size: 11.5px; color: #8a8780; cursor: pointer;
  padding: 3px 6px; border-radius: 7px; }
.mc-btn:hover { background: #f0efec; }
.mc-x { font-size: 13px; }

.mc-list { flex: 1 1 auto; overflow-y: auto; padding: 10px 11px; }
.mc-empty { padding: 26px 14px; font-size: 12px; color: #97948d; text-align: center;
  line-height: 1.75; }
.mc-q { margin: 4px 0 8px 30px; padding: 7px 10px; border-radius: 12px 12px 3px 12px;
  background: #eef4fd; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; }
.mc-a { font-size: 12.5px; line-height: 1.68; overflow-wrap: break-word; }
.mc-a p, .mc-a ul, .mc-a ol, .mc-a blockquote { margin: 0 0 7px; }
.mc-a ul, .mc-a ol { padding-left: 19px; }
.mc-a code { background: #f4f3f0; border-radius: 4px; padding: 0 3px; font-size: 11.5px; }
.mc-a pre { background: #f4f3f0; border-radius: 8px; padding: 7px 9px; overflow-x: auto; }
.mc-a h1, .mc-a h2, .mc-a h3 { font-size: 13px; margin: 2px 0 5px; }
.mc-turn + .mc-turn { border-top: 1px solid #f0efec; margin-top: 12px; padding-top: 10px; }

/* 인용 칩 — 작은 원형 숫자 */
a.chat-cite { display: inline-block; min-width: 15px; height: 15px; padding: 0 3px; margin: 0 2px;
  border-radius: 8px; background: #2383e2; color: #fff; font-size: 9.5px; font-weight: 600;
  line-height: 15px; text-align: center; cursor: pointer; text-decoration: none;
  vertical-align: 1px; }
a.chat-cite:hover { background: #1668c4; }
a.chat-cite.on { background: #0f4c94; }
/* 독자 메모 근거 칩 — 논문 문단 칩과 한눈에 구별되게 형광펜 색으로 */
a.chat-cite-note { background: #ffe08a; color: #4a3b00; border: 1px solid #d9a406;
  line-height: 13px; }
a.chat-cite-note:hover { background: #f7cf60; }
a.chat-cite-note.on { background: #e0b53c; color: #2f2500; }

.mc-meta { font-size: 10.5px; color: #97948d; margin: 3px 0 7px; display: flex; gap: 7px;
  align-items: center; flex-wrap: wrap; }
.mc-meta .mc-link { border: none; background: none; color: #6b6862; font-size: 10.5px;
  cursor: pointer; padding: 1px 5px; border-radius: 6px; text-decoration: underline; }
.mc-meta .mc-link:hover { background: #f0efec; }

/* 근거 카드 (인용 칩 클릭 → 펼침) */
.mc-ev { border: 1px solid #e6e4e0; border-radius: 10px; padding: 8px 9px; margin: 0 0 8px;
  background: #faf9f7; }
.mc-ev-head { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #6b6862; }
.mc-ev-title { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mc-seg { display: inline-flex; border: 1px solid #e0ded9; border-radius: 7px; overflow: hidden; }
.mc-seg button { border: none; background: none; font-size: 10.5px; padding: 2px 7px;
  cursor: pointer; color: #6b6862; }
.mc-seg button.on { background: #2383e2; color: #fff; }
.mc-ev-body { display: grid; gap: 9px; margin-top: 7px; max-height: 210px; overflow-y: auto; }
.mc-ev-body.two { grid-template-columns: 1fr 1fr; }
.mc-ev-lab { font-size: 10px; color: #97948d; margin-bottom: 2px; }
/* 근거 카드 안의 '메모' 섹션 — 색 점 + 표시한 문장 + 내가 쓴 메모 */
.mc-ev-note { border-left: 2px solid #d9a406; padding: 1px 0 1px 7px; margin: 7px 0 0; }
.mc-ev-note .mc-ev-lab { display: flex; align-items: center; gap: 4px; }
.mc-ev-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
  border: 1px solid rgba(0,0,0,.18); }
.mc-ev-quote { font-size: 11.5px; line-height: 1.6; color: #4a4740; }
.mc-ev-mine { font-size: 11.5px; line-height: 1.6; white-space: pre-wrap; color: #37352f;
  margin-top: 3px; }
.mc-ev-txt { font-size: 11.5px; line-height: 1.6; white-space: pre-wrap; color: #4a4740; }
.mc-hits { margin: 0 0 8px; padding: 6px; border: 1px solid #e6e4e0; border-radius: 10px; }
.mc-hit { display: block; width: 100%; text-align: left; border: none; background: none;
  font-size: 11px; line-height: 1.5; color: #5b5851; padding: 4px 6px; border-radius: 7px;
  cursor: pointer; }
.mc-hit:hover { background: #f0efec; }
.mc-hit b { color: #37352f; }

.mc-note { border: 1px solid #e6e4e0; background: #f7f6f3; border-radius: 10px; padding: 9px 11px;
  font-size: 11.5px; line-height: 1.65; color: #6b6862; margin-bottom: 8px; }
.mc-err { border: 1px solid rgba(179,38,30,.35); background: rgba(179,38,30,.08); color: #b3261e;
  border-radius: 10px; padding: 9px 11px; font-size: 11.5px; line-height: 1.6; margin-bottom: 8px; }

.mc-foot { border-top: 1px solid #ecebe8; padding: 8px 9px; display: flex; gap: 6px;
  align-items: flex-end; }
.mc-input { flex: 1 1 auto; resize: none; max-height: 96px; box-sizing: border-box;
  border: 1px solid #e0ded9; border-radius: 10px; padding: 7px 9px; font: inherit;
  font-size: 12.5px; line-height: 1.5; background: #fff; color: inherit; outline: none; }
.mc-input:focus { border-color: #2383e2; }
.mc-input:disabled { background: #f4f3f0; color: #97948d; }
.mc-send { flex: 0 0 30px; width: 30px; height: 30px; border: none; border-radius: 50%;
  background: #2383e2; color: #fff; font-size: 13px; cursor: pointer; }
.mc-send:hover { background: #1668c4; }
.mc-send:disabled { background: #d8d6d1; cursor: default; }

.mc-wait { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: #97948d;
  margin: 0 0 9px; }
.mc-dots span { display: inline-block; width: 5px; height: 5px; margin-right: 3px;
  border-radius: 50%; background: #a9a69f; animation: mdChatDot 1.1s infinite ease-in-out; }
.mc-dots span:nth-child(2) { animation-delay: .18s; }
.mc-dots span:nth-child(3) { animation-delay: .36s; }
@keyframes mdChatDot { 0%, 60%, 100% { opacity: .25; } 30% { opacity: 1; } }

/* 인용 칩 클릭 → 본문 해당 행 깜빡임 (원문·번역 셀 모두) */
.chat-cite-flash { animation: mdChatFlash 1.3s ease-out; }
@keyframes mdChatFlash { 0%, 55% { outline: 2px solid #2383e2; outline-offset: 2px; }
  100% { outline-color: transparent; } }

@media (prefers-color-scheme: dark) {
  #md-chat-panel { background: #1f1f1f; border-color: #3a3a3a; color: #d4d4d4; }
  .mc-head, .mc-foot { border-color: #2c2c2c; }
  .mc-model, .mc-a code, .mc-a pre { background: #2c2c2c; color: #b8b8b8; }
  .mc-btn:hover, .mc-meta .mc-link:hover, .mc-hit:hover { background: #2c2c2c; }
  .mc-q { background: #24303d; }
  .mc-empty, .mc-ev-lab, .mc-meta { color: #8f8f8f; }
  .mc-ev, .mc-hits, .mc-note { background: #262626; border-color: #3a3a3a; color: #b8b8b8; }
  .mc-ev-txt, .mc-hit, .mc-ev-quote { color: #b8b8b8; }
  .mc-hit b, .mc-ev-mine { color: #d4d4d4; }
  .mc-tog { color: #8f8f8f; }
  .mc-seg { border-color: #3a3a3a; }
  .mc-input { background: #262626; border-color: #3a3a3a; }
  .mc-input:disabled { background: #232323; color: #7d7d7d; }
  .mc-turn + .mc-turn { border-color: #2c2c2c; }
}
"""

# 서랍 마크업 + 클라이언트 로직. 서랍은 body에 고정돼 있어 뷰어가 다시 그려져도 상태가 유지된다.
HTML = """
<div id="md-chat-panel">
  <div class="mc-handle" title="드래그해서 너비 조절"></div>
  <div class="mc-head"><b>논문에 질문</b><span class="mc-model"></span>
    <span class="mc-sp"></span>
    <label class="mc-tog" title="내 하이라이트·메모도 검색과 답변 근거에 넣습니다">
      <input type="checkbox" class="mc-tog-in" checked>메모 포함<b class="mc-tog-n"></b></label>
    <button class="mc-btn mc-clear" title="이 논문의 대화 기록을 지웁니다">지우기</button>
    <button class="mc-btn mc-x" title="닫기 (Esc)">&#10005;</button></div>
  <div class="mc-list"></div>
  <div class="mc-foot">
    <textarea class="mc-input" rows="1"
      placeholder="이 논문에 대해 물어보세요 — Enter 전송 · Shift+Enter 줄바꿈"></textarea>
    <button class="mc-send" title="전송 (Enter)">&#10148;</button>
  </div>
</div>
<script>
(function(){
  if (window.__mdChatInit) return; window.__mdChatInit = true;
  var panel = document.getElementById('md-chat-panel');
  if (!panel) return;
  var list = panel.querySelector('.mc-list');
  var input = panel.querySelector('.mc-input');
  var sendBtn = panel.querySelector('.mc-send');
  var badge = panel.querySelector('.mc-model');
  var togIn = panel.querySelector('.mc-tog-in');
  var togN = panel.querySelector('.mc-tog-n');
  var turns = [], loaded = false, busy = false, pending = null, errMsg = null;
  var openEv = {};   // '<턴id>:<근거키>' → 근거 카드 표시 모드 ('both'|'en'|'ko'), 없으면 닫힘

  // '메모 포함' 토글 — 브라우저에 기억시킨다 (사생활 모드 등에서 던지는 예외는 무시)
  var useNotes = true;
  try { useNotes = localStorage.getItem('md4chat.notes') !== '0'; } catch (e) {}
  togIn.checked = useNotes;
  togIn.addEventListener('change', function(){
    useNotes = !!togIn.checked;
    try { localStorage.setItem('md4chat.notes', useNotes ? '1' : '0'); } catch (e) {}
  });

  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s;
    return d.innerHTML; }
  function tok(){ return window.__mdChatTok || ''; }
  function ready(){ return !!window.__mdChatReady; }

  // ---- 본문(뷰어 정렬 행)으로 이동 + 깜빡임 ----
  function cellsOf(row){
    var els = document.querySelectorAll('.sbs-grid .sbs-cell[data-row="' + row + '"]');
    if (!els.length) els = document.querySelectorAll('[data-row="' + row + '"]');
    var out = [];
    for (var i = 0; i < els.length; i++){
      out.push(els[i]);
      // 정렬 그리드는 첫 표시 셀에만 data-row가 붙는다 — 짝 셀(번역)도 함께 깜빡이게
      var sib = els[i].nextElementSibling;
      if (sib && sib.classList.contains('sbs-cell') && !sib.hasAttribute('data-row')) out.push(sib);
    }
    return out;
  }
  function flash(els, cls){
    els.forEach(function(el){
      el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
      setTimeout(function(){ el.classList.remove(cls); }, 1400);
    });
  }
  function gotoRow(row){
    var els = cellsOf(row);
    if (!els.length) return false;
    els[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(els, 'chat-cite-flash');
    return true;
  }
  // 메모 근거 → 그 하이라이트(mark)로. 뷰어의 md-anno-flash를 그대로 재사용한다.
  function marksOf(id){
    var ms = document.querySelectorAll('mark.md-anno'), out = [];
    for (var i = 0; i < ms.length; i++){
      if ((ms[i].getAttribute('data-ids') || '').split(' ').indexOf(id) >= 0) out.push(ms[i]);
    }
    return out;
  }
  function gotoNote(id, row){
    var ms = marksOf(id);
    if (!ms.length) return gotoRow(row);      // 아직 안 칠해졌거나 위치를 못 찾은 메모
    ms[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(ms, 'md-anno-flash');
    return true;
  }
  function noted(row){                        // 이 행에 하이라이트가 칠해져 있나 (📝 표시용)
    var els = cellsOf(row);
    for (var i = 0; i < els.length; i++) if (els[i].querySelector('mark.md-anno')) return true;
    return false;
  }
  function rowText(row){                 // 검색된 문단 미리보기 — 화면에 그려진 셀에서 긁는다
    var els = cellsOf(row);
    return els.length ? (els[0].textContent || '').replace(/\\s+/g, ' ').trim() : '';
  }
  function headingOf(row){               // 그 행 앞쪽에서 가장 가까운 헤더 (검색된 문단 목록 표시용)
    var cells = Array.prototype.slice.call(
      document.querySelectorAll('.sbs-grid .sbs-cell[data-row]'));
    var i = -1;
    for (var k = 0; k < cells.length; k++)
      if (cells[k].getAttribute('data-row') === String(row)) { i = k; break; }
    for (var j = i; j >= 0; j--){
      var h = cells[j].querySelector('h1,h2,h3,h4,h5,h6');
      if (h) return (h.textContent || '').trim();
    }
    return '';
  }

  // ---- 렌더 ----
  var SWATCH = {yellow: '#ffe08a', green: '#b5e7b8', blue: '#a9d8f5', pink: '#f9bcd4',
                purple: '#d8c6f5'};
  var SIDE = {en: '원문', ko: '번역', both: '원문·번역'};
  function keyOf(cite){                       // 근거 카드 키 — 메모는 id, 문단은 행
    return cite.kind === 'note' ? 'a:' + cite.id : 'r:' + cite.row;
  }
  function citeOf(turn, row){                 // 문단 근거 (행)
    var cs = turn.citations || [];
    for (var i = 0; i < cs.length; i++)
      if (cs[i].row === row && cs[i].kind !== 'note') return cs[i];
    for (i = 0; i < cs.length; i++) if (cs[i].row === row) return cs[i];  // 메모라도 있으면
    return null;
  }
  function noteCiteOf(turn, id){
    var cs = turn.citations || [];
    for (var i = 0; i < cs.length; i++)
      if (cs[i].kind === 'note' && cs[i].id === id) return cs[i];
    return null;
  }
  function noteBox(cite){                     // 근거 카드 위쪽 '메모' 섹션
    var box = document.createElement('div');
    box.className = 'mc-ev-note';
    var lab = document.createElement('div');
    lab.className = 'mc-ev-lab';
    var dot = document.createElement('span');
    dot.className = 'mc-ev-dot';
    dot.style.background = SWATCH[cite.color] || SWATCH.yellow;
    lab.appendChild(dot);
    var lt = document.createElement('span');
    lt.textContent = '내 메모 · ' + (SIDE[cite.side] || '원문') + '에 표시';
    lab.appendChild(lt);
    box.appendChild(lab);
    var q = document.createElement('div');
    q.className = 'mc-ev-quote'; q.textContent = '“' + (cite.quote || '') + '”';
    box.appendChild(q);
    if ((cite.note || '').trim()){
      var mine = document.createElement('div');
      mine.className = 'mc-ev-mine'; mine.textContent = cite.note;
      box.appendChild(mine);
    }
    return box;
  }
  function evCard(turn, cite){
    var key = turn.id + ':' + keyOf(cite);
    var mode = openEv[key] || 'both';
    var box = document.createElement('div');
    box.className = 'mc-ev';
    var hasKo = !!cite.ko;
    var head = document.createElement('div');
    head.className = 'mc-ev-head';
    head.innerHTML = '<span class="mc-ev-title">[' + esc(String(cite.n)) + '] '
      + (cite.kind === 'note' ? '독자 메모 · ' : '')
      + esc(cite.heading || ('행 ' + cite.row)) + '</span>'
      + '<span class="mc-seg">'
      + (hasKo ? '<button data-m="both">나란히</button>' : '')
      + '<button data-m="en">원문</button>'
      + (hasKo ? '<button data-m="ko">번역</button>' : '')
      + '</span>'
      + '<button class="mc-btn mc-ev-go" title="뷰어에서 이 문단으로 이동">본문으로</button>';
    box.appendChild(head);
    if (!hasKo && mode !== 'en') mode = 'en';
    head.querySelectorAll('.mc-seg button').forEach(function(b){
      if (b.getAttribute('data-m') === mode) b.classList.add('on');
      b.addEventListener('click', function(){ openEv[key] = b.getAttribute('data-m'); render(); });
    });
    head.querySelector('.mc-ev-go').addEventListener('click', function(){
      if (cite.kind === 'note') gotoNote(cite.id, cite.row); else gotoRow(cite.row);
    });
    if (cite.kind === 'note') box.appendChild(noteBox(cite));   // 메모 → 그 아래 원문·번역
    var body = document.createElement('div');
    body.className = 'mc-ev-body' + (mode === 'both' && hasKo ? ' two' : '');
    function col(label, text){
      var c = document.createElement('div');
      var l = document.createElement('div'); l.className = 'mc-ev-lab'; l.textContent = label;
      var t = document.createElement('div'); t.className = 'mc-ev-txt'; t.textContent = text || '';
      c.appendChild(l); c.appendChild(t); body.appendChild(c);
    }
    if (mode === 'both'){ col('원문', cite.en); if (hasKo) col('번역', cite.ko); }
    else if (mode === 'ko') col('번역', cite.ko);
    else col('원문', cite.en);
    box.appendChild(body);
    return box;
  }
  function hitsCard(turn){
    var box = document.createElement('div');
    box.className = 'mc-hits';
    (turn.retrieved || []).forEach(function(row){
      var b = document.createElement('button');
      b.className = 'mc-hit';
      var c = citeOf(turn, row);
      var prev = rowText(row) || (c ? (c.en || '') : '');
      var head = (c && c.heading) || headingOf(row);
      var mark = noted(row) ? ' \\uD83D\\uDCDD' : '';   // 📝 — 내가 표시해 둔 행
      b.innerHTML = '<b>r' + row + (head ? ' · ' + esc(head) : '') + mark + '</b> — '
        + esc(prev.slice(0, 80));
      b.addEventListener('click', function(){ gotoRow(row); });
      box.appendChild(b);
    });
    return box;
  }
  function turnNode(turn, idx){
    var wrap = document.createElement('div');
    wrap.className = 'mc-turn';
    var q = document.createElement('div');
    q.className = 'mc-q'; q.textContent = turn.question || '';
    wrap.appendChild(q);
    var a = document.createElement('div');
    a.className = 'mc-a';
    a.innerHTML = turn.answer_html || '';   // 서버에서 마크다운 렌더 (raw HTML 비활성)
    wrap.appendChild(a);
    var cites = turn.citations || [];
    a.querySelectorAll('a.chat-cite').forEach(function(chip){
      var row = parseInt(chip.getAttribute('data-row'), 10);
      var isNote = chip.getAttribute('data-kind') === 'note';
      var c = isNote ? noteCiteOf(turn, chip.getAttribute('data-id')) : citeOf(turn, row);
      if (!c) return;
      chip.title = isNote
        ? '내 메모 — “' + (c.quote || '').slice(0, 90) + '”'
          + ((c.note || '').trim() ? ' / ' + c.note.replace(/\\s+/g, ' ').slice(0, 70) : '')
        : (c.heading ? c.heading + ' — ' : '') + (c.en || '').replace(/\\s+/g, ' ').slice(0, 160);
      var key = turn.id + ':' + keyOf(c);
      if (openEv[key]) chip.classList.add('on');
      chip.addEventListener('click', function(ev){
        ev.preventDefault();
        if (openEv[key]) delete openEv[key]; else openEv[key] = 'both';
        if (isNote) gotoNote(c.id, c.row); else gotoRow(c.row);
        render();
      });
    });
    var evs = document.createElement('div');
    cites.forEach(function(c){
      if (openEv[turn.id + ':' + keyOf(c)]) evs.appendChild(evCard(turn, c));
    });
    wrap.appendChild(evs);
    var meta = document.createElement('div');
    meta.className = 'mc-meta';
    var bits = [];
    if (turn.model) bits.push(esc(turn.model));
    if (turn.cost_usd) bits.push('$' + Number(turn.cost_usd).toFixed(4));
    meta.innerHTML = bits.join(' · ');
    var n = (turn.retrieved || []).length;
    if (n){
      var btn = document.createElement('button');
      btn.className = 'mc-link';
      btn.textContent = '검색된 문단 ' + n + '개';
      var shownKey = 'hits:' + turn.id;
      btn.addEventListener('click', function(){
        if (openEv[shownKey]) delete openEv[shownKey]; else openEv[shownKey] = 1;
        render();
      });
      meta.appendChild(btn);
    }
    wrap.appendChild(meta);
    if (openEv['hits:' + turn.id]) wrap.appendChild(hitsCard(turn));
    if (turn.error){
      var e = document.createElement('div');
      e.className = 'mc-err'; e.textContent = turn.error;
      wrap.appendChild(e);
    }
    return wrap;
  }
  function render(){
    badge.textContent = window.__mdChatModel || (turns.length ? turns[turns.length - 1].model : '');
    var nn = window.__mdChatNotes || 0;
    togN.textContent = nn ? ' ' + nn : '';
    list.innerHTML = '';
    if (!ready()){
      var note = document.createElement('div');
      note.className = 'mc-err';
      note.textContent = (window.__mdChatReason || 'AI 키가 설정되지 않았습니다.')
        + ' 홈 → 설정에서 키를 넣으세요.';
      list.appendChild(note);
    }
    if (!turns.length && !pending && ready()){
      var em = document.createElement('div');
      em.className = 'mc-empty';
      em.textContent = '이 논문의 문단' + (nn ? '과 내가 표시해 둔 메모' : '') + '만 근거로 답합니다.\\n'
        + '예: "핵심 기여가 뭐야?"'
        + (nn ? ', "내가 메모한 내용 정리해줘"' : ', "참가자는 몇 명이고 어떻게 뽑았어?"') + '\\n'
        + '답 속 숫자 칩을 누르면 근거 문단(노란 칩은 메모)으로 이동합니다.';
      list.appendChild(em);
    }
    turns.forEach(function(t, i){ list.appendChild(turnNode(t, i)); });
    if (pending){
      var pw = document.createElement('div');
      pw.className = 'mc-turn';
      var pq = document.createElement('div');
      pq.className = 'mc-q'; pq.textContent = pending;
      pw.appendChild(pq);
      var w = document.createElement('div');
      w.className = 'mc-wait';
      w.innerHTML = '<span class="mc-dots"><span></span><span></span><span></span></span>'
        + '문단 찾는 중…';
      pw.appendChild(w);
      list.appendChild(pw);
    }
    if (errMsg){
      var er = document.createElement('div');
      er.className = 'mc-err'; er.textContent = errMsg;
      list.appendChild(er);
    }
    input.disabled = !ready() || busy;
    sendBtn.disabled = input.disabled;
    list.scrollTop = list.scrollHeight;
  }

  // ---- 서버 ----
  function loadHistory(){
    loaded = true;
    fetch('/chat/' + tok()).then(function(r){ return r.json(); }).then(function(j){
      turns = j.turns || [];
      window.__mdChatReady = !!j.ready;
      if (j.reason) window.__mdChatReason = j.reason;
      if (j.model) window.__mdChatModel = j.model;
      window.__mdChatNotes = j.notes || 0;
      render();
    }).catch(function(){ render(); });
  }
  function ask(){
    var q = (input.value || '').trim();
    if (!q || busy || !ready()) return;
    input.value = ''; autosize();
    pending = q; errMsg = null; busy = true; render();
    fetch('/chat/' + tok(), {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({question: q, include_notes: useNotes})})
      .then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; },
                                             function(){ return {ok: false, j: {}}; }); })
      .then(function(res){
        busy = false; pending = null;
        if (!res.ok || res.j.error){
          errMsg = res.j.error || '답변을 받지 못했습니다.';
          if (!input.value) input.value = q;      // 질문을 잃지 않게 되돌려 놓는다
        } else {
          turns.push(res.j);
        }
        render(); autosize();
      })
      .catch(function(e){
        busy = false; pending = null; errMsg = '요청 실패: ' + e; render();
      });
  }
  function clearAll(){
    turns = []; openEv = {}; errMsg = null; render();
    fetch('/chat/' + tok(), {method: 'DELETE'}).catch(function(){});
  }
  function autosize(){
    input.style.height = 'auto';
    input.style.height = Math.min(96, Math.max(30, input.scrollHeight)) + 'px';
  }

  // ---- 열기/닫기 (메모 서랍과 배타) ----
  // 'open'은 여러 경로에서 붙었다 뗀다(버튼·✕·Esc·메모 서랍). 한 곳에서 관찰해 body 클래스를
  // 맞춰야 본문 자리 비우기가 어느 경로로 닫아도 따라온다.
  function syncBody(){ document.body.classList.toggle('mc-open', panel.classList.contains('open')); }
  if (window.MutationObserver)
    new MutationObserver(syncBody).observe(panel, {attributes: true, attributeFilter: ['class']});
  syncBody();

  // 너비 드래그 — 브라우저에 기억시킨다 (--mc-w를 패널 너비와 본문 padding이 같이 읽는다)
  var MC_MIN = 300, MC_MAX = 720;
  function setWidth(px){
    px = Math.max(MC_MIN, Math.min(MC_MAX, Math.round(px)));
    document.documentElement.style.setProperty('--mc-w', px + 'px');
    try { localStorage.setItem('md4chat.w', String(px)); } catch (e) {}
  }
  try { var w0 = parseInt(localStorage.getItem('md4chat.w'), 10);
        if (w0) setWidth(w0); } catch (e) {}
  var mcDrag = false;
  panel.querySelector('.mc-handle').addEventListener('mousedown', function(ev){
    mcDrag = true; ev.preventDefault(); document.body.style.userSelect = 'none';
  });
  window.addEventListener('mousemove', function(ev){
    if (mcDrag) setWidth(window.innerWidth - ev.clientX);
  });
  window.addEventListener('mouseup', function(){
    if (mcDrag){ mcDrag = false; document.body.style.userSelect = ''; }
  });

  function annoPanel(){ return document.getElementById('md-anno-panel'); }
  window.__mdChatTogglePanel = function(){
    var opening = !panel.classList.contains('open');
    panel.classList.toggle('open', opening);
    if (opening){
      var ap = annoPanel();
      if (ap) ap.classList.remove('open');          // 없을 수도 있다 (메모 기능 미탑재)
      if (!loaded) loadHistory(); else render();
      setTimeout(function(){ if (!input.disabled) input.focus(); }, 60);
    }
  };
  var ap0 = annoPanel();
  if (ap0 && window.MutationObserver){               // 메모 서랍이 열리면 채팅 서랍은 닫는다
    new MutationObserver(function(){
      if (ap0.classList.contains('open')) panel.classList.remove('open');
    }).observe(ap0, {attributes: true, attributeFilter: ['class']});
  }
  panel.querySelector('.mc-x').addEventListener('click', function(){
    panel.classList.remove('open');
  });
  panel.querySelector('.mc-clear').addEventListener('click', clearAll);
  sendBtn.addEventListener('click', ask);
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function(ev){
    if (ev.key === 'Enter' && !ev.shiftKey){ ev.preventDefault(); ask(); }
  });
  document.addEventListener('keydown', function(ev){
    if (ev.key === 'Escape' && panel.classList.contains('open')) panel.classList.remove('open');
  });
  render();
})();
</script>
"""


def readiness() -> tuple[bool, str]:
    """LLM 키가 준비됐는지 (준비됐나?, 안내 메시지). 네트워크는 건드리지 않는다."""
    try:
        config.build_provider()
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — 설정 파일 손상 등도 안내로 보여준다
        return False, str(e) or e.__class__.__name__
    return True, ""


def _model_name() -> str:
    """헤더 배지에 쓸 모델명 (키가 없으면 빈 문자열)."""
    try:
        return str(config.build_provider().model)
    except Exception:  # noqa: BLE001
        return ""


def init_js(token: str, ready: bool = False, reason: str = "") -> str:
    """토큰·준비 상태를 클라이언트에 실어주는 초기화 JS."""
    payload = json.dumps({"tok": token, "ready": bool(ready), "reason": reason,
                          "model": _model_name() if ready else ""},
                         ensure_ascii=False).replace("<", "\\u003c")
    return (f"(function(){{var s = {payload};"
            "window.__mdChatTok = s.tok; window.__mdChatReady = s.ready;"
            "window.__mdChatReason = s.reason; window.__mdChatModel = s.model;})();")


def register_routes(fastapi_app, wd_for: Callable[[str], object],  # noqa: ANN001
                    build_provider: Callable[[], object] | None = None) -> None:
    """`/chat/{token}` GET·POST·DELETE 등록 (annotations 라우트와 같은 패턴)."""
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException

    make = build_provider or config.build_provider

    def _readiness() -> tuple[bool, str]:
        try:
            make()
        except RuntimeError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, str(e) or e.__class__.__name__
        return True, ""

    @fastapi_app.get("/chat/{token}")
    def _get_chat(token: str):  # noqa: ANN202 — 이 논문의 대화 기록 + 키 준비 상태
        cur = wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        ok, reason = _readiness()
        model = ""
        if ok:
            try:
                model = str(make().model)
            except Exception:  # noqa: BLE001
                model = ""
        return {"turns": chat.load(cur), "ready": ok, "reason": reason, "model": model,
                "notes": len(annotations.load(cur))}   # 서랍의 '메모 포함' 토글 옆 개수

    # 동기 def — LLM 호출(수 초)이 이벤트 루프를 막지 않도록 FastAPI 스레드풀에서 돌린다.
    @fastapi_app.post("/chat/{token}")
    def _post_chat(token: str, body: dict):  # noqa: ANN202 — 질문 → 답변 턴
        cur = wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        question = str((body or {}).get("question") or "").strip()
        if not question:
            return JSONResponse({"error": "질문이 비어 있습니다."}, status_code=400)
        try:
            provider = make()
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        want_notes = (body or {}).get("include_notes", True)
        try:
            turn = chat.answer(cur, provider, question, chat.load(cur),
                               include_notes=bool(want_notes))
        except Exception as e:  # noqa: BLE001 — SDK 예외를 그대로 사용자에게 요약
            msg = (str(e).strip().splitlines() or [""])[0] or e.__class__.__name__
            return JSONResponse({"error": msg[:200]}, status_code=502)
        chat.append(cur, turn)
        return turn

    @fastapi_app.delete("/chat/{token}")
    def _del_chat(token: str):  # noqa: ANN202 — 대화 기록 삭제
        cur = wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        chat.clear(cur)
        return {"ok": True}
