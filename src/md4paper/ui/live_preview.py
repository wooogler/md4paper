"""마크다운 편집기 라이브 프리뷰 — 원문 편집기(CodeMirror 6)를 문서처럼 보이게 한다(Obsidian식).

편집기가 코드 편집기처럼 보여(고정폭 글꼴·줄 번호·`#`·`**`·링크 주소·`<a id>` 태그) 개발자가 아니면 부담스럽다.
위지윅(Milkdown Crepe)은 문서를 해석했다 다시 써내며 파일 절반을 재조판하고 그림 설명·블록 수식을 잃는다
(실측: PLAN.md §15, side-session-notes viewer-crepe.md). 여기서는 **글자는 그대로 두고 보이는 모양만** 바꾼다 —
CodeMirror의 데코레이션으로 기호를 숨기고 헤더·강조·링크에 모양을 입히고, 그림·표·수식은 그 자리에 위젯을 그린다.
저장되는 것은 언제나 편집기의 원문이라 왕복 손실이 없다.

  · 편집기에 포커스가 있을 때 커서가 있는 줄(블록 위젯은 그 블록)에서는 원래 기호를 그대로 보여 준다 —
    고칠 곳은 원문으로 고친다.
  · 그림·표·수식 위젯을 누르면 커서가 그 자리로 가며 원문이 드러난다.
  · 인라인 꾸밈(헤더·강조·링크·HTML 숨기기·인라인 수식)은 화면에 보이는 범위만 ViewPlugin이, 여러 줄을 덮는
    블록 위젯(표·블록 수식)은 StateField가 맡는다 — CodeMirror는 블록 교체를 플러그인에서 받지 않는다.

**새 번들이 없다.** NiceGUI의 `ui.codemirror`는 `nicegui-codemirror` ES 모듈로 @codemirror/view·state·language를
그대로 export한다. 같은 모듈을 import하면 편집기와 같은 인스턴스의 클래스가 나와(instanceof가 맞는다)
`StateEffect.appendConfig`로 떠 있는 편집기에 확장을 덧붙일 수 있다. Compartment에 담아 켜고 끈다.
`getElement(id).editor`가 NiceGUI 구현 세부라는 위험은 스크롤 싱크(_md_sync_js)와 같다.

수식: 브라우저 쪽에 LaTeX 변환기가 없어 서버(`/md4math`, latex2mathml — 프리뷰와 같은 라이브러리)에 묶어 묻고
MathML을 캐시한다. 기다리는 동안은 원문 LaTeX를 흐리게 보인다.
"""

from __future__ import annotations

import functools
import json

MAX_TEX = 200        # 한 번에 변환할 수식 수 상한
MAX_TEX_LEN = 4000   # 수식 하나 길이 상한


@functools.lru_cache(maxsize=2048)
def _mathml_one(tex: str) -> str | None:
    try:
        from latex2mathml.converter import convert

        return convert(tex)
    except Exception:  # noqa: BLE001 — 못 읽는 수식은 원문으로 남긴다
        return None


def mathml(texs: object) -> dict[str, str | None]:
    """LaTeX 목록 → {LaTeX: MathML | None}. 이상한 입력은 버린다."""
    if not isinstance(texs, list):
        return {}
    out: dict[str, str | None] = {}
    for t in texs[:MAX_TEX]:
        if isinstance(t, str) and t.strip() and len(t) <= MAX_TEX_LEN:
            out[t] = _mathml_one(t)
    return out


CSS = """
.cm-editor.md4lp .cm-gutters { display: none; }
.cm-editor.md4lp .cm-scroller { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo',
  'Noto Sans KR', sans-serif; font-size: 16px; line-height: 1.7; }
.cm-editor.md4lp .cm-content { padding: 14px 22px 64px; }
.cm-editor.md4lp .cm-line { color: #37352f; }
/* 문법 강조 색은 지운다 — 문서처럼 보이게. 아래 md4lp-* 꾸밈이 구체도로 이긴다 */
.cm-editor.md4lp .cm-content span { color: inherit; text-decoration: none; font-family: inherit; }
.cm-editor.md4lp .cm-activeLine { background: rgba(35, 131, 226, .05); }
.cm-editor.md4lp .md4lp-h { font-weight: 700; line-height: 1.3; padding-top: .7em; padding-bottom: .15em; }
.cm-editor.md4lp .md4lp-h1 { font-size: 1.75em; }
.cm-editor.md4lp .md4lp-h2 { font-size: 1.4em; }
.cm-editor.md4lp .md4lp-h3 { font-size: 1.2em; }
.cm-editor.md4lp .md4lp-h4 { font-size: 1.08em; }
.cm-editor.md4lp .md4lp-h5, .cm-editor.md4lp .md4lp-h6 { font-size: 1em; color: #5f5e5b; }
.cm-editor.md4lp .cm-content .md4lp-link { color: #2383e2; }
.cm-editor.md4lp .cm-content .md4lp-strong { font-weight: 700; }
.cm-editor.md4lp .cm-content .md4lp-em { font-style: italic; }
.cm-editor.md4lp .cm-content .md4lp-code { font-family: SFMono-Regular, Menlo, Consolas, monospace; font-size: 85%;
  color: #eb5757; background: rgba(135, 131, 120, .15); border-radius: 4px; padding: 0 .25em; }
.cm-editor.md4lp .cm-content .md4lp-sup { vertical-align: super; font-size: .75em; }
.cm-editor.md4lp .md4lp-quote { border-left: 3px solid #d3d1cb; padding-left: 14px; color: #55534e; }
.cm-editor.md4lp .md4lp-codeblock { font-family: SFMono-Regular, Menlo, Consolas, monospace; font-size: 14px;
  background: #f3f2ef; }
.cm-editor.md4lp .md4lp-bullet { color: #8a8984; }
.cm-editor.md4lp .md4lp-img { cursor: pointer; }
.cm-editor.md4lp .md4lp-img img { max-width: 100%; max-height: 280px; border: 1px solid #e3e2de; border-radius: 6px;
  vertical-align: middle; }
.cm-editor.md4lp .md4lp-img-block { display: block; padding: 4px 0; }
/* contain: 넓은 표가 편집기 내용 폭을 밀어 다른 줄이 화면 밖에서 줄바꿈되지 않게 — 표만 가로로 스크롤 */
.cm-editor.md4lp .md4lp-table { overflow-x: auto; contain: inline-size; margin: 4px 0 10px; cursor: pointer;
  line-height: 1.5; }
.cm-editor.md4lp .md4lp-table table { border-collapse: collapse; font-size: .9em; }
/* 편집기 줄은 아무 데서나 줄바꿈(overflow-wrap: anywhere)이라 칸이 좁아지면 낱말·숫자가 쪼개진다 → 표 안은 보통대로,
   넘치면 표 상자가 가로로 스크롤한다 */
.cm-editor.md4lp .md4lp-table th, .cm-editor.md4lp .md4lp-table td { border: 1px solid #e3e2de; padding: 4px 8px;
  vertical-align: top; overflow-wrap: normal; word-break: normal; min-width: 3.5em; }
.cm-editor.md4lp .md4lp-table th { background: #f7f6f3; font-weight: 600; }
.cm-editor.md4lp .md4lp-table img { max-height: 120px; max-width: 220px; }
.cm-editor.md4lp .md4lp-math { cursor: pointer; }
.cm-editor.md4lp .md4lp-math-block { display: block; text-align: center; padding: 8px 0; overflow-x: auto;
  contain: inline-size; }
.cm-editor.md4lp .md4lp-math-raw { font-family: SFMono-Regular, Menlo, Consolas, monospace; font-size: 85%;
  color: #b45309; }
@media (prefers-color-scheme: dark) {
  .cm-editor.md4lp .cm-line { color: #d4d4d4; }
  .cm-editor.md4lp .md4lp-h5, .cm-editor.md4lp .md4lp-h6 { color: #a3a3a3; }
  .cm-editor.md4lp .cm-content .md4lp-link { color: #529cca; }
  .cm-editor.md4lp .cm-content .md4lp-code { color: #ff7369; background: rgba(135, 131, 120, .25); }
  .cm-editor.md4lp .md4lp-quote { border-left-color: #4a4a4a; color: #b5b5b5; }
  .cm-editor.md4lp .md4lp-codeblock { background: #262626; }
  .cm-editor.md4lp .md4lp-img img { border-color: #3a3a3a; }
  .cm-editor.md4lp .md4lp-table th, .cm-editor.md4lp .md4lp-table td { border-color: #3a3a3a; }
  .cm-editor.md4lp .md4lp-table th { background: #262626; }
}
"""

# (cmId, tok, on) — 편집기가 뜰 때까지 기다렸다 확장을 붙인다. 같은 편집기에 다시 부르면 켜고 끄기만 한다.
_JS = r"""
(async function(cmId, tok, on){
  var host = null, view = null;
  for (var i = 0; i < 200 && !view; i++){
    host = (typeof getElement === 'function') ? getElement(cmId) : null;
    view = host && host.editor;
    if (!view) await new Promise(function(r){ setTimeout(r, 50); });
  }
  if (!view) return;
  if (host.__md4lp){ host.__md4lp(on); return; }
  var CM = await import('nicegui-codemirror');
  var refresh = CM.StateEffect.define();
  var HIDE = CM.Decoration.replace({});
  var MATH = window.__md4MathCache = window.__md4MathCache || {};

  function served(src){
    if (/^(https?:|data:|\/wdimages\/)/.test(src)) return src;
    return '/wdimages/' + tok + '/' + src.replace(/\/+$/, '').split('/').pop();
  }
  function esc(s){
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  // 위젯을 누르면 커서를 그 자리로 — 원문이 드러나 바로 고칠 수 있게
  function reveal(view, dom){
    dom.addEventListener('mousedown', function(e){
      if (e.button !== 0) return;
      e.preventDefault();
      var p = view.posAtDOM(dom);
      view.dispatch({selection: {anchor: p}, scrollIntoView: false});
      view.focus();
    });
  }

  // --- 수식: 서버에 묶어 물어 MathML 캐시를 채우고, 도착하면 데코를 다시 만든다 ---
  var want = {}, timer = null;
  function ask(tex){
    if (tex in MATH || want[tex]) return;
    want[tex] = 1;
    if (timer) return;
    timer = setTimeout(function(){
      var list = Object.keys(want); want = {}; timer = null;
      fetch('/md4math', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({tex: list})})
        .then(function(r){ return r.json(); })
        .then(function(d){
          list.forEach(function(t){ MATH[t] = (d.items || {})[t] || null; });
          if (view.dom.isConnected) view.dispatch({effects: refresh.of(null)});
        })
        .catch(function(){ list.forEach(function(t){ MATH[t] = null; }); });
    }, 40);
  }
  class MathW extends CM.WidgetType {
    constructor(tex, block){ super(); this.tex = tex; this.block = block; this.ready = (tex in MATH); }
    eq(o){ return o.tex === this.tex && o.block === this.block && o.ready === this.ready; }
    toDOM(view){
      var el = document.createElement(this.block ? 'div' : 'span');
      el.className = 'md4lp-math' + (this.block ? ' md4lp-math-block' : '');
      var html = MATH[this.tex];
      if (html){
        el.innerHTML = html;
        var m = el.querySelector('math');
        if (m && this.block) m.setAttribute('display', 'block');
      } else {
        el.textContent = this.block ? '$$' + this.tex + '$$' : '$' + this.tex + '$';
        el.classList.add('md4lp-math-raw');
        if (!(this.tex in MATH)) ask(this.tex);
      }
      reveal(view, el);
      return el;
    }
    ignoreEvent(){ return false; }
  }

  class ImgW extends CM.WidgetType {
    constructor(src, alt, block){ super(); this.src = src; this.alt = alt; this.block = block; }
    eq(o){ return o.src === this.src && o.alt === this.alt && o.block === this.block; }
    toDOM(view){
      var w = document.createElement('span');
      w.className = 'md4lp-img' + (this.block ? ' md4lp-img-block' : '');
      var im = document.createElement('img');
      im.src = served(this.src); im.alt = this.alt; im.title = this.alt; im.loading = 'lazy';
      im.onload = function(){ view.requestMeasure(); };  // 그림이 뜨면 줄 높이가 바뀐다
      w.appendChild(im);
      reveal(view, w);
      return w;
    }
    ignoreEvent(){ return false; }
  }

  class BulletW extends CM.WidgetType {
    eq(){ return true; }
    toDOM(){ var s = document.createElement('span'); s.className = 'md4lp-bullet'; s.textContent = '•'; return s; }
  }
  var BULLET = CM.Decoration.replace({widget: new BulletW()});

  // 표 칸 안의 인라인 마크다운 — 위젯이라 문법 트리가 없으니 흔한 것만 가볍게 그린다
  function cell(s){
    var h = esc(s.trim());
    h = h.replace(/&lt;br\s*\/?&gt;/g, '<br>').replace(/&lt;\/?(a|sup|sub|span)\b[^&]*?&gt;/g, '');
    h = h.replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, function(_, a, u){
      return '<img src="' + served(u.replace(/&amp;/g, '&')) + '" alt="' + a + '">'; });
    h = h.replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, '<span class="md4lp-link">$1</span>');
    h = h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
    h = h.replace(/`([^`]+)`/g, '<code class="md4lp-code">$1</code>');
    return h.replace(/\\([\\`*_{}\[\]()#+\-.!|~$<>])/g, '$1');
  }
  function splitRow(line){
    var t = line.trim().replace(/\\\|/g, '\u0000');
    if (t.charAt(0) === '|') t = t.slice(1);
    if (t.slice(-1) === '|') t = t.slice(0, -1);
    return t.split('|').map(function(c){ return c.replace(/\u0000/g, '|'); });
  }
  class TableW extends CM.WidgetType {
    constructor(text){ super(); this.text = text; }
    eq(o){ return o.text === this.text; }
    toDOM(view){
      var lines = this.text.split('\n'), sep = splitRow(lines[1]);
      var align = sep.map(function(c){ c = c.trim();
        return /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : ''; });
      var html = '<table><thead><tr>' + splitRow(lines[0]).map(function(c, i){
        return '<th' + (align[i] ? ' style="text-align:' + align[i] + '"' : '') + '>' + cell(c) + '</th>'; }).join('')
        + '</tr></thead><tbody>';
      lines.slice(2).forEach(function(l){
        html += '<tr>' + splitRow(l).map(function(c, i){
          return '<td' + (align[i] ? ' style="text-align:' + align[i] + '"' : '') + '>' + cell(c) + '</td>'; }).join('')
          + '</tr>';
      });
      var w = document.createElement('div');
      w.className = 'md4lp-table';
      w.innerHTML = html + '</tbody></table>';
      reveal(view, w);
      return w;
    }
    ignoreEvent(){ return false; }
  }

  function touched(st){
    var s = new Set();
    st.selection.ranges.forEach(function(r){
      for (var l = st.doc.lineAt(r.from).number; l <= st.doc.lineAt(r.to).number; l++) s.add(l);
    });
    return s;
  }

  // --- 블록(표·블록 수식): 여러 줄을 덮으므로 StateField. 범위는 문서가 바뀔 때만 다시 센다 ---
  var scanned = new WeakMap();
  function scan(doc){
    var hit = scanned.get(doc);
    if (hit) return hit;
    var out = [], fence = false, n = doc.lines, i = 1;
    while (i <= n){
      var t = doc.line(i).text, s = t.trim();
      if (/^(```|~~~)/.test(s)){ fence = !fence; i++; continue; }
      if (fence){ i++; continue; }
      if (s.charAt(0) === '|' && i < n && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(doc.line(i + 1).text.trim())){
        var j = i + 1;
        while (j + 1 <= n && doc.line(j + 1).text.trim().charAt(0) === '|') j++;
        out.push({from: doc.line(i).from, to: doc.line(j).to, kind: 'table',
                  text: doc.sliceString(doc.line(i).from, doc.line(j).to)});
        i = j + 1; continue;
      }
      if (s.slice(0, 2) === '$$'){
        var k = i;
        if (!(s.length >= 4 && s.slice(-2) === '$$')){
          while (k + 1 <= n && doc.line(k).text.trim().slice(-2) !== '$$') k++;
          if (doc.line(k).text.trim().slice(-2) !== '$$'){ i++; continue; }  // 닫히지 않은 $$
        }
        var raw = doc.sliceString(doc.line(i).from, doc.line(k).to).trim();
        var tex = raw.slice(2, -2).trim();
        if (tex) out.push({from: doc.line(i).from, to: doc.line(k).to, kind: 'math', text: tex});
        i = k + 1; continue;
      }
      i++;
    }
    scanned.set(doc, out);
    return out;
  }
  function blockDecos(st){
    var sel = st.selection.ranges, out = [];
    scan(st.doc).forEach(function(b){
      if (sel.some(function(r){ return r.from <= b.to && r.to >= b.from; })) return;
      var w = b.kind === 'table' ? new TableW(b.text) : new MathW(b.text, true);
      out.push(CM.Decoration.replace({widget: w, block: true}).range(b.from, b.to));
    });
    return CM.Decoration.set(out);
  }
  var blocks = CM.StateField.define({
    create: blockDecos,
    update: function(d, tr){
      return (tr.docChanged || tr.selection || tr.effects.some(function(e){ return e.is(refresh); }))
        ? blockDecos(tr.state) : d;
    },
    provide: function(f){ return CM.EditorView.decorations.from(f); }
  });

  // --- 인라인: 화면에 보이는 범위만 문법 트리를 훑는다 ---
  var CODEISH = /^(InlineCode|FencedCode|CodeBlock|CodeText|URL|HTMLBlock|Comment)$/;
  var INLINE_MATH = /(^|[^\\\d$])\$(?![\s$\d])([^$\n]*?[^\s\\$])\$(?![\d$])/g;
  function inline(v){
    // 기호는 편집기에 포커스가 있을 때만 커서 줄에서 드러낸다 — 막 열었을 때 첫 줄 '#'이 보이지 않게
    var st = v.state, act = v.hasFocus ? touched(st) : new Set(), tree = CM.syntaxTree(st), out = [];
    function mark(cls, a, b){ if (b > a) out.push(CM.Decoration.mark({class: cls}).range(a, b)); }
    function hide(a, b){ if (b > a) out.push(HIDE.range(a, b)); }
    function lines(a, b, cls){
      for (var p = a; p <= b;){ var l = st.doc.lineAt(p);
        out.push(CM.Decoration.line({class: cls}).range(l.from)); p = l.to + 1; }
    }
    v.visibleRanges.forEach(function(vr){
      var sup = null;
      tree.iterate({from: vr.from, to: vr.to, enter: function(n){
        var name = n.name, ln = st.doc.lineAt(n.from), show = act.has(ln.number);
        var h = /^ATXHeading(\d)$/.exec(name);
        if (h){ out.push(CM.Decoration.line({class: 'md4lp-h md4lp-h' + h[1]}).range(ln.from)); return; }
        if (name === 'Blockquote'){ lines(n.from, n.to, 'md4lp-quote'); return; }
        if (name === 'FencedCode' || name === 'CodeBlock'){ lines(n.from, n.to, 'md4lp-codeblock'); return false; }
        if (name === 'Emphasis') mark('md4lp-em', n.from, n.to);
        else if (name === 'StrongEmphasis') mark('md4lp-strong', n.from, n.to);
        else if (name === 'InlineCode') mark('md4lp-code', n.from, n.to);
        else if (name === 'Link') mark('md4lp-link', n.from, n.to);
        if (show) return;
        var par = n.node.parent, auto = par && par.name === 'Autolink';
        switch (name){
          case 'HeaderMark': hide(n.from, Math.min(n.to + 1, ln.to)); break;
          case 'QuoteMark': hide(n.from, Math.min(n.to + 1, ln.to)); break;
          case 'EmphasisMark': case 'CodeMark': case 'LinkTitle': case 'LinkLabel': hide(n.from, n.to); break;
          case 'LinkMark': hide(n.from, n.to); break;
          case 'URL': if (!auto) hide(n.from, n.to); break;
          case 'Escape': hide(n.from, n.from + 1); break;
          case 'ListMark':
            if (/^[-*+]$/.test(st.sliceDoc(n.from, n.to))) out.push(BULLET.range(n.from, n.to));
            break;
          case 'HTMLTag':
            var tag = st.sliceDoc(n.from, n.to);
            if (/^<sup\b/i.test(tag)) sup = n.to;
            else if (/^<\/sup>/i.test(tag) && sup !== null){ mark('md4lp-sup', sup, n.from); sup = null; }
            hide(n.from, n.to); break;
          case 'Image':
            var m = /^!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?/.exec(st.sliceDoc(n.from, n.to));
            if (m) out.push(CM.Decoration.replace({widget: new ImgW(m[2], m[1],
              ln.text.trim() === st.sliceDoc(n.from, n.to).trim())}).range(n.from, n.to));
            return false;
        }
      }});
      // 인라인 수식 $…$ — 문법 트리가 모르므로 줄을 훑는다(코드·URL 안은 건너뜀, 금액 $10 같은 건 안 잡힘)
      for (var p = vr.from; p <= vr.to;){
        var l = st.doc.lineAt(p); p = l.to + 1;
        if (act.has(l.number) || l.text.trim().slice(0, 2) === '$$' || l.text.indexOf('$') < 0) continue;
        INLINE_MATH.lastIndex = 0;
        var mm;
        while ((mm = INLINE_MATH.exec(l.text))){
          var a = l.from + mm.index + mm[1].length, b = a + mm[2].length + 2;
          var node = tree.resolveInner(a + 1, 1), code = false;
          for (; node; node = node.parent) if (CODEISH.test(node.name)){ code = true; break; }
          if (!code) out.push(CM.Decoration.replace({widget: new MathW(mm[2], false)}).range(a, b));
        }
      }
    });
    return CM.Decoration.set(out, true);
  }
  var deco = CM.ViewPlugin.fromClass(class {
    constructor(v){ this.decorations = inline(v); }
    update(u){
      if (u.docChanged || u.viewportChanged || u.selectionSet || u.focusChanged
          || CM.syntaxTree(u.startState) !== CM.syntaxTree(u.state)
          || u.transactions.some(function(t){ return t.effects.some(function(e){ return e.is(refresh); }); }))
        this.decorations = inline(u.view);
    }
  }, {decorations: function(p){ return p.decorations; }});

  // 클래스는 editorAttributes로 — CodeMirror가 포커스 때마다 .cm-editor의 class를 다시 써서 손으로 붙인 건 지워진다
  var comp = new CM.Compartment(), ext = [blocks, deco, CM.EditorView.editorAttributes.of({class: 'md4lp'})];
  view.dispatch({effects: CM.StateEffect.appendConfig.of(comp.of(on ? ext : []))});
  host.__md4lp = function(v){ view.dispatch({effects: comp.reconfigure(v ? ext : [])}); };
})(%s, %s, %s);
"""


def live_preview_js(cm_id: int, token: str, on: bool = True) -> str:
    """편집기(ui.codemirror의 id)에 라이브 프리뷰를 붙이는 JS. 이미 붙어 있으면 켜고 끄기만 한다."""
    return _JS % (json.dumps(cm_id), json.dumps(token), "true" if on else "false")
