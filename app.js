
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
    // 방금 감쌌으면 e.target은 감싸기 전 노드다 — 손가락 아래를 다시 본다
    var under = doc.elementFromPoint(e.clientX, e.clientY);
    var sp = under && under.closest ? under.closest('.sent') : null;
    if (!sp || !cell.contains(sp)){ clearSent(); closeSheet(); return; }
    var pi = sp.getAttribute('data-pi'), key = row.id + ':' + pi;
    clearSent();
    if (tapped === key){ closeSheet(); return; }
    [].forEach.call(row.querySelectorAll('.sent[data-pi="' + pi + '"]'), function(s){ s.classList.add('on'); });
    if (bothVisible(row)){ tapped = null; closeSheet(); return; }   // 양쪽이 다 보이면 짝 표시로 충분
    var txt = sentText(pr, other, +pi);
    openSheet(label, [para(txt || md4T('(짝이 되는 문장이 없어요 — 번역에서 합쳐지거나 빠진 문장)'), txt ? '' : 'more')],
              [peekButton(row)]);
    tapped = key;
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
    }};
  }
  // Google Drive — 데스크톱이 만든 `Narani` 폴더(같은 Cloud 프로젝트라 drive.file로 보인다)를 읽는다.
  // 로그인은 팝업 대신 **리디렉트**(OAuth 클라이언트 측 흐름) — iOS 홈 화면 앱에선 팝업이 막히거나
  // 다른 창으로 열린다. 돌아온 주소의 #access_token을 읽고 원래 화면(#!…)으로 되돌린다.
  // access token은 한 시간짜리다. 끊기면 읽던 것(캐시)은 그대로 열리고, 새로 받을 때만 다시 연결한다.
  var GSCOPE = 'https://www.googleapis.com/auth/drive.file', GAPI = 'https://www.googleapis.com/drive/v3';
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
    function api(url){
      var t = gToken();
      if (!t) return Promise.reject(fail('auth'));
      return fetch(url, {headers: {Authorization: 'Bearer ' + t}}).then(function(r){
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
        cachePut('index.json', ix); setUiLang(ix.ui_lang); pruneCache(ix); showIndex(ix);
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
})();
