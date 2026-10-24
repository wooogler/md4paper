"""캔버스 수식 팝오버 — 수식을 한 번 누르면 원본 PDF의 그 영역(크롭)을 띄워 LaTeX와 대조하고,
틀렸으면 `다시 생성`으로 같은 그림을 LLM에 다시 읽힌다.

흐름: 정렬 그리드를 그릴 때 수식 블록 앞에 빈 표식(`span.md4-fx[data-fx]`, §formula_fix.tag)을
붙인다 → 클릭한 `<math>`(또는 읽기 실패로 남은 수식 그림) 바로 앞의 표식으로 레코드 id를 안다 →
`/formula/<tok>/<id>`로 쪽·LaTeX·크롭 주소를 받아 띄운다. `다시 생성`은 `/formula/<tok>/<id>/regen`
(문서는 그대로, 후보와 미리보기 HTML만 돌려준다) → `적용`을 눌러야 `md4-fx-apply` 이벤트로
서버가 원천에 반영하고 캔버스를 다시 그린다.

**영역 지정.** 추출 때 잘라 둔 크롭이 수식을 덜 담았으면(첨자·적분 끝이 잘림) 그 그림으로는 몇 번을
다시 물어도 소용없다. 그래서 PDF 쪽을 띄워 사람이 드래그로 영역을 고르고, 그 영역을 새로 잘라 읽힌다.
고른 영역은 `적용` 때에야 그 수식의 크롭·bbox로 저장된다. 문장 속 인라인 수식(`inline-N`)은 크롭도
좌표도 없어서 처음부터 이 길로 간다 — 쪽은 앞뒤 낱말을 PDF 텍스트 레이어에서 찾아 짐작한다.

클릭 규칙은 문장 짝 툴팁과 같게 맞춘다: 한 번 클릭만(e.detail === 1), 250ms 미뤄 띄우고 그사이
두 번째 클릭이 오면 물린다 — 더블클릭(메모)은 그대로다. 드래그 선택 중이면 띄우지 않는다.
"""

from __future__ import annotations

import json
from typing import Callable

from narani import config

_MD_EXTRAS = "fenced-code-blocks tables latex"  # 캔버스(grid_view)와 같은 렌더 — 미리보기가 그대로 보이게


def preview_html(block: str) -> str:
    """후보 블록을 캔버스와 같은 렌더러(markdown2 latex)로 — 팝오버 미리보기용."""
    from nicegui.elements.markdown import prepare_content

    return prepare_content(block, _MD_EXTRAS)


def register_routes(fastapi_app, wd_for: Callable[[str], object],  # noqa: ANN001
                    build_provider: Callable[[], object] | None = None) -> None:
    """`/formula/{token}/{fid}`(정보) · `/formulaimg/{token}/{fid}`(원본 크롭) · `…/regen`(다시 읽기)."""
    from fastapi import Body
    from fastapi.responses import FileResponse
    from starlette.exceptions import HTTPException

    from narani import formula_fix

    make = build_provider or config.build_provider

    def _sub(token: str, fid: str):  # noqa: ANN202
        cur = wd_for(token)
        sub = formula_fix.subject(cur, fid) if cur is not None else None
        if sub is None:
            raise HTTPException(status_code=404)
        return cur, sub

    @fastapi_app.get("/formula/{token}/{fid}")
    def _get_formula(token: str, fid: str):  # noqa: ANN202
        from narani import pdfio

        cur, sub = _sub(token, fid)
        has_img = not sub["inline"] and (cur.extract_images / sub.get("file", "")).is_file()
        src = formula_fix.source_pdf(cur)
        return {"id": sub["id"], "inline": sub["inline"], "page": sub.get("page"),
                "number": sub.get("number") or "", "latex": sub.get("latex") or "",
                "kind": sub.get("kind") or "", "error": sub.get("error") or "",
                "img": f"/formulaimg/{token}/{sub['id']}" if has_img else "",
                # 영역 지정 화면용 — 지금 영역(없으면 크롭을 쪽에서 찾아 짐작)과 쪽 크기(pt)
                "bbox": None if sub["inline"] else formula_fix.guess_bbox(cur, sub),
                "pages": [list(p) for p in pdfio.page_sizes(src)] if src else []}

    @fastapi_app.get("/formulaimg/{token}/{fid}")
    def _get_formula_img(token: str, fid: str):  # noqa: ANN202 — 추출 때 잘라 둔 원본 크롭
        cur, sub = _sub(token, fid)
        if sub["inline"]:
            raise HTTPException(status_code=404)
        base = cur.extract_images.resolve()
        target = (base / sub["file"]).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(status_code=404)
        # 영역을 바꿔 적용하면 같은 이름으로 덮이므로 캐시하지 않는다
        return FileResponse(target, media_type="image/png", headers={"Cache-Control": "no-store"})

    @fastapi_app.post("/formula/{token}/{fid}/regen")
    def _regen_formula(token: str, fid: str, body: dict = Body(default=None)):  # noqa: ANN202 — 동기: 스레드풀에서 LLM 대기
        import base64

        cur, _sub_ = _sub(token, fid)
        body = body or {}
        try:
            provider = make()
        except Exception as e:  # noqa: BLE001 — 키 없음 등은 팝오버에 그대로 보여 준다
            return {"error": str(e) or e.__class__.__name__}
        got = formula_fix.regenerate(cur, fid, provider, page=body.get("page"), rect=body.get("rect"))
        crop = got.pop("crop", None)
        if crop:
            got["crop"] = "data:image/png;base64," + base64.b64encode(crop).decode("ascii")
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
#md4-fx-pop .fx-empty { color: #888; font-size: 12px; padding: 6px 0; }
#md4-fx-pop pre { white-space: pre-wrap; word-break: break-all; background: #f6f6f6; border-radius: 6px;
  padding: 6px 8px; margin: 0; font-size: 12px; max-height: 140px; overflow: auto; }
#md4-fx-pop .fx-new { border: 1px solid #cfe2fb; background: #f5f9ff; border-radius: 6px; padding: 6px 8px;
  overflow-x: auto; }
#md4-fx-pop .fx-btns { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
#md4-fx-pop .fxc-gap, #md4-fx-crop .fxc-gap { flex: 1; }
#md4-fx-pop .fx-btns button, #md4-fx-crop .fxc-bar button { border: 1px solid #d0d0d0; background: #fff;
  border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 13px; color: inherit; }
#md4-fx-pop .fx-btns button.fx-primary, #md4-fx-crop .fxc-bar button.fx-primary {
  background: #2383e2; border-color: #2383e2; color: #fff; }
#md4-fx-pop .fx-btns button:disabled, #md4-fx-crop .fxc-bar button:disabled { opacity: .55; cursor: default; }
#md4-fx-pop .fx-err { color: #c62828; margin-top: 8px; white-space: pre-wrap; }
#md4-fx-pop .fx-note { color: #888; font-size: 12px; margin-top: 6px; }
/* 영역 지정 — PDF 쪽 위에 드래그로 사각형 */
#md4-fx-crop { position: fixed; inset: 0; z-index: 3100; display: none; background: rgba(0,0,0,.45);
  align-items: center; justify-content: center; }
#md4-fx-crop.open { display: flex; }
#md4-fx-crop .fxc-panel { background: #fff; color: #222; border-radius: 10px; box-shadow: 0 12px 40px rgba(0,0,0,.3);
  width: min(1040px, 96vw); height: 92vh; display: flex; flex-direction: column; overflow: hidden; }
#md4-fx-crop .fxc-bar { display: flex; align-items: center; gap: 6px; padding: 8px 12px;
  border-bottom: 1px solid #e6e6e6; font-size: 13px; }
#md4-fx-crop .fxc-bar button[data-c="prev"], #md4-fx-crop .fxc-bar button[data-c="next"],
#md4-fx-crop .fxc-bar button[data-c="zin"], #md4-fx-crop .fxc-bar button[data-c="zout"] { padding: 2px 9px; }
#md4-fx-crop .fxc-pg { min-width: 64px; text-align: center; color: #555; }
#md4-fx-crop .fxc-hint { color: #888; margin-left: 8px; font-size: 12px; }
#md4-fx-crop .fxc-scroll { flex: 1; overflow: auto; background: #f0f0f0; padding: 12px; }
#md4-fx-crop .fxc-page { position: relative; margin: 0 auto; cursor: crosshair; user-select: none; }
#md4-fx-crop .fxc-page img { display: block; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.15);
  pointer-events: none; }
#md4-fx-crop .fxc-sel { position: absolute; display: none; border: 2px solid #2383e2;
  background: rgba(35,131,226,.12); box-shadow: 0 0 0 9999px rgba(0,0,0,.18); pointer-events: none; }
@media (prefers-color-scheme: dark) {
  #md4-fx-pop { background: #262626; color: #ddd; border-color: #3a3a3a; }
  #md4-fx-pop .fx-head b { color: #eee; }
  #md4-fx-pop .fx-src { background: #fff; border-color: #3a3a3a; }  /* 크롭은 흰 바탕 그림 */
  #md4-fx-pop pre { background: #1e1e1e; }
  #md4-fx-pop .fx-new { background: #1f2a38; border-color: #2d4566; }
  #md4-fx-pop .fx-btns button, #md4-fx-crop .fxc-bar button { background: #2e2e2e; color: #ddd; border-color: #444; }
  #md4-fx-pop .fx-btns button.fx-primary, #md4-fx-crop .fxc-bar button.fx-primary {
    background: #2383e2; border-color: #2383e2; color: #fff; }
  #md4-fx-crop .fxc-panel { background: #262626; color: #ddd; }
  #md4-fx-crop .fxc-bar { border-color: #3a3a3a; }
  #md4-fx-crop .fxc-pg { color: #bbb; }
  #md4-fx-crop .fxc-scroll { background: #1b1b1b; }
}
"""

HTML = """
<div id="md4-fx-pop" role="dialog" aria-label="⦃수식 원본 대조⦄"></div>
<div id="md4-fx-crop" role="dialog" aria-label="⦃수식 영역 지정⦄">
  <div class="fxc-panel">
    <div class="fxc-bar">
      <button data-c="prev" title="⦃이전 쪽⦄">‹</button><span class="fxc-pg"></span>
      <button data-c="next" title="⦃다음 쪽⦄">›</button>
      <button data-c="zout" title="⦃축소⦄">−</button><button data-c="zin" title="⦃확대⦄">+</button>
      <span class="fxc-hint"></span>
      <span class="fxc-gap"></span>
      <button data-c="extra" style="display:none"></button>
      <button data-c="cancel">⦃취소⦄</button>
      <button class="fx-primary" data-c="ok" disabled>⦃이 영역으로 다시 생성⦄</button>
    </div>
    <div class="fxc-scroll"><div class="fxc-page"><img alt="⦃PDF 쪽⦄" draggable="false"/>
      <div class="fxc-sel"></div></div></div>
  </div>
</div>
<script>
(function(){
  if (window.__md4FxInit) return; window.__md4FxInit = true;
  var pop = document.getElementById('md4-fx-pop'), crop = document.getElementById('md4-fx-crop');
  if (!pop || !crop) return;
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
        ? {id: prev.getAttribute('data-fx'), el: m, cell: cell} : null;
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

  // 이 행이 속한 PDF 쪽 (0-based) — 인라인 수식의 쪽을 문맥으로 못 찾았을 때 여기서 시작한다
  function rowPage(cell){
    var best = null, cs = document.querySelectorAll('.sbs-grid .sbs-cell[data-page]');
    for (var i = 0; i < cs.length; i++){
      if (cs[i] === cell || (cs[i].compareDocumentPosition(cell) & Node.DOCUMENT_POSITION_FOLLOWING))
        best = +cs[i].getAttribute('data-page');
    }
    return best;
  }

  function place(rect){
    var w = pop.offsetWidth, h = pop.offsetHeight, vw = innerWidth, vh = innerHeight;
    var x = Math.min(Math.max(16, rect.left), vw - w - 16);
    var y = rect.bottom + 8;
    if (y + h > vh - 16) y = Math.max(16, rect.top - h - 8);
    pop.style.left = x + 'px'; pop.style.top = y + 'px';
  }
  function close(){ pop.classList.remove('open'); closeCrop(); cur = null; cand = null; }
  window.__md4FxClose = close;

  function render(msg){
    if (!cur) return;
    var d = cur.info, h = '', page = cur.region ? cur.region.page : d.page;
    h += '<div class="fx-head"><b>' + (d.inline ? md4T('인라인 수식') : md4T('수식')) + '</b><span>'
      + (page ? 'p.' + esc(page) : '') + (d.number ? ' · ' + md4T('식 ({n})', {n: esc(d.number)}) : '')
      + '</span><button class="fx-x" data-act="close" title="' + md4T('닫기') + '">×</button></div>';
    var src = cand && cand.crop ? cand.crop : (cur.region ? '' : d.img);
    h += '<div class="fx-lbl">' + (cur.region ? md4T('원본 PDF — 지정한 영역') : md4T('원본 PDF')) + '</div><div class="fx-src">'
      + (src ? '<img src="' + esc(src) + '" alt="' + md4T('원본 수식') + '"/>'
             : '<div class="fx-empty">' + (cur.region ? md4T('다시 생성하면 지정한 영역이 여기 보입니다.')
                 : md4T('잘라 둔 원본 그림이 없습니다 — 영역 지정으로 PDF에서 이 수식을 골라 주세요.'))
               + '</div>')
      + '</div>';
    h += '<div class="fx-lbl">' + md4T('지금 LaTeX') + '</div><pre>'
      + (d.latex ? esc(d.latex) : md4T('(읽기 실패 — 그림으로 남아 있음)')) + '</pre>';
    if (cand && cand.html){
      h += '<div class="fx-lbl">' + md4T('다시 생성한 결과') + '</div><div class="fx-new md-preview">' + cand.html + '</div>';
      h += '<pre style="margin-top:4px">' + esc(cand.latex) + '</pre>';
    }
    if (msg) h += '<div class="' + (msg.err ? 'fx-err' : 'fx-note') + '">' + esc(msg.text) + '</div>';
    var canRegen = !cur.busy && (d.img || cur.region);
    h += '<div class="fx-btns"><button data-act="crop"' + (cur.busy ? ' disabled' : '') + '>' + md4T('영역 지정') + '</button>'
      + '<span class="fxc-gap"></span>';
    if (cand && cand.html){
      h += '<button data-act="cancel">' + md4T('취소') + '</button>'
        + '<button data-act="regen"' + (canRegen ? '' : ' disabled') + '>' + md4T('다시 생성') + '</button>'
        + '<button class="fx-primary" data-act="apply">' + md4T('적용') + '</button>';
    } else {
      h += '<button class="fx-primary" data-act="regen"' + (canRegen ? '' : ' disabled') + '>'
        + (cur.busy ? md4T('생성 중…') : md4T('다시 생성')) + '</button>';
    }
    h += '</div>';
    pop.innerHTML = h;
    place(cur.rect);
  }

  function open(t){
    var tok = window.__md4FxTok; if (!tok) return;
    var rect = t.el.getBoundingClientRect(), rp = rowPage(t.cell);
    fetch('/formula/' + tok + '/' + encodeURIComponent(t.id)).then(function(r){
      if (!r.ok) throw 0; return r.json();
    }).then(function(info){
      cur = {id: t.id, tok: tok, info: info, rect: rect, busy: false, region: null,
             rowPage: rp == null ? null : rp + 1};
      cand = null;
      pop.classList.add('open');
      render();
    }).catch(function(){});
  }

  function regen(){
    if (!cur || cur.busy) return;
    var me = cur; me.busy = true; cand = null;
    render({text: me.region ? md4T('지정한 영역을 LLM에 다시 읽히는 중…') : md4T('원본 그림을 LLM에 다시 읽히는 중…')});
    fetch('/formula/' + me.tok + '/' + encodeURIComponent(me.id) + '/regen', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(me.region ? {page: me.region.page, rect: me.region.rect} : {})})
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (cur !== me) return;
        me.busy = false;
        if (res.error){ render({err: true, text: md4T('다시 생성하지 못했습니다: {e}', {e: res.error})}); return; }
        cand = res; render({text: md4T('원본과 비교해 보고 맞으면 적용하세요.')});
      })
      .catch(function(){ if (cur === me){ me.busy = false; render({err: true, text: md4T('서버에 닿지 못했습니다.')}); } });
  }

  // --- 영역 지정 — PDF 쪽 위에 드래그로 사각형. 좌표는 쪽 크기(pt, 좌상단 원점)로 서버에 넘긴다 ---
  var cimg = crop.querySelector('img'), csel = crop.querySelector('.fxc-sel'),
      cpage = crop.querySelector('.fxc-page'), cscroll = crop.querySelector('.fxc-scroll'),
      cok = crop.querySelector('[data-c="ok"]');
  // 그림 영역 다시 지정(figure_crop.py)도 이 화면을 쓴다 — 무엇을 할지는 ctx가 정한다:
  // ctx = {tok, pages[[w,h]…], hint, ok(단추 글자), onOk(page, rect), extra?: {label, run}}
  var cs = null, drag = null, ctx = null;   // cs = {page, rect(pt), scale}
  var cextra = crop.querySelector('[data-c="extra"]');
  function sizeOf(p){ var ps = ctx && ctx.pages; return ps && ps[p - 1] ? ps[p - 1] : [612, 792]; }
  function drawSel(){
    if (!cs || !cs.rect){ csel.style.display = 'none'; cok.disabled = true; return; }
    var k = cimg.clientWidth / sizeOf(cs.page)[0], r = cs.rect;
    csel.style.display = 'block';
    csel.style.left = r[0] * k + 'px'; csel.style.top = r[1] * k + 'px';
    csel.style.width = (r[2] - r[0]) * k + 'px'; csel.style.height = (r[3] - r[1]) * k + 'px';
    cok.disabled = (r[2] - r[0]) < 3 || (r[3] - r[1]) < 3;
  }
  function showPage(p, rect){
    var n = (ctx.pages || []).length || 1;
    p = Math.max(1, Math.min(n, p));
    cs = {page: p, rect: rect || null, scale: cs ? cs.scale : 1};
    var sz = sizeOf(p), w = Math.round(Math.min(980, innerWidth * 0.9) * cs.scale);
    cpage.style.width = w + 'px'; cimg.style.width = w + 'px';
    cimg.style.height = Math.round(w * sz[1] / sz[0]) + 'px';
    crop.querySelector('.fxc-pg').textContent = 'p.' + p + ' / ' + n;
    cimg.src = '/pdfpage/' + ctx.tok + '/' + (p - 1);
    drawSel();
    if (cs.rect){   // 지금 영역이 보이게
      var k = w / sz[0];
      cscroll.scrollTop = Math.max(0, cs.rect[1] * k - cscroll.clientHeight / 3);
      cscroll.scrollLeft = Math.max(0, cs.rect[0] * k - 40);
    } else { cscroll.scrollTop = 0; }
  }
  function openCropWith(c, page, rect){
    ctx = c; cs = null;
    crop.querySelector('.fxc-hint').textContent = c.hint || '';
    cok.textContent = c.ok || md4T('이 영역으로');
    cextra.style.display = c.extra ? '' : 'none';
    cextra.textContent = c.extra ? c.extra.label : '';
    crop.classList.add('open');
    showPage(page || 1, rect ? rect.slice() : null);
  }
  window.__md4CropOpen = openCropWith;
  function openCrop(){
    if (!cur) return;
    var d = cur.info, r = cur.region, me = cur;
    var p = r ? r.page : (d.page || cur.rowPage || 1);
    openCropWith({tok: cur.tok, pages: d.pages, hint: md4T('드래그해 수식을 넉넉히 감싸세요'),
                  ok: md4T('이 영역으로 다시 생성'),
                  onOk: function(page, rect){
                    if (cur !== me) return;
                    cur.region = {page: page, rect: rect};
                    regen();
                  }},
                 p, r ? r.rect : (d.bbox && p === d.page ? d.bbox : null));
  }
  function closeCrop(){ crop.classList.remove('open'); drag = null; }
  function ptAt(e){
    var b = cimg.getBoundingClientRect(), k = sizeOf(cs.page)[0] / b.width;
    return [Math.max(0, Math.min(b.width, e.clientX - b.left)) * k,
            Math.max(0, Math.min(b.height, e.clientY - b.top)) * k];
  }
  cpage.addEventListener('mousedown', function(e){
    if (e.button !== 0 || !cs) return;
    e.preventDefault();
    drag = ptAt(e); cs.rect = [drag[0], drag[1], drag[0], drag[1]]; drawSel();
  });
  document.addEventListener('mousemove', function(e){
    if (!drag || !cs) return;
    var q = ptAt(e);
    cs.rect = [Math.min(drag[0], q[0]), Math.min(drag[1], q[1]),
               Math.max(drag[0], q[0]), Math.max(drag[1], q[1])];
    drawSel();
  });
  document.addEventListener('mouseup', function(){ drag = null; });
  crop.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('button[data-c]');
    if (!b){ if (e.target === crop) closeCrop(); return; }
    var c = b.getAttribute('data-c');
    if (c === 'cancel') closeCrop();
    else if (c === 'prev') showPage(cs.page - 1, null);
    else if (c === 'next') showPage(cs.page + 1, null);
    else if (c === 'zin' || c === 'zout'){
      var keep = cs.rect;
      cs.scale = Math.max(0.6, Math.min(3, cs.scale * (c === 'zin' ? 1.4 : 1 / 1.4)));
      showPage(cs.page, keep);
    }
    else if (c === 'extra' && ctx.extra){ var x = ctx.extra.run; closeCrop(); x(); }
    else if (c === 'ok' && cs.rect){
      var done = ctx.onOk, page = cs.page, rect = cs.rect.map(function(v){ return Math.round(v * 10) / 10; });
      closeCrop();
      done(page, rect);
    }
  });

  pop.addEventListener('click', function(e){
    var b = e.target.closest && e.target.closest('button[data-act]'); if (!b) return;
    var act = b.getAttribute('data-act');
    if (act === 'close') close();
    else if (act === 'cancel'){ cand = null; render(); }
    else if (act === 'regen') regen();
    else if (act === 'crop') openCrop();
    else if (act === 'apply' && cand && typeof emitEvent === 'function'){
      b.disabled = true; b.textContent = md4T('적용 중…');
      var args = {id: cur.id, latex: cand.latex, kind: cand.kind};
      if (cur.region){ args.page = cur.region.page; args.rect = cur.region.rect; }
      if (cur.info.inline) args.expect = cur.info.latex;   // 그사이 문서가 바뀌었으면 서버가 물린다
      emitEvent('md4-fx-apply', args);
    }
  });
  document.addEventListener('mousedown', function(e){
    if (crop.classList.contains('open')) return;          // 영역 지정 중엔 팝오버를 그대로 둔다
    if (pop.classList.contains('open') && !pop.contains(e.target)) close();
  }, true);
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape') return;
    if (crop.classList.contains('open')) closeCrop();
    else if (pop.classList.contains('open')) close();
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
