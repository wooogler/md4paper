
(function(){
  var CFG = window.NARANI_CFG || {}, CATS = CFG.catalogs || {};
  function known(l){ return !!l && (l in CATS || l === CFG.source_lang); }
  function pick(){
    var saved = null;
    try { saved = localStorage.getItem('narani:ui_lang'); } catch (e) {}
    if (known(saved)) return saved;
    var prefs = navigator.languages || [navigator.language || ''], i, l;
    for (i = 0; i < prefs.length; i++){
      l = String(prefs[i]);
      if (known(l)) return l;
      if (/^zh-(TW|HK|MO|Hant)/i.test(l) && known('zh-Hant')) return 'zh-Hant';
      if (/^zh/i.test(l) && known('zh-Hans')) return 'zh-Hans';
      if (known(l.split('-')[0])) return l.split('-')[0];
    }
    return known(CFG.fallback_lang) ? CFG.fallback_lang : CFG.source_lang;
  }
  window.md4Lang = pick();
  window.md4T = function(s, kw){
    var r = (CATS[window.md4Lang] || {})[s] || s;
    if (kw) r = r.replace(/\{(\w+)\}/g, function(m, k){ return (k in kw) ? kw[k] : m; });
    return r;
  };
})();
(function(){

  function textNodes(root){
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), out = [], n;
    while ((n = w.nextNode())) out.push(n);
    return out;
  }
  // 평문 오프셋 구간들을 텍스트 노드를 쪼개 감싼다 (구간끼리 겹치지 않아야 한다).
  // 자를 지점을 먼저 다 모은 뒤 노드별로 뒤에서 앞으로 자른다 — 한 노드를 쪼개도 다른 노드의
  // 오프셋은 그대로고, 같은 노드 안에서는 뒷쪽부터 잘라야 앞 오프셋이 살아남는다.
  // 감싸도 textContent는 그대로라 하이라이트 앵커(오프셋)와 문장 span이 서로를 망가뜨리지 않는다.
  function wrapRanges(scope, segs, make){
    var ns = textNodes(scope), nodes = [], groups = [], t = 0, i, k, z;
    for (i = 0; i < ns.length; i++){ var L = ns[i].nodeValue.length; nodes.push([ns[i], t, t + L]); t += L; }
    for (i = 0; i < segs.length; i++){
      for (k = 0; k < nodes.length; k++){
        var s0 = nodes[k][1], e0 = nodes[k][2];
        if (e0 <= segs[i].s || s0 >= segs[i].e) continue;
        var a0 = Math.max(segs[i].s - s0, 0), b0 = Math.min(segs[i].e - s0, e0 - s0);
        if (b0 <= a0) continue;
        var g = null;
        for (z = 0; z < groups.length; z++) if (groups[z][0] === nodes[k][0]) { g = groups[z]; break; }
        if (!g){ g = [nodes[k][0], []]; groups.push(g); }
        g[1].push({a: a0, b: b0, seg: segs[i]});
      }
    }
    for (i = 0; i < groups.length; i++){
      var node = groups[i][0], hs = groups[i][1];
      // MathML 안의 글자는 감싸지 않는다 — <mi> 안에 HTML span이 들어가면 수식 조판이 풀려
      // 글자가 띄엄띄엄 흩어진다. 오프셋 계산엔 그대로 들어가므로 앞뒤 구간은 어긋나지 않는다.
      if (node.parentElement && node.parentElement.closest('math')) continue;
      hs.sort(function(x, y){ return y.a - x.a; });
      for (k = 0; k < hs.length; k++){
        var h = hs[k];
        if (h.b < node.nodeValue.length) node.splitText(h.b);
        var target = h.a > 0 ? node.splitText(h.a) : node;
        var el = make(h.seg);
        target.parentNode.replaceChild(el, target);
        el.appendChild(target);
      }
    }
  }

  // 블록 경계 — <p>a</p><p>b</p>의 textContent는 "ab"라 경계 문자가 없다. 블록별로 나눠
  // 문장을 쪼개지 않으면 앞 문단의 끝과 뒤 문단의 첫 문장이 한 문장으로 붙는다.
  var BLOCK = /^(P|LI|H1|H2|H3|H4|H5|H6|BLOCKQUOTE|PRE|TD|TH|FIGCAPTION|DT|DD)$/;
  function blockRanges(scope){
    var ns = textNodes(scope), out = [], t = 0, cur = null;
    for (var i = 0; i < ns.length; i++){
      var el = ns[i].parentElement, L = ns[i].nodeValue.length;
      while (el && el !== scope && !BLOCK.test(el.tagName)) el = el.parentElement;
      if (!cur || cur.el !== el){ cur = {el: el, s: t, e: t + L}; out.push(cur); }
      else cur.e = t + L;
      t += L;
    }
    return out;
  }

  // 문장 끝처럼 보이지만 아닌 것들: et al. · e.g. · 0.5 · 3.1 · 이니셜(A. Jelson) ·
  // 괄호 안(인용 덩어리 [3, Adams et al., 2022]) · 마침표 뒤가 소문자로 이어지는 경우.
  // ABBR 앞의 "줄머리 또는 공백·여는괄호" 조건은 꼭 필요하다 — 없으면 "critical." "systems." 처럼
  // al·ch·ms로 끝나는 흔한 낱말이 죄다 약어로 잡혀 문장이 안 잘린다.
  var ABBR = /(?:^|[\s(\[\u201c"'])(?:e\.g|i\.e|cf|vs|etc|al|Fig|Figs|Eq|Eqs|Sec|Secs|Tab|No|Dr|Mr|Mrs|Ms|Prof|St|approx|resp|Ref|Refs|pp|Vol|Ch|Inc|Ltd|Univ)\.$/i;
  var CLOSERS = '.!?\u2026"\u2019\u201d\')]';
  function trimRange(txt, s, e){
    while (s < e && /\s/.test(txt.charAt(s))) s++;
    while (e > s && /\s/.test(txt.charAt(e - 1))) e--;
    return e > s ? {s: s, e: e} : null;
  }
  // 짝이 맞는 괄호 안쪽만 표시한다. 짝 없이 열린 괄호 하나가 남은 문단(추출이 흔히 그렇다)에서
  // 그 뒤 전체가 "괄호 안"이 돼 한 문장으로 뭉치던 것을 막는다.
  function bracketMask(txt, s0, e0){
    var n = e0 - s0, delta = [], stack = [], mask = [], run = 0, i;
    for (i = 0; i <= n; i++) delta[i] = 0;
    for (i = s0; i < e0; i++){
      var c = txt.charAt(i);
      if (c === '(' || c === '[' || c === '{') stack.push(i);
      else if (c === ')' || c === ']' || c === '}'){
        if (stack.length){ var o = stack.pop(); delta[o - s0]++; delta[i - s0 + 1]--; }
      }
    }
    for (i = 0; i < n; i++){ run += delta[i]; mask[i] = run > 0; }
    return mask;
  }
  function sentences(txt, s0, e0){
    var out = [], start = s0, i, r, inBracket = bracketMask(txt, s0, e0);
    for (i = s0; i < e0; i++){
      // 일본어·중국어 문장 끝(。！？)은 뒤에 공백 없이 다음 문장이 붙는다 — 약어·소수점 걱정도 없다
      var cjk = '\u3002\uff01\uff1f'.indexOf(txt.charAt(i)) >= 0;
      if (inBracket[i - s0] || ('.!?\u2026'.indexOf(txt.charAt(i)) < 0 && !cjk)) continue;
      var j = i + 1;
      while (j < e0 && (CLOSERS + '\u300d\u300f\uff09').indexOf(txt.charAt(j)) >= 0) j++;   // 닫는 따옴표·괄호까지 문장에 포함
      if (!cjk && j < e0 && !/\s/.test(txt.charAt(j))) continue;  // 0.5 · 3.1 처럼 붙어 있으면 문장 끝이 아님
      var head = txt.slice(start, i + 1);
      if (!cjk && (ABBR.test(head) || /(^|[\s(\[])[A-Z]\.$/.test(head))) continue;
      var k = j; while (k < e0 && /\s/.test(txt.charAt(k))) k++;
      if (!cjk && k < e0 && /[a-z,;]/.test(txt.charAt(k))) continue;  // 소문자로 이어지면 아직 한 문장
      r = trimRange(txt, start, j); if (r) out.push(r);
      start = k; i = k - 1;
    }
    r = trimRange(txt, start, e0); if (r) out.push(r);
    return out;
  }
  function sentsOf(scope){
    var txt = scope.textContent, bs = blockRanges(scope), out = [], i;
    for (i = 0; i < bs.length; i++) out = out.concat(sentences(txt, bs[i].s, bs[i].e));
    return out;
  }

  // 문장 수가 같으면 순서대로 1:1 (번역이 문장을 지킨 흔한 경우 — 추측이 아니다).
  // 다르면 길이비 DP로 1:2·2:1·누락까지 허용해 맞춘다. 어림짐작이므로 절반 넘게 짝을 못 지으면
  // 아예 포기하고 문단 표시로 물러난다 — 틀린 짝을 보여 주느니 안 보여 주는 게 낫다.
  var MOVES = [[1, 1, 0], [1, 2, 1.4], [2, 1, 1.4], [1, 0, 4], [0, 1, 4]];
  function pairUp(E, K){
    var m = E.length, n = K.length, i, j, a, out = [];
    if (!m || !n) return null;
    if (m === n){ for (i = 0; i < m; i++) out.push([[i], [i]]); return out; }
    var eL = [], kL = [], totE = 0, totK = 0;
    for (i = 0; i < m; i++){ eL.push(E[i].e - E[i].s); totE += eL[i]; }
    for (j = 0; j < n; j++){ kL.push(K[j].e - K[j].s); totK += kL[j]; }
    var r = totK / Math.max(1, totE), INF = 1e18, d = [], bk = [];
    for (i = 0; i <= m; i++){
      d.push([]); bk.push([]);
      for (j = 0; j <= n; j++){ d[i].push(INF); bk[i].push(null); }
    }
    d[0][0] = 0;
    for (i = 0; i <= m; i++) for (j = 0; j <= n; j++){
      if (d[i][j] >= INF) continue;
      for (var t = 0; t < MOVES.length; t++){
        var di = MOVES[t][0], dj = MOVES[t][1];
        if (i + di > m || j + dj > n) continue;
        var x = 0, y = 0;
        for (a = 0; a < di; a++) x += eL[i + a];
        for (a = 0; a < dj; a++) y += kL[j + a];
        var c = d[i][j] + Math.abs(x * r - y) / Math.sqrt(Math.max(1, x * r + y)) + MOVES[t][2];
        if (c < d[i + di][j + dj]){ d[i + di][j + dj] = c; bk[i + di][j + dj] = [i, j, di, dj]; }
      }
    }
    if (d[m][n] >= INF) return null;
    var path = [], p = [m, n];
    while (p[0] || p[1]){
      var b = bk[p[0]][p[1]]; if (!b) return null;
      path.push(b); p = [b[0], b[1]];
    }
    path.reverse();
    var solo = 0;
    for (i = 0; i < path.length; i++){
      var es = [], ks = [];
      for (a = 0; a < path[i][2]; a++) es.push(path[i][0] + a);
      for (a = 0; a < path[i][3]; a++) ks.push(path[i][1] + a);
      if (!es.length || !ks.length) solo++;
      out.push([es, ks]);
    }
    return solo * 2 > out.length ? null : out;
  }

  var doc = document, body = doc.body;
  body.classList.add('js');
  var sheet = doc.getElementById('sheet');
  var shLabel = sheet.querySelector('.sh-label'), shText = sheet.querySelector('.sh-text'),
      shActs = sheet.querySelector('.sh-acts');
  var lib = doc.getElementById('lib'), topTitle = doc.getElementById('top-title');
  var cur = null;          // 지금 연 논문 article
  var tapped = null;       // 시트를 연 문장 짝 (같은 문장을 다시 누르면 닫는다)

  // ---- 저장 (이 브라우저에만 — 없거나 막혀 있어도 동작은 한다) ----
  function load(k, d){ try { var v = localStorage.getItem('md4m:' + k); return v === null ? d : v; } catch (e) { return d; } }
  function store(k, v){ try { localStorage.setItem('md4m:' + k, v); } catch (e) {} }

  // ---- 보기 모드 · 글자 크기 ----
  var mode = load('mode', 'ko');
  if (doc.getElementById('m-' + mode)) doc.getElementById('m-' + mode).checked = true;
  // 모드를 바꿔도 읽던 문단을 지킨다. change 때는 CSS가 이미 바뀌어 문단 위치가 달라져 있으므로
  // 라벨을 누르는 순간(바뀌기 전) 화면 맨 위 문단을 잡아 둔다.
  var modeAnchor = null;
  [].forEach.call(doc.querySelectorAll('.seg label'), function(l){
    l.addEventListener('pointerdown', function(){ modeAnchor = topRow(); });
  });
  [].forEach.call(doc.querySelectorAll('input.mode'), function(r){
    r.addEventListener('change', function(){
      var anchor = modeAnchor || topRow(); modeAnchor = null;
      clearSent(); closeSheet();
      store('mode', r.id.slice(2));
      if (anchor) anchor.scrollIntoView({block: 'start'});
    });
  });
  var fs = +load('fs', 17) || 17;
  function setFs(v){ fs = Math.max(13, Math.min(26, v)); doc.documentElement.style.setProperty('--fs', fs + 'px'); store('fs', fs); }
  setFs(fs);
  doc.getElementById('fs-dn').onclick = function(){ var a = topRow(); setFs(fs - 1); if (a) a.scrollIntoView({block: 'start'}); };
  doc.getElementById('fs-up').onclick = function(){ var a = topRow(); setFs(fs + 1); if (a) a.scrollIntoView({block: 'start'}); };

  // ---- 화면 맨 위 문단 ----
  function topRow(){
    if (!cur) return null;
    var rows = cur.querySelectorAll('.row'), y = 70, i;
    for (i = 0; i < rows.length; i++){
      var r = rows[i].getBoundingClientRect();
      if (r.height && r.bottom > y) return rows[i];
    }
    return null;
  }
  // ---- 아래 시트 ----
  function closeSheet(){ sheet.classList.remove('open'); tapped = null; }
  function openSheet(label, nodes, acts){
    shLabel.textContent = label; shText.innerHTML = ''; shActs.innerHTML = '';
    nodes.forEach(function(n){ shText.appendChild(n); });
    acts.forEach(function(a){ shActs.appendChild(a); });
    var close = doc.createElement('button'); close.textContent = md4T('닫기'); close.onclick = function(){ clearSent(); closeSheet(); };
    shActs.appendChild(close);
    sheet.scrollTop = 0; sheet.classList.add('open');
  }
  function para(text, cls){ var p = doc.createElement('div'); if (cls) p.className = cls; p.textContent = text; return p; }
  function button(text, fn){ var b = doc.createElement('button'); b.textContent = text; b.onclick = fn; return b; }

  // ---- 문장 짝 (누른 행만 그때 맞춘다 — 한 편에 행이 수백 개라 미리 다 하면 무겁다) ----
  function prepare(row){
    if (row.__pair !== undefined) return row.__pair;
    var en = row.querySelector('.cell.en'), ko = row.querySelector('.cell.ko');
    row.__pair = null;
    if (!en || !ko) return null;
    var E = sentsOf(en), K = sentsOf(ko), pairs = pairUp(E, K);
    if (!pairs) return null;
    var segE = [], segK = [], i, z;
    for (i = 0; i < pairs.length; i++){
      for (z = 0; z < pairs[i][0].length; z++) segE.push({s: E[pairs[i][0][z]].s, e: E[pairs[i][0][z]].e, pi: i});
      for (z = 0; z < pairs[i][1].length; z++) segK.push({s: K[pairs[i][1][z]].s, e: K[pairs[i][1][z]].e, pi: i});
    }
    var txtE = en.textContent, txtK = ko.textContent;   // 감싸도 textContent는 그대로다
    function make(seg){ var sp = doc.createElement('span'); sp.className = 'sent'; sp.setAttribute('data-pi', seg.pi); return sp; }
    wrapRanges(en, segE, make); wrapRanges(ko, segK, make);
    row.__pair = {pairs: pairs, en: E, ko: K, txt: {en: txtE, ko: txtK}};
    return row.__pair;
  }
  function clearSent(){
    [].forEach.call(doc.querySelectorAll('.sent.on'), function(s){ s.classList.remove('on'); });
  }
  function sentText(pr, side, pi){
    var idx = pr.pairs[pi][side === 'en' ? 0 : 1], src = pr[side], out = [];
    for (var i = 0; i < idx.length; i++) out.push(pr.txt[side].slice(src[idx[i]].s, src[idx[i]].e));
    return out.join(' ');
  }
  function bothVisible(row){
    return doc.getElementById('m-both').checked || row.classList.contains('peek');
  }
  function peekButton(row){
    return button(row.classList.contains('peek') ? md4T('문단 대조 닫기') : md4T('이 문단 대조해서 보기'), function(){
      row.classList.toggle('peek'); clearSent(); closeSheet();
    });
  }
  function onSentence(e, cell){
    var row = cell.parentElement, side = cell.classList.contains('ko') ? 'ko' : 'en';
    if (!row.querySelector('.cell.en') || !row.querySelector('.cell.ko')){ clearSent(); closeSheet(); return; }  // 짝이 될 쪽이 없는 행
    var other = side === 'ko' ? 'en' : 'ko', label = other === 'en' ? md4T('원문') : md4T('번역');
    var pr = prepare(row);
    if (!pr){
      if (bothVisible(row)) return;
      openSheet(label, [para(md4T('이 문단은 문장 짝을 맞추지 못했어요. 문단째로 대조해 보세요.'), 'more')], [peekButton(row)]);
      return;
    }
    var pi = sentAt(e, cell, pr, side);
    if (pi === null){ clearSent(); closeSheet(); return; }
    var key = row.id + ':' + pi;
    clearSent();
    if (tapped === key){ closeSheet(); return; }
    [].forEach.call(row.querySelectorAll('.sent[data-pi="' + pi + '"]'), function(s){ s.classList.add('on'); });
    // 쓰는 쪽이 문장에 붙일 동작(폰 리더의 하이라이트·메모)을 줄 수 있다 — {nodes, buttons}
    var acts = typeof sentenceActs === 'function' ? sentenceActs(row, +pi, pr, side) : null;
    if (bothVisible(row) && !acts){ tapped = null; closeSheet(); return; }   // 양쪽이 다 보이면 짝 표시로 충분
    var nodes = [];
    if (!bothVisible(row)){
      var txt = sentText(pr, other, +pi);
      nodes.push(para(txt || md4T('(짝이 되는 문장이 없어요 — 번역에서 합쳐지거나 빠진 문장)'), txt ? '' : 'more'));
    }
    openSheet(bothVisible(row) ? '' : label, nodes.concat(acts ? acts.nodes : []), acts ? acts.buttons : []);
    tapped = key;
  }
  function sentAt(e, cell, pr, side){
    // 방금 감쌌으면 e.target은 감싸기 전 노드다 — 손가락 아래를 다시 본다
    var under = doc.elementFromPoint(e.clientX, e.clientY);
    var sp = under && under.closest ? under.closest('.sent') : null;
    if (sp && cell.contains(sp)) return sp.getAttribute('data-pi');
    // 못 찾으면 글자 위치로 — 누른 자리의 오프셋을 품은 문장
    var r = doc.caretRangeFromPoint ? doc.caretRangeFromPoint(e.clientX, e.clientY) : null;
    if (!r || !cell.contains(r.startContainer)) return null;
    var pre = doc.createRange(); pre.selectNodeContents(cell);
    try { pre.setEnd(r.startContainer, r.startOffset); } catch (err) { return null; }
    var off = pre.toString().length, list = pr[side], i, k;
    for (i = 0; i < pr.pairs.length; i++){
      var idx = pr.pairs[i][side === 'en' ? 0 : 1];
      for (k = 0; k < idx.length; k++) if (list[idx[k]].s <= off && off < list[idx[k]].e) return String(i);
    }
    return null;
  }

  // ---- 인용 · 각주 ----
  function visibleTarget(id){
    // 같은 앵커가 원문(-en-)·번역(-ko-) 셀에 하나씩 있다 — 지금 보이는 쪽을 고른다
    var m = /^(p\d+)-(?:en|ko)-(.*)$/.exec(id), cands = [id];
    if (m) cands.push(m[1] + '-ko-' + m[2], m[1] + '-en-' + m[2]);
    for (var i = 0; i < cands.length; i++){
      var el = doc.getElementById(cands[i]);
      if (el && el.getClientRects().length) return el;
    }
    return doc.getElementById(id);
  }
  function blockText(el){
    var b = el && el.closest ? el.closest('p, li, td, div.cell') : null;
    return b ? b.textContent.replace(/\s+/g, ' ').trim() : '';
  }
  function onLink(a, e){
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '#') return;               // 바깥 링크는 그대로 (새 창)
    var id = href.slice(1), m = /^(p\d+)-(?:en|ko)-(ref|fn)-(.+)$/.exec(id);
    e.preventDefault();
    if (m){
      var info = (DATA[m[1]] || {})[m[2] === 'ref' ? 'cites' : 'fns'] || {}, tip = info[m[3]];
      var text = (m[2] === 'ref' ? (tip && tip.text) : tip) || blockText(visibleTarget(id));
      var acts = [];
      if (m[2] === 'ref' && tip && tip.url){
        var go = doc.createElement('a'); go.href = tip.url; go.target = '_blank'; go.rel = 'noopener';
        go.textContent = md4T('논문 링크 열기'); acts.push(go);
      }
      acts.push(button(m[2] === 'ref' ? md4T('참고문헌 목록에서 보기') : md4T('각주로 이동'), function(){
        closeSheet(); var t = visibleTarget(id); if (t) t.scrollIntoView({block: 'start'});
      }));
      openSheet((m[2] === 'ref' ? md4T('참고문헌 {n}', {n: m[3]}) : md4T('각주 {n}', {n: m[3]})), [para(text || md4T('(서지 정보를 찾지 못했어요)'))], acts);
      return;
    }
    if (id === 'lib'){ showLib(true); return; }
    var pm = /^(p\d+)$/.exec(id);
    if (pm){ openPaper(pm[1], true); return; }
    var t = visibleTarget(id);
    if (t) t.scrollIntoView({block: 'start'});
  }

  doc.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (a){ if (!sheet.contains(a)) onLink(a, e); return; }
    if (sheet.contains(e.target) || e.target.closest('.top')) return;
    var sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed) return;               // 길게 눌러 고르는 중이면 건드리지 않는다
    var cell = e.target.closest('.row .cell.ko, .row .cell.en');
    if (!cell || e.target.closest('img, table, math, pre')){ clearSent(); closeSheet(); return; }
    onSentence(e, cell);
  });
  doc.addEventListener('keydown', function(e){ if (e.key === 'Escape'){ clearSent(); closeSheet(); } });


  var CFG = window.NARANI_CFG || {};
  var PID = 'p0', DC = 'narani-data';
  var main = doc.querySelector('main'), libList = doc.getElementById('lib-list'),
      libBar = doc.getElementById('lib-bar'), loading = doc.getElementById('loading');
  var DATA = {}, INDEX = null, loadSeq = 0, routed = false;
  var APP = CFG.app_name || 'Narani';

  // ---- 화면 문구 ----
  function setText(id, s){ var el = doc.getElementById(id); if (el) el.textContent = s; }
  function labels(){
    doc.documentElement.lang = md4Lang;
    setText('lbl-ko', md4T('번역')); setText('lbl-en', md4T('원문')); setText('lbl-both', md4T('대조'));
    setText('fs-dn', md4T('가') + '−'); setText('fs-up', md4T('가') + '+');
    doc.getElementById('fs-dn').setAttribute('aria-label', md4T('글자 작게'));
    doc.getElementById('fs-up').setAttribute('aria-label', md4T('글자 크게'));
    doc.getElementById('back').setAttribute('aria-label', md4T('논문 목록'));
  }
  function setUiLang(l){
    var cats = CFG.catalogs || {};
    if (!l || l === md4Lang || !(l in cats || l === CFG.source_lang)) return;
    try { localStorage.setItem('narani:ui_lang', l); } catch (e) {}
    window.md4Lang = l; labels();
  }

  // ---- 저장소 ----
  function localSource(){
    return {get: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }, put: function(name, obj){   // 미리보기 서버는 mobile/ 아래만 받아 준다
      return fetch('data/' + name, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify(obj)}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
      });
    }};
  }
  // Google Drive — 데스크톱이 만든 `Narani` 폴더(같은 Cloud 프로젝트라 drive.file로 보인다)를 읽는다.
  // 로그인은 팝업 대신 **리디렉트**(OAuth 클라이언트 측 흐름) — iOS 홈 화면 앱에선 팝업이 막히거나
  // 다른 창으로 열린다. 돌아온 주소의 #access_token을 읽고 원래 화면(#!…)으로 되돌린다.
  // access token은 한 시간짜리다. 끊기면 읽던 것(캐시)은 그대로 열리고, 새로 받을 때만 다시 연결한다.
  var GSCOPE = 'https://www.googleapis.com/auth/drive.file', GAPI = 'https://www.googleapis.com/drive/v3',
      GUP = 'https://www.googleapis.com/upload/drive/v3';
  var authError = '';
  function qs(o){ return Object.keys(o).map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function takeRedirect(){
    var h = location.hash.replace(/^#/, '');
    if (!/(^|&)(access_token|error)=/.test(h)) return;
    var p = {}, saved = null;
    h.split('&').forEach(function(kv){ var i = kv.indexOf('='); if (i > 0) p[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); });
    try { saved = JSON.parse(sessionStorage.getItem('narani:gstate') || 'null'); sessionStorage.removeItem('narani:gstate'); } catch (e) {}
    history.replaceState(null, '', location.pathname + location.search + ((saved && saved.back) || ''));
    if (!saved || saved.s !== p.state) return;
    if (p.access_token){
      try {
        localStorage.setItem('narani:gtok', JSON.stringify({at: p.access_token, exp: Date.now() + (+p.expires_in || 3600) * 1000}));
        localStorage.setItem('narani:gconn', '1');
      } catch (e) {}
    } else authError = p.error || 'error';
  }
  function gToken(){
    try { var t = JSON.parse(localStorage.getItem('narani:gtok') || 'null'); return t && t.exp > Date.now() + 60000 ? t.at : null; }
    catch (e) { return null; }
  }
  function gSignIn(){
    var st = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem('narani:gstate', JSON.stringify({s: st, back: location.hash})); } catch (e) {}
    var o = {client_id: CFG.google_client_id, redirect_uri: location.origin + location.pathname.replace(/index\.html$/, ''), response_type: 'token',
             scope: GSCOPE, include_granted_scopes: 'true', state: st};
    location.href = 'https://accounts.google.com/o/oauth2/v2/auth?' + qs(o);
  }
  function driveSource(){
    var folder = null, ids = null;
    function fail(kind){ var e = new Error(kind); e.kind = kind; return e; }
    function api(url, opt){
      var t = gToken();
      if (!t) return Promise.reject(fail('auth'));
      opt = opt || {};
      var h = opt.headers || {}; h.Authorization = 'Bearer ' + t; opt.headers = h;
      return fetch(url, opt).then(function(r){
        if (r.status === 401){ try { localStorage.removeItem('narani:gtok'); } catch (e) {} throw fail('auth'); }
        if (!r.ok) throw fail('http ' + r.status);
        return r;
      });
    }
    function list(q){
      var out = [];
      function page(tok){
        var u = GAPI + '/files?' + qs({q: q, fields: 'nextPageToken,files(id,name,modifiedTime)', pageSize: 1000,
                                       spaces: 'drive', orderBy: 'modifiedTime desc'}) + (tok ? '&pageToken=' + encodeURIComponent(tok) : '');
        return api(u).then(function(r){ return r.json(); }).then(function(d){
          out = out.concat(d.files || []);
          return d.nextPageToken ? page(d.nextPageToken) : out;
        });
      }
      return page(null);
    }
    function findFolder(){
      if (folder) return Promise.resolve(folder);
      return list("mimeType = 'application/vnd.google-apps.folder' and trashed = false and appProperties has { key='narani' and value='store' }")
        .then(function(fs){
          if (!fs.length) throw fail('nofolder');
          folder = fs[0].id; return folder;
        });
    }
    function loadIds(){
      return findFolder().then(function(f){ return list("'" + f + "' in parents and trashed = false"); }).then(function(fs){
        ids = {}; fs.forEach(function(x){ if (!(x.name in ids)) ids[x.name] = x.id; }); return ids;
      });
    }
    return {
      drive: true,
      get: function(name){
        // 목록을 받을 때마다 이름표를 새로 받는다 — 데스크톱이 그사이 새 번들을 올렸을 수 있다
        var p = (name === 'index.json' || !ids) ? loadIds() : Promise.resolve(ids);
        return p.then(function(m){
          if (!m[name]) throw fail(name === 'index.json' ? 'nofolder' : 'missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media').then(function(r){ return r.json(); });
        });
      },
      // 폰이 쓰는 파일(mobile/…)만 — 있으면 내용만 바꾸고, 없으면 앱 폴더에 새로 만든다
      put: function(name, obj){
        var body = JSON.stringify(obj);
        return (ids ? Promise.resolve(ids) : loadIds()).then(function(m){
          if (m[name]) return api(GUP + '/files/' + m[name] + '?uploadType=media',
                                  {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: body});
          var b = 'narani' + Math.random().toString(36).slice(2);
          var meta = JSON.stringify({name: name, parents: [folder], mimeType: 'application/json'});
          return api(GUP + '/files?uploadType=multipart&fields=id', {method: 'POST',
            headers: {'Content-Type': 'multipart/related; boundary=' + b},
            body: '--' + b + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + meta +
                  '\r\n--' + b + '\r\nContent-Type: application/json\r\n\r\n' + body + '\r\n--' + b + '--'})
            .then(function(r){ return r.json(); }).then(function(d){ ids[name] = d.id; });
        });
      }
    };
  }
  takeRedirect();
  var source = CFG.local ? localSource() : (CFG.google_client_id ? driveSource() : null);
  function sourceProblem(err){
    // 저장소 오류 → 목록 위에 보일 안내와 단추
    var kind = err && err.kind;
    if (source && source.drive && (kind === 'auth' || authError)){
      var first = true;
      try { first = !localStorage.getItem('narani:gconn'); } catch (e) {}
      return {text: first ? md4T('Google Drive에 연결해 데스크톱에서 보낸 논문을 받아요.') : md4T('Google Drive 연결이 만료됐어요.'),
              button: first ? md4T('Google Drive 연결') : md4T('다시 연결'), act: function(){ gSignIn(); }};
    }
    if (kind === 'nofolder') return {text: md4T('Drive에 Narani 폴더가 없어요. 데스크톱 앱에서 먼저 폰으로 보내 주세요.')};
    return {text: md4T('목록을 불러오지 못했어요. 인터넷 연결을 확인하세요.')};
  }

  // ---- 오프라인 캐시 (받은 목록·번들) ----
  function cacheGet(name){
    if (!window.caches) return Promise.resolve(null);
    return caches.open(DC).then(function(c){ return c.match('__data/' + name); })
      .then(function(r){ return r ? r.json() : null; }).catch(function(){ return null; });
  }
  function cachePut(name, obj){
    if (!window.caches) return Promise.resolve();
    return caches.open(DC).then(function(c){
      return c.put('__data/' + name, new Response(JSON.stringify(obj), {headers: {'Content-Type': 'application/json'}}));
    }).catch(function(){});
  }
  function haveMap(){ try { return JSON.parse(localStorage.getItem('narani:have') || '{}') || {}; } catch (e) { return {}; } }
  function setHave(file, hash){
    var m = haveMap(); if (hash) m[file] = hash; else delete m[file];
    try { localStorage.setItem('narani:have', JSON.stringify(m)); } catch (e) {}
  }
  function pruneCache(ix){
    // 목록에서 빠진 논문의 캐시는 지운다 (폰 저장 공간)
    var keep = {}, m = haveMap(), k;
    ix.papers.forEach(function(e){ keep[e.file] = 1; });
    for (k in m) if (!keep[k]) setHave(k, null);
    if (!window.caches) return;
    caches.open(DC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){
          var name = decodeURIComponent(new URL(q.url).pathname).replace(/^.*?__data\//, '');
          if (name !== 'index.json' && !keep[name]) c.delete(q);
        });
      });
    }).catch(function(){});
  }
  function getPaper(e){
    return cacheGet(e.file).then(function(c){
      if (c && c.hash === e.hash) return c;
      if (!source){ if (c) return c; throw new Error('no source'); }
      return source.get(e.file).then(function(b){
        cachePut(e.file, b); setHave(e.file, b.hash); return b;
      }, function(err){ if (c) return c; throw err; });
    });
  }

  // ---- 목록 ----
  function fileId(e){ return String(e.file || '').replace(/^papers\//, '').replace(/\.json$/, ''); }
  function entryOf(id){
    if (!INDEX) return null;
    for (var i = 0; i < INDEX.papers.length; i++) if (fileId(INDEX.papers[i]) === id) return INDEX.papers[i];
    return null;
  }
  function fmtDate(sec){
    var d = new Date(sec * 1000);
    try { return d.toLocaleString(md4Lang, {dateStyle: 'medium', timeStyle: 'short'}); } catch (e) { return d.toLocaleString(); }
  }
  function el(tag, cls, text){ var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function renderLib(note){
    var have = haveMap(), n = 0;
    libList.innerHTML = '';
    doc.getElementById('lib-h').textContent = INDEX ? md4T('논문 {n}편', {n: INDEX.papers.length}) : APP;
    (INDEX ? INDEX.papers : []).forEach(function(e){
      var off = have[e.file] === e.hash; if (off) n++;
      var li = el('li'), a = el('a');
      a.href = '#!' + fileId(e); a.setAttribute('data-open', fileId(e));
      var title = el('span', 'li-title', e.title_ko || e.title);
      if (off) title.appendChild(el('span', 'dl', '⤓'));
      a.appendChild(title);
      if (e.title_ko) a.appendChild(el('span', 'li-en', e.title));
      var meta = el('span', 'li-meta', [e.authors, e.year, e.venue].filter(function(x){ return x; }).join(' · '));
      meta.appendChild(el('span', 'badge', e.has_ko ? md4T('번역') : md4T('원문만')));
      a.appendChild(meta); li.appendChild(a); libList.appendChild(li);
    });
    if (INDEX && !INDEX.papers.length) libList.appendChild(el('li', 'empty', md4T('올린 논문이 없어요. 데스크톱 앱에서 폰으로 보내 주세요.')));
    libBar.innerHTML = '';
    var info = [];
    if (INDEX && INDEX.updated) info.push(md4T('{date}에 올린 목록', {date: fmtDate(INDEX.updated)}));
    if (INDEX && INDEX.papers.length) info.push(md4T('오프라인 {n}/{m}편', {n: n, m: INDEX.papers.length}));
    if (note) info.push(note);
    libBar.appendChild(el('span', 'grow', info.join(' · ')));
    if (source){
      var re = el('button', '', md4T('새로 고침')); re.onclick = function(){ loadIndex(true); };
      libBar.appendChild(re);
    }
    if (INDEX && n < INDEX.papers.length && source){
      var all = el('button', '', md4T('모두 오프라인에 저장')); all.onclick = function(){ saveAll(all); };
      libBar.appendChild(all);
    }
  }
  function saveAll(btn){
    var list = INDEX.papers.slice(), total = list.length, done = 0, failed = 0;
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
    btn.disabled = true;
    (function next(){
      if (!list.length){ renderLib(failed ? md4T('{n}편은 받지 못했어요', {n: failed}) : ''); return; }
      var e = list.shift();
      getPaper(e).catch(function(){ failed++; }).then(function(){
        done++; btn.textContent = md4T('저장 중… {n}/{m}', {n: done, m: total}); next();
      });
    })();
  }
  function addAct(pr){
    if (!pr || !pr.button) return;
    var b = el('button', '', pr.button); b.onclick = pr.act; libBar.appendChild(b);
  }
  function showIndex(ix, note, pr){
    INDEX = ix; renderLib(note); addAct(pr);
    if (!routed){ routed = true; route(); }
  }
  function loadIndex(force){
    if (!force) libBar.textContent = md4T('불러오는 중…');
    return cacheGet('index.json').then(function(cached){
      if (cached && !force) showIndex(cached);
      if (!source){
        if (!cached) libBar.textContent = md4T('연결된 저장소가 없어요.');
        return;
      }
      return source.get('index.json').then(function(ix){
        cachePut('index.json', ix); setUiLang(ix.ui_lang); pruneCache(ix); showIndex(ix); flushPending();
      }, function(err){
        var pr = sourceProblem(err), offline = !pr.button && err && !err.kind;
        if (cached) showIndex(cached, offline ? md4T('오프라인 — 받아 둔 목록이에요') : pr.text, pr);
        else { libBar.innerHTML = ''; libBar.appendChild(el('span', 'grow', pr.text)); addAct(pr); }
      });
    });
  }

  // ---- 목록 ↔ 논문 ----
  function unmount(){ if (cur){ cur.parentNode.removeChild(cur); cur = null; } }
  function showLib(push){
    loadSeq++; unmount(); loading.style.display = 'none';
    lib.classList.remove('off'); topTitle.textContent = APP;
    closeSheet(); window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!lib');
  }
  function mount(b, target){
    main.insertAdjacentHTML('beforeend', b.html);
    cur = doc.getElementById(PID); cur.classList.add('on');
    DATA[PID] = {key: b.key, title: b.title_ko || b.title, cites: b.cites || {}, fns: b.fns || {}};
    var t = target ? doc.getElementById(target) : null, off = 0;
    if (!t){
      var saved = load('pos:' + b.key, '').split(':');
      t = saved[0] ? doc.getElementById(PID + '-' + saved[0]) : null; off = +saved[1] || 0;
    }
    if (t){ t.scrollIntoView({block: 'start'}); if (off) window.scrollBy(0, off); } else window.scrollTo(0, 0);
  }
  function openPaper(id, push, target){
    if (id === PID){ if (cur) window.scrollTo(0, 0); return !!cur; }
    var e = entryOf(id);
    if (!e) return false;
    var seq = ++loadSeq;
    unmount(); closeSheet();
    lib.classList.add('off'); topTitle.textContent = e.title_ko || e.title;
    loading.style.display = ''; loading.textContent = md4T('불러오는 중…');
    window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!' + id);
    getPaper(e).then(function(b){
      if (seq !== loadSeq) return;
      loading.style.display = 'none'; mount(b, target);
      notesOpen(e, b);
      if (INDEX) renderLib();
    }, function(err){
      if (seq !== loadSeq) return;
      if (err && err.kind === 'auth'){
        loading.textContent = md4T('Google Drive 연결이 만료됐어요.') + ' ';
        var b = el('button', '', md4T('다시 연결')); b.onclick = function(){ gSignIn(); };
        loading.appendChild(b);
      } else loading.textContent = md4T('이 논문을 받지 못했어요. 인터넷에 연결된 뒤 다시 열어 보세요.');
    });
    return true;
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}
  function route(){
    var h = location.hash.replace(/^#!?/, '');
    if (h && h !== 'lib' && openPaper(h, false)) return;
    showLib(false);
  }
  doc.getElementById('back').onclick = function(){ showLib(true); };
  window.addEventListener('popstate', route);
  libList.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[data-open]') : null;
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    openPaper(a.getAttribute('data-open'), true);
  });
  var saveTimer = null;
  window.addEventListener('scroll', function(){
    if (!cur || saveTimer) return;
    saveTimer = setTimeout(function(){
      saveTimer = null;
      var r = topRow(); if (!r || !cur) return;
      var into = Math.max(0, Math.round(64 - r.getBoundingClientRect().top));
      store('pos:' + cur.getAttribute('data-key'), r.id.replace(/^p\d+-/, '') + ':' + into);
    }, 400);
  }, {passive: true});

  labels(); topTitle.textContent = APP;
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(function(){});
  loadIndex(false);

  var COLORS = ['yellow', 'green', 'blue', 'pink', 'purple'], ACTX = 40;
  var NOTE = {key: null, fid: null, base: [], applied: 0};
  function lsGet(k, d){ try { var v = JSON.parse(localStorage.getItem(k) || 'null'); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function ops(key){ return lsGet('narani:ops:' + key, []); }
  function upTs(key){ return lsGet('narani:opsup:' + key, 0); }
  function lastTs(list){ return list.length ? list[list.length - 1].ts : 0; }
  function pendingCount(key){ var t = upTs(key); return ops(key).filter(function(o){ return o.ts > t; }).length; }

  function items(){   // 데스크톱 것 위에 아직 합쳐지지 않은 폰 기록을 얹는다
    var map = {}, order = [];
    NOTE.base.forEach(function(it){ map[it.id] = it; order.push(it.id); });
    ops(NOTE.key).forEach(function(o){
      if (o.ts <= NOTE.applied) return;
      if (o.op === 'del'){ delete map[o.id]; return; }
      if (!(o.item.id in map)) order.push(o.item.id);
      map[o.item.id] = o.item;
    });
    return order.filter(function(id){ return map[id]; }).map(function(id){ return map[id]; });
  }

  // ---- 위치 찾기 (데스크톱 뷰어의 resolve와 같은 순서: 제자리 → 앞뒤 문맥째 → 가장 가까운 같은 문구) ----
  function resolve(txt, a){
    var q = a.quote || '';
    if (!q) return null;
    if (txt.substr(a.start, q.length) === q) return [a.start, a.start + q.length];
    var probe = (a.prefix || '') + q + (a.suffix || ''), i = txt.indexOf(probe);
    if (i >= 0) return [i + (a.prefix || '').length, i + (a.prefix || '').length + q.length];
    var best = -1, bd = Infinity, from = 0, j;
    while ((j = txt.indexOf(q, from)) >= 0){ var d = Math.abs(j - a.start); if (d < bd){ bd = d; best = j; } from = j + 1; }
    return best >= 0 ? [best, best + q.length] : null;
  }
  function cellAt(row, side){
    var r = doc.getElementById(PID + '-r' + row);
    return r ? (r.querySelector('.cell.' + side) || r.querySelector('.cell.same')) : null;
  }
  var TXT = null;   // 칠하는 동안만 쓰는 셀 평문 표 (셀마다 textContent를 거듭 세지 않게)
  function textOf(c){
    if (!TXT) return c.textContent;
    for (var i = 0; i < TXT.length; i++) if (TXT[i].c === c) return TXT[i].t;
    return c.textContent;
  }
  function locate(a){
    for (var d = 0; d <= 3; d++){        // 행이 조금 밀렸으면 이웃 행에서
      var rows = d ? [a.row - d, a.row + d] : [a.row];
      for (var k = 0; k < rows.length; k++){
        var c = cellAt(rows[k], a.side);
        if (!c) continue;
        var hit = resolve(textOf(c), a);
        if (hit) return {cell: c, s: hit[0], e: hit[1]};
      }
    }
    // 그래도 없으면 같은 쪽 셀 전체에서 문구로 — 다시 번역·조립해 행이 크게 밀렸거나, 원문·번역 절 수가
    // 달라 나란히 맞추지 못한 논문(번역 셀이 원문 뒤에 따로 붙는다). 원래 행에 가장 가까운 것.
    if (!cur || !a.quote) return null;
    var cells = cur.querySelectorAll('.cell.' + a.side + ', .cell.same'), best = null, bd = Infinity;
    for (var i = 0; i < cells.length; i++){
      var t = textOf(cells[i]), probe = (a.prefix || '') + a.quote + (a.suffix || ''), j = t.indexOf(probe), s;
      if (j >= 0) s = j + (a.prefix || '').length;
      else if ((j = t.indexOf(a.quote)) >= 0) s = j;
      else continue;
      var rn = +cells[i].parentElement.id.replace(/^p\d+-r/, ''), dd = Math.abs(rn - a.row) + (j === s ? 0 : 0.5);
      if (dd < bd){ bd = dd; best = {cell: cells[i], s: s, e: s + a.quote.length}; }
    }
    return best;
  }

  // ---- 칠하기 ----
  function unpaint(){
    if (!cur) return;
    var ms = cur.querySelectorAll('mark.anno'), parents = [];
    for (var i = 0; i < ms.length; i++){
      var m = ms[i], p = m.parentNode;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
      if (parents.indexOf(p) < 0) parents.push(p);
    }
    parents.forEach(function(p){ p.normalize(); });
  }
  function paint(){
    if (!cur || !NOTE.key) return;
    unpaint();
    TXT = [].map.call(cur.querySelectorAll('.cell'), function(c){ return {c: c, t: c.textContent}; });
    var list = items(), groups = [], lost = [];
    list.forEach(function(it){
      var placed = false;
      (it.anchors || []).forEach(function(a){
        var h = locate(a);
        if (!h) return;
        var g = null;
        for (var i = 0; i < groups.length; i++) if (groups[i].cell === h.cell){ g = groups[i]; break; }
        if (!g){ g = {cell: h.cell, segs: []}; groups.push(g); }
        // 겹치는 표시는 먼저 온 것만 칠한다 (겹쳐 감싸면 마크가 엉킨다)
        for (i = 0; i < g.segs.length; i++) if (g.segs[i].s < h.e && h.s < g.segs[i].e) return;
        g.segs.push({s: h.s, e: h.e, it: it});
        placed = true;
      });
      if (!placed) lost.push(it);
    });
    TXT = null;
    groups.forEach(function(g){
      wrapRanges(g.cell, g.segs, function(seg){
        var m = doc.createElement('mark');
        m.className = 'anno c-' + (COLORS.indexOf(seg.it.color) >= 0 ? seg.it.color : 'yellow');
        m.setAttribute('data-aid', seg.it.id);
        return m;
      });
    });
    list.forEach(function(it){    // 메모가 있으면 셀(원문·번역)마다 마지막 조각 뒤에 ✎ — 보기 모드에 따라 한쪽만 보이므로
      if (!it.note) return;
      var ms = cur.querySelectorAll('mark.anno[data-aid="' + it.id + '"]'), last = [];
      for (var i = 0; i < ms.length; i++){
        var c = ms[i].closest('.cell'), k;
        for (k = 0; k < last.length; k++) if (last[k].c === c) break;
        if (k < last.length) last[k].m = ms[i]; else last.push({c: c, m: ms[i]});
      }
      last.forEach(function(x){ x.m.classList.add('note-end'); });
    });
    noteHead(list, lost);
  }

  // ---- 논문 머리의 메모 목록 ----
  function noteHead(list, lost){
    var head = cur && cur.querySelector('header.ph');
    if (!head) return;
    var box = head.querySelector('.anno-head');
    if (!box){ box = el('details', 'anno-head toc'); head.appendChild(box); }
    var pend = pendingCount(NOTE.key);
    box.style.display = (list.length || pend) ? '' : 'none';
    box.innerHTML = '';
    var sum = el('summary', '', md4T('하이라이트·메모 {n}개', {n: list.length}));
    if (pend) sum.appendChild(el('span', 'anno-pend', ' · ' + md4T('올릴 기록 {n}개', {n: pend})));
    box.appendChild(sum);
    if (pend && source && source.drive && !gToken()){   // 연결이 끊겨 못 올린 기록 — 다시 연결하면 올라간다
      var rc = button(md4T('다시 연결'), function(){ gSignIn(); });
      rc.className = 'anno-reconnect'; box.appendChild(rc);
    }
    var ol = el('ol');
    list.forEach(function(it){
      var li = el('li'), a = (it.anchors || [])[0] || {};
      var q = el('span', 'aq c-' + it.color, (a.quote || '').slice(0, 120));
      li.appendChild(q);
      if (it.note) li.appendChild(el('span', 'an', it.note));
      if (lost.indexOf(it) >= 0) li.appendChild(el('span', 'al', md4T('(본문에서 위치를 찾지 못했어요)')));
      else li.onclick = function(){
        var m = cur.querySelector('mark.anno[data-aid="' + it.id + '"]');
        if (m) m.scrollIntoView({block: 'center'});
      };
      ol.appendChild(li);
    });
    box.appendChild(ol);
  }

  // ---- 열기 · 받기 ----
  function notesOpen(e, b){
    NOTE = {key: b.key, fid: fileId(e), base: [], applied: 0};
    paint();
    var key = b.key, name = 'notes/' + NOTE.fid + '.json';
    if (!e.notes_hash) return;
    cacheGet(name).then(function(c){
      if (c && c.__h === e.notes_hash) return c;
      if (!source) return c;
      return source.get(name).then(function(n){ n.__h = e.notes_hash; cachePut(name, n); return n; },
                                   function(){ return c; });
    }).then(function(n){
      if (!n || NOTE.key !== key) return;
      NOTE.base = n.items || []; NOTE.applied = +n.applied || 0;
      var mine = ops(key), left = mine.filter(function(o){ return o.ts > NOTE.applied; });
      if (left.length !== mine.length) lsSet('narani:ops:' + key, left);   // 데스크톱이 합친 기록은 지운다
      paint();
    });
  }

  // ---- 기록 남기기 · 올리기 ----
  function record(op){
    var list = ops(NOTE.key), t = Date.now() / 1000;
    op.ts = Math.max(t, lastTs(list) + 0.001);
    list.push(op);
    lsSet('narani:ops:' + NOTE.key, list);
    var keys = lsGet('narani:opskeys', {}); keys[NOTE.key] = NOTE.fid; lsSet('narani:opskeys', keys);
    paint();
    upload(NOTE.key, NOTE.fid);
  }
  var uploading = {};
  function upload(key, fid){
    if (!source || !source.put || uploading[key]) return;
    var list = ops(key);
    if (!list.length || lastTs(list) <= upTs(key)) return;
    uploading[key] = true;
    var top = lastTs(list);
    source.put('mobile/' + fid + '.json', {format: 1, key: key, ops: list}).then(function(){
      lsSet('narani:opsup:' + key, top);
    }, function(){}).then(function(){
      uploading[key] = false;
      if (NOTE.key === key && cur) noteHead(items(), []);
      if (lastTs(ops(key)) > upTs(key) && lastTs(ops(key)) > top) upload(key, fid);   // 올리는 사이 새로 적은 것
    });
  }
  function flushPending(){   // 오프라인일 때 적어 둔 기록 — 목록을 새로 받으면(연결되면) 올린다
    var keys = lsGet('narani:opskeys', {});
    for (var k in keys) upload(k, keys[k]);
  }
  window.addEventListener('online', flushPending);

  // ---- 문장 시트의 동작 (CORE_JS의 onSentence가 부른다) ----
  function spanOf(list, idx){
    if (!idx || !idx.length) return null;
    var s = list[idx[0]].s, e = list[idx[0]].e;
    for (var i = 1; i < idx.length; i++){ s = Math.min(s, list[idx[i]].s); e = Math.max(e, list[idx[i]].e); }
    return [s, e];
  }
  function anchorOf(cell, row, side, sp){
    var txt = cell.textContent;
    return {side: side, row: row, start: sp[0], end: sp[1], quote: txt.slice(sp[0], sp[1]),
            prefix: txt.slice(Math.max(0, sp[0] - ACTX), sp[0]), suffix: txt.slice(sp[1], sp[1] + ACTX)};
  }
  function itemAt(row, side, sp){
    var list = items();
    for (var i = 0; i < list.length; i++){
      var an = list[i].anchors || [];
      for (var k = 0; k < an.length; k++){
        if (an[k].side !== side) continue;
        var h = locate(an[k]);
        if (h && h.cell === cellAt(row, side) && h.s < sp[1] && sp[0] < h.e) return list[i];
      }
    }
    return null;
  }
  function sentenceActs(rowEl, pi, pr, side){
    if (!source || !source.put || !NOTE.key) return null;
    var row = +rowEl.id.replace(/^p\d+-r/, '');
    var spans = {en: spanOf(pr.en, pr.pairs[pi][0]), ko: spanOf(pr.ko, pr.pairs[pi][1])};
    if (!spans[side]) return null;
    var it = itemAt(row, side, spans[side]);
    function fresh(color, note){
      var an = [];
      ['en', 'ko'].forEach(function(s){
        var c = rowEl.querySelector('.cell.' + s);
        if (c && spans[s]) an.push(anchorOf(c, row, s, spans[s]));
      });
      return {id: 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), color: color,
              note: note || '', anchors: an, created: Date.now() / 1000};
    }
    var nodes = [], dots = el('div', 'anno-dots');
    if (it && it.note) nodes.push(para(it.note, 'anno-note'));
    COLORS.forEach(function(c){
      var b = el('button', 'dot c-' + c + (it && it.color === c ? ' on' : ''));
      b.setAttribute('aria-label', c);
      b.onclick = function(){
        record({op: 'put', item: it ? Object.assign({}, it, {color: c}) : fresh(c)});
        clearSent(); closeSheet();
      };
      dots.appendChild(b);
    });
    nodes.push(dots);
    var buttons = [button(it && it.note ? md4T('메모 고치기') : md4T('메모 달기'), function(){ editNote(it, fresh); })];
    if (it) buttons.push(button(md4T('지우기'), function(){ record({op: 'del', id: it.id}); clearSent(); closeSheet(); }));
    return {nodes: nodes, buttons: buttons};
  }
  function editNote(it, fresh){
    var ta = el('textarea', 'anno-ta');
    ta.value = (it && it.note) || ''; ta.rows = 4; ta.placeholder = md4T('메모를 적으세요');
    var save = button(md4T('저장'), function(){
      var note = ta.value.replace(/\s+$/, '');
      if (it) record({op: 'put', item: Object.assign({}, it, {note: note})});
      else if (note) record({op: 'put', item: fresh('yellow', note)});
      clearSent(); closeSheet();
    });
    openSheet(md4T('메모'), [ta], [save]);
    setTimeout(function(){ ta.focus(); }, 50);
  }
})();
