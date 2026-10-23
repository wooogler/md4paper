"""본문의 그림·표 참조("Figure 3", "Fig. 2a", "Table 1", "그림 3", "표 1", "図3", "Tabla 2") 호버 미리보기 + 클릭 확대.

인용 툴팁(#ref-N)과 같은 짜임이다 — 서버는 논문마다 `window.__mdFigRefs` 맵만 실어 주고,
참조를 찾아 감싸고 띄우는 일은 클라이언트 스크립트 하나가 한다(spa.add_body_html로 한 번만).

**왜 마크다운이 아니라 DOM에서 감싸나.** 마크다운 단계에서 링크를 심으면 그림 alt·캡션 줄·
인용 링크 글자·코드·수식을 여기서 다 피해야 하고, 뷰어가 마크다운을 그리는 여러 경로를 다 고쳐야
한다. 렌더된 DOM에서는 조상이 `a, code, pre, blockquote, h1-6`이면 건너뛰는 검사 하나로 끝난다.
감싸도 textContent는 그대로라 하이라이트 앵커(평문 오프셋)와 문장 짝 span이 영향을 받지 않는다.
감싸는 요소는 `<a>`다 — 하이라이트·문장 짝 스크립트가 이미 `a` 위에서는 자리를 비켜 준다.

섹션 참조("Section 3.4", "§3.2", "부록 A.1", "3.4절")도 같은 길로 감싼다 — 호버하면 그 섹션 헤더를,
클릭하면 같은 쪽(원문/번역) 헤더로 스크롤한다. 섹션 맵은 서버가 아니라 렌더된 헤더에서 짓는다.

맵 키는 `fig-3` / `tab-1` / `sec-3.4` (로마 숫자 `Table II` → `tab-2`). 하위 그림 참조("Figure 2a")는 2로.
캡션 번호와 맞는 것만 맵에 들어가므로, 없는 번호는 감싸지도 띄우지도 않는다.
"""

from __future__ import annotations

import json
import re

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _num(tok: str) -> int | None:
    """'3' → 3, 'IV' → 4. 그 밖은 None."""
    if tok.isdigit():
        return int(tok)
    if not tok or any(c not in _ROMAN for c in tok):
        return None
    total = 0
    for i, c in enumerate(tok):
        v = _ROMAN[c]
        total += -v if i + 1 < len(tok) and _ROMAN[tok[i + 1]] > v else v
    return total


def _key(word: str, tok: str) -> str | None:
    n = _num(tok)
    if n is None:
        return None
    return ("tab-" if word.lower().startswith("tab") else "fig-") + str(n)


# > **Figure 3:** caption  /  > **Table II** — assemble/figures.py가 내는 캡션 줄
_CAP_RE = re.compile(r"^>\s*\*\*\s*(Figure|Fig\.?|Table|Tab\.?)\s+([0-9]+|[IVXLC]+)\s*[.:]?\s*\*\*\s*[.:]?\s*(.*)$",
                     re.IGNORECASE)
# ![Figure 3](src) — 그림 추출이 alt에 라벨을 넣어 둔다 (빈 alt면 캡션 근처로 짝을 찾는다)
_IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
_ALT_RE = re.compile(r"^\s*(Figure|Fig\.?|Table|Tab\.?)\s+([0-9]+|[IVXLC]+)\s*$", re.IGNORECASE)
_GRID_RE = re.compile(r"^\s*\|.*\|\s*$")
_HEAD_RE = re.compile(r"^#{1,6}\s")
_MDLINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_LINE_ATTR_RE = re.compile(r'\s(?:data-line="\d+"|class="code-line")')

_NEAR = 3  # 캡션에서 이만큼의 비지 않은 줄 안에 있어야 짝으로 본다 (Ream Fig 5: 그림·"Anon."·캡션)


def _plain(s: str) -> str:
    s = _MDLINK_RE.sub(lambda m: m.group(1), s)
    s = _HTML_RE.sub("", s).replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", s).strip()


def _table_html(rows: list[str]) -> str:
    from md4paper.ui import mdrender

    return _LINE_ATTR_RE.sub("", mdrender.render("\n".join(rows))).strip()


def build_index(md: str) -> dict[str, dict]:
    """served 마크다운 → {키: {"src": 이미지 주소} 또는 {"html": 표 HTML}, + "cap": 캡션 평문}.

    짝 찾기 우선순위: alt에 라벨이 든 그림(`![Table 2](..)`) → 캡션 위(그림)·아래(표)의 가까운
    그림/마크다운 표 → 반대쪽. 같은 번호 캡션이 여럿이면(오인된 본문 줄) 짝이 있는 것을 쓴다.
    """
    lines = md.splitlines()
    out: dict[str, dict] = {}
    caps: dict[str, str] = {}
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if in_fence:
            continue
        m = _IMG_RE.match(line.strip())
        if m:
            a = _ALT_RE.match(m.group(1))
            k = _key(a.group(1), a.group(2)) if a else None
            if k and k not in out:
                out[k] = {"src": m.group(2)}
            continue
        c = _CAP_RE.match(line.strip())
        if not c:
            continue
        k = _key(c.group(1), c.group(2))
        if not k:
            continue
        cap = _plain(c.group(3))
        if k in out and "cap" not in out[k]:
            out[k]["cap"] = cap
        if k in out:
            continue
        caps.setdefault(k, cap)
        asset = _near_asset(lines, i, table=k.startswith("tab-"))
        if asset:
            out[k] = {**asset, "cap": cap}
    for k, cap in caps.items():  # alt로 먼저 잡힌 그림인데 캡션이 뒤에 온 경우까지 채운다
        if k in out:
            out[k].setdefault("cap", cap)
    for v in out.values():
        v.setdefault("cap", "")
    return out


def _near_asset(lines: list[str], i: int, *, table: bool) -> dict | None:
    """캡션 줄 i 근처의 그림(라벨 없는 alt)·마크다운 표. 표는 아래를, 그림은 위를 먼저 본다."""
    def scan(step: int) -> dict | None:
        j, seen = i + step, 0
        while 0 <= j < len(lines) and seen < _NEAR:
            s = lines[j].strip()
            if not s:
                j += step
                continue
            if _HEAD_RE.match(s) or _CAP_RE.match(s):
                return None
            m = _IMG_RE.match(s)
            if m:
                return None if _ALT_RE.match(m.group(1)) else {"src": m.group(2)}
            if _GRID_RE.match(s):
                if not table:
                    return None
                a = b = j
                while a - 1 >= 0 and _GRID_RE.match(lines[a - 1]):
                    a -= 1
                while b + 1 < len(lines) and _GRID_RE.match(lines[b + 1]):
                    b += 1
                return {"html": _table_html(lines[a:b + 1])}
            seen += 1
            j += step
        return None

    order = (1, -1) if table else (-1, 1)
    return scan(order[0]) or scan(order[1])


def index_js(en_md: str, ko_md: str = "") -> str:
    """window.__mdFigRefs = {en: {...}, ko: {...}} — 번역 쪽은 번역된 캡션·표를 쓴다.

    참조를 감쌀지는 원문 맵으로 정하고(클라이언트), 번역 쪽에 없는 번호는 원문 것을 보여 준다.
    """
    en = build_index(en_md)
    ko = build_index(ko_md) if ko_md else {}
    for k, v in ko.items():
        if not v.get("cap") and k in en:
            v["cap"] = en[k]["cap"]
    payload = json.dumps({"en": en, "ko": ko}, ensure_ascii=False).replace("<", "\\u003c")
    # 화면을 갈아 끼울 때 본문이 맵보다 먼저 그려질 수 있다 — 맵이 바뀌면 다시 훑는다
    return f"window.__mdFigRefs = {payload}; window.__mdFigRefScan && window.__mdFigRefScan();"


CSS = """
.md-preview a.md-figref { color: inherit; text-decoration: none; cursor: zoom-in;
  border-bottom: 1px dotted rgba(35,131,226,.55); }
.md-preview a.md-figref[data-fig^="sec-"] { cursor: pointer; }
.md-preview a.md-figref:hover { border-bottom-color: #2383e2; color: #2383e2; }
#md-fig-tip { position: fixed; z-index: 10000; display: none; max-width: 360px; padding: 6px;
  background: #2b2b2e; color: #eee; border-radius: 8px; cursor: zoom-in;
  box-shadow: 0 8px 28px rgba(0,0,0,.35); font-size: 12px; line-height: 1.4; }
#md-fig-tip.open { display: block; }
#md-fig-tip img { display: block; max-width: 348px; max-height: 240px; margin: 0 auto;
  background: #fff; border-radius: 4px; }
#md-fig-tip .mft-tbl { max-height: 220px; max-width: 348px; overflow: hidden; background: #fff; color: #1c1c1e;
  border-radius: 4px; padding: 4px; font-size: 9px; line-height: 1.25; position: relative; }
#md-fig-tip .mft-tbl::after { content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 28px;
  background: linear-gradient(rgba(255,255,255,0), #fff); }
#md-fig-tip .mft-tbl table, #md-tbl-view table { border-collapse: collapse; }
#md-fig-tip .mft-tbl td, #md-fig-tip .mft-tbl th { border: 1px solid #ddd; padding: 1px 4px; white-space: nowrap; }
#md-fig-tip .mft-cap { margin-top: 5px; max-height: 3.9em; overflow: hidden; color: #cfcfcf; }
#md-fig-tip .mft-cap b { color: #fff; }
#md-fig-tip.sec { cursor: pointer; padding: 6px 10px; max-width: 420px; font-size: 13px; font-weight: 600; }
.md-preview .md-sec-flash { animation: md-sec-flash 1.6s ease-out; border-radius: 4px; }
@keyframes md-sec-flash { 0%, 30% { background: rgba(35,131,226,.22); } 100% { background: transparent; } }
#md-tbl-view { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 10001; display: none;
  align-items: center; justify-content: center; background: rgba(0,0,0,.82); }
#md-tbl-view.open { display: flex; }
#md-tbl-view .mtv-card { max-width: calc(100vw - 64px); max-height: calc(100vh - 64px); overflow: auto;
  background: #fff; color: #1c1c1e; border-radius: 8px; padding: 18px 22px; font-size: 14px; }
#md-tbl-view .mtv-cap { margin-bottom: 12px; max-width: 900px; line-height: 1.45; color: #444; }
#md-tbl-view td, #md-tbl-view th { border: 1px solid #d0d0d0; padding: 4px 9px; }
#md-tbl-view th { background: #f3f3f5; }
#md-tbl-view .mtv-close { position: absolute; top: 14px; right: 16px; width: 30px; height: 30px; border: 0;
  border-radius: 6px; background: rgba(255,255,255,.08); color: #eee; font-size: 15px; cursor: pointer; }
#md-tbl-view .mtv-close:hover { background: rgba(255,255,255,.16); }
"""

# 감싸기 + 호버 미리보기 + 클릭 확대. 그림(과 이미지로 추출된 표)은 기존 확대 뷰어
# (window.__mdImgZoomOpen)를 쓰고, 마크다운 표는 #md-tbl-view에 렌더된 표를 띄운다.
HTML = """
<div id="md-fig-tip"></div>
<div id="md-tbl-view"><button class="mtv-close" title="⦃닫기 (Esc)⦄">&#10005;</button>
  <div class="mtv-card"><div class="mtv-cap"></div><div class="mtv-body"></div></div></div>
<script>
(function(){
  if (window.__mdFigRef) return; window.__mdFigRef = true;
  var tip = document.getElementById('md-fig-tip'), view = document.getElementById('md-tbl-view');
  if (!tip || !view) return;
  // 단어 앞이 글자면 안 된다(목표 1·Subtable 1) — 뒤는 한국어 조사가 바로 붙으니 숫자·영문자만 막는다.
  // 번역 언어별 낱말: 그림/표(ko) · 図/表(ja) · 图/表(zh) · Figura/Tabla(es). 일·중은 띄어 쓰지 않으니 앞을 막지 않는다.
  var RE = /(?<![A-Za-z\\u3131-\\u318e\\uac00-\\ud7a3])(Figures?|Figuras?|Figs?\\.?|Tables?|Tablas?|Tab\\.|그림|표|図|图|圖|表)(\\s*|\\u00a0)(\\d+|[IVXLC]+)(?:([a-z])|\\(([a-z])\\))?(?![0-9A-Za-z])/g;
  var MORE = /^(\\s*(?:,|;|、|and|&|–|-|및|와|과|y|および|及び|和|与)\\s*)(\\d+)(?:[a-z]|\\([a-z]\\))?(?![0-9A-Za-z])/;
  var ROMAN = {I: 1, V: 5, X: 10, L: 50, C: 100};
  function num(t){
    if (/^\\d+$/.test(t)) return parseInt(t, 10);
    var n = 0;
    for (var i = 0; i < t.length; i++){ var v = ROMAN[t[i]], w = ROMAN[t[i + 1]] || 0; n += v < w ? -v : v; }
    return n;
  }
  // 섹션 참조: "Section 3.4", "Sec. 5", "§3.2.1", "부록 A.1", "Appendix B" / 한국어 "3.4절".
  // 번호가 본문 헤더("## 3.4 결과", "# A Appendix")에 실제로 있을 때만 감싼다 — 맵은 DOM 헤더에서 짓는다.
  var SECRE = /(?<![A-Za-z\\u3131-\\u318e\\uac00-\\ud7a3])([Ss]ections?|SECTIONS?|[Ss]ecs?\\.|§§?|섹션|절|[Aa]ppendix|[Aa]ppendices|APPENDIX|부록|[Ss]ecci(?:ón|ones)|[Aa]péndices?|付録|附录)(\\s*|\\u00a0)(\\d+(?:\\.\\d+)*|[A-Z](?:\\.\\d+)*)(?![0-9A-Za-z]|\\.\\d)/g;
  var SECRE2 = /(?<![0-9.A-Za-z])(\\d+(?:\\.\\d+)*)(\\s?)(?:절(?![차반대약감단])|節|节)/g;   // 3.4절 · 3.4節 · 3.4节
  var SECMORE = /^(\\s*(?:,|;|、|and|&|–|-|및|와|과|y|および|及び|和|与)\\s*)(\\d+(?:\\.\\d+)*|[A-Z](?:\\.\\d+)*)(?![0-9A-Za-z]|\\.\\d)/;
  var HNUM = /^\\s*(Appendix\\s+|APPENDIX\\s+|부록\\s+|Apéndice\\s+|付録\\s*|附录\\s*)?([A-Z]|\\d+)((?:\\.\\d+)*)\\.?(?:\\s+|$)/;
  var secCache = null;
  function headText(h){
    var t = '';
    for (var c = h.firstChild; c; c = c.nextSibling)
      if (!(c.nodeType === 1 && c.classList.contains('md-sec-jump'))) t += c.textContent;
    return t.replace(/\\s+/g, ' ').trim();
  }
  function secIndex(){                    // {"sec-3.4": [헤더 요소들(원문·번역 셀마다)]}
    if (secCache) return secCache;
    var idx = {};
    [].forEach.call(document.querySelectorAll('.md-preview h1, .md-preview h2, .md-preview h3, .md-preview h4, .md-preview h5, .md-preview h6'), function(h){
      if (h.closest('.sbs-ghost')) return;  // 숨은 짝 셀의 헤더로는 이동할 수 없다
      var t = headText(h), m = HNUM.exec(t);
      if (!m) return;
      // 한 글자 번호("A Study of …")는 부록 헤더일 때만 — 제목 첫 단어 'A'를 섹션 A로 보지 않게
      if (/^[A-Z]$/.test(m[2]) && !m[3] && !m[1] && !/appendix|부록|apéndice|付録|附录/i.test(t)) return;
      var k = 'sec-' + m[2] + m[3];
      (idx[k] = idx[k] || []).push(h);
    });
    secCache = idx;
    return idx;
  }
  function secKey(tok){                   // "Appendix B"에 B 헤더가 없으면 B.1로
    var idx = secIndex(), k = 'sec-' + tok;
    if (idx[k]) return k;
    if (/^[A-Z]$/.test(tok) && idx[k + '.1']) return k + '.1';
    return null;
  }
  function kindOf(w){ return (/^Tab|^표|^表/.test(w)) ? 'tab-' : 'fig-'; }
  function anyMap(){ var m = window.__mdFigRefs || {}; return m.en || {}; }
  var SKIP = 'a, code, pre, blockquote, h1, h2, h3, h4, h5, h6, script, style, textarea, .md-figref, math';

  function wrapNode(n, map){
    var s = n.nodeValue, m, cuts = [];
    RE.lastIndex = 0;
    while ((m = RE.exec(s))){
      var k = kindOf(m[1]), key = k + num(m[3]);
      if (map[key]) cuts.push({s: m.index, e: m.index + m[0].length, key: key});   // "Figure 3" 통째로
      var rest = s.slice(RE.lastIndex), mm;             // "Tables 2 and 3", "Figures 2, 3" — 뒤 번호도
      var at = RE.lastIndex;
      while ((mm = MORE.exec(rest))){
        var k2 = k + parseInt(mm[2], 10), a0 = at + mm[1].length, a1 = at + mm[0].length;
        if (map[k2]) cuts.push({s: a0, e: a1, key: k2});
        at = a1; rest = s.slice(at);
      }
      RE.lastIndex = Math.max(RE.lastIndex, at);
    }
    SECRE.lastIndex = 0;
    while ((m = SECRE.exec(s))){
      var sk = secKey(m[3]);
      if (sk) cuts.push({s: m.index, e: m.index + m[0].length, key: sk});
      var at2 = SECRE.lastIndex, rest2 = s.slice(at2), m2;   // "Sections 3 and 4"
      while ((m2 = SECMORE.exec(rest2))){
        var sk2 = secKey(m2[2]), b0 = at2 + m2[1].length, b1 = at2 + m2[0].length;
        if (sk2) cuts.push({s: b0, e: b1, key: sk2});
        at2 = b1; rest2 = s.slice(at2);
      }
      SECRE.lastIndex = Math.max(SECRE.lastIndex, at2);
    }
    SECRE2.lastIndex = 0;
    while ((m = SECRE2.exec(s))){
      var sk3 = secKey(m[1]);
      if (sk3) cuts.push({s: m.index, e: m.index + m[0].length, key: sk3});
    }
    cuts.sort(function(x, y){ return x.s - y.s; });
    cuts = cuts.filter(function(c, i){ return !i || c.s >= cuts[i - 1].e; });   // 겹치면 앞 것만
    for (var i = cuts.length - 1; i >= 0; i--){
      var c = cuts[i], mid = n.splitText(c.s); mid.splitText(c.e - c.s);
      var a = document.createElement('a');
      a.className = 'md-figref'; a.setAttribute('data-fig', c.key);
      mid.parentNode.insertBefore(a, mid); a.appendChild(mid);
    }
  }
  function scan(root){
    var map = anyMap();
    if (!root.querySelectorAll) return;
    var roots = root.matches && root.matches('.md-preview') ? [root]
      : [].slice.call(root.querySelectorAll('.md-preview'));
    if (!roots.length && root.closest && root.closest('.md-preview')) roots = [root];
    roots.forEach(function(r){
      var w = document.createTreeWalker(r, NodeFilter.SHOW_TEXT, null), ns = [], n;
      while ((n = w.nextNode())){
        if (!/\\d/.test(n.nodeValue)) continue;
        var p = n.parentElement;
        if (p && p.closest(SKIP)) continue;
        ns.push(n);
      }
      ns.forEach(function(t){ wrapNode(t, map); });
    });
  }
  // 맵이 바뀌었다(논문 전환) — 앞 논문 맵으로 감싼 것을 다 풀고 다시 훑는다.
  // 일부만 풀면 "Tables 2 and 3"처럼 앞 번호에 기대는 뒤 번호를 놓친다.
  window.__mdFigRefScan = function(){
    secCache = null;
    [].forEach.call(document.querySelectorAll('a.md-figref'), function(a){
      var p = a.parentNode;
      while (a.firstChild) p.insertBefore(a.firstChild, a);
      p.removeChild(a); p.normalize();
    });
    scan(document.body);
  };
  scan(document.body);
  // 헤더가 새로 그려지면 섹션 맵이 바뀐다 — 앞서 그려진 셀이 뒤 섹션을 가리킬 수 있으니
  // 잠시 뒤 문서 전체를 한 번 더 훑는다(이미 감싼 곳은 건너뛰므로 덧감싸지 않는다).
  var resecT = null;
  new MutationObserver(function(muts){
    var heads = false;
    for (var i = 0; i < muts.length; i++)
      for (var j = 0; j < muts[i].addedNodes.length; j++){
        var a = muts[i].addedNodes[j];
        if (a.nodeType !== 1 || a.classList.contains('md-figref')) continue;
        if (/^H[1-6]$/.test(a.tagName) || (a.querySelector && a.querySelector('h1, h2, h3, h4, h5, h6'))) heads = true;
        else scan(a);
      }
    if (heads){
      secCache = null;
      clearTimeout(resecT);
      resecT = setTimeout(function(){ secCache = null; scan(document.body); }, 120);
    }
  }).observe(document.body, {childList: true, subtree: true});

  function sideOf(el){ var c = el.closest('[data-side]'); return c ? c.getAttribute('data-side') : null; }
  function secHead(a){                    // 참조와 같은 쪽(원문/번역)의 헤더, 없으면 아무 쪽
    var hs = secIndex()[a.getAttribute('data-fig')] || [], side = sideOf(a);
    for (var i = 0; i < hs.length; i++) if (hs[i].isConnected && sideOf(hs[i]) === side) return hs[i];
    for (i = 0; i < hs.length; i++) if (hs[i].isConnected) return hs[i];
    return null;
  }
  function entry(a){
    var m = window.__mdFigRefs || {}, key = a.getAttribute('data-fig');
    if (key.indexOf('sec-') === 0){ var h = secHead(a); return h ? {key: key, head: h} : null; }
    var cell = a.closest('[data-side]'), side = cell ? cell.getAttribute('data-side') : 'en';
    var e = (m[side] || {})[key] || (m.en || {})[key];
    return e ? {key: key, e: e} : null;
  }
  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function label(key){ return (key.indexOf('tab-') === 0 ? 'Table ' : 'Figure ') + key.slice(4); }
  var cur = null, hideT = null;
  function place(a){
    var r = a.getBoundingClientRect(), gap = 6, w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    var y = r.bottom + gap;
    if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - gap);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function show(a){
    var it = entry(a); if (!it) return;
    clearTimeout(hideT);
    cur = it;
    tip.classList.toggle('sec', !!it.head);
    if (it.head){                         // 섹션: 헤더 글자만 (클릭 = 그 헤더로 이동)
      tip.innerHTML = '<div class="mft-sec">' + esc(headText(it.head)) + '</div>';
      tip.classList.add('open'); place(a);
      return;
    }
    var body = it.e.src ? '<img src="' + esc(it.e.src) + '" alt="">'
      : '<div class="mft-tbl">' + it.e.html + '</div>';
    tip.innerHTML = body + (it.e.cap ? '<div class="mft-cap"><b>' + label(it.key) + '.</b> ' + esc(it.e.cap) + '</div>' : '');
    tip.classList.add('open');
    place(a);
    var im = tip.querySelector('img'); if (im && !im.complete) im.onload = function(){ if (cur === it) place(a); };
  }
  function hideSoon(){ clearTimeout(hideT); hideT = setTimeout(function(){ tip.classList.remove('open'); cur = null; }, 180); }
  function jump(h){
    h.scrollIntoView({behavior: 'smooth', block: 'start'});
    h.classList.remove('md-sec-flash'); void h.offsetWidth; h.classList.add('md-sec-flash');
    setTimeout(function(){ h.classList.remove('md-sec-flash'); }, 1700);
  }
  // 확대 뷰어가 본문 그림을 열 때 캡션을 찾는다 — 같은 그림 파일을 가리키는 맵 항목(클릭한 쪽 언어 먼저)
  function pathOf(u){ try { return new URL(u, location.href).pathname; } catch (e) { return ''; } }
  window.__mdFigCaptionFor = function(src, el){
    var m = window.__mdFigRefs || {}, p = pathOf(src), side = (el && el.closest && sideOf(el)) || 'en';
    var sides = side === 'en' ? ['en'] : [side, 'en'];
    for (var i = 0; i < sides.length; i++){
      var mp = m[sides[i]] || {};
      for (var k in mp) if (mp[k].src && pathOf(mp[k].src) === p && mp[k].cap) return {label: label(k), text: mp[k].cap};
    }
    return null;
  };
  function openBig(it){
    tip.classList.remove('open');
    if (it.head){ cur = null; if (it.head.isConnected) jump(it.head); return; }
    var cap = label(it.key) + '. ' + (it.e.cap || '');
    if (it.e.src){
      if (window.__mdImgZoomOpen) window.__mdImgZoomOpen(it.e.src, cap, {label: label(it.key), text: it.e.cap || ''});
      return;
    }
    view.querySelector('.mtv-cap').innerHTML = '<b>' + label(it.key) + '.</b> ' + esc(it.e.cap || '');
    view.querySelector('.mtv-body').innerHTML = it.e.html;
    view.classList.add('open');
  }
  document.addEventListener('mouseover', function(ev){
    var a = ev.target.closest ? ev.target.closest('a.md-figref') : null;
    if (a) show(a);
  });
  document.addEventListener('mouseout', function(ev){
    var a = ev.target.closest ? ev.target.closest('a.md-figref') : null;
    if (a) hideSoon();
  });
  tip.addEventListener('mouseenter', function(){ clearTimeout(hideT); });
  tip.addEventListener('mouseleave', hideSoon);
  tip.addEventListener('click', function(){ if (cur) openBig(cur); });
  document.addEventListener('click', function(ev){
    var a = ev.target.closest ? ev.target.closest('a.md-figref') : null;
    if (!a) return;
    if (String(window.getSelection() || '')) return;   // 드래그로 글자를 고르던 중이면 그대로
    ev.preventDefault();
    var it = entry(a); if (it) openBig(it);
  });
  function closeView(){ view.classList.remove('open'); }
  view.addEventListener('click', function(ev){
    if (ev.target === view || (ev.target.closest && ev.target.closest('.mtv-close'))) closeView();
  });
  document.addEventListener('keydown', function(ev){
    if (ev.key === 'Escape' && view.classList.contains('open')) closeView();
  });
  window.addEventListener('scroll', function(ev){ if (cur && !tip.contains(ev.target)) { tip.classList.remove('open'); cur = null; } }, true);
})();
</script>
"""
