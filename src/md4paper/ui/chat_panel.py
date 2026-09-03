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

# '읽던 자리로' 되돌리기 알약 — 아래 CSS/HTML 문자열과 이 상수가 한 짝이다(테스트가 대조한다).
JUMP_BACK_ID = "md-jump-back"    # body 직속 오버레이 id — 서랍 밖이라 서랍을 닫아도 남는다
JUMP_BACK_Z = 8500               # 본문 위, 서랍(9000)·메모 카드(10003)·찾기 바 아래
JUMP_BACK_API = "__mdJumpBack"   # 다른 점프(목차·섹션 트리)가 나중에 재사용할 전역 이름
JUMP_EDGE_PX = 6                 # 기준선 = 컨테이너 위끝 + 6px. app.py PDF 싱크(top+6)와 같은 정의라
                                 # "화면 맨 위가 읽던 자리"가 뷰어 전체에서 한 뜻이 된다

CSS = """
/* 오른쪽 챗봇 컬럼 — 메모 서랍과 같은 자리·같은 시각 언어 (동시에 열리지 않는다).
   본문을 덮지 않고 **오른쪽을 차지한다**: 열리면 단계 패널이 그만큼 좁아져(body.mc-open)
   PDF 패널처럼 나란히 놓인다. 위치는 fixed로 두는데, 서랍이 body에 붙어 있어야 뷰어가
   다시 그려져도 대화 상태가 살아남기 때문이다 — 자리는 padding으로 비운다. */
:root { --mc-w: 380px; --mc-top: 50px; }
/* --mc-top은 헤더를 실제로 재서 넣는다(아래 syncTop) — 헤더는 탭 아이콘+라벨이라 높이가
   고정이 아니고, 상수로 두면 서랍이 헤더 위로 올라와 탭 라벨을 가린다. */
#md-chat-panel { position: fixed; top: var(--mc-top); right: 0; bottom: 0; width: var(--mc-w); z-index: 9000;
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
.mc-head { display: flex; align-items: center; gap: 7px; padding: 10px 12px; flex-wrap: nowrap;
  border-bottom: 1px solid #ecebe8; font-size: 13px; }
/* 좁은 서랍에 드롭다운까지 들어가므로 줄바꿈을 막고, 남는 폭은 드롭다운이 먼저 양보한다 */
.mc-head > b, .mc-head .mc-btn { white-space: nowrap; flex: 0 0 auto; }
.mc-model { font-size: 10.5px; padding: 1px 4px; border-radius: 9px; background: #f0efec;
  color: #6b6862; flex: 0 1 auto; min-width: 44px; max-width: 116px; border: none;
  cursor: pointer; font-family: inherit; }
.mc-model:hover { background: #e6e4e0; }
.mc-model:disabled { cursor: default; opacity: .7; }
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
/* 근거 카드 안의 '메모' 섹션 — 색 점 + 표시한 문장 + 내가 쓴 메모 */
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

/* '읽던 자리로' 되돌리기 알약 — 인용 칩 점프로 잃은 자리를 되돌린다.
   서랍 밖 body 직속이라 서랍을 닫아도 남고, 뷰어가 다시 그려져도 살아 있다.
   right/bottom/max-width는 JS가 본문 스크롤 컨테이너를 재서 넣는다 — CSS는 모양만 정한다
   (CSS로 right: calc(var(--mc-w) + …)를 쓰면 PDF를 켠 순간 PDF 위에 얹힌다). */
:root { --mjb-ring: #8a8780; }
#md-jump-back { position: fixed; z-index: 8500; right: 24px; bottom: 24px; display: none;
  align-items: center; max-width: 300px; background: #fff; color: #37352f;
  border: 1px solid #e6e4e0; border-radius: 999px; box-shadow: 0 6px 20px rgba(0,0,0,.16);
  font-size: 12px; overflow: hidden; }
#md-jump-back.on { display: inline-flex; animation: mdJumpIn .14s ease-out; }
#md-jump-back button { border: none; background: none; color: inherit; font: inherit;
  cursor: pointer; padding: 6px 12px; line-height: 1.45; white-space: nowrap; }
#md-jump-back .mjb-go { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
#md-jump-back .mjb-go:hover { background: #f7f6f3; }
#md-jump-back .mjb-i { color: #2383e2; font-weight: 600; }   /* 인용 칩과 같은 파랑 — 그 점프의 되돌림 */
#md-jump-back .mjb-w { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: #97948d; }
#md-jump-back .mjb-x { color: #97948d; font-size: 11px; padding: 6px 10px 6px 8px;
  border-left: 1px solid #ecebe8; }
#md-jump-back .mjb-x:hover { background: #f0efec; color: #6b6862; }
#md-jump-back button:focus-visible { outline: 2px solid #2383e2; outline-offset: -2px; }
@keyframes mdJumpIn { from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); } }
/* 도착 표시 — 근거 문단의 파란 chat-cite-flash와 다른 무채색 테두리.
   "여기가 근거다"와 "여기로 돌아왔다"는 다른 말이다. flash()가 1400ms에 클래스를 떼므로 1.3s. */
.mjb-flash { animation: mdJumpFlash 1.3s ease-out; }
@keyframes mdJumpFlash { 0%, 55% { outline: 2px solid var(--mjb-ring, #8a8780);
    outline-offset: 2px; }
  100% { outline-color: transparent; } }
@media (prefers-reduced-motion: reduce) { #md-jump-back.on { animation: none; } }

@media (prefers-color-scheme: dark) {
  #md-chat-panel { background: #1f1f1f; border-color: #3a3a3a; color: #d4d4d4; }
  .mc-head, .mc-foot { border-color: #2c2c2c; }
  .mc-model, .mc-a code, .mc-a pre { background: #2c2c2c; color: #b8b8b8; }
  .mc-model:hover { background: #3a3a3a; }
  .mc-btn:hover, .mc-meta .mc-link:hover, .mc-hit:hover { background: #2c2c2c; }
  .mc-q { background: #24303d; }
  .mc-empty, .mc-meta { color: #8f8f8f; }
  .mc-hit b { color: #d4d4d4; }
  .mc-tog { color: #8f8f8f; }
  .mc-input { background: #262626; border-color: #3a3a3a; }
  .mc-input:disabled { background: #232323; color: #7d7d7d; }
  .mc-turn + .mc-turn { border-color: #2c2c2c; }
  :root { --mjb-ring: #a9a69f; }
  #md-jump-back { background: #262626; border-color: #3a3a3a; color: #d4d4d4;
    box-shadow: 0 6px 20px rgba(0,0,0,.5); }
  #md-jump-back .mjb-go:hover, #md-jump-back .mjb-x:hover { background: #2c2c2c; }
  #md-jump-back .mjb-i { color: #5a96e6; }
  #md-jump-back .mjb-w, #md-jump-back .mjb-x { color: #8f8f8f; }
  #md-jump-back .mjb-x { border-left-color: #333; }
}
"""

# 서랍 마크업 + 클라이언트 로직. 서랍은 body에 고정돼 있어 뷰어가 다시 그려져도 상태가 유지된다.
HTML = """
<div id="md-chat-panel">
  <div class="mc-handle" title="드래그해서 너비 조절"></div>
  <div class="mc-head"><b>논문에 질문</b><select class="mc-model" title="이 서랍에서 쓸 모델 — 번역 설정과 별개입니다"></select>
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
<!-- 서랍 **밖**이다: 서랍 안에 두면 서랍을 닫는 순간 함께 사라지는데, 서랍을 접고 본문만
     읽는 사람에게도 읽던 자리로 돌아갈 길은 있어야 한다. -->
<div id="md-jump-back" role="status" aria-live="polite">
  <button type="button" class="mjb-go" aria-label="읽던 자리로 돌아가기"
    title="칩을 누르기 전 읽던 자리로 돌아갑니다"><span class="mjb-i"
    aria-hidden="true">&#8617;</span><span>읽던 자리로</span><span class="mjb-w"></span></button>
  <button type="button" class="mjb-x" aria-label="되돌아가기 버튼 닫기"
    title="닫기 (Esc)">&#10005;</button>
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
  var openHits = {};  // '<턴id>' → 그 턴의 '검색된 문단' 목록이 펼쳐져 있나

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

  // ---- 모델 고르기 (챗봇 전용 — 번역 설정과 별개) ----
  var models = [], picked = '';
  var PROV = {openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Gemini'};
  function renderModels(){
    var cur = picked || window.__mdChatModel
      || (turns.length ? turns[turns.length - 1].model : '');
    if (!models.length){                    // 목록을 아직 못 받았으면 이름만 (드롭다운은 비활성)
      badge.innerHTML = '';
      var o = document.createElement('option');
      o.textContent = cur || ''; badge.appendChild(o); badge.disabled = true;
      return;
    }
    badge.disabled = false;
    badge.innerHTML = '';
    var byProv = {};
    models.forEach(function(m){ (byProv[m.provider] = byProv[m.provider] || []).push(m); });
    Object.keys(byProv).forEach(function(prov){
      var g = document.createElement('optgroup');
      g.label = PROV[prov] || prov;
      byProv[prov].forEach(function(m){
        var o = document.createElement('option');
        o.value = m.provider + '/' + m.model;
        // 키 없는 제공사는 목록에 남기되 못 고르게 — 왜 안 보이는지 헤매지 않도록
        o.textContent = m.model + (m.price ? '  ' + m.price : '') + (m.has_key ? '' : '  (키 없음)');
        o.disabled = !m.has_key;
        if (m.model === cur) o.selected = true;
        g.appendChild(o);
      });
      badge.appendChild(g);
    });
  }
  badge.addEventListener('change', function(){
    var v = (badge.value || '').split('/');
    if (v.length < 2) return;
    var prev = picked;
    picked = v[1];
    fetch('/chat/' + tok() + '/model', {method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: v[0], model: v[1]})})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (j.error){ picked = prev; errMsg = j.error; render(); return; }
        picked = j.model || picked;
        window.__mdChatModel = picked;
        window.__mdChatReady = !!j.ready;
        if (j.reason) window.__mdChatReason = j.reason;
        if (j.models) models = j.models;
        errMsg = null;
        render();
      })
      .catch(function(){ picked = prev; renderModels(); });
  });
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
    jbMark(els[0]);        // 스크롤을 시작하기 **전에** 읽던 자리를 집는다(중간값을 집으면 밀린다)
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
    jbMark(ms[0]);                            // gotoRow로 빠진 경우엔 거기서 무장한다(이중 무장 없음)
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

  // ---- '읽던 자리로' 되돌리기 (칩 점프가 읽던 자리를 잃지 않게) ----
  // 칩을 누르면 본문이 근거 문단으로 날아가 버려 읽던 자리를 잃는다. 점프 직전의 화면을
  // 구조(행 → 쪽 → 블록 → 블록 안 비율)로 집어 두고, 알약을 눌렀을 때 그 화면을 재현한다.
  var JB_EDGE = 6;            // 기준선 = 컨테이너 위끝 + 6 (app.py PDF 싱크 top+6과 같은 정의)
  var JB_ARM = 0.30, JB_ARM_MIN = 120;   // 이만큼 움직일 점프에만 알약을 띄운다
  var JB_HOME = 0.22, JB_HOME_MIN = 80;  // 이만큼 가까워지면 알약은 할 일이 없다 (무장 임계보다
                                         //  작다 = 히스테리시스. 뜨자마자 사라지지 않는다)
  var JB_GRACE = 700;         // 나가는 점프가 멎을 때까지 자동 해제를 미룬다
  var JB_BLOCKS = 'p,li,h1,h2,h3,h4,h5,h6,pre,blockquote,table,figure,img';
  var jbEl = document.getElementById('md-jump-back');
  var pin = null, jbG = null, jbRaf = 0, jbFrames = 0, jbKey = '', jbJumpAt = 0;

  function jbScroller(from){
    if (from && from.closest){
      var c = from.closest('.sbs-grid, .vpane');     // 실측: computed overflow 조상 탐색과 동일
      if (c){ jbG = c; return c; }
    }
    if (jbG && jbG.isConnected && jbG.clientHeight > 0) return jbG;  // 재렌더까지는 그대로 쓴다
    jbG = null;
    var all = document.querySelectorAll('.sbs-grid, .vpane');
    for (var i = 0; i < all.length; i++){
      var r = all[i].getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      // [data-row]가 뷰어 그리드를 가려낸다 — 번역 탭 결과 그리드(같은 클래스, data-row 없음)를
      // 잡지 않게. 실측으로 숨은 탭은 DOM에서 아예 빠지지만 app.py가 바뀌어도 버티게 남긴다.
      if (all[i].matches('[data-row]') || all[i].querySelector('[data-row]')){
        jbG = all[i]; break;
      }
    }
    return jbG;
  }
  function jbNorm(s){ return (s || '').replace(/\\s+/g, ' ').trim(); }
  function jbCells(g, row){
    var sel = '[data-row="' + row + '"]';
    var self = (g.matches && g.matches(sel)) ? [g] : [];   // .vpane은 g.querySelector로 안 잡힌다
    return self.concat([].slice.call(g.querySelectorAll(sel)));
  }
  // 지금 화면 맨 위에 걸린 자리를 구조로 집는다. 참조(노드)는 담지 않는다 — 뷰어는 패널을 토글할
  // 때마다 그리드를 통째로 다시 만들므로, 붙들어 둔 노드는 조용히 죽은 노드가 된다.
  function jbCapture(g){
    var edge = g.getBoundingClientRect().top + JB_EDGE;
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    var els = (g.matches && g.matches('[data-row]') ? [g] : [])
      .concat([].slice.call(g.querySelectorAll('[data-row]')));
    var band = null, i, r;
    for (i = 0; i < els.length; i++){
      r = els[i].getBoundingClientRect();
      if (r.top > edge) break;                  // 아직 기준선 아래 — 문서 순서라 여기서 끝
      var key = els[i].getAttribute('data-row');
      // 한 행의 두 셀은 같은 grid row라 top이 같고 DOM에서도 붙어 있다 → 연속 묶음이면 한 밴드
      if (!band || band.row !== key) band = {row: key, cs: [], t: r.top, b: r.bottom};
      band.cs.push(els[i]);
      band.t = Math.min(band.t, r.top); band.b = Math.max(band.b, r.bottom);
    }
    if (!band) return null;
    // 쪽 고르기: 기준선 아래로 내용이 이어지는 셀만, 그중 원문(en) 우선. 짧은 쪽 셀을 잡아
    // 비율이 늘 1이 되는 사고를 막고, 재번역에도 원문 텍스트가 더 잘 버틴다.
    var live = [];
    for (i = 0; i < band.cs.length; i++)
      if (band.cs[i].getBoundingClientRect().bottom > edge) live.push(band.cs[i]);
    var pool = live.length ? live : band.cs, cell = pool[0];
    for (i = 0; i < pool.length; i++)
      if (pool[i].getAttribute('data-side') === 'en'){ cell = pool[i]; break; }
    // 블록 하위 앵커 — 실측으로 한 셀이 2440px·17블록까지 간다. 행 단위로만 집으면 최대 3화면
    // 어긋난다. 폭이 바뀌어 줄바꿈이 전부 달라져도 블록 안 비율은 버틴다.
    var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
    var bi = 0, bf = 0, head = '';
    for (i = 0; i < bs.length; i++)
      if (bs[i].getBoundingClientRect().top <= edge) bi = i;
    if (bs.length){
      var br = bs[bi].getBoundingClientRect();
      bf = br.height > 0 ? Math.max(0, Math.min(1, (edge - br.top) / br.height)) : 0;
      head = jbNorm(bs[bi].textContent).slice(0, 72);
    }
    var span = band.b - band.t;
    var rf = span > 0 ? Math.max(0, Math.min(1, (edge - band.t) / span)) : 0;
    var row = parseInt(band.row, 10) || 0;
    var pv = document.querySelector('.vpdf');
    return {row: row, side: cell.getAttribute('data-side') || '', bi: bi, bf: bf, rf: rf,
            ratio: max > 0 ? g.scrollTop / max : 0,   // app.py 복원과 같은 통화 (최후 폴백)
            head: head, sec: headingOf(row), at: Date.now(),
            dest: g.scrollTop, destFixed: 0,
            pdf: pv ? pv.scrollTop : null, pdfH: pv ? pv.scrollHeight : null};
  }
  function jbNeed(g, el){        // scrollIntoView({block:'center'})가 만들 이동량 (범위 클램프)
    var gr = g.getBoundingClientRect(), er = el.getBoundingClientRect();
    var want = (er.top - gr.top) - Math.max(0, (g.clientHeight - er.height) / 2);
    var lo = -g.scrollTop, hi = (g.scrollHeight - g.clientHeight) - g.scrollTop;
    return Math.max(lo, Math.min(hi, want));
  }
  function jbMark(target){
    if (!jbEl || !target) return false;
    var g = jbScroller(target);
    if (!g) return false;
    var need = jbNeed(g, target);
    // 이미 화면에 있는 문단으로 가는 칩엔 알약을 띄우지 않는다 — 자리를 잃지 않았으니 소음이다
    if (Math.abs(need) < Math.max(JB_ARM_MIN, g.clientHeight * JB_ARM)) return false;
    if (pin){
      // 연달아 칩을 눌러도 '처음 읽던 자리'를 지킨다(히스토리 스택이 아니라 집 하나다).
      // 예외: 지난 점프가 놓아둔 자리에서 1.2화면 이상 옮겨가 정착했으면 거기가 새 '읽던 자리'다.
      if (Math.abs(g.scrollTop - (pin.dest || 0)) > g.clientHeight * 1.2) pin = jbCapture(g);
    } else pin = jbCapture(g);
    if (!pin) return false;
    pin.dest = g.scrollTop + need;      // 점프가 끝나면 여기 있을 것 (아래 tick이 실측으로 고친다)
    pin.destFixed = 0;
    jbJumpAt = Date.now();
    var go = jbEl.querySelector('.mjb-go');
    jbEl.querySelector('.mjb-w').textContent = pin.sec ? ' · ' + pin.sec.slice(0, 26) : '';
    go.title = '칩을 누르기 전 읽던 자리로 돌아갑니다' + (pin.sec ? ' — ' + pin.sec : '')
      + (pin.head ? ' \\u201C' + pin.head.slice(0, 50) + '\\u201D' : '');
    go.setAttribute('aria-label', '읽던 자리로 돌아가기' + (pin.sec ? ' — ' + pin.sec : ''));
    jbKey = '';                          // 다음 프레임에 좌표를 새로 쓰게
    if (!jbRaf) jbRaf = requestAnimationFrame(jbTick);
    return true;
  }
  function jbTarget(g, p){
    var cs = jbCells(g, p.row), cell = null, i;
    for (i = 0; i < cs.length; i++) if (cs[i].getAttribute('data-side') === p.side) cell = cs[i];
    cell = cell || cs[0] || null;
    if (cell){
      var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
      if (bs.length){
        var el = bs[Math.min(p.bi, bs.length - 1)];
        var same = cell.getAttribute('data-side') === p.side;
        // 같은 쪽이면 텍스트로 검증한다 — 재번역으로 행 인덱스가 밀렸을 때 조용히 틀린 자리로
        // 가는 것이 비율 폴백보다 더 나쁘다(티가 안 난다). 다른 쪽 셀이면 검증하지 않는다:
        // 그 짝은 같은 문장의 번역이라 텍스트가 당연히 다르다.
        if (!same || !p.head || jbNorm(el.textContent).slice(0, 40) === p.head.slice(0, 40))
          return {el: el, f: p.bf};
      }
    }
    if (p.head){                       // 행 인덱스가 밀렸다 → 텍스트로 다시 찾는다
      var pre = p.head.slice(0, 40), hit = null;
      var all = g.querySelectorAll(JB_BLOCKS);
      for (i = 0; i < all.length; i++){
        if (jbNorm(all[i].textContent).indexOf(pre) === 0){ hit = all[i]; break; }
      }
      if (hit) return {el: hit, f: p.bf};
    }
    if (cell && cell !== g) return {el: cell, f: p.rf};   // 빈 셀·표만 있는 셀 → 행 밴드 비율
    return null;                                          // → 호출자가 p.ratio로 떨어진다
  }
  // 앵커의 위끝이 edge - f*height에 오면 그 화면 한 장이 재현된다. offsetTop은 쓰지 않는다 —
  // 셀은 grid item이고 마크다운은 래퍼 한 겹 안이라 offset 부모 사슬이 렌더마다 같지 않다.
  function jbTop(g, p, t){
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    t = t || jbTarget(g, p);
    if (!t) return max > 0 ? p.ratio * max : 0;
    var er = t.el.getBoundingClientRect();
    var y = g.scrollTop + (er.top - g.getBoundingClientRect().top) - JB_EDGE + t.f * er.height;
    return Math.max(0, Math.min(max, y));
  }
  function jbPlace(){
    var g = jbScroller(null), r = g && g.getBoundingClientRect();
    if (!g || !r || r.width < 160 || r.height < 120){ jbEl.classList.remove('on'); return null; }
    // 컨테이너 rect를 재기 때문에 서랍 폭(--mc-w)·본문 padding·목차 너비·텍스트|PDF 분할을
    // 하나도 몰라도 언제나 '지금 읽는 영역' 안에 온다. +24는 세로 스크롤바 여유.
    var right = Math.max(12, window.innerWidth - r.right + 24);
    var bottom = Math.max(14, window.innerHeight - r.bottom + 18);
    var mw = Math.max(180, Math.min(300, r.width - 28));
    var key = Math.round(right) + '|' + Math.round(bottom) + '|' + Math.round(mw);
    if (key !== jbKey){                 // 값이 바뀔 때만 써서 레이아웃을 흔들지 않는다
      jbKey = key;
      jbEl.style.right = right + 'px'; jbEl.style.bottom = bottom + 'px';
      jbEl.style.maxWidth = mw + 'px';
    }
    jbEl.classList.add('on');
    return g;
  }
  // rAF 하나가 배치와 해제를 다 맡는다 — 재렌더·splitter·서랍 폭·목차·리사이즈·탭 이동을 전부
  // 흡수하고, 탭이 숨으면 저절로 멈춘다. resize/scroll 리스너나 MutationObserver를 늘리지 않는다.
  function jbTick(){
    jbRaf = 0;
    if (!pin) return;
    var g = jbPlace();
    // 프레임마다 rect 1~2회. 무거운 일(앵커 재해석)은 12프레임(≈200ms)마다 한 번.
    if (g && (++jbFrames % 12 === 0) && Date.now() - jbJumpAt > JB_GRACE){
      if (!pin.destFixed){ pin.dest = g.scrollTop; pin.destFixed = 1; }  // 점프가 놓아둔 실제 자리
      // 스스로 그 자리로 돌아왔으면 알약은 할 일이 없다 ("읽던 자리로"가 거짓말이 된다)
      if (Math.abs(g.scrollTop - jbTop(g, pin)) < Math.max(JB_HOME_MIN, g.clientHeight * JB_HOME))
        return jbDismiss();
    }
    jbRaf = requestAnimationFrame(jbTick);
  }
  function jbAfter(g, p){
    g.dispatchEvent(new Event('scroll'));   // PDF 싱크 프라이밍 (app.py의 복원과 같은 손짓)
    var pv = document.querySelector('.vpdf');
    if (!pv || p.pdf == null || pv.scrollHeight !== p.pdfH) return;   // lazy 이미지로 좌표계가 변했다
    // 싱크는 페이지 위끝까지만 맞춘다 — 페이지 안에서 보던 위치까지 되돌린다.
    // 싱크의 rAF 콜백이 지난 뒤에 써야 덮이지 않는다.
    requestAnimationFrame(function(){
      requestAnimationFrame(function(){ pv.scrollTop = p.pdf; });
    });
  }
  function jbBack(){
    var p = pin, g = jbScroller(null);
    if (!p) return;
    if (!g) return;                    // 본문이 없다(다른 탭·PDF 전용) → 아무것도 하지 않고 핀을 지킨다
    var t = jbTarget(g, p), y = jbTop(g, p, t);
    var far = Math.abs(y - g.scrollTop) > g.clientHeight * 4;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var soft = !far && !reduce;        // 먼 거리 smooth는 느리고 멀미난다
    g.scrollTo({top: y, behavior: soft ? 'smooth' : 'auto'});
    if (t && t.el !== g && t.el.classList) flash([t.el], 'mjb-flash');
    var n = 0, last = -1;
    setTimeout(function nudge(){       // 늦게 뜬 그림·KaTeX가 위쪽을 밀면 좌표가 달라진다
      if (last >= 0 && Math.abs(g.scrollTop - last) > 24) return jbAfter(g, p);  // 사용자가 굴렸다
      var y2 = jbTop(g, p);
      if (Math.abs(g.scrollTop - y2) > 4) g.scrollTop = y2;
      last = g.scrollTop;
      if (++n < 3) setTimeout(nudge, 180); else jbAfter(g, p);
    }, soft ? 420 : 60);
    jbDismiss();                       // 돌아왔으면 그 버튼은 할 일이 없다
  }
  function jbDismiss(){
    pin = null; jbKey = ''; jbFrames = 0;
    if (jbEl) jbEl.classList.remove('on');
    if (jbRaf){ cancelAnimationFrame(jbRaf); jbRaf = 0; }
  }
  if (jbEl){
    jbEl.querySelector('.mjb-go').addEventListener('click', jbBack);
    jbEl.querySelector('.mjb-x').addEventListener('click', jbDismiss);
    // fixed 알약 위에서 굴리면 밑의 그리드가 안 움직인다(가장 가까운 스크롤 조상이 body다).
    // 넘겨주지 않으면 "읽다가 알약 위에서 휠이 먹통"이라는 새 짜증을 만든다.
    jbEl.addEventListener('wheel', function(ev){
      var g = jbScroller(null);
      if (!g) return;
      var unit = ev.deltaMode === 1 ? 16 : (ev.deltaMode === 2 ? g.clientHeight : 1);
      g.scrollTop += ev.deltaY * unit;
      ev.preventDefault();
    }, {passive: false});
  }
  // 다른 점프(목차·섹션 트리 등)도 나중에 이걸 재사용할 수 있게 이름 하나만 내놓는다.
  // mark(el)은 **프로그래매틱 스크롤을 시작하기 직전**에 부른다. 반환값 = 알약을 띄웠나.
  window.__mdJumpBack = {mark: jbMark, back: jbBack, dismiss: jbDismiss,
                         state: function(){ return pin; }};

  // ---- 렌더 ----
  var SWATCH = {yellow: '#ffe08a', green: '#b5e7b8', blue: '#a9d8f5', pink: '#f9bcd4',
                purple: '#d8c6f5'};
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
      // 칩은 곧장 뷰어의 그 문단으로 보낸다 — 원문·번역은 거기 이미 나란히 있으므로
      // 서랍 안에 한 번 더 그리지 않는다(툴팁으로 어디로 가는지만 미리 보여준다).
      chip.addEventListener('click', function(ev){
        ev.preventDefault();
        if (isNote) gotoNote(c.id, c.row); else gotoRow(c.row);
      });
    });
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
      btn.addEventListener('click', function(){
        if (openHits[turn.id]) delete openHits[turn.id]; else openHits[turn.id] = 1;
        render();
      });
      meta.appendChild(btn);
    }
    wrap.appendChild(meta);
    if (openHits[turn.id]) wrap.appendChild(hitsCard(turn));
    if (turn.error){
      var e = document.createElement('div');
      e.className = 'mc-err'; e.textContent = turn.error;
      wrap.appendChild(e);
    }
    return wrap;
  }
  function render(){
    renderModels();
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
      if (j.models) models = j.models;
      if (j.picked) picked = j.picked;
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
    turns = []; openHits = {}; errMsg = null; render();
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
  // 서랍 위끝을 헤더 아래에 맞춘다 (메모 서랍이 여는 순간 하는 것과 같은 계산).
  // 창 크기가 바뀌면 헤더 높이도 바뀔 수 있어 resize에서도 다시 잰다.
  function syncTop(){
    var h = document.querySelector('.q-header');
    var top = h ? Math.max(0, h.getBoundingClientRect().bottom) : 0;
    document.documentElement.style.setProperty('--mc-top', top + 'px');
  }
  window.addEventListener('resize', syncTop);
  syncTop();
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
      syncTop();
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
    if (ev.key !== 'Escape') return;
    // 알약이 있으면 알약만 닫는다 — Esc 한 번에 알약과 서랍이 동시에 사라지지 않게.
    // 단 그림 확대·메모 카드·찾기 바가 열려 있으면 Esc의 주인은 그쪽이라 비켜선다.
    if (pin && !document.querySelector('#md-img-zoom.open, #md-anno-pop.open, #md4-find.on')){
      jbDismiss();
      return;
    }
    if (panel.classList.contains('open')) panel.classList.remove('open');
  });
  render();
})();
</script>
"""


def model_options() -> list[dict]:
    """서랍 드롭다운에 채울 모델 목록 — 제공사별 저렴→비쌈, 키 없는 건 표시만 하고 못 고른다."""
    from md4paper.llm.base import PRICING

    keys = config.key_status()
    out: list[dict] = []
    for prov in config.PROVIDERS:
        for mid in config.MODEL_TIERS.get(prov, ()):
            pr = PRICING.get(mid)
            out.append({"provider": prov, "model": mid,
                        "price": (f"${pr[0]:g}/${pr[1]:g}" if pr else ""),
                        "has_key": bool(keys.get(prov))})
    return out


def readiness() -> tuple[bool, str]:
    """LLM 키가 준비됐는지 (준비됐나?, 안내 메시지). 네트워크는 건드리지 않는다."""
    try:
        config.build_chat_provider()
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — 설정 파일 손상 등도 안내로 보여준다
        return False, str(e) or e.__class__.__name__
    return True, ""


def _model_name() -> str:
    """헤더 드롭다운의 현재 값 (키가 없으면 빈 문자열)."""
    try:
        return str(config.build_chat_provider().model)
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

    make = build_provider or config.build_chat_provider

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
                "models": model_options(), "picked": config.resolve_chat_choice()[1],
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

    @fastapi_app.put("/chat/{token}/model")
    def _put_model(token: str, body: dict):  # noqa: ANN202 — 챗봇이 쓸 모델 고르기
        if wd_for(token) is None:
            raise HTTPException(status_code=404)
        prov = str((body or {}).get("provider") or "").strip()
        mid = str((body or {}).get("model") or "").strip()
        try:
            config.set_chat_choice(prov or None, mid or None)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        ok, reason = _readiness()
        return {"ok": True, "ready": ok, "reason": reason,
                "model": config.resolve_chat_choice()[1], "models": model_options()}

    @fastapi_app.delete("/chat/{token}")
    def _del_chat(token: str):  # noqa: ANN202 — 대화 기록 삭제
        cur = wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        chat.clear(cur)
        return {"ok": True}
