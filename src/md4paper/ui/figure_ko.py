"""그림 속 글자 번역 — 확대 뷰어의 '가' 단추 → 메뉴(번역 그림 보기 · 글자 번역 · 고치기 · 지우기).

일은 §figure_translate가 한다(LLM은 라벨만, 그림은 코드가). 여기는 라우트와 화면만:
- `GET  /figko/{token}/{name}` 지금 상태(번역 그림 있음·원본 바뀜·줄 목록·대략 비용)
- `POST …/run` 글자 번역(LLM 한 번, 이 논문의 번역 모델) · `…/edit` 한국어를 고쳐 다시 그리기(LLM 없음)
  · `…/remove` 번역 그림 지우기
name은 원본(`figure-3.png`)이든 번역 그림(`figure-3.ko.png`)이든 된다 — 번역 열(ko.md)에서 연 확대 뷰어는
번역 그림을 보고 있다. 바꾼 뒤엔 ko.md 참조가 바뀌므로 `md4-figko-changed`로 캔버스를 다시 그리게 한다
(`app.py`). 같은 주소의 그림을 다시 그렸으니 브라우저 캐시를 새로 받는다(figure_crop과 같은 까닭).
"""

from __future__ import annotations

from typing import Callable

from md4paper.i18n import t

CSS = """
#md-img-zoom .mdz-bar button[data-act="figko"] { display: none; font-size: 13px; font-weight: 700; }
#md-img-zoom.can-figko .mdz-bar button[data-act="figko"] { display: flex; }
#md-img-zoom .mdz-bar button[data-act="figko"].on { color: #8fd18f; }
#md-img-zoom .mdz-kmenu { position: absolute; top: 50px; right: 16px; min-width: 230px; max-width: 320px;
  padding: 5px; border-radius: 10px; background: rgba(32,32,36,.96); box-shadow: 0 8px 28px rgba(0,0,0,.5);
  color: #e8e8e8; font-size: 13px; display: none; z-index: 2; }
#md-img-zoom .mdz-kmenu.open { display: block; }
#md-img-zoom .mdz-kmenu button { display: block; width: 100%; text-align: left; padding: 7px 10px; border: none;
  background: none; color: inherit; font: inherit; border-radius: 7px; cursor: pointer; }
#md-img-zoom .mdz-kmenu button:hover:not([disabled]) { background: rgba(255,255,255,.12); }
#md-img-zoom .mdz-kmenu button[disabled] { color: #8a8a8a; cursor: default; }
#md-img-zoom .mdz-kmenu .note { padding: 5px 10px 7px; color: #a9a9a9; font-size: 11.5px; line-height: 1.45; }
#md-img-zoom .mdz-kmenu .sub { color: #9a9a9a; font-size: 11.5px; }
#md-img-zoom .mdz-kedit { position: absolute; top: 50px; right: 16px; bottom: 16px; width: min(420px, 90vw);
  display: none; flex-direction: column; border-radius: 10px; background: #fff; color: #222;
  box-shadow: 0 12px 40px rgba(0,0,0,.55); z-index: 3; font-size: 13px; }
#md-img-zoom .mdz-kedit.open { display: flex; }
#md-img-zoom .mdz-kedit .hd { padding: 10px 14px 6px; font-weight: 700; }
#md-img-zoom .mdz-kedit .hd .sub { font-weight: 400; color: #777; font-size: 11.5px; margin-top: 2px; }
#md-img-zoom .mdz-kedit .rows { flex: 1; overflow-y: auto; padding: 2px 14px 8px; }
#md-img-zoom .mdz-kedit .row { padding: 6px 0; border-top: 1px solid #eee; }
#md-img-zoom .mdz-kedit .en { color: #777; font-size: 11.5px; margin-bottom: 3px; word-break: break-word; }
#md-img-zoom .mdz-kedit input { width: 100%; box-sizing: border-box; padding: 5px 7px; border: 1px solid #ccc;
  border-radius: 6px; font: inherit; font-size: 13px; }
#md-img-zoom .mdz-kedit .ft { display: flex; justify-content: flex-end; gap: 6px; padding: 8px 14px 12px; }
#md-img-zoom .mdz-kedit .ft button { padding: 6px 12px; border-radius: 6px; border: 1px solid #ccc;
  background: #fff; cursor: pointer; font: inherit; }
#md-img-zoom .mdz-kedit .ft button.pri { background: #26a69a; border-color: #26a69a; color: #fff; }
"""

def register_routes(fastapi_app, wd_for: Callable[[str], object]) -> None:  # noqa: ANN001
    """`/figko/{token}/{name}`(상태) · `…/run` · `…/edit` · `…/remove`."""
    from fastapi import Body
    from starlette.exceptions import HTTPException

    from md4paper import figure_translate as ft

    def _wd(token: str, name: str):  # noqa: ANN202
        cur = wd_for(token)
        if cur is None or not ft.is_candidate(ft.original_name(cur, name)):
            raise HTTPException(status_code=404)
        return cur

    def _model(cur) -> str:  # noqa: ANN001
        from md4paper.ui import translate_job

        return translate_job.default_model(cur)

    def _saved(cur) -> None:  # noqa: ANN001 — 저장 위치 사본도 새 ko.md·그림으로
        from md4paper import library

        try:
            library.auto_export(cur)
        except Exception:  # noqa: BLE001 — 번역 그림은 이미 만들었다
            pass

    @fastapi_app.get("/figko/{token}/{name}")
    def _get(token: str, name: str):  # noqa: ANN202
        cur = _wd(token, name)
        model = _model(cur)
        st = ft.info(cur, name)
        return {**st, "model": st.get("model") or model, "next_model": model,
                "est_usd": ft.estimate_usd(model.partition("/")[2] or model, 1), "has_ko_md": cur.ko_md.exists()}

    @fastapi_app.post("/figko/{token}/{name}/run")
    def _run(token: str, name: str):  # noqa: ANN202 — 동기: LLM 한 번(수 초~수십 초)
        from md4paper.ui import translate_job

        cur = _wd(token, name)
        try:
            prov = translate_job.provider_for(cur)
            res = ft.translate(cur, name, prov)
        except Exception as e:  # noqa: BLE001 — 키 없음·글꼴 없음·LLM 오류를 화면에 그대로
            return {"ok": False, "error": str(e)[:300]}
        _saved(cur)
        return {"ok": True, **res}

    @fastapi_app.post("/figko/{token}/{name}/edit")
    def _edit(token: str, name: str, body: dict = Body(default=None)):  # noqa: ANN202
        cur = _wd(token, name)
        try:
            res = ft.edit(cur, name, (body or {}).get("edits") or {})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        _saved(cur)
        return {"ok": True, **res}

    @fastapi_app.post("/figko/{token}/{name}/remove")
    def _remove(token: str, name: str):  # noqa: ANN202
        cur = _wd(token, name)
        ok = ft.remove(cur, name)
        _saved(cur)
        return {"ok": ok, **({} if ok else {"error": t("지울 번역이 없습니다.")})}


HTML = """
<script>
(function(){
  if (window.__md4FigKoInit) return; window.__md4FigKoInit = true;
  var box = document.getElementById('md-img-zoom');
  if (!box) return;
  var btn = box.querySelector('.mdz-bar button[data-act="figko"]');
  var menu = document.createElement('div'); menu.className = 'mdz-kmenu'; box.appendChild(menu);
  var ed = document.createElement('div'); ed.className = 'mdz-kedit'; box.appendChild(ed);
  var cur = null, busy = false, seq = 0;
  function esc(t){ var d = document.createElement('div'); d.textContent = t == null ? '' : t; return d.innerHTML; }
  function parse(path){
    var m = /^\\/wdimages\\/([^\\/]+)\\/([^\\/?#]+)$/.exec(path || '');
    return m ? {tok: m[1], name: decodeURIComponent(m[2]), path: path} : null;
  }
  function base(c){ return '/figko/' + c.tok + '/' + encodeURIComponent(c.name); }
  function url(c, name){ return '/wdimages/' + c.tok + '/' + encodeURIComponent(name); }
  function isKo(name){ return /\\.[a-z]{2,3}(-[A-Za-z0-9]{2,8})*\\.png$/.test(name || ''); }   // 번역 그림 — figure-3.<언어>.png
  function hide(){ menu.classList.remove('open'); ed.classList.remove('open'); }
  function load(c){
    var my = ++seq;
    return fetch(base(c)).then(function(r){ if (!r.ok) throw 0; return r.json(); }).then(function(info){
      if (my !== seq) return null;
      c.info = info;
      if (btn) btn.classList.toggle('on', !!info.has);
      return info;
    }).catch(function(){ return null; });
  }
  // 확대 뷰어가 그림을 열 때 부른다 (app.py openZoom) — 본문 그림이 아니면 path = ''
  window.__md4FigKoOpen = function(path){
    hide(); cur = parse(path);
    if (cur) cur.view = cur.name;   // 지금 확대 뷰어가 보여 주는 그림 (원본 또는 .ko.png)
    if (btn) btn.classList.remove('on');
    if (cur) load(cur);
  };
  function show(name, bust){
    var u = url(cur, name);
    cur.view = name;
    var go = function(){ if (window.__mdImgZoomSrc) window.__mdImgZoomSrc(u + (bust ? '?v=' + Date.now() : '')); };
    if (bust) fetch(u, {cache: 'reload'}).catch(function(){}).then(go); else go();
  }
  function changed(res, what){
    if (typeof emitEvent === 'function') emitEvent('md4-figko-changed', Object.assign({what: what}, res || {}));
  }
  function post(path, body){
    return fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body || {})}).then(function(r){ return r.json(); });
  }
  function run(){
    var c = cur; busy = true; render();
    post(base(c) + '/run').then(function(res){
      busy = false;
      if (res && res.ok && res.ko) show(res.ko, true);
      changed(res, 'run');
      return load(c);
    }, function(){ busy = false; changed({ok: false, error: md4T('서버에 닿지 못했습니다.')}, 'run'); })
      .then(function(){ if (menu.classList.contains('open')) render(); });
  }
  function removeKo(){
    var c = cur;
    post(base(c) + '/remove').then(function(res){
      hide(); if (res && res.ok) show(c.info.name, false);
      changed(res, 'remove'); return load(c);
    });
  }
  function render(){
    var i = cur && cur.info;
    if (!i){ menu.innerHTML = '<div class="note">' + md4T('이 그림의 정보를 읽는 중…') + '</div>'; return; }
    var h = [];
    if (busy) h.push('<button disabled>' + md4T('글자 번역 중… (수 초~수십 초)') + '</button>');
    if (!busy && i.has)
      h.push('<button data-k="toggle">' + (isKo(cur.view) ? md4T('원본 그림 보기') : md4T('번역 그림 보기')) + '</button>');
    if (!busy){
      var again = i.record && !i.stale;
      h.push('<button data-k="run">' + (again ? md4T('다시 번역') : md4T('그림 글자 번역')) +
             ' <span class="sub">≈ ' + esc(i.est_usd < 0.0001 ? '< $0.0001' : '$' + i.est_usd.toFixed(4)) +
             ' · ' + esc(i.next_model || '') + '</span></button>');
    }
    if (!busy && i.record && !i.stale && (i.lines || []).length) h.push('<button data-k="edit">' + md4T('번역 고치기…') + '</button>');
    if (!busy && i.record) h.push('<button data-k="remove">' + md4T('번역 지우기') + '</button>');
    if (i.stale) h.push('<div class="note">' + md4T('원본 그림이 바뀌어(영역 다시 지정·되돌리기) 번역 그림을 뺐습니다 — 다시 번역하세요.') + '</div>');
    else if (i.record && !i.translated) h.push('<div class="note">' + (i.kind === 'screenshot'
        ? md4T('번역할 글자를 찾지 못했습니다 (스크린샷의 화면 글자는 그대로 둡니다).')
        : md4T('번역할 글자를 찾지 못했습니다.')) + '</div>');
    else if (i.has) h.push('<div class="note">' + md4T('줄 {n}개 번역', {n: i.translated}) + ' · ' + esc(i.model || '') +
        (i.edited ? md4T(' · 직접 고침') : '') + (i.kind === 'screenshot' ? md4T(' · 스크린샷: 화면 글자는 그대로') : '') +
        (i.has_ko_md ? '' : md4T(' · 번역이 생기면 번역 열에 이 그림이 들어갑니다')) + '</div>');
    else h.push('<div class="note">' + md4T('축 라벨·범례·상자 글자를 번역 언어로 바꾼 그림을 만듭니다. 그림의 선·막대·숫자는 건드리지 않습니다 — AI는 글자의 번역만 정하고, 그림은 원본에서 그 글자 자리만 고쳐 그립니다.') + '</div>');
    menu.innerHTML = h.join('');
  }
  window.__md4FigKoMenu = function(path){
    if (!cur) cur = parse(path);
    if (!cur) return;
    ed.classList.remove('open');
    if (menu.classList.contains('open')){ menu.classList.remove('open'); return; }
    menu.classList.add('open'); render();
    load(cur).then(function(){ if (menu.classList.contains('open')) render(); });
  };
  // 메뉴·편집 패널 안의 클릭은 뷰어 배경('바깥 클릭 = 닫기')까지 올라가지 않게 — 메뉴를 다시 그리면
  // 눌린 단추가 DOM에서 떨어져 closest()로는 '메뉴 안'인지 알 수 없다
  menu.addEventListener('click', function(ev){
    ev.stopPropagation();
    var b = ev.target.closest && ev.target.closest('button[data-k]');
    if (!b || b.disabled || !cur || !cur.info) return;
    var k = b.getAttribute('data-k'), i = cur.info;
    if (k === 'toggle'){ show(isKo(cur.view) ? i.name : i.ko, false); menu.classList.remove('open'); }
    else if (k === 'run') run();
    else if (k === 'remove') removeKo();
    else if (k === 'edit') openEdit();
  });
  function openEdit(){
    var i = cur.info; menu.classList.remove('open');
    // 번역한 줄 먼저, 그다음 원문 그대로 둔 낱말 줄 — 숫자·한두 글자(눈금·배지)는 고칠 일이 없어 뺀다
    var rows = (i.lines || []).filter(function(x){
      return x.action === 'translate' || (x.action === 'keep' && !x.ui && /[A-Za-z]{3,}/.test(x.en));
    }).sort(function(a, b){ return (a.action === 'translate' ? 0 : 1) - (b.action === 'translate' ? 0 : 1); });
    ed.innerHTML = '<div class="hd">' + md4T('번역 고치기') + '<div class="sub">' + md4T('비워 두면 그 줄은 원문 그대로 둡니다. AI를 다시 부르지 않고 그림만 다시 그립니다.') + '</div></div><div class="rows">' +
      rows.map(function(x){
        return '<div class="row"><div class="en">' + esc(x.en) + '</div><input data-id="' + x.id + '" value="' +
          esc(x.action === 'translate' ? x.ko : '') + '" placeholder="' + md4T('(원문 그대로)') + '"></div>';
      }).join('') + '</div><div class="ft"><button data-e="cancel">' + md4T('취소') + '</button>' +
      '<button class="pri" data-e="save">' + md4T('다시 그리기') + '</button></div>';
    ed.classList.add('open');
  }
  ed.addEventListener('click', function(ev){
    ev.stopPropagation();
    var b = ev.target.closest && ev.target.closest('button[data-e]');
    if (!b) return;
    if (b.getAttribute('data-e') === 'cancel'){ ed.classList.remove('open'); return; }
    var edits = {}, c = cur;
    [].forEach.call(ed.querySelectorAll('input[data-id]'), function(inp){ edits[inp.getAttribute('data-id')] = inp.value; });
    b.disabled = true;
    post(base(c) + '/edit', {edits: edits}).then(function(res){
      ed.classList.remove('open');
      if (res && res.ok) show(res.ko || c.info.name, true);
      changed(res, 'edit'); return load(c);
    }, function(){ b.disabled = false; });
  });
})();
</script>
"""
