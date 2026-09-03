"""다단 조판의 읽기 순서 복구 — 순수 기하(LLM 없음).

Docling의 규칙 기반 reading-order 예측기는 첫 페이지처럼 조판이 복잡한 쪽에서 **오른쪽 단을
왼쪽 단보다 먼저** 내보내거나, 여러 열로 짜인 저자 그리드를 열 우선(column-major)으로 훑는다.
그 결과 초록이 "1 Introduction" 뒤로 밀리고 저자 순서가 뒤바뀐다. 이 모듈은 prov bbox만 보고
그 쪽의 **단(column) 구조**를 스스로 세운 뒤, 위반이 증명된 쪽에 한해 순서를 되돌린다.

설계 원칙(PLAN.md §1 "확신 없으면 추측하지 않는다"):
- 위반이 **증명되지 않은 쪽은 손대지 않는다** — 출력이 바이트 단위로 종전과 같다.
- 학회·논문별 상수를 쓰지 않는다. 단 폭·행 높이·그리드 여부를 전부 그 쪽의 좌표에서 유도한다.
- 되돌린 뒤 영숫자 문자 다중집합이 달라지면(= 텍스트가 새거나 늘면) 그 쪽을 통째로 원복한다.

호출 위치: docling_backend.extract_to 에서 _relocate_footer_blocks 뒤, save_as_markdown 앞
(prov bbox가 살아 있는 마지막 지점).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 순서 판정에서 제외하는 라벨 — 러닝 헤더/푸터는 본문 흐름이 아니고, 각주는 아래쪽 자기 영역에 산다.
_SKIP_JUDGE = frozenset({"page_header", "page_footer", "footnote", "caption"})
_EDGE_LABELS = frozenset({"page_header", "page_footer"})
# 위반 판정에서 빼는 비-본문 단위 — 그림·표는 플로트라 어느 단에 놓였는지가 읽기 순서의 증거가 못 된다
# (윗단에 나란히 놓인 두 장짜리 그림이 '오른쪽 단 먼저'로 오인된다).
_FLOAT_LABELS = frozenset({"picture", "table", "chart", "form", "key_value_area"})
# 단 구조를 유도할 때 빼는 라벨 — 가운데 정렬된 그림 캡션이나 1.5단짜리 표는 단 사이 여백을
# 가로질러 좌우 단을 하나로 붙여 버린다(= 그 쪽의 복구가 조용히 꺼진다).
_NON_COLUMN = _SKIP_JUDGE | _FLOAT_LABELS

_FULL_WIDTH = 0.85     # 텍스트 폭의 이 비율 이상이면 단을 가로지르는 전폭 블록(제목·전폭 그림)
_COL_COVER = 0.50      # 한 단의 이 비율 이상을 덮는 단이 둘이면 역시 전폭
_WIDE_MIN = 0.30       # 단 구조를 유도할 때 쓸 '본문 폭' 조각의 최소 폭(텍스트 폭 대비)
_COL_MERGE_TOL = 1.0   # x 구간 병합 허용 오차(pt) — 1pt 스치는 접촉으로 단이 합쳐지지 않게
_CENTER_TOL = 2.5      # 같은 그리드 열로 볼 x 중심 오차(pt)
_GRID_MAX_W = 0.60     # 그리드 셀로 볼 최대 폭(단 폭 대비) — 본문 문단은 단을 꽉 채우므로 제외
_GRID_MIN_FRAGS = 4    # 이보다 적으면 그리드로 보지 않는다
_GRID_MIN_COLS = 3     # 그리드로 인정할 최소 열 수 — 2개는 본문 단과 구별이 안 된다
_ROW_GAP = 0.50        # 행 병합 허용 간격(줄 높이 대비) — 셀 안 줄 사이는 붙고, 행 사이는 떨어진다
_LATTICE_TOL = 0.15    # 그리드 열 간격의 허용 편차(평균 대비) — 저자 그리드는 등간격 격자다
_COLLAPSE_MIN = 0.85   # 단 구조가 하나로 뭉쳤는데 그 폭이 본문 폭의 이 비율 이상이면 '붕괴'


class _Unjudged(Exception):  # noqa: N818 — 예외라기보다 '이 쪽은 판정 못 함' 신호
    """이 쪽의 기하를 세울 수 없다 — 좌표 없는 단위, 끊어진 참조, 단 구조 붕괴.

    되돌리지 못한 것이 아니라 **재보지도 못한** 쪽이다. 조용히 넘기면 뒤엉킨 쪽이
    '정상'으로 보고되므로, 호출자가 이 쪽을 의심 목록에 올린다.
    """


@dataclass
class _Frag:
    """한 아이템의 한 prov = 페이지 위 한 조각."""

    unit: int          # 부모 children 안에서의 단위 인덱스
    prov: int          # 그 단위 안에서의 prov 인덱스 (불투명 단위는 0)
    page: int
    left: float
    right: float
    top: float
    bottom: float
    label: str
    key: tuple = field(default=(), compare=False)
    alt: tuple = field(default=(), compare=False)    # 그리드 행을 더 잘게 본 대안 키(위반 교차확인용)
    sep: bool = field(default=False, compare=False)  # 단을 가로지르는 전폭 블록
    cell: bool = field(default=False, compare=False)  # 단 폭을 채우지 못하는 좁은 조각(셀·제목)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


def _label_of(item) -> str:  # noqa: ANN001 — DocItem
    return str(getattr(item, "label", "")).replace("DocItemLabel.", "").lower()


def _top_left(bbox, height: float) -> tuple[float, float]:
    """bbox를 좌상단 원점 (top, bottom)으로. 원점이 하단인 좌표계도 같은 결과가 되게."""
    origin = str(getattr(bbox, "coord_origin", "")).lower()
    lo, hi = min(bbox.t, bbox.b), max(bbox.t, bbox.b)
    if "bottom" in origin and height:
        return height - hi, height - lo
    return lo, hi


def _descend(document, item) -> list:  # noqa: ANN001
    """단위(그룹 포함)에 딸린 모든 아이템 — 그룹은 하위 전체를 훑는다."""
    out = [item]
    for ref in getattr(item, "children", []) or []:
        try:
            out.extend(_descend(document, ref.resolve(document)))
        except Exception:  # noqa: BLE001 — 끊어진 참조는 무시
            pass
    return out


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


# --- 단(column) 구조 -------------------------------------------------------
def _columns(frags: list[_Frag], text_w: float) -> list[tuple[float, float]]:
    """'본문 폭' 조각들의 x 구간을 병합해 단 경계를 얻는다.

    본문 문단은 단을 가로로 꽉 채우므로 그 조각들만 모으면 단 구조가 그대로 드러난다.
    저자 셀처럼 짧은 조각은 단 사이 여백을 걸치고 있어 기준에서 뺀다.

    캡션·각주·그림·표(_NON_COLUMN)도 뺀다 — 본문 흐름이 아닌데도 단 사이 여백을 가로지르는
    일이 잦아(가운데 정렬된 "Figure 2: …" 한 줄이면 충분하다) 좌우 단을 하나로 붙여 버린다.
    """
    spans = sorted((f.left, f.right) for f in frags
                   if f.label not in _NON_COLUMN
                   and _WIDE_MIN * text_w <= f.width < _FULL_WIDTH * text_w)
    merged: list[list[float]] = []
    for lo, hi in spans:
        if merged and lo < merged[-1][1] - _COL_MERGE_TOL:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _is_full_width(f: _Frag, cols: list[tuple[float, float]], text_w: float) -> bool:
    if f.width >= _FULL_WIDTH * text_w:
        return True
    covered = sum(1 for lo, hi in cols
                  if min(f.right, hi) - max(f.left, lo) >= _COL_COVER * (hi - lo))
    return covered >= 2


def _column_of(f: _Frag, cols: list[tuple[float, float]]) -> int:
    """가장 많이 겹치는 단. 어느 단과도 안 겹치면 왼쪽 경계로 가장 가까운 단."""
    best, best_ov = 0, 0.0
    for i, (lo, hi) in enumerate(cols):
        ov = min(f.right, hi) - max(f.left, lo)
        if ov > best_ov:
            best, best_ov = i, ov
    if best_ov > 0:
        return best
    return min(range(len(cols)), key=lambda i: abs(f.cx - (cols[i][0] + cols[i][1]) / 2))


# --- 저자 그리드 -----------------------------------------------------------
def _center_clusters(frags: list[_Frag]) -> list[list[_Frag]]:
    """x 중심이 _CENTER_TOL 안에서 같은 조각끼리 묶는다 (가운데 정렬된 그리드 열)."""
    clusters: list[list[_Frag]] = []
    for f in sorted(frags, key=lambda f: f.cx):
        if clusters and f.cx - clusters[-1][-1].cx <= _CENTER_TOL:
            clusters[-1].append(f)
        else:
            clusters.append([f])
    return clusters


def _rows(frags: list[_Frag], gap: float) -> list[list[_Frag]]:
    """세로로 이어지는(간격 ≤ gap) 조각들을 한 행으로 묶는다."""
    rows: list[list[_Frag]] = []
    bottom = None
    for f in sorted(frags, key=lambda f: (f.top, f.left)):
        if rows and f.top - bottom <= gap:
            rows[-1].append(f)
            bottom = max(bottom, f.bottom)
        else:
            rows.append([f])
            bottom = f.bottom
    return rows


def _lattices(centers: list[float]) -> list[list[int]]:
    """등간격 격자 위에 놓인 열 중심들의 인덱스 — 저자 그리드의 가장 확실한 지문.

    마지막 줄이 가운데로 몰려 반 칸 어긋나도 중심들은 여전히 같은 간격으로 늘어선다.
    저자 그리드는 본문 블록과 한 띠를 나눠 쓰는 일이 많으므로(전폭 그림이 없는 첫 페이지)
    전체가 아니라 **부분집합**에서 격자를 찾고, 격자 칸이 빈 곳 없이 이어질 때만 인정한다.

    후보를 **점 수가 많은 순으로 전부** 돌려준다. 예전에는 가장 긴 것 하나만 돌려줬는데,
    한 쪽에 격자가 여럿 보일 때 가짜가 진짜를 가렸다. 실측(CoAuthor CHI'22): 왼쪽 단의 짧은
    왼쪽정렬 헤딩들(ABSTRACT·CCS CONCEPTS·ACMReference Format:·ACM ISBN …)이 길이가 조금씩
    달라 중심이 ~10.7pt 등간격으로 늘어서면서 4점짜리 가짜 격자를 만들었고, 진짜 저자 격자
    (142.3 / 306.5 / 470.6, 간격 164.2)는 3점이라 밀려 **한 번도 시도되지 않았다**.
    하류 가드가 가짜를 정확히 걷어냈지만 그때는 이미 `_grid`가 None을 반환한 뒤였다.
    """
    seen: set[tuple[int, ...]] = set()
    out: list[list[int]] = []
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            step = centers[j] - centers[i]
            if step <= 0:
                continue
            tol = max(_CENTER_TOL, _LATTICE_TOL * step)
            slots: dict[int, int] = {}
            for k, cx in enumerate(centers):
                pos = (cx - centers[i]) / step
                slot = round(pos)
                if abs(pos - slot) * step <= tol and slot not in slots:
                    slots[slot] = k
            keys = sorted(slots)
            if keys != list(range(keys[0], keys[0] + len(keys))):
                continue  # 중간에 빈 칸이 있으면 격자로 보지 않는다
            hit = [slots[s] for s in keys]
            if len(hit) >= _GRID_MIN_COLS and tuple(hit) not in seen:
                seen.add(tuple(hit))
                out.append(hit)
    # 점이 많은 것부터. 동점은 인덱스 순으로 — 순서가 실행마다 흔들리지 않게.
    return sorted(out, key=lambda h: (-len(h), h))


def _grid(frags: list[_Frag], cols: list[tuple[float, float]], line_h: float) -> tuple | None:
    """가운데 정렬된 k열 그리드(저자 블록)를 찾는다.

    반환: (조각집합, 열클러스터중심, 행목록, **세로로 겹치는 것만 묶은** 행목록).

    본문 단과 어긋난 격자여야 한다 — 한 본문 단 안에 그리드 열이 둘 이상 들어가거나, 그리드
    열이 단 사이 여백을 걸치고 있을 때. 본문 단과 그대로 겹치는 배치는 단 순서로 충분하다.

    행 묶음을 두 가지로 낸다. `rows`는 간격 허용치(line_h·_ROW_GAP)로 묶은 것 — 한 셀이
    이름·소속·이메일 여러 아이템으로 쪼개 나오는 조판에서 그 조각들을 한 행으로 되붙이려면
    필요하다. 하지만 line_h는 그 쪽 조각 높이에서 유도한 값이라, 셀 하나가 여러 줄짜리 한
    아이템으로 나오면 실제 줄 간격보다 훨씬 커지고 **이웃한 두 행이 통째로 붙는다**.
    그래서 세로로 겹치는 것만 묶은 `fine`도 함께 내서, 위반 판정에서 둘을 교차확인한다.
    """
    col_w = sorted(hi - lo for lo, hi in cols)[len(cols) // 2]
    cands = [f for f in frags if f.width < _GRID_MAX_W * col_w]
    if len(cands) < _GRID_MIN_FRAGS:
        return None
    clusters = sorted(_center_clusters(cands), key=lambda c: c[0].cx)
    if len(clusters) < _GRID_MIN_COLS:
        return None
    all_centers = [sum(f.cx for f in c) / len(c) for c in clusters]
    others = [f for f in frags if f.width >= _GRID_MAX_W * col_w]
    # 격자 후보를 점 수가 많은 순으로 **차례로** 시도한다. 아래 가드들은 가짜 격자를 정확히
    # 걷어내지만, 예전처럼 후보를 하나만 받으면 그 하나가 가짜일 때 진짜가 가려졌다.
    for hit in _lattices(all_centers):
        got = _grid_on(hit, all_centers, clusters, cols, others, line_h)
        if got is not None:
            return got
    return None


def _grid_on(hit: list[int], all_centers: list[float], clusters: list[list[_Frag]],
             cols: list[tuple[float, float]], others: list[_Frag], line_h: float) -> tuple | None:
    """격자 후보 하나를 검증한다. 통과하면 `_grid`의 반환 튜플, 아니면 None."""
    centers = [all_centers[k] for k in hit]
    # 본문 단과 어긋났는지 — 한 단 안에 그리드 열이 둘 이상 들어가거나, 단 사이 여백을 걸친 열이 있을 때.
    # 본문 단과 1:1로 겹치는 배치라면 단 순서만으로 충분하므로 그리드로 다루지 않는다.
    homes = [next((i for i, (lo, hi) in enumerate(cols) if lo <= cx <= hi), None) for cx in centers]
    if all(h is not None for h in homes) and len(set(homes)) == len(homes):
        return None
    # 격자가 통째로 **한 본문 단 안에** 들어 있으면 저자 그리드가 아니다. 왼쪽 정렬된 짧은
    # 제목들(부록의 'B.1 …', 'Your Task:' 등)은 길이가 제각각이라 중심이 등간격으로 늘어서기
    # 쉬운데, 그것을 그리드로 받으면 그 쪽 전체가 행 우선으로 뒤섞인다. 저자 그리드는 정의상
    # 본문 단 경계를 가로지른다(그래서 단 순서로는 못 읽는다) — 그 성질을 요구한다.
    if len(set(homes)) == 1 and homes[0] is not None:
        return None
    # 그리드가 차지한 세로 구간에는 본문 폭 블록이 없어야 한다 — 짧은 섹션 제목·낱개 조각이
    # 우연히 격자에 얹혀 본문 문단까지 그리드로 빨아들이는 것을 막는다.
    grouped = [f for k in hit for f in clusters[k]]
    rows = _clean_run(_rows(grouped, line_h * _ROW_GAP), others)
    if len(rows) < 2:
        # 셀 하나가 여러 줄짜리 한 아이템으로 나오면 line_h(그 쪽 조각 높이의 20퍼센타일)가
        # 실제 행 간격보다 커져 이웃한 두 행이 통째로 붙는다. 실측(CoAuthor CHI'22): 이름 행과
        # 이메일/소속 행 사이가 2.8pt인데 line_h·_ROW_GAP은 3.03pt라 6개 셀이 한 행이 됐고,
        # '행이 둘 미만'으로 저자 격자 전체를 놓쳤다. 그때는 세로로 겹치는 것만 묶어 다시 본다.
        rows = _clean_run(_rows(grouped, 0.0), others)
    if len(rows) < 2:
        return None
    members = [f for row in rows for f in row]
    if len(members) < _GRID_MIN_FRAGS:
        return None
    ranks = {min(range(len(centers)), key=lambda i: abs(f.cx - centers[i])) for f in members}
    if len(ranks) < _GRID_MIN_COLS:
        return None
    # 행은 '나란히 놓인 셀들'이어야 한다. 한 행에 하나씩만 있는 배치는 그리드가 아니라 그냥
    # 세로로 늘어선 목록이고, 그것을 행 우선으로 읽으면 멀쩡한 순서를 뒤섞는다.
    if sum(1 for row in rows if len(row) >= 2) < 2:
        return None
    # 그리드가 차지한 세로 구간 **전체**에 본문 폭 블록이 없어야 한다. 행 단위 검사(_clean_run)만
    # 하면 문단 사이 빈틈에 얹힌 조각들로 구간이 쪽 전체까지 벌어지고, _assign_keys가 그 구간 안의
    # 모든 조각을 셀로 빨아들여 본문이 통째로 재배치된다.
    top, bottom = min(f.top for f in members), max(f.bottom for f in members)
    if any(o.top < bottom and top < o.bottom for o in others):
        return None
    return members, centers, rows, _rows(members, 0.0)


def _clean_run(rows: list[list[_Frag]], others: list[_Frag]) -> list[list[_Frag]]:
    """본문 폭 블록과 세로로 겹치지 않는, 가장 긴 연속 행 구간."""
    def clear(row: list[_Frag]) -> bool:
        top, bottom = min(f.top for f in row), max(f.bottom for f in row)
        return not any(o.top < bottom and top < o.bottom for o in others)

    best: list[list[_Frag]] = []
    run: list[list[_Frag]] = []
    for row in rows:
        run = run + [row] if clear(row) else []
        if len(run) > len(best):
            best = run
    return best


# --- 순서 키 ---------------------------------------------------------------
def _assign_keys(frags: list[_Frag], page: int) -> dict | None:
    """페이지 page의 정경(canonical) 순서 키를 각 조각에 심는다. 단 구조가 없으면 None."""
    own = [f for f in frags if f.page == page]
    body = [f for f in own if f.label not in _EDGE_LABELS]
    if not body:
        return None
    text_l = min(f.left for f in body)
    text_r = max(f.right for f in body)
    text_w = text_r - text_l
    if text_w <= 0:
        return None
    cols = _columns(body, text_w)
    if len(cols) < 2:
        # 단 폭짜리 조각들이 **하나의 구간으로 뭉쳐 본문 폭을 거의 다 덮었다면** 1단 조판이
        # 아니라 단 사이 여백을 가로지른 조각이 좌우 단을 붙인 것이다 — 판정 실패로 올린다.
        if cols and cols[0][1] - cols[0][0] >= _COLLAPSE_MIN * text_w:
            raise _Unjudged("column-collapse")
        return None  # 1단 조판 — 되돌릴 순서가 없다

    # 단 폭을 채우는 조각(본문 흐름)과 그러지 못하는 조각(저자 셀·짧은 제목)을 갈라 둔다.
    # 한 아이템의 prov가 이 둘에 걸치면 같은 흐름이 아니므로 되붙이지 않는다.
    col_w = sorted(hi - lo for lo, hi in cols)[len(cols) // 2]
    for f in own:
        f.cell = f.width < _GRID_MAX_W * col_w

    seps = sorted((f for f in own if f.label not in _EDGE_LABELS
                   and _is_full_width(f, cols, text_w)), key=lambda f: f.top)
    sep_band = {id(f): i + 0.5 for i, f in enumerate(seps)}
    sep_cy = [f.cy for f in seps]
    for f in seps:
        f.sep = True

    def band_of(f: _Frag) -> float:
        if id(f) in sep_band:
            return sep_band[id(f)]
        return float(sum(1 for cy in sep_cy if cy < f.cy))

    # 그리드는 띠 안에서 찾는다 — 각주·전폭 블록·러닝 헤더는 제외
    inner = [f for f in own if id(f) not in sep_band and f.label not in _SKIP_JUDGE]
    bands: dict[float, list[_Frag]] = {}
    for f in inner:
        bands.setdefault(band_of(f), []).append(f)
    # 줄 높이는 그 쪽 전체 조각에서 잡는다 — 그리드 셀 하나가 여러 줄짜리 한 조각으로 나오는
    # 조판에서는 셀 높이로 재면 행 간격까지 삼켜 버린다(모든 저자가 한 행으로 뭉친다).
    line_h = _percentile([f.height for f in body if f.height > 0], 0.2) or 1.0
    grids: dict[float, tuple] = {}
    for band, group in bands.items():
        g = _grid(group, cols, line_h)
        if g is not None:
            grids[band] = g

    for f in own:
        band = band_of(f)
        if f.label == "footnote":
            # 각주는 그 쪽 본문 뒤로 보낸다. 제자리(왼쪽 단 아래)에 두면 아이템 스트림에서
            # 왼쪽 단과 오른쪽 단 사이에 끼어, 쪽 아래 주석이 본문 한가운데 놓인 꼴이 된다.
            # (본문 export 라벨에서 각주는 빠지므로 마크다운 자체는 어느 쪽이든 같다 —
            #  다만 쪽을 넘어가는 문단을 각주가 갈라놓지 않도록 _limit_splits가 함께 막는다.)
            f.key = (0, 1, 1, 0, 0, 0, f.top, f.left)
            continue
        grid = grids.get(band)
        if grid is not None:
            members, centers, rows, fine = grid
            g_top = min(x.top for x in members)
            g_bottom = max(x.bottom for x in members)
            if g_top <= f.cy <= g_bottom:
                def nearest(groups: list[list[_Frag]], frag: _Frag = f) -> int:
                    return min(range(len(groups)),
                               key=lambda i: abs(frag.cy - sum(x.cy for x in groups[i])
                                                 / len(groups[i])))
                rank = min(range(len(centers)), key=lambda i: abs(f.cx - centers[i]))
                f.key = (0, 0, band, 1, nearest(rows), rank, f.top, f.left)
                f.alt = (band, nearest(fine), rank)
                continue
            region = 0 if f.cy < g_top else 2
        else:
            region = 0
        f.key = (0, 0, band, region, _column_of(f, cols), 0, f.top, f.left)

    # 다른 쪽 조각은 **다시 배열하지 않는다**. 그 쪽에는 그 쪽의 단 구조가 있는데 여기서
    # (쪽, top, left)로 새로 매기면 이미 옳던 이웃 쪽을 위→아래·왼→오른쪽으로 뭉개 버린다.
    # 방출 순서(unit, prov)를 그대로 지킨 채 이 쪽의 앞/뒤로 보내기만 한다.
    for f in frags:
        if f.page != page:
            f.key = (-1 if f.page < page else 1, f.unit, f.prov, 0, 0, 0, 0, 0)
    _limit_splits(frags, page)
    return {"columns": len(cols), "grid_bands": len(grids), "text_width": round(text_w, 1)}


def _limit_splits(frags: list[_Frag], page: int) -> None:
    """꼭 필요한 자리에서만 아이템을 쪼개도록 키를 다듬는다.

    쪼개는 게 정당한 경우는 하나뿐이다: **이 쪽에서 시작한** 아이템의 조각들이 이 쪽 안에서
    서로 다른 단(또는 다른 페이지)으로 갈릴 때. 그 밖의 경우는 통째로 둔다 —
    - 전폭 표·그림을 사이에 두고 이어지는 문단(플로트는 본문 흐름을 끊지 않는다),
    - 앞 쪽에서 넘어온 문단(이 쪽에서 옮길 이유가 없다),
    - 이 쪽 **맨 끝**에서 다음 쪽으로 이어지는 문단(뒤따르는 본문이 없으니 쪼갤 이유가 없다).
    쪼개면 문단 한가운데에 표나 페이지 경계가 끼어드는 손해만 난다.

    마지막 경우가 앞 경우의 대칭이다. 다음 쪽으로 넘어가는 문단은 각주(키[1]=1)와 다음 쪽
    조각들 사이에 끼어 두 동강이 난다. 다만 이 쪽에 **뒤따르는 본문이 남아 있다면** 통째로
    두는 쪽이 더 나쁘다 — 다음 쪽 첫 문단이 이 쪽 나머지보다 앞서 나오기 때문이다.
    그래서 이 쪽 본문 흐름의 마지막 자리를 차지한 아이템에만 적용한다.
    """
    groups: dict[int, list[_Frag]] = {}
    for f in frags:
        groups.setdefault(f.unit, []).append(f)

    def merge(part: list[_Frag]) -> None:
        """쪼개지 않기로 한 아이템을 **자기가 시작한 자리**에 통째로 둔다.

        기준을 '조각들 중 가장 앞선 키'로 잡으면, 전폭 표를 사이에 두고 이어지는 문단이
        표보다 앞으로 끌려 올라간다(왼쪽 단 아래에서 시작해 오른쪽 단 위로 이어지는 문단은
        뒤쪽 조각의 키가 더 작다). 아이템이 시작하는 곳은 첫 prov이므로 그것을 닻으로 쓴다.
        """
        if len(part) < 2:
            return
        base = min(part, key=lambda f: f.prov).key
        for f in part:
            f.key = (*base, f.prov)

    # 이 쪽 본문 흐름의 마지막 자리 (각주·다른 쪽 조각 제외)
    tail = max((f.key for f in frags
                if f.page == page and f.key and f.key[0] == 0 and f.key[1] == 0), default=None)

    for group in groups.values():
        here = [f for f in group if f.page == page]
        away = [f for f in group if f.page != page]
        if away and min(f.page for f in group) < page:
            merge(group)  # 앞 쪽에서 이어지는 아이템 — 이 쪽 사정으로 쪼개지 않는다
            continue
        if away and here and tail is not None and max(f.key for f in here) == tail:
            merge(group)  # 이 쪽 끝에서 다음 쪽으로 이어지는 아이템 — 역시 쪼개지 않는다
            continue
        merge(away)  # 뒤 쪽 몫끼리는 붙여 둔다
        if len({f.key[2] for f in here}) > 1:
            merge(here)  # 이 쪽 안에서 띠를 넘나들면 앞선 띠에 함께 둔다
    for f in frags:  # 키 길이를 맞춘다 (튜플 비교가 길이에 걸리지 않게)
        if len(f.key) == 8:
            f.key = (*f.key, 0)


def _violation(frags: list[_Frag], page: int, text_w: float) -> list[str]:
    """docling이 실제로 낸 순서가 정경 순서를 어겼는지. 어긴 종류 목록을 돌려준다.

    - column: **본문 폭** 조각들이 오른쪽 단을 먼저 훑고 왼쪽 단으로 되돌아왔다.
      짧은 조각((a)/(b) 부그림 라벨 등)은 판정에서 뺀다 — 본문이 이미 L→R인 쪽은 손대지 않는다.
      전폭 블록(전폭 그림 등)도 뺀다 — 앞 쪽에서 이어지는 문단을 그림보다 먼저 내보내는 것은
      docling의 정당한 선택이라 위반으로 볼 수 없다.
    - grid: 저자 그리드를 행 우선이 아니라 열 우선으로 훑었다. 행 묶음이 갈릴 수 있는
      배치(셀 하나가 여러 줄짜리 한 아이템)에서는 간격으로 묶은 행과 겹침으로만 묶은 행이
      **둘 다** 위반이라고 할 때만 인정한다 — 한쪽만 위반이면 이미 옳은 쪽을 뒤집게 된다.
    """
    def sequence(pick, want) -> list:  # noqa: ANN001 — 단위별 최소 키를 방출 순서대로
        best: dict[int, tuple] = {}
        for f in frags:
            if f.page != page or not f.key or f.key[0] != 0 or not want(f):
                continue
            k = pick(f)
            if f.unit not in best or k < best[f.unit]:
                best[f.unit] = k
        return [best[u] for u in sorted(best)]

    # 한 아이템이 여러 조각으로 앞질러 나가는 것(단 이어짐)은 위반이 아니므로 **단위별 최소 키**로 본다.
    kinds: list[str] = []
    wide = sequence(lambda f: f.key[2:5],
                    lambda f: (f.label not in _SKIP_JUDGE and f.label not in _FLOAT_LABELS
                               and not f.sep and f.key[3] != 1
                               and f.width >= _WIDE_MIN * text_w))
    if any(a > b for a, b in zip(wide, wide[1:])):
        kinds.append("column")

    def broken(pick) -> bool:  # noqa: ANN001
        seq = sequence(pick, lambda f: f.key[3] == 1)
        return any(a > b for a, b in zip(seq, seq[1:]))

    if broken(lambda f: (f.key[2], f.key[4], f.key[5])) and broken(lambda f: f.alt):
        kinds.append("grid")
    return kinds


# --- 문서 반영 -------------------------------------------------------------
def _spans_ok(item) -> bool:  # noqa: ANN001
    """prov charspan이 텍스트를 순서대로 덮고 있는지 — 아니면 쪼개지 않는다."""
    text = getattr(item, "text", None)
    if not isinstance(text, str):
        return False
    prev = 0
    for pv in item.prov:
        cs = getattr(pv, "charspan", None)
        if not cs or len(cs) != 2 or not (prev <= cs[0] <= cs[1] <= len(text)):
            return False
        prev = cs[1]
    return True


def _piece_text(item, lo: int, hi: int) -> tuple[str, list]:
    """prov 인덱스 [lo, hi] 구간의 텍스트 조각과 charspan을 다시 매긴 prov 목록."""
    spans = [tuple(pv.charspan) for pv in item.prov]
    start = 0 if lo == 0 else spans[lo][0]
    end = len(item.text) if hi == len(spans) - 1 else spans[hi][1]
    provs = [pv.model_copy(update={"charspan": (max(0, spans[i][0] - start),
                                               max(0, spans[i][1] - start))})
             for i, pv in enumerate(item.prov) if lo <= i <= hi]
    return item.text[start:end], provs


def _alnum(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


def _unit_frags(document, parent, page: int) -> tuple | None:  # noqa: ANN001
    """부모 children 중 page를 건드리는 연속 구간과 그 조각들.

    반환: (구간 시작, 구간 끝, 조각 목록, 단위 아이템 목록, 쪼갤 수 있는 단위 인덱스 집합).
    그룹·그림·표는 불투명 단위로 보고 첫 prov 위치 하나만 쓴다(쪼개지 않는다).

    이 쪽을 건드리는 아이템이 아예 없으면 None. 있는데 기하를 못 세우면 _Unjudged —
    '순서가 옳다'가 아니라 '재보지 못했다'이므로 호출자가 의심 목록에 올린다.
    """
    items = []
    for ref in parent.children:
        try:
            items.append(ref.resolve(document))
        except Exception as e:  # noqa: BLE001
            raise _Unjudged("broken-ref") from e
    hits = [i for i, it in enumerate(items)
            if any(int(pv.page_no) == page
                   for d in _descend(document, it) for pv in (getattr(d, "prov", None) or []))]
    if not hits:
        return None
    start, end = min(hits), max(hits)

    frags: list[_Frag] = []
    splittable: set[int] = set()
    for idx in range(start, end + 1):
        it = items[idx]
        own = getattr(it, "prov", None) or []
        can_split = (len(own) > 1 and not (getattr(it, "children", None) or [])
                     and _spans_ok(it))
        if can_split:
            splittable.add(idx)
            provs, label = own, _label_of(it)
        else:
            # 불투명 단위(그룹·그림·표)는 첫 prov 하나로 자리를 잡고, 라벨도 그 prov를 가진
            # 아이템에서 가져온다 — 각주만 담은 그룹이 본문 한가운데로 끼지 않게.
            found = _all_provs(document, it)[:1]
            provs = [pv for _, pv in found]
            label = _label_of(found[0][0]) if found else _label_of(it)
        if not provs:
            raise _Unjudged("no-prov")  # 좌표 없는 단위가 끼면 기하로 자리를 못 정한다
        for j, pv in enumerate(provs):
            pg = int(pv.page_no)
            size = getattr(getattr(document, "pages", {}).get(pg, None), "size", None)
            height = float(getattr(size, "height", 0) or 0)
            top, bottom = _top_left(pv.bbox, height)
            frags.append(_Frag(unit=idx, prov=j if can_split else 0, page=pg,
                               left=min(pv.bbox.l, pv.bbox.r), right=max(pv.bbox.l, pv.bbox.r),
                               top=top, bottom=bottom, label=label))
    return start, end, frags, items, splittable


def _all_provs(document, item) -> list:  # noqa: ANN001
    """단위에 딸린 (아이템, prov) 쌍 전부 — 그룹은 하위까지 훑는다."""
    pairs = []
    for d in _descend(document, item):
        pairs.extend((d, pv) for pv in (getattr(d, "prov", None) or []))
    return pairs


def _repair_page(document, parent, page: int) -> dict | None:  # noqa: ANN001,C901
    got = _unit_frags(document, parent, page)
    if got is None:
        return None
    start, end, frags, items, splittable = got
    info = _assign_keys(frags, page)
    if info is None:
        return None
    own = [f for f in frags if f.page == page and f.label not in _EDGE_LABELS]
    text_w = max(f.right for f in own) - min(f.left for f in own)
    kinds = _violation(frags, page, text_w)
    if not kinds:
        return None

    order = sorted(frags, key=lambda f: f.key)
    # 새 순서에서 이어붙는 같은 단위의 조각들은 한 아이템으로 유지 (원문 보존).
    # 단, **같은 흐름 구간**일 때만이다 — 띠·영역(저자 그리드/본문)·단이 다르면 새 순서에서
    # 우연히 이웃이 되었을 뿐이므로 되붙이지 않는다. 되붙이면 저자 셀과 그 아래 본문 문단이
    # 한 블록이 되어(둘은 원래 한 아이템의 다른 prov다) 저자 줄이 본문에 묻힌다.
    pieces: list[tuple[int, int, int]] = []  # (unit, prov_lo, prov_hi)
    prev: _Frag | None = None
    for f in order:
        same_flow = (prev is not None and prev.key[1:6] == f.key[1:6]
                     and not (prev.page == page and f.page == page and prev.cell != f.cell))
        if (pieces and prev is not None and pieces[-1][0] == f.unit
                and pieces[-1][2] == f.prov - 1 and same_flow):
            pieces[-1] = (f.unit, pieces[-1][1], f.prov)
        else:
            pieces.append((f.unit, f.prov, f.prov))
        prev = f
    unrepaired = {"violations": kinds, "columns": info["columns"],
                  "grid_bands": info["grid_bands"], "split": 0, "repaired": False}
    if [p[0] for p in pieces] == [i for i in range(start, end + 1)] and all(
            p[1] == 0 for p in pieces):
        # 위반은 쟀는데 정경 순서가 방출 순서와 같다 — 되돌릴 게 없다. 고쳐지지 않았다고 보고한다.
        return unrepaired

    from docling_core.types.doc.document import RefItem

    before = sorted(_alnum("".join(getattr(d, "text", "") or ""
                                   for i in range(start, end + 1)
                                   for d in _descend(document, items[i]))))
    saved_children = list(parent.children)
    saved_items = {i: (items[i].text, getattr(items[i], "orig", None), list(items[i].prov))
                   for i in splittable}
    n_texts = len(document.texts)
    try:
        # 아이템을 건드리기 전에 모든 조각의 텍스트·prov를 미리 뜬다 (원본 상태 기준)
        cut = {(unit, lo, hi): _piece_text(items[unit], lo, hi)
               for unit, lo, hi in pieces
               if unit in splittable and not (lo == 0 and hi == len(items[unit].prov) - 1)}
        refs: list = []
        used: set[int] = set()
        for unit, lo, hi in pieces:
            it = items[unit]
            if (unit, lo, hi) not in cut:
                refs.append(RefItem(cref=it.self_ref))
                used.add(unit)
                continue
            text, new_provs = cut[(unit, lo, hi)]
            if unit not in used:  # 첫 조각은 원본 아이템을 재사용 (참조 번호 유지)
                it.text = text
                if getattr(it, "orig", None) is not None:
                    it.orig = text
                it.prov = new_provs
                used.add(unit)
                refs.append(RefItem(cref=it.self_ref))
            else:
                piece = it.model_copy(deep=True)
                piece.self_ref = f"#/texts/{len(document.texts)}"
                piece.text = text
                if getattr(piece, "orig", None) is not None:
                    piece.orig = text
                piece.prov = new_provs
                piece.children = []
                document.texts.append(piece)
                refs.append(RefItem(cref=piece.self_ref))
        parent.children[start:end + 1] = refs
        after = sorted(_alnum("".join(getattr(d, "text", "") or ""
                                      for ref in refs
                                      for d in _descend(document, ref.resolve(document)))))
        if after != before:
            raise ValueError("reading-order 복구 중 텍스트가 달라짐")
    except Exception:  # noqa: BLE001 — 어떤 이유로든 실패하면 그 쪽을 통째로 원복
        parent.children[:] = saved_children
        for i, (text, orig, provs) in saved_items.items():
            items[i].text = text
            if orig is not None:
                items[i].orig = orig
            items[i].prov = provs
        del document.texts[n_texts:]
        return unrepaired
    return {"violations": kinds, "columns": info["columns"],
            "grid_bands": info["grid_bands"], "split": len(refs) - (end - start + 1),
            "repaired": True}


def _unjudged(kind: str) -> dict:
    """판정 자체를 못 한 쪽의 보고 — 되돌린 적이 없으니 의심 목록으로 간다."""
    return {"violations": [kind], "columns": 0, "grid_bands": 0, "split": 0, "repaired": False}


def repair_reading_order(document) -> dict:  # noqa: ANN001 — DoclingDocument
    """읽기 순서가 어긋난 쪽을 되돌린다. 반환: meta 조각(고친 쪽 / 못 고친 쪽).

    위반이 증명된 쪽만 건드리므로, 정상 논문의 출력은 종전과 바이트 단위로 같다.

    `reading_order_suspect`는 **되돌리지 못한** 쪽이다. 위반을 쟀는데 원복된 쪽뿐 아니라,
    애초에 **재보지 못한** 쪽(좌표 없는 단위, 끊어진 참조, 단 구조 붕괴, 예기치 못한 예외)도
    싣는다 — 판정 실패는 '정상'이 아니다. 그 쪽만 마크다운 스트림이 여전히 뒤엉켜 있을 수
    있으므로, front_matter의 "본문 뒤 블록 끌어올리기" 복구는 오직 이 목록이 비어 있지 않을
    때만 켠다 — 나머지 논문에서는 통째로 no-op이 된다.
    """
    parent = getattr(document, "body", None)
    if parent is None or not getattr(parent, "children", None):
        return {"reordered_pages": [], "reading_order_suspect": []}
    pages = sorted(int(p) for p in getattr(document, "pages", {}))
    seen: dict[int, dict] = {}
    for page in pages:
        try:
            got = _repair_page(document, parent, page)
        except _Unjudged as e:  # 이 쪽은 기하로 판정할 수 없었다
            got = _unjudged(str(e))
        except Exception:  # noqa: BLE001 — 한 쪽의 실패가 추출 전체를 막지 않게
            got = _unjudged("error")
        if got:
            seen[page] = got
    return {
        "reordered_pages": sorted(p for p, v in seen.items() if v["repaired"]),
        "reading_order_suspect": sorted(p for p, v in seen.items() if not v["repaired"]),
        "reading_order": seen,
    }


# --- export 순서 기하 (문단 재결합용) ---------------------------------------
@dataclass
class PageGeom:
    """한 쪽의 본문 띠 — 단 수, 위/아래 경계, 줄 높이, 그리고 **단별** 흐름 구간.

    col_top/col_bottom은 본문 흐름에 참여하는 조각(각주·캡션·러닝 헤더·전폭 블록 제외)만으로
    잰다. 쪽 전체 띠로 재면 첫 페이지에서 아래쪽 각주 뭉치가 '단의 끝'을 아래로 끌어내려,
    단 경계에서 끊긴 초록을 다시 잇지 못한다.
    """

    n_cols: int
    band_top: float
    band_bottom: float
    line_h: float
    col_top: dict[int, float] = field(default_factory=dict)
    col_bottom: dict[int, float] = field(default_factory=dict)


@dataclass
class ItemGeom:
    """export 스트림의 한 아이템 — 시작/끝 조각의 쪽·단과 라벨."""

    text: str
    label: str
    page_start: int
    col_start: int
    top: float
    page_end: int
    col_end: int
    bottom: float
    full_width: bool


@dataclass
class ExportGeometry:
    """save_as_markdown이 내보낼 순서 그대로의 아이템 기하."""

    items: list[ItemGeom]
    pages: dict[int, PageGeom]


def _stream_items(document, parent) -> list:  # noqa: ANN001
    """body children을 방출 순서대로 훑어 prov가 있는 잎 아이템만 모은다."""
    out = []
    for ref in getattr(parent, "children", []) or []:
        try:
            item = ref.resolve(document)
        except Exception:  # noqa: BLE001 — 끊어진 참조는 건너뛴다
            continue
        for d in _descend(document, item):
            if getattr(d, "prov", None):
                out.append(d)
    return out


def export_geometry(document) -> ExportGeometry | None:  # noqa: ANN001 — DoclingDocument
    """방출 순서대로의 아이템 기하 — 문단 재결합이 "정말 이어지는 자리인지" 볼 근거.

    좌표가 없으면 None. 이 값이 없으면 재결합은 아무것도 하지 않는다(fail closed).
    """
    parent = getattr(document, "body", None)
    if parent is None:
        return None
    items = _stream_items(document, parent)
    if not items:
        return None

    frags: list[_Frag] = []
    for idx, it in enumerate(items):
        label = _label_of(it)
        for j, pv in enumerate(it.prov):
            pg = int(pv.page_no)
            size = getattr(getattr(document, "pages", {}).get(pg, None), "size", None)
            height = float(getattr(size, "height", 0) or 0)
            top, bottom = _top_left(pv.bbox, height)
            frags.append(_Frag(unit=idx, prov=j, page=pg,
                               left=min(pv.bbox.l, pv.bbox.r), right=max(pv.bbox.l, pv.bbox.r),
                               top=top, bottom=bottom, label=label))
    if not frags:
        return None

    pages: dict[int, PageGeom] = {}
    cols_by_page: dict[int, list[tuple[float, float]]] = {}
    widths: dict[int, float] = {}
    for pg in {f.page for f in frags}:
        body = [f for f in frags if f.page == pg and f.label not in _EDGE_LABELS]
        if not body:
            continue
        text_w = max(f.right for f in body) - min(f.left for f in body)
        if text_w <= 0:
            continue
        cols = _columns(body, text_w) or [(min(f.left for f in body), max(f.right for f in body))]
        cols_by_page[pg], widths[pg] = cols, text_w
        pages[pg] = PageGeom(n_cols=len(cols),
                             band_top=min(f.top for f in body),
                             band_bottom=max(f.bottom for f in body),
                             line_h=_percentile([f.height for f in body if f.height > 0], 0.2) or 1.0)

    by_unit: dict[int, list[_Frag]] = {}
    for f in frags:
        by_unit.setdefault(f.unit, []).append(f)

    def place(f: _Frag) -> tuple[int, bool]:
        cols = cols_by_page.get(f.page)
        if not cols:
            return 0, True
        # '단을 가로지른다'는 판정은 단이 둘 이상일 때만 뜻이 있다. 1단 조판에서는 본문 문단이
        # 모두 지면 폭을 채우므로, 전폭으로 보면 그 쪽에서는 아무것도 이어붙일 수 없게 된다.
        return _column_of(f, cols), len(cols) >= 2 and _is_full_width(f, cols, widths[f.page])

    # 단별 흐름 구간 — 본문 흐름에 참여하지 않는 조각(각주·캡션·가장자리·전폭)은 빼고 잰다.
    for f in frags:
        pg = pages.get(f.page)
        if pg is None or f.label in _SKIP_JUDGE:
            continue
        col, full = place(f)
        if full:
            continue
        pg.col_top[col] = min(pg.col_top.get(col, f.top), f.top)
        pg.col_bottom[col] = max(pg.col_bottom.get(col, f.bottom), f.bottom)

    geoms: list[ItemGeom] = []
    for idx, it in enumerate(items):
        part = by_unit.get(idx)
        if not part:
            continue
        head, tail = part[0], part[-1]
        c0, w0 = place(head)
        c1, w1 = place(tail)
        geoms.append(ItemGeom(
            text=getattr(it, "text", "") or "", label=_label_of(it),
            page_start=head.page, col_start=c0, top=head.top,
            page_end=tail.page, col_end=c1, bottom=tail.bottom,
            full_width=w0 or w1))
    return ExportGeometry(items=geoms, pages=pages)
