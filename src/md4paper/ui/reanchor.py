"""문구가 바뀐 하이라이트·메모를 다시 찾는 JS — 재변환·재번역 뒤에도 메모가 제자리를 찾게.

뷰어는 메모를 셀 평문의 quote(+앞뒤 문맥)로 다시 찾는다. 그런데 다시 변환하면 원문이 조금 바뀌고
(띄어쓰기 "( 0 . 607 )"↔"(0.607)", 깨졌던 글자 "C -tests"→"t -tests", 수식 $…$), 다시 번역하면
같은 영어 문장도 한국어 표현이 달라진다. 정확한 문구로는 못 찾으니 두 단계로 물러난다.

  1) 공백·따옴표 모양·대소문자를 무시하고 같은 문구 — 확실하므로 좌표를 고쳐 저장한다.
  2) 문장(1~3개 묶음) 단위 글자 bigram 유사도(Dice) — 가장 비슷한 자리가 기준을 넘고 2등과
     충분히 벌어질 때만 붙인다. 아주 비슷할 때만 저장하고, 애매하면 **표시만** 한다(원래 quote를
     짐작으로 덮어쓰면 되돌릴 길이 없다).

sentpair.JS와 같은 방식 — 함수 선언만 담고, 쓰는 쪽 IIFE 안에 끼워 넣는다(sentsOf에 기댄다).
"""

FUZZY_MIN = 0.5      # 이보다 덜 비슷하면 다른 문장으로 본다
FUZZY_MARGIN = 0.1   # 2등과 이만큼은 벌어져야 한다 — 비슷한 문장이 여럿이면 붙이지 않는다
FUZZY_SAVE = 0.75    # 이 이상이면 새 문구로 앵커를 고쳐 저장한다
ROW_WINDOW = 25      # 원래 행에서 이만큼 안쪽만 본다 (나란히 보기 격자일 때)

JS = """
  function normMap(txt){            // 공백 제거·따옴표 통일·소문자 + 원문 위치 표
    var out = [], pos = [];
    for (var i = 0; i < txt.length; i++){
      var c = txt.charAt(i);
      if (/\\s/.test(c)) continue;
      if (/["'`\\u2018\\u2019\\u201c\\u201d\\u201e\\u2032\\u2033]/.test(c)) c = "'";  // 따옴표는 모양 무시
      out.push(c.toLowerCase()); pos.push(i);
    }
    return {t: out.join(''), pos: pos};
  }
  function bigrams(s){
    var m = {}, n = 0;
    for (var i = 0; i + 1 < s.length; i++){ var k = s.substr(i, 2); m[k] = (m[k] || 0) + 1; n++; }
    return {m: m, n: n};
  }
  function dice(a, b){
    if (!a.n || !b.n) return 0;
    var hit = 0;
    for (var k in a.m) if (b.m[k]) hit += Math.min(a.m[k], b.m[k]);
    return 2 * hit / (a.n + b.n);
  }
  // 앵커 b를 cells(같은 쪽 셀들)에서 다시 찾는다 → {sc, s, e, row, score, sure} 또는 null
  function fuzzyFind(b, cells){
    var q = normMap(b.quote || '').t;
    if (q.length < 6) return null;
    var near = cells.filter(function(c){
      return !c.classList.contains('sbs-cell')
        || Math.abs(+c.getAttribute('data-row') - b.row) <= __ROW_WINDOW__;
    });
    var i, j, L, exact = null;
    for (i = 0; i < near.length; i++){           // 1) 공백·따옴표만 다른 같은 문구
      var nm = normMap(near[i].textContent), k = nm.t.indexOf(q);
      if (k < 0) continue;
      var row = +near[i].getAttribute('data-row');
      if (!exact || Math.abs(row - b.row) < Math.abs(exact.row - b.row))
        exact = {sc: near[i], s: nm.pos[k], e: nm.pos[k + q.length - 1] + 1, row: row, score: 1, sure: true};
    }
    if (exact) return exact;
    var qb = bigrams(q), cands = [];              // 2) 문장 묶음 유사도
    for (i = 0; i < near.length; i++){
      var txt = near[i].textContent, ss = sentsOf(near[i]);
      for (j = 0; j < ss.length; j++){
        for (L = 1; L <= 3 && j + L - 1 < ss.length; L++){
          var s = ss[j].s, e = ss[j + L - 1].e, t = normMap(txt.slice(s, e)).t;
          if (t.length > q.length * 2.5) break;
          if (t.length * 2.5 < q.length) continue;
          cands.push({sc: near[i], s: s, e: e, row: +near[i].getAttribute('data-row'),
                      score: dice(qb, bigrams(t))});
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
""".replace("__ROW_WINDOW__", str(ROW_WINDOW)).replace("__FUZZY_MIN__", str(FUZZY_MIN)) \
    .replace("__FUZZY_MARGIN__", str(FUZZY_MARGIN)).replace("__FUZZY_SAVE__", str(FUZZY_SAVE))
