"""캔버스 수식 팝오버 — 수식을 한 번 누르면 원본 PDF의 그 영역(크롭)을 띄워 LaTeX와 대조하고,
틀렸으면 `다시 생성`으로 같은 그림을 LLM에 다시 읽힌다.

흐름: 정렬 그리드를 그릴 때 수식 블록 앞에 빈 표식(`span.md4-fx[data-fx]`, §formula_fix.tag)을
붙인다 → 클릭한 `<math>`(또는 읽기 실패로 남은 수식 그림) 바로 앞의 표식으로 레코드 id를 안다 →
`/formula/<tok>/<id>`로 쪽·LaTeX·크롭 주소를 받아 띄운다. `다시 생성`은 `/formula/<tok>/<id>/regen`
(문서는 그대로, 후보와 미리보기 HTML만 돌려준다) → `적용`을 눌러야 `md4-fx-apply` 이벤트로
서버가 원천에 반영하고 캔버스를 다시 그린다.

클릭 규칙은 문장 짝 툴팁과 같게 맞춘다: 한 번 클릭만(e.detail === 1), 250ms 미뤄 띄우고 그사이
두 번째 클릭이 오면 물린다 — 더블클릭(메모)은 그대로다. 드래그 선택 중이면 띄우지 않는다.
"""

from __future__ import annotations

import json
from typing import Callable

from md4paper import config

_MD_EXTRAS = "fenced-code-blocks tables latex"  # 캔버스(grid_view)와 같은 렌더 — 미리보기가 그대로 보이게


def preview_html(block: str) -> str:
    """후보 블록을 캔버스와 같은 렌더러(markdown2 latex)로 — 팝오버 미리보기용."""
    from nicegui.elements.markdown import prepare_content

    return prepare_content(block, _MD_EXTRAS)


def register_routes(fastapi_app, wd_for: Callable[[str], object],  # noqa: ANN001
                    build_provider: Callable[[], object] | None = None) -> None:
    """`/formula/{token}/{fid}`(정보) · `/formulaimg/{token}/{fid}`(원본 크롭) · `…/regen`(다시 읽기)."""
    from fastapi.responses import FileResponse
    from starlette.exceptions import HTTPException

    from md4paper import formula_fix

    make = build_provider or config.build_provider

    def _rec(token: str, fid: str):  # noqa: ANN202
        cur = wd_for(token)
        rec = formula_fix.find(cur, fid) if cur is not None else None
        if rec is None:
            raise HTTPException(status_code=404)
        return cur, rec

    @fastapi_app.get("/formula/{token}/{fid}")
    def _get_formula(token: str, fid: str):  # noqa: ANN202
        _cur, rec = _rec(token, fid)
        return {"id": rec["id"], "page": rec.get("page"), "number": rec.get("number") or "",
                "latex": rec.get("latex") or "", "kind": rec.get("kind") or "",
                "error": rec.get("error") or "", "img": f"/formulaimg/{token}/{rec['id']}"}

    @fastapi_app.get("/formulaimg/{token}/{fid}")
    def _get_formula_img(token: str, fid: str):  # noqa: ANN202 — 추출 때 잘라 둔 원본 크롭
        cur, rec = _rec(token, fid)
        base = cur.extract_images.resolve()
        target = (base / rec["file"]).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target, media_type="image/png")

    @fastapi_app.post("/formula/{token}/{fid}/regen")
    def _regen_formula(token: str, fid: str):  # noqa: ANN202 — 동기 함수: 스레드풀에서 LLM을 기다린다
        cur, _rec_ = _rec(token, fid)
        try:
            provider = make()
        except Exception as e:  # noqa: BLE001 — 키 없음 등은 팝오버에 그대로 보여 준다
            return {"error": str(e) or e.__class__.__name__}
        got = formula_fix.regenerate(cur, fid, provider)
        if not got.get("error"):
            got["html"] = preview_html(got["block"])
        return got


def init_js(token: str) -> str:
    """논문별 토큰 — 화면을 갈아 끼울 때마다 다시 싣는다(아래 HTML 스크립트는 한 번만 돈다)."""
    return f"window.__md4FxTok = {json.dumps(token)};"


CSS = """
.sbs-grid .md-preview math[display="block"].md4-fx-on, .sbs-grid .md-preview math.md4-fx-on,
.sbs-grid .md-preview img.md4-fx-on { cursor: pointer; border-radius: 4px; }
.sbs-grid .md-preview math.md4-fx-on:hover, .sbs-grid .md-preview img.md4-fx-on:hover {
  background: rgba(35,131,226,.07); }
#md4-fx-pop { position: fixed; z-index: 3000; display: none; width: min(560px, calc(100vw - 32px));
  max-height: calc(100vh - 32px); overflow: auto; background: #fff; color: #222;
  border: 1px solid #ddd; border-radius: 10px; box-shadow: 0 10px 34px rgba(0,0,0,.18);
  padding: 12px 14px; font-size: 13px; }
#md4-fx-pop.open { display: block; }
#md4-fx-pop .fx-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; color: #666; }
#md4-fx-pop .fx-head b { color: #222; font-weight: 600; }
#md4-fx-pop .fx-x { margin-left: auto; border: 0; background: none; font-size: 18px; line-height: 1;
  cursor: pointer; color: #888; padding: 0 2px; }
#md4-fx-pop .fx-lbl { font-size: 11px; color: #888; margin: 8px 0 4px; letter-spacing: .02em; }
#md4-fx-pop .fx-src { background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 8px;
  text-align: center; }
#md4-fx-pop .fx-src img { max-width: 100%; height: auto; display: inline-block; }
#md4-fx-pop pre { white-space: pre-wrap; word-break: break-all; background: #f6f6f6; border-radius: 6px;
  padding: 6px 8px; margin: 0; font-size: 12px; max-height: 140px; overflow: auto; }
#md4-fx-pop .fx-new { border: 1px solid #cfe2fb; background: #f5f9ff; border-radius: 6px; padding: 6px 8px;
  overflow-x: auto; }
#md4-fx-pop .fx-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }
#md4-fx-pop .fx-btns button { border: 1px solid #d0d0d0; background: #fff; border-radius: 6px;
  padding: 4px 12px; cursor: pointer; font-size: 13px; }
#md4-fx-pop .fx-btns button.fx-primary { background: #2383e2; border-color: #2383e2; color: #fff; }
#md4-fx-pop .fx-btns button:disabled { opacity: .55; cursor: default; }
#md4-fx-pop .fx-err { color: #c62828; margin-top: 8px; white-space: pre-wrap; }
#md4-fx-pop .fx-note { color: #888; font-size: 12px; margin-top: 6px; }
@media (prefers-color-scheme: dark) {
  #md4-fx-pop { background: #262626; color: #ddd; border-color: #3a3a3a; }
  #md4-fx-pop .fx-head b { color: #eee; }
  #md4-fx-pop .fx-src { background: #fff; border-color: #3a3a3a; }  /* 크롭은 흰 바탕 그림 */
  #md4-fx-pop pre { background: #1e1e1e; }
  #md4-fx-pop .fx-new { background: #1f2a38; border-color: #2d4566; }
  #md4-fx-pop .fx-btns button { background: #2e2e2e; color: #ddd; border-color: #444; }
  #md4-fx-pop .fx-btns button.fx-primary { background: #2383e2; border-color: #2383e2; color: #fff; }
}
"""

HTML = """
<div id="md4-fx-pop" role="dialog" aria-label="수식 원본 대조"></div>
<script>
(function(){
  if (window.__md4FxInit) return; window.__md4FxInit = true;
  var pop = document.getElementById('md4-fx-pop');
  if (!pop) return;
  var timer = null, cur = null, cand = null;

  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

  // 클릭 지점 → 표식 id. 셀 안의 표식·수식·그림을 문서 순서로 늘어놓고, 그 수식 바로 앞이 표식이면 그 id.
  // (문장 짝 분할이 글자 노드를 span으로 감싸도 순서는 그대로라 흔들리지 않는다)
  function target(el){
    if (!el || !el.closest) return null;
    var m = el.closest('math') || (el.tagName === 'IMG' ? el : null);
    if (!m) return null;
    var cell = m.closest('.sbs-grid .sbs-cell');
    if (!cell || cell.classList.contains('sbs-ghost')) return null;
    var all = cell.querySelectorAll('span.md4-fx, math, img');
    for (var i = 0; i < all.length; i++){
      if (all[i] !== m) continue;
      var prev = all[i - 1];
      return prev && prev.classList && prev.classList.contains('md4-fx')
        ? {id: prev.getAttribute('data-fx'), el: m} : null;
    }
    return null;
  }
  window.__md4FxAt = function(el){ return !!target(el); };   // 문장 짝 툴팁·그림 확대가 양보할 때 쓴다

  // 눌러 볼 수 있는 수식에 손가락 커서 — 캔버스를 다시 그릴 때마다 (표식이 새로 생긴다)
  function mark(){
    var fx = document.querySelectorAll('.sbs-grid span.md4-fx');
    for (var i = 0; i < fx.length; i++){
      var n = fx[i].nextElementSibling;
      if (n && (n.tagName.toLowerCase() === 'math' || n.tagName === 'IMG')) n.classList.add('md4-fx-on');
    }
  }
  new MutationObserver(function(){ requestAnimationFrame(mark); })
    .observe(document.body, {childList: true, subtree: true});

  function place(rect){
    var w = pop.offsetWidth, h = pop.offsetHeight, vw = innerWidth, vh = innerHeight;
    var x = Math.min(Math.max(16, rect.left), vw - w - 16);
    var y = rect.bottom + 8;
    if (y + h > vh - 16) y = Math.max(16, rect.top - h - 8);
    pop.style.left = x + 'px'; pop.style.top = y + 'px';
  }
  function close(){ pop.classList.remove('open'); cur = null; cand = null; }
  window.__md4FxClose = close;

  function render(msg){
    if (!cur) return;
    var d = cur.info, h = '';
    h += '<div class="fx-head"><b>수식</b><span>p.' + esc(d.page) + (d.number ? ' · 식 (' + esc(d.number) + ')' : '')
      + '</span><button class="fx-x" data-act="close" title="닫기">×</button></div>';
    h += '<div class="fx-lbl">원본 PDF</div><div class="fx-src"><img src="' + esc(d.img) + '" alt="원본 수식"/></div>';
    h += '<div class="fx-lbl">지금 LaTeX</div><pre>' + (d.latex ? esc(d.latex) : '(읽기 실패 — 그림으로 남아 있음)') + '</pre>';
    if (cand && cand.html){
      h += '<div class="fx-lbl">다시 생성한 결과</div><div class="fx-new md-preview">' + cand.html + '</div>';
      h += '<pre style="margin-top:4px">' + esc(cand.latex) + '</pre>';
    }
    if (msg) h += '<div class="' + (msg.err ? 'fx-err' : 'fx-note') + '">' + esc(msg.text) + '</div>';
    h += '<div class="fx-btns">';
    if (cand && cand.html){
      h += '<button data-act="cancel">취소</button>'
        + '<button data-act="regen">다시 생성</button>'
        + '<button class="fx-primary" data-act="apply">적용</button>';
    } else {
      h += '<button class="fx-primary" data-act="regen"' + (cur.busy ? ' disabled' : '') + '>'
        + (cur.busy ? '생성 중…' : '다시 생성') + '</button>';
    }
    h += '</div>';
    pop.innerHTML = h;
    place(cur.rect);
  }

  function open(t){
    var tok = window.__md4FxTok; if (!tok) return;
    var rect = t.el.getBoundingClientRect();
    fetch('/formula/' + tok + '/' + encodeURIComponent(t.id)).then(function(r){
      if (!r.ok) throw 0; return r.json();
    }).then(function(info){
      cur = {id: t.id, tok: tok, info: info, rect: rect, busy: false}; cand = null;
      pop.classList.add('open');
      render();
    }).catch(function(){});
  }

  function regen(){
    if (!cur || cur.busy) return;
    var me = cur; me.busy = true; cand = null;
    render({text: '원본 그림을 LLM에 다시 읽히는 중…'});
    fetch('/formula/' + me.tok + '/' + encodeURIComponent(me.id) + '/regen', {method: 'POST'})
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (cur !== me) return;
        me.busy = false;
        if (res.error){ render({err: true, text: '다시 생성하지 못했습니다: ' + res.error}); return; }
        cand = res; render({text: '원본과 비교해 보고 맞으면 적용하세요.'});
      })
      .catch(function(){ if (cur === me){ me.busy = false; render({err: true, text: '서버에 닿지 못했습니다.'}); } });
  }

  pop.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('button[data-act]'); if (!b) return;
    var act = b.getAttribute('data-act');
    if (act === 'close') close();
    else if (act === 'cancel'){ cand = null; render(); }
    else if (act === 'regen') regen();
    else if (act === 'apply' && cand && typeof emitEvent === 'function'){
      b.disabled = true; b.textContent = '적용 중…';
      emitEvent('md4-fx-apply', {id: cur.id, latex: cand.latex, kind: cand.kind});
    }
  });
  document.addEventListener('mousedown', function(e){
    if (pop.classList.contains('open') && !pop.contains(e.target)) close();
  }, true);
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape' && pop.classList.contains('open')) close();
  });

  // 한 번 클릭 = 원본 대조. 두 번째 클릭(더블클릭 = 메모)이 오면 물린다 — 문장 짝 툴팁과 같은 규칙.
  document.addEventListener('click', function(e){
    if (timer){ clearTimeout(timer); timer = null; }
    if (e.detail !== 1) return;
    var sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    var t = target(e.target); if (!t) return;
    e.preventDefault();                       // 그림을 감싼 링크가 있어도 이동하지 않게
    timer = setTimeout(function(){ timer = null; open(t); }, 250);
  });
})();
</script>
"""
