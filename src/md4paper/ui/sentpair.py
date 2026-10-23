"""원문↔번역 문장 짝 맞추기 JS — 뷰어(app.py의 하이라이트 스크립트)와 모바일 HTML(§mobile)이 같이 쓴다.

함수 선언만 담는다(전역 상태 없음). 쓰는 쪽이 자기 IIFE 안에 문자열로 끼워 넣으면 그 스코프의
지역 함수가 된다 — 두 화면의 문장 분할·짝 규칙이 갈라지지 않게 한 군데에 둔다.

  textNodes(root)          → 텍스트 노드 목록 (문서 순서)
  wrapRanges(scope, segs, make) → 평문 오프셋 구간을 make()가 만든 요소로 감싼다
  sentsOf(scope)           → 블록 경계를 지킨 문장 구간 [{s, e}]
  pairUp(E, K)             → 원문·번역 문장 짝 [[en 인덱스들], [ko 인덱스들]] (못 맞추면 null)

일반 문자열이다(raw 아님) — 정규식의 역슬래시를 두 번 써 둔 것은 JS에 하나로 들어가게 하려는 것이다.
원래 app.py 하이라이트 스크립트 안에 있던 코드를 글자 그대로 옮겼다.
"""

JS = """
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
  var ABBR = /(?:^|[\\s(\\[\\u201c"'])(?:e\\.g|i\\.e|cf|vs|etc|al|Fig|Figs|Eq|Eqs|Sec|Secs|Tab|No|Dr|Mr|Mrs|Ms|Prof|St|approx|resp|Ref|Refs|pp|Vol|Ch|Inc|Ltd|Univ)\\.$/i;
  var CLOSERS = '.!?\\u2026"\\u2019\\u201d\\')]';
  function trimRange(txt, s, e){
    while (s < e && /\\s/.test(txt.charAt(s))) s++;
    while (e > s && /\\s/.test(txt.charAt(e - 1))) e--;
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
      var cjk = '\\u3002\\uff01\\uff1f'.indexOf(txt.charAt(i)) >= 0;
      if (inBracket[i - s0] || ('.!?\\u2026'.indexOf(txt.charAt(i)) < 0 && !cjk)) continue;
      var j = i + 1;
      while (j < e0 && (CLOSERS + '\\u300d\\u300f\\uff09').indexOf(txt.charAt(j)) >= 0) j++;   // 닫는 따옴표·괄호까지 문장에 포함
      if (!cjk && j < e0 && !/\\s/.test(txt.charAt(j))) continue;  // 0.5 · 3.1 처럼 붙어 있으면 문장 끝이 아님
      var head = txt.slice(start, i + 1);
      if (!cjk && (ABBR.test(head) || /(^|[\\s(\\[])[A-Z]\\.$/.test(head))) continue;
      var k = j; while (k < e0 && /\\s/.test(txt.charAt(k))) k++;
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
"""
