"""문구가 바뀐 하이라이트·메모를 다시 찾는 JS — 재변환·재번역 뒤에도 메모가 제자리를 찾게.

뷰어는 메모를 셀 평문의 quote(+앞뒤 문맥)로 다시 찾는다. 그런데 다시 변환하면 원문이 조금 바뀌고
(띄어쓰기 "( 0 . 607 )"↔"(0.607)", 깨졌던 글자 "C -tests"→"t -tests", 수식 $…$), 다시 번역하면
같은 영어 문장도 한국어 표현이 달라진다. 정확한 문구로는 못 찾으니 두 단계로 물러난다.

  1) 공백·따옴표·하이픈 모양·대소문자를 무시하고 같은 문구 — 확실하므로 좌표를 고쳐 저장한다.
  2) 문장(1~3개 묶음) 단위 글자 bigram 유사도(Dice) — 가장 비슷한 자리가 기준을 넘고 2등과
     충분히 벌어질 때만 붙인다. 앞뒤 문맥(prefix/suffix)도 CTX_WEIGHT만큼 점수에 넣는다 — 같은 문구가
     참고문헌·표에 또 나와도 문맥이 다르면 밀린다. 한글 비율이 크게 다른 자리(번역 쪽 셀 안의 영어
     참고문헌 항목 등)는 후보에서 뺀다. 아주 비슷하면(FUZZY_SAVE 이상) 확실한 것으로, 아니면 추정(guess)으로
     저장한다 — 어느 쪽이든 원래 단서는 orig에 남으므로 짐작으로 덮어써도 되돌릴 수 있다.

행 번호는 믿을 수 없다 — 재변환으로 문단이 잘게 나뉘면 같은 문장이 수십 행 뒤로 밀린다. 그래서
  · 짝 앵커(같은 표시의 다른 쪽)가 붙었으면 **그 행**의 반대쪽 셀에서 pairUp으로 짝 문장을 다시 고른다
    (siblingFind). 더블클릭 표시는 원래 한 행의 짝 문장이었으므로 구조가 근거다 — 유사도는 확인용.
  · 문장 유사도 탐색(fuzzyFind)은 짝 앵커의 행(없으면 자기 행) 근처를 본다. 서랍의 "다시 찾기"는
    창을 없애고 문서 전체를 본다.
문구가 바뀐 채 붙인 앵커는 guess(추정)로 표시하고 원래 단서를 orig에 한 번 남긴다(되돌릴 수 있게).

sentpair.JS와 같은 방식 — 함수 선언만 담고, 쓰는 쪽 IIFE 안에 끼워 넣는다(sentsOf에 기댄다).
"""

FUZZY_MIN = 0.5      # 이보다 덜 비슷하면 다른 문장으로 본다
FUZZY_MARGIN = 0.1   # 2등과 이만큼은 벌어져야 한다 — 비슷한 문장이 여럿이면 붙이지 않는다
FUZZY_SAVE = 0.75    # 이 이상이면 새 문구로 앵커를 고쳐 저장한다
CTX_WEIGHT = 0.25    # 유사도 점수에서 앞뒤 문맥이 차지하는 몫 (문맥이 없는 앵커는 문구만)
SCRIPT_GAP = 0.4     # 문구와 후보의 한글 비율이 이만큼 넘게 다르면 다른 글로 본다
ROW_WINDOW = 25      # 원래 행에서 이만큼 안쪽만 본다 (나란히 보기 격자일 때)

JS = """
  // 공백·하이픈·대시 제거, 따옴표 모양 통일, 유니코드 호환 분해(NFKC: ﬁ→fi, 전각→반각), 소문자
  // + 정규화 글자마다 원문 위치 표
  function normMap(txt){
    var out = [], pos = [];
    for (var i = 0; i < txt.length; i++){
      var c = txt.charAt(i);
      // 하이픈·대시는 아예 뺀다 — 줄바꿈 하이픈("explo- ration")·"pre-defined"↔"predefined"·
      // "—"↔" - "가 같아지게
      if (/\\s|[\\u00ad\\u200b\\-\\u2010-\\u2015\\u2212]/.test(c)) continue;
      if (/["'`\\u2018\\u2019\\u201c\\u201d\\u201e\\u2032\\u2033]/.test(c)) c = "'";  // 따옴표는 모양 무시
      else if (c.normalize) c = c.normalize('NFKC');
      c = c.toLowerCase();
      for (var j = 0; j < c.length; j++){ if (/\\s/.test(c.charAt(j))) continue; out.push(c.charAt(j)); pos.push(i); }
    }
    return {t: out.join(''), pos: pos};
  }
  function bigrams(s){
    var m = {}, n = 0;
    for (var i = 0; i + 1 < s.length; i++){ var k = s.substr(i, 2); m[k] = (m[k] || 0) + 1; n++; }
    return {m: m, n: n};
  }
  function hangulRatio(t){
    var h = 0, n = 0;
    for (var i = 0; i < t.length; i++){ var c = t.charCodeAt(i);
      if (c >= 0xac00 && c <= 0xd7a3) h++;
      if (c > 0x2f) n++; }
    return n ? h / n : 0;
  }
  function dice(a, b){
    if (!a.n || !b.n) return 0;
    var hit = 0;
    for (var k in a.m) if (b.m[k]) hit += Math.min(a.m[k], b.m[k]);
    return 2 * hit / (a.n + b.n);
  }
  // 앵커 b를 cells(같은 쪽 셀들)에서 다시 찾는다 → {sc, s, e, row, score, sure} 또는 null
  // center: 찾을 행 중심(기본 b.row), wide: 참이면 행 창 없이 문서 전체
  function fuzzyFind(b, cells, center, wide){
    var q = normMap(b.quote || '').t;
    if (q.length < 6) return null;
    if (center == null) center = b.row;
    var near = cells.filter(function(c){
      return wide || !c.classList.contains('sbs-cell')
        || Math.abs(+c.getAttribute('data-row') - center) <= __ROW_WINDOW__;
    });
    var i, j, L, exact = null;
    for (i = 0; i < near.length; i++){           // 1) 공백·따옴표만 다른 같은 문구
      var nm = normMap(near[i].textContent), k = nm.t.indexOf(q);
      if (k < 0) continue;
      var row = +near[i].getAttribute('data-row');
      if (!exact || Math.abs(row - center) < Math.abs(exact.row - center))
        exact = {sc: near[i], s: nm.pos[k], e: nm.pos[k + q.length - 1] + 1, row: row, score: 1, sure: true};
    }
    if (exact) return exact;
    var qb = bigrams(q), cands = [], qh = hangulRatio(q);   // 2) 문장 묶음 유사도 (+ 앞뒤 문맥)
    var pre = normMap(b.prefix || '').t, suf = normMap(b.suffix || '').t;
    var cb = bigrams(pre + '\\u0000' + suf), cw = (pre.length + suf.length >= 6) ? __CTX_WEIGHT__ : 0;
    for (i = 0; i < near.length; i++){
      var txt = near[i].textContent, ss = sentsOf(near[i]);
      for (j = 0; j < ss.length; j++){
        for (L = 1; L <= 3 && j + L - 1 < ss.length; L++){
          var s = ss[j].s, e = ss[j + L - 1].e, t = normMap(txt.slice(s, e)).t;
          if (t.length > q.length * 2.5) break;
          if (t.length * 2.5 < q.length) continue;
          var th = hangulRatio(t);   // 한쪽만 한글이 있거나 비율이 크게 다르면 다른 글
          if (Math.abs(th - qh) > __SCRIPT_GAP__ || (th > 0) !== (qh > 0)) continue;
          var sc = dice(qb, bigrams(t));
          if (cw) sc = (1 - cw) * sc + cw * dice(cb, bigrams(
            normMap(txt.slice(Math.max(0, s - 60), s)).t + '\\u0000' + normMap(txt.slice(e, e + 60)).t));
          cands.push({sc: near[i], s: s, e: e, row: +near[i].getAttribute('data-row'), score: sc});
        }
      }
    }
    if (!cands.length) return null;
    cands.sort(function(x, y){ return y.score - x.score; });
    var best = cands[0], second = 0;
    for (i = 1; i < cands.length; i++){           // 2등은 1등과 겹치지 않는 자리 중에서
      var c = cands[i];
      if (c.sc === best.sc && c.s < best.e && best.s < c.e) continue;
      second = c.score; break;
    }
    if (best.score < __FUZZY_MIN__ || best.score - second < __FUZZY_MARGIN__) return null;
    best.sure = best.score >= __FUZZY_SAVE__;
    return best;
  }
  // 짝 앵커가 sibCell의 [s, e)에 붙었을 때, 같은 행의 반대쪽 셀(cell)에서 b의 짝 문장 구간을 고른다.
  // 원문 → 번역 순으로 pairUp한다(만들 때와 같은 규칙). → {sc, s, e, row, score, sure} 또는 null
  function siblingFind(b, cell, sibCell, s, e){
    if (!cell || !sibCell) return null;
    var S = sentsOf(sibCell), T = sentsOf(cell), enFirst = b.side === 'ko';
    var P = enFirst ? pairUp(S, T) : pairUp(T, S);
    if (!P) return null;
    var hit = {}, idx = [], i, j;
    for (i = 0; i < S.length; i++) if (S[i].s < e && S[i].e > s) hit[i] = 1;
    for (i = 0; i < P.length; i++){
      var mine = P[i][enFirst ? 0 : 1], theirs = P[i][enFirst ? 1 : 0], on = false;
      for (j = 0; j < mine.length; j++) if (hit[mine[j]]) on = true;
      if (on) idx = idx.concat(theirs);
    }
    if (!idx.length) return null;
    var a0 = Infinity, b0 = -1;
    for (i = 0; i < idx.length; i++){ a0 = Math.min(a0, T[idx[i]].s); b0 = Math.max(b0, T[idx[i]].e); }
    var score = dice(bigrams(normMap(b.quote || '').t), bigrams(normMap(cell.textContent.slice(a0, b0)).t));
    if (score < __FUZZY_MIN__) return null;   // 구조로 고른 짝이라도 전혀 다른 문장이면 붙이지 않는다
    return {sc: cell, s: a0, e: b0, row: +cell.getAttribute('data-row'), score: score,
            sure: score >= __FUZZY_SAVE__};
  }
""".replace("__ROW_WINDOW__", str(ROW_WINDOW)).replace("__FUZZY_MIN__", str(FUZZY_MIN)) \
    .replace("__FUZZY_MARGIN__", str(FUZZY_MARGIN)).replace("__FUZZY_SAVE__", str(FUZZY_SAVE)) \
    .replace("__CTX_WEIGHT__", str(CTX_WEIGHT)).replace("__SCRIPT_GAP__", str(SCRIPT_GAP))
