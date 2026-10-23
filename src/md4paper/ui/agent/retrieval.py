"""에이전트 tool용 논문 검색 — 정렬 행 청크 + BM25, 논문별 메모리 캐시.

`ui/chat.py`의 검색 부분을 옮겨 왔다. 검색 단위는 여전히 **뷰어의 정렬 행**이다
(`app.align_rows`의 인덱스 = DOM의 `data-row`) — 그래야 답의 인용 칩이 그 문단으로 곧장 간다.

chat.py와 다른 점 두 가지:
- **논문별 캐시.** 에이전트는 한 턴에 여러 tool(search/read_rows/outline…)로 같은 논문을 여러 번
  본다. `(경로, en.md·ko.md·annotations.json의 mtime)`이 같으면 청크와 색인을 다시 만들지 않는다.
  메모를 새로 그으면 annotations.json mtime이 바뀌어 저절로 무효화된다.
- **질문 확장(LLM) 없음.** 검색어를 펼치는 일은 에이전트 모델이 query를 고쳐 다시 부르는 것으로
  대신한다 — tool 안에서 LLM을 또 부르면 비용·지연이 두 겹이 된다.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from md4paper.ui import annotations
from md4paper.workdir import WorkDir

TOP_K = 8          # 검색 기본 결과 수
CACHE_PAPERS = 16  # 메모리에 들고 있을 논문 수 (가장 오래 안 쓴 것부터 버린다)


# --- 청크 ---------------------------------------------------------------


@dataclass
class Chunk:
    """뷰어 정렬 행 하나. row는 절대 재번호하지 않는다 (DOM data-row와 맞아야 한다)."""

    row: int
    heading: str          # 가장 가까운 앞선 헤더 텍스트 (없으면 "")
    en: str
    ko: str | None
    searchable: bool = True   # 헤더만 있는 행·빈 행은 검색에서 제외
    notes: list[dict] = field(default_factory=list)   # 이 행에 걸린 독자 메모 (attach_notes)
    head_level: int = 0   # 이 행이 섹션 첫 행이면 그 헤더 레벨(1~6), 아니면 0 — outline용
    figures: list[str] = field(default_factory=list)   # 이 행 그림의 설명 "Figure 3: …" (attach_figures)


_HEAD_LINE_RE = re.compile(r"^\s*#{1,6}\s+.*$", re.MULTILINE)


def _body_of(block: str) -> str:
    """블록에서 헤더 줄을 뺀 본문 (헤더만 있는 행을 판별하는 데 쓴다)."""
    return _HEAD_LINE_RE.sub("", block).strip()


def build_chunks(en_md: str, ko_md: str | None = None) -> list[Chunk]:
    """원문(+번역)을 뷰어와 똑같은 정렬 행으로 쪼갠다.

    번역이 없거나 섹션 수가 어긋나면 뷰어와 같은 폴백(섹션 단위 EN 행)을 쓴다.
    """
    from md4paper.ui.app import _heading_of, _split_sections, align_rows

    rows = align_rows(en_md, ko_md) if ko_md else None
    if rows is None:
        rows = [(s, None, _heading_of(s)) for s in _split_sections(en_md)]
    chunks: list[Chunk] = []
    head = ""
    for k, (enb, kob, hd) in enumerate(rows):
        if hd:
            head = hd[1]
        chunks.append(Chunk(row=k, heading=head, en=enb, ko=kob,
                            searchable=bool(_body_of(enb) or _body_of(kob or "")),
                            head_level=int(hd[0]) if hd else 0))
    return chunks


# --- 독자 메모 붙이기 ---------------------------------------------------
# annotations.json의 항목은 v2 구조 — 표시 하나가 원문·번역 양쪽 앵커를 최대 둘 가진다.
# 색·메모는 표시당 하나뿐이라 청크에는 표시 하나를 메모 하나로 붙인다.

COLOR_KO = {"yellow": "노랑", "green": "초록", "blue": "파랑", "pink": "분홍", "purple": "보라"}
SIDE_KO = {"en": "원문", "ko": "번역", "both": "원문·번역"}
_PROBE = 40   # 행이 밀렸을 때 quote로 다시 찾을 때 쓰는 앞부분 길이


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _note_of(item: object) -> dict | None:
    """annotations 항목 → 청크에 붙일 메모 dict (`{id, row, side, color, quote, note}`)."""
    if not isinstance(item, dict):
        return None
    ancs = [a for a in (item.get("anchors") or []) if isinstance(a, dict)]
    aid = str(item.get("id") or "")
    if not ancs or not aid:
        return None
    en = next((a for a in ancs if a.get("side") == "en"), None)
    ko = next((a for a in ancs if a.get("side") == "ko"), None)
    main = en or ko or ancs[0]     # 원문 앵커를 대표로 (annotations의 문서 순서와 같은 기준)
    try:
        row = int(main.get("row") or 0)
    except (TypeError, ValueError):
        row = 0
    return {"id": aid, "row": row,
            "side": "both" if (en and ko) else str(main.get("side") or "en"),
            "color": str(item.get("color") or "yellow"),
            "quote": _flat(str(main.get("quote") or "")),
            "note": str(item.get("note") or "").strip()}


def _row_by_quote(chunks: list[Chunk], quote: str) -> int | None:
    """인용문이 든 행을 찾는다 (원문·번역 어느 쪽이든). 앞부분만이라도 걸리면 그 행."""
    if not quote:
        return None
    probes = [quote] + ([quote[:_PROBE]] if len(quote) > _PROBE else [])
    for probe in probes:
        for c in chunks:
            if probe in _flat(c.en) or probe in _flat(c.ko or ""):
                return c.row
    return None


def attach_notes(chunks: list[Chunk], items: object) -> list[Chunk]:
    """하이라이트·메모를 행 번호로 청크에 붙인다 (같은 chunks 목록을 그대로 돌려준다).

    행이 범위 밖이면(재조립으로 밀린 경우) 인용문으로 어느 행인지 다시 찾고, 그래도 못 찾으면
    검색에서 빠진다 — 저장된 메모 자체는 건드리지 않으니 뷰어 목록에는 그대로 남는다.
    메모가 붙은 행은 헤더만 있는 행이라도 검색 대상이 된다(메모가 곧 내용이다).
    """
    by_row = {c.row: c for c in chunks}
    for c in chunks:
        c.notes = []
    for raw in items if isinstance(items, list) else []:
        n = _note_of(raw)
        if n is None:
            continue
        target = by_row.get(n["row"])
        if target is None:
            row = _row_by_quote(chunks, n["quote"])
            if row is None:
                continue
            n["row"], target = row, by_row[row]
        target.notes.append(n)
        target.searchable = True
    return chunks


# --- 그림 설명 붙이기 ---------------------------------------------------
# 이미지 줄은 `![Figure 3](images/figure-3.png)`뿐이라 에이전트는 그림 내용을 모른다. 태그 PDF에서 읽은
# 설명(figure_alt)이 있으면 그 행에 붙여 검색·읽기에 함께 싣는다.

_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def attach_figures(chunks: list[Chunk], alts: dict[str, str]) -> list[Chunk]:
    """원문 블록의 그림 → 설명 ("라벨: 설명"). 같은 그림은 한 행에 한 번만."""
    if not alts:
        return chunks
    for c in chunks:
        for m in _IMG_REF_RE.finditer(c.en):
            name = m.group(2).split("#")[0].split("?")[0].rsplit("/", 1)[-1]
            alt = alts.get(name)
            if not alt:
                continue
            line = f"{m.group(1).strip() or name}: {' '.join(alt.split())}"
            if line not in c.figures:
                c.figures.append(line)
                c.searchable = True
    return chunks


def _read_chunks(wd: WorkDir, include_notes: bool = True) -> list[Chunk]:
    from md4paper import figure_alt

    en = wd.en_md.read_text(encoding="utf-8") if wd.en_md.exists() else ""
    ko = wd.ko_md.read_text(encoding="utf-8") if wd.ko_md.exists() else None
    chunks = build_chunks(en, ko)
    try:
        attach_figures(chunks, figure_alt.alts(wd))
    except Exception:  # noqa: BLE001 — 설명은 덤이다
        pass
    if include_notes:
        attach_notes(chunks, annotations.load(wd))
    return chunks


# --- 토크나이저 ---------------------------------------------------------

_TEX_RE = re.compile(r"\\[a-z]+", re.IGNORECASE)      # \alpha, \frac 등 LaTeX 명령
_SYM_RE = re.compile(r"[*_#`>|~$\[\]()!{}\\/,;:\"'^=+<>&%@?]")  # 마크다운·수식 기호
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-.]*")
_HANGUL_RE = re.compile(r"[가-힣]+")

# 영어 기능어 — 문서 수가 수백뿐인 한 편짜리 색인에서는 이런 흔한 말의 idf가 과대평가돼
# "how many participants" 같은 질문이 엉뚱한 문단으로 간다. 한국어는 바이그램이라 따로 안 뺀다.
_STOP = frozenset("""a about all also an and any are as at be been both but by can did do does
each for from had has have how i if in into is it its many may more most much no not of on only
or other our so some such than that the their then there these they this those to us very was we
were what when where which who why will with would you your""".split())


def _stem(w: str) -> str:
    """아주 단순한 스테밍 — 끝의 ing/ed/s만 떼서 어휘 변형을 맞춘다."""
    for suf in ("ing", "ed"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    """영문은 단어(간단 스테밍), 한글은 문자 바이그램으로. 마크다운·LaTeX 기호는 버린다.

    한국어는 형태소 분석기 없이(새 의존성 없이) 다뤄야 하므로 바이그램을 쓴다 —
    '설문조사'와 '설문'이 '설문' 바이그램으로 겹쳐 검색이 걸린다.
    """
    t = _SYM_RE.sub(" ", _TEX_RE.sub(" ", text.lower()))
    toks: list[str] = []
    for m in _WORD_RE.finditer(t):
        w = m.group(0).strip(".-")
        if w and w not in _STOP:
            toks.append(_stem(w))
    for m in _HANGUL_RE.finditer(t):
        s = m.group(0)
        if len(s) == 1:
            toks.append(s)
        else:
            toks.extend(s[i:i + 2] for i in range(len(s) - 1))
    return toks


# --- BM25 ---------------------------------------------------------------


def doc_text(chunk: Chunk, include_notes: bool = True) -> str:
    """색인에 넣을 한 행의 텍스트 — 원문 + 번역 (+ 독자 메모의 인용문·본문)."""
    parts = [chunk.en, chunk.ko or "", *chunk.figures]
    if include_notes:
        for n in chunk.notes:
            parts += [n.get("quote", ""), n.get("note", "")]
    return " ".join(p for p in parts if p)


class BM25Index:
    """청크(원문+번역+메모) 위의 BM25 색인. 순수 파이썬 — 논문 한 편이면 즉시 만들어진다."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75,
                 include_notes: bool = True) -> None:
        self.k1, self.b = k1, b
        self.rows: list[int] = []
        self.tf: list[Counter] = []
        self.length: list[int] = []
        df: Counter = Counter()
        for c in chunks:
            if not c.searchable:
                continue
            toks = tokenize(doc_text(c, include_notes))
            if not toks:
                continue
            tf = Counter(toks)
            self.rows.append(c.row)
            self.tf.append(tf)
            self.length.append(len(toks))
            df.update(tf.keys())
        n = len(self.rows)
        self.avgdl = (sum(self.length) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query_tokens: list[str], k: int = TOP_K) -> list[tuple[int, float]]:
        """(row, 점수) 목록 — 점수 높은 순, 0점은 버린다."""
        if not self.rows or not query_tokens:
            return []
        wanted = Counter(query_tokens)
        scored: list[tuple[int, float]] = []
        for i, tf in enumerate(self.tf):
            score = 0.0
            dl = self.length[i] or 1
            for t, qn in wanted.items():
                f = tf.get(t, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or dl))
                # 질문에 같은 토큰이 여러 번 나오면 살짝 더 무겁게 (바이그램 반복도 신호다)
                score += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom * (1 + 0.15 * (qn - 1))
            if score > 0:
                scored.append((self.rows[i], score))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]


# "내가 메모한 것 정리해 줘" 류 — 이런 질문은 어휘가 겹치지 않아도 메모 행을 봐야 답이 된다.
_NOTE_HINT_RE = re.compile(r"메모|코멘트|하이라이트|표시|note|comment|highlight|annotat",
                           re.IGNORECASE)


def wants_notes(question: str) -> bool:
    """질문이 독자 메모를 가리키는지 (그렇다면 메모 행을 앞쪽에 우선 넣는다)."""
    return bool(_NOTE_HINT_RE.search(question or ""))


def retrieve(chunks: list[Chunk], question: str, k: int = TOP_K, include_notes: bool = True,
             index: BM25Index | None = None) -> list[int]:
    """질문으로 관련 행 번호를 뽑는다. 결과가 비면 문서 앞부분으로 폴백.

    `index`를 주면(캐시된 색인) 다시 만들지 않는다 — 같은 chunks로 만든 것이어야 한다.
    """
    toks = tokenize(question)
    noted = [c.row for c in chunks if c.notes] if include_notes else []
    idx = index if index is not None else BM25Index(chunks, include_notes=include_notes)
    rows = [r for r, _s in idx.search(toks, k + len(noted))]
    if noted and wants_notes(question):
        # 메모 이야기를 묻는 질문 — 메모 행을 앞으로 (점수 순 먼저, 못 걸린 것은 문서 순서로)
        front = [r for r in rows if r in set(noted)]
        front += [r for r in noted if r not in front]
        rows = front + [r for r in rows if r not in set(front)]
    rows = rows[:k]
    if not rows:  # 색인이 비었거나 겹치는 어휘가 없음 → 초록 부근이라도 보여준다
        rows = [c.row for c in chunks if c.searchable][:3]
    return rows


# --- 논문별 캐시 --------------------------------------------------------


@dataclass
class Paper:
    """캐시된 논문 하나 — 청크(메모 포함)와 그 위의 색인."""

    chunks: list[Chunk]
    index: BM25Index

    def by_row(self) -> dict[int, Chunk]:
        return {c.row: c for c in self.chunks}


_cache: OrderedDict[tuple, Paper] = OrderedDict()
_cache_lock = threading.Lock()


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def cache_key(wd: WorkDir) -> tuple:
    """캐시 키 — 경로 + 원문·번역·메모·그림 설명 파일의 mtime. 하나라도 바뀌면 다른 키가 된다."""
    return (str(Path(wd.root).resolve()), _mtime(wd.en_md), _mtime(wd.ko_md),
            _mtime(wd.annotations_json), _mtime(wd.figure_alts_json))


def paper(wd: WorkDir) -> Paper:
    """논문의 청크·색인 (캐시). 파일이 바뀌었으면 다시 만들고 옛 항목은 버린다."""
    if not wd.figure_alts_json.exists():  # 뷰어에서 한 번도 안 연 옛 논문 — 그림 설명을 먼저 짝지어 둔다
        from md4paper import figure_alt

        try:
            figure_alt.ensure(wd)
        except Exception:  # noqa: BLE001 — 설명은 덤이다
            pass
    key = cache_key(wd)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    chunks = _read_chunks(wd)
    built = Paper(chunks, BM25Index(chunks))
    with _cache_lock:
        for old in [k for k in _cache if k[0] == key[0]]:   # 같은 논문의 낡은 판
            del _cache[old]
        _cache[key] = built
        while len(_cache) > CACHE_PAPERS:
            _cache.popitem(last=False)
    return built


def chunks_of(wd: WorkDir, include_notes: bool = True) -> list[Chunk]:
    """워크디렉토리의 en.md(+ko.md)로 청크를 만들고 저장된 메모를 붙인다 (메모 포함이면 캐시)."""
    if not include_notes:
        return _read_chunks(wd, include_notes=False)
    return paper(wd).chunks


def clear_cache() -> None:
    """캐시 비우기 (테스트·수동 갱신용)."""
    with _cache_lock:
        _cache.clear()
