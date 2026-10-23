"""그림 영역 다시 지정 — 확대 뷰어의 ✂ 단추 → PDF 쪽에서 드래그 → 그 영역으로 그림을 바꾼다.

영역을 고르는 화면은 수식 팝오버의 것(`window.__md4CropOpen`, formula_pop.py)을 그대로 쓴다.
바꾸는 일은 §figure_fix: 원천(extract/images)·본문 그림(out/images)·저장 위치 사본을 함께 갈아 끼우고
마크다운은 건드리지 않는다. 바꾼 뒤에는 브라우저 캐시를 새로 받고(`fetch(cache:'reload')`) 캔버스를
다시 그려 표시 폭 힌트(#mdw=)도 새 그림 크기로 맞춘다.
"""

from __future__ import annotations

from typing import Callable

CSS = """
#md-img-zoom .mdz-bar button[data-act="recrop"] { display: none; }
#md-img-zoom.can-recrop .mdz-bar button[data-act="recrop"] { display: flex; }
"""

def register_routes(fastapi_app, wd_for: Callable[[str], object]) -> None:  # noqa: ANN001
    """`/figure/{token}/{name}`(지금 자리) · `…/crop`(영역으로 바꾸기) · `…/restore`(원래 그림으로)."""
    from fastapi import Body
    from starlette.exceptions import HTTPException

    from md4paper import figure_fix, pdfio

    def _src(token: str, name: str):  # noqa: ANN202 — (워크디렉토리, extract 파일명)
        cur = wd_for(token)
        src = figure_fix.extract_name(cur, name) if cur is not None else None
        if src is None:
            raise HTTPException(status_code=404)
        return cur, src

    @fastapi_app.get("/figure/{token}/{name}")
    def _get_figure(token: str, name: str):  # noqa: ANN202 — 동기: 템플릿 매칭이 쪽을 훑을 수 있다
        cur, src = _src(token, name)
        pdf = figure_fix.source_pdf(cur)
        at = figure_fix.where(cur, src) or {}
        return {"name": src, "page": at.get("page"), "bbox": at.get("bbox"), "guess": bool(at.get("guess")),
                "pages": [list(p) for p in pdfio.page_sizes(pdf)] if pdf else [],
                "backup": figure_fix.has_backup(cur, src)}

    @fastapi_app.post("/figure/{token}/{name}/crop")
    def _crop_figure(token: str, name: str, body: dict = Body(default=None)):  # noqa: ANN202
        cur, src = _src(token, name)
        body = body or {}
        try:
            return {"ok": True, **figure_fix.replace(cur, src, int(body.get("page") or 1), body.get("rect"))}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    @fastapi_app.post("/figure/{token}/{name}/restore")
    def _restore_figure(token: str, name: str):  # noqa: ANN202
        cur, src = _src(token, name)
        try:
            return {"ok": True, **figure_fix.restore(cur, src)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}


HTML = """
<script>
(function(){
  if (window.__md4FigCropInit) return; window.__md4FigCropInit = true;
  // 확대 뷰어가 부른다 — path = /wdimages/<토큰>/<파일>
  function done(path, res, what){
    if (!res || !res.ok){
      if (typeof emitEvent === 'function')
        emitEvent('md4-fig-recropped', {ok: false, error: (res && res.error) || '서버에 닿지 못했습니다.'});
      return;
    }
    // 같은 주소라 브라우저가 옛 그림을 계속 쓴다 — 캐시를 새로 받은 뒤 캔버스를 다시 그리게 한다
    fetch(path, {cache: 'reload'}).catch(function(){}).then(function(){
      if (typeof emitEvent === 'function') emitEvent('md4-fig-recropped', {ok: true, what: what});
    });
  }
  window.__md4FigRecrop = function(path){
    var m = /^\\/wdimages\\/([^\\/]+)\\/([^\\/?#]+)$/.exec(path || '');
    if (!m || !window.__md4CropOpen) return;
    // 번역 그림(figure-3.ko.png, figure_ko.py)에서 열었으면 원본 그림을 다시 자른다
    var tok = m[1], name = m[2].replace(/\\.ko\\.png$/, '.png');
    var base = '/figure/' + tok + '/' + encodeURIComponent(name);
    fetch(base).then(function(r){ if (!r.ok) throw 0; return r.json(); }).then(function(info){
      window.__md4CropOpen({
        tok: tok, pages: info.pages,
        hint: info.guess ? '드래그해 그림 전체를 감싸세요 (지금 사각형은 짐작한 자리)'
                         : '드래그해 그림 전체를 감싸세요',
        ok: '이 영역으로 바꾸기',
        onOk: function(page, rect){
          fetch(base + '/crop', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                 body: JSON.stringify({page: page, rect: rect})})
            .then(function(r){ return r.json(); })
            .then(function(res){ done(path, res, 'crop'); }, function(){ done(path, null); });
        },
        extra: info.backup ? {label: '원래 그림으로', run: function(){
          fetch(base + '/restore', {method: 'POST'}).then(function(r){ return r.json(); })
            .then(function(res){ done(path, res, 'restore'); }, function(){ done(path, null); });
        }} : null
      }, info.page || 1, info.bbox);
    }).catch(function(){
      if (typeof emitEvent === 'function')
        emitEvent('md4-fig-recropped', {ok: false, error: '이 그림의 원본을 찾지 못했습니다.'});
    });
  };
})();
</script>
"""
