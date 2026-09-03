"""뷰어 RAG 챗봇 백엔드 — 청크(정렬 문단) 검색 + 답변 생성 + 인용 해석 + 기록 저장.

NiceGUI와 무관한 순수 로직이라 테스트에서 그대로 부를 수 있다.

검색 단위는 **뷰어의 정렬 행**이다 (`app.align_rows`의 인덱스 = DOM의 `data-row`).
그래서 답변에 붙는 인용을 뷰어의 그 문단으로 곧바로 스크롤할 수 있다 —
별도의 청크 좌표계를 만들면 재렌더마다 어긋난다.

임베딩 없이 BM25(순수 파이썬)만 쓴다. 한 논문은 수백 문단 규모라 색인이 즉시 만들어지고,
질문 확장(LLM이 뽑아 주는 동의어 키워드)으로 어휘 불일치를 메운다.

사용자가 뷰어에서 남긴 하이라이트·메모(`annotations.json`)도 같은 행에 붙여 함께 검색한다 —
"내가 표시해 둔 데서 뭐라고 했지?"가 논문 본문 질문과 한 창에서 답된다. 다만 메모는 논문이
아니라 **읽는 사람이 쓴 글**이므로 프롬프트에서 구분해 주고 인용 표기도 따로 쓴다(`[[a…]]`).
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from html import escape

from pydantic import BaseModel, Field

from md4paper.llm.base import Provider
from md4paper.ui import annotations
from md4paper.workdir import WorkDir

VERSION = 1
MAX_TURNS = 200         # 기록 상한 (오래된 턴부터 버린다)
MAX_QUESTION = 2000     # 질문 길이 상한
TOP_K = 8               # 프롬프트에 넣을 문단 수
CHUNK_CHARS = 1800      # 프롬프트용 한 문단 길이 상한 (섹션 통째 행이 프롬프트를 삼키지 않게)
HISTORY_TURNS = 6       # 프롬프트에 실어 보낼 최근 대화 턴 수
ANSWER_TOKENS = 1500

_append_lock = threading.Lock()  # 같은 논문에 동시 요청이 와도 기록이 깨지지 않게


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
                            searchable=bool(_body_of(enb) or _body_of(kob or ""))))
    return chunks


# --- 독자 메모 붙이기 ---------------------------------------------------
# annotations.json의 항목은 v2 구조 — 표시 하나가 원문·번역 양쪽 앵커를 최대 둘 가진다.
# 색·메모는 표시당 하나뿐이라 청크에는 표시 하나를 메모 하나로 붙인다.

_COLOR_KO = {"yellow": "노랑", "green": "초록", "blue": "파랑", "pink": "분홍", "purple": "보라"}
_SIDE_KO = {"en": "원문", "ko": "번역", "both": "원문·번역"}
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


def chunks_of(wd: WorkDir, include_notes: bool = True) -> list[Chunk]:
    """워크디렉토리의 en.md(+ko.md)로 청크를 만들고 저장된 메모를 붙인다.

    메모는 뷰어에서 실시간으로 바뀌므로 질문마다 새로 읽는다 (캐시하지 않는다).
    """
    en = wd.en_md.read_text(encoding="utf-8") if wd.en_md.exists() else ""
    ko = wd.ko_md.read_text(encoding="utf-8") if wd.ko_md.exists() else None
    chunks = build_chunks(en, ko)
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
    parts = [chunk.en, chunk.ko or ""]
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


# --- 질문 확장 ----------------------------------------------------------


class QueryPlan(BaseModel):
    """질문을 검색어로 펼친 결과 (영·한 키워드)."""

    keywords_en: list[str] = Field(default_factory=list,
                                   description="English search keywords / phrases")
    keywords_ko: list[str] = Field(default_factory=list,
                                   description="Korean search keywords / phrases")


_EXPAND_SYSTEM = """너는 학술 논문 검색을 돕는다. 사용자의 질문을 논문 본문에서 찾을 만한
검색 키워드로 펼쳐라. 논문은 영어 원문이고 한국어 번역이 함께 있을 수 있다.
- keywords_en: 논문에 실제로 쓰일 영어 용어·동의어 (3~8개)
- keywords_ko: 같은 뜻의 한국어 용어 (2~6개)
질문에 없는 주제를 새로 만들지 말고, 약어는 풀어쓴 형태도 함께 넣어라."""


def expand_query(provider: Provider, question: str) -> QueryPlan | None:
    """LLM으로 검색 키워드를 펼친다. 실패하면 None — 질문 원문 토큰만으로 검색한다."""
    try:
        return provider.parse(_EXPAND_SYSTEM, f"질문: {question}", QueryPlan, max_tokens=300)
    except Exception:  # noqa: BLE001 — 확장은 부가 기능, 실패해도 답변은 해야 한다
        return None


# "내가 메모한 것 정리해 줘" 류 — 이런 질문은 어휘가 겹치지 않아도 메모 행을 봐야 답이 된다.
_NOTE_HINT_RE = re.compile(r"메모|코멘트|하이라이트|표시|note|comment|highlight|annotat",
                           re.IGNORECASE)


def wants_notes(question: str) -> bool:
    """질문이 독자 메모를 가리키는지 (그렇다면 메모 행을 앞쪽에 우선 넣는다)."""
    return bool(_NOTE_HINT_RE.search(question or ""))


def retrieve(chunks: list[Chunk], question: str, plan: QueryPlan | None = None,
             k: int = TOP_K, include_notes: bool = True) -> list[int]:
    """질문(+확장 키워드)으로 관련 행 번호를 뽑는다. 결과가 비면 문서 앞부분으로 폴백."""
    toks = tokenize(question)
    if plan is not None:
        for kw in list(plan.keywords_en) + list(plan.keywords_ko):
            toks.extend(tokenize(str(kw)))
    noted = [c.row for c in chunks if c.notes] if include_notes else []
    hits = BM25Index(chunks, include_notes=include_notes).search(toks, k + len(noted))
    rows = [r for r, _s in hits]
    if noted and wants_notes(question):
        # 메모 이야기를 묻는 질문 — 메모 행을 앞으로 (점수 순 먼저, 못 걸린 것은 문서 순서로)
        front = [r for r in rows if r in set(noted)]
        front += [r for r in noted if r not in front]
        rows = front + [r for r in rows if r not in set(front)]
    rows = rows[:k]
    if not rows:  # 색인이 비었거나 겹치는 어휘가 없음 → 초록 부근이라도 보여준다
        rows = [c.row for c in chunks if c.searchable][:3]
    return rows


# --- 프롬프트 -----------------------------------------------------------

_SYSTEM = """너는 지금 사용자가 읽고 있는 이 논문 한 편만 담당하는 도우미다.

규칙:
- **아래에 제공된 문단만** 근거로 답한다. 제공된 문단에 없는 내용은 추측하지 말고,
  "제공된 문단에서는 찾지 못했다"고 분명히 말한다(어느 섹션을 보면 좋을지 힌트는 줄 수 있다).
- **질문과 같은 언어로 답한다.** 한국어 질문이면 한국어로, 영어 질문이면 영어로.
  문단은 영어 원문(과 있으면 한국어 번역)이 함께 주어진다.
- 근거를 쓴 문장 끝마다 반드시 `[[r<번호>]]` 표기를 붙인다 (예: `주장을 검증했다 [[r12]]`).
  번호는 아래에 제공된 문단 번호만 쓴다. 각주나 `[12]` 같은 다른 형식은 절대 쓰지 않는다 —
  논문 자체의 참고문헌 인용 `[12]`와 헷갈리기 때문이다.
- 간결하게. 필요하면 짧은 목록. 마크다운을 쓸 수 있고, 수식은 `$...$`로 쓴다."""

_SYSTEM_NOTES = """
- 문단 아래에 **독자 메모**가 붙어 있을 수 있다. 이것은 논문 내용이 아니라 **이 논문을 읽는
  사람이 직접 남긴** 하이라이트·코멘트다. 답에 쓸 때는 "메모에 따르면", "표시해 둔 문장은"처럼
  논문 본문과 분명히 구분해 말하고, 근거 표기는 `[[a<메모 id>]]`를 쓴다 (예: `[[am1k2x8]]`).
  논문 문단 근거는 계속 `[[r<번호>]]`다."""


def build_system(has_notes: bool = False) -> str:
    """시스템 프롬프트 — 프롬프트에 독자 메모가 실렸을 때만 메모 규칙을 덧붙인다."""
    return _SYSTEM + (_SYSTEM_NOTES if has_notes else "")


def _clip(text: str, limit: int = CHUNK_CHARS) -> str:
    t = text.strip()
    return t if len(t) <= limit else t[:limit].rstrip() + " …"


def _outline(wd: WorkDir, limit: int = 60) -> str:
    """섹션 제목 트리 (질문이 어느 섹션 이야기인지 LLM이 감을 잡게). 없으면 빈 문자열."""
    if not wd.sections_map.exists():
        return ""
    try:
        secs = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    except (OSError, ValueError):
        return ""
    lines = []
    for e in sorted(secs, key=lambda x: x.get("out_line", 0))[:limit]:
        lvl = e.get("level", 1)
        indent = "  " * (max(1, int(lvl)) - 1) if str(lvl).isdigit() else ""
        lines.append(f"{indent}- {e.get('text', '')}")
    return "\n".join(lines)


def _title(wd: WorkDir) -> str:
    """논문 제목 — paper_meta.json이 있으면 거기서, 없으면 en.md의 첫 헤더."""
    if wd.paper_meta_json.exists():
        try:
            t = json.loads(wd.paper_meta_json.read_text(encoding="utf-8")).get("title", "")
            if t:
                return str(t)
        except (OSError, ValueError):
            pass
    if wd.en_md.exists():
        for line in wd.en_md.read_text(encoding="utf-8").splitlines()[:80]:
            m = re.match(r"^#\s+(.*)$", line)
            if m:
                return m.group(1).strip()
    return ""


_TAG_BAD_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def note_tag(note_id: str) -> str:
    """메모 id → 프롬프트·인용 마커에 쓸 태그. 항상 `a`로 시작하고 마커 문법을 깨지 않는다."""
    s = _TAG_BAD_RE.sub("", str(note_id))[:64]
    return s if s[:1] in ("a", "A") else "a" + s


def note_line(n: dict) -> str:
    """프롬프트에 넣을 독자 메모 한 줄."""
    where = _SIDE_KO.get(n.get("side", "en"), "원문")
    color = _COLOR_KO.get(n.get("color", ""), n.get("color", ""))
    body = " ".join(n.get("note", "").split()) or "(메모 없이 하이라이트만)"
    return (f'독자 메모 [{note_tag(n["id"])}] ({where}에 표시, {color}): '
            f'"{_clip(n.get("quote", ""), 300)}" — {body}')


def build_user_prompt(wd: WorkDir, chunks: list[Chunk], rows: list[int], question: str,
                      history: list[dict] | None = None, include_notes: bool = True) -> str:
    """제목·섹션 개요 + 최근 대화 + 검색된 문단(+그 행의 독자 메모) + 질문."""
    by_row = {c.row: c for c in chunks}
    parts: list[str] = []
    title = _title(wd)
    if title:
        parts.append(f"# 논문: {title}")
    outline = _outline(wd)
    if outline:
        parts.append("## 섹션 개요\n" + outline)
    recent = (history or [])[-HISTORY_TURNS:]
    if recent:
        convo = []
        for t in recent:
            convo.append(f"사용자: {_clip(str(t.get('question', '')), 400)}")
            convo.append(f"도우미: {_clip(str(t.get('answer_md', '')), 600)}")
        parts.append("## 최근 대화\n" + "\n".join(convo))
    blocks = []
    for r in rows:
        c = by_row.get(r)
        if c is None:
            continue
        head = f" ({c.heading})" if c.heading else ""
        block = f"[r{r}]{head}\nEN: {_clip(c.en)}"
        if c.ko:
            block += f"\nKO: {_clip(c.ko)}"
        if include_notes:
            for n in c.notes:
                block += "\n" + note_line(n)
        blocks.append(block)
    parts.append("## 제공된 문단 (이것만 근거로 쓴다)\n" + "\n\n".join(blocks))
    parts.append(f"## 질문\n{question}")
    return "\n\n".join(parts)


# --- 인용 해석 · 렌더 ---------------------------------------------------

#   `[[r12]]` — 논문 문단(행 번호) / `[[a1k2x8]]` — 독자 메모(annotations의 id, 'a'로 시작)
_MARKER_RE = re.compile(r"\[\[\s*(?:[rR]\s*(\d+)|([aA][A-Za-z0-9_.\-]*))\s*\]\]")
_PH_OPEN, _PH_CLOSE = "\ue000", "\ue001"   # 사용자 정의 영역 — 마크다운 렌더를 통과시킬 자리표
_PH_RE = re.compile(_PH_OPEN + r"(\d+)" + _PH_CLOSE)


def _ref_of(m: re.Match) -> int | str:
    """마커 → 참조 키. 행은 int, 메모는 태그 문자열 (두 종류가 한 목록에서 번호를 공유한다)."""
    return int(m.group(1)) if m.group(1) is not None else m.group(2)


def resolve_citations(text: str, allowed_rows, allowed_notes=()) -> tuple[str, list]:
    """`[[rN]]`·`[[a…]]` 마커를 정리한다 → (정리된 텍스트, 등장 순서의 참조 목록).

    참조 목록은 행 번호(int)와 메모 태그(str)가 섞여 있고 번호 1,2,3…을 공유한다.
    허용 목록(프롬프트에 실제로 넣은 행·메모)에 없는 것은 LLM의 환각이므로 마커를 지운다.
    같은 참조가 여러 번 나오면 같은 번호(= 목록에서의 위치)를 쓴다.
    """
    allowed: set = set(allowed_rows) | set(allowed_notes)
    order: list = []

    def sub(m: re.Match) -> str:
        ref = _ref_of(m)
        if ref not in allowed:
            return ""
        if ref not in order:
            order.append(ref)
        return f"[[r{ref}]]" if isinstance(ref, int) else f"[[{ref}]]"

    out = _MARKER_RE.sub(sub, text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([.,;:!?)])", r"\1", out)
    return out.strip(), order


def render_html(text: str, refs: list, notes: dict[str, dict] | None = None) -> str:
    """마크다운 → HTML + 인용 칩. raw HTML은 비활성이라 답변 속 `<script>`는 이스케이프된다.

    `refs`는 resolve_citations가 준 참조 목록(행 int + 메모 태그 str),
    `notes`는 메모 태그 → 메모 dict (칩에 원래 id와 행 번호를 실어 준다).
    """
    from markdown_it import MarkdownIt

    by_tag = notes or {}
    n_by_ref = {ref: i + 1 for i, ref in enumerate(refs)}
    marked = _MARKER_RE.sub(
        lambda m: (f"{_PH_OPEN}{n_by_ref[_ref_of(m)]}{_PH_CLOSE}"
                   if _ref_of(m) in n_by_ref else ""), text)
    # html=False — 답변에 섞인 raw HTML(<script> 등)은 이스케이프한다 (commonmark 기본은 허용)
    html = MarkdownIt("commonmark", {"html": False}).render(marked)

    def chip(m: re.Match) -> str:
        n = int(m.group(1))
        ref = refs[n - 1]
        if isinstance(ref, int):
            return f'<a class="chat-cite" data-kind="row" data-row="{ref}" data-n="{n}">{n}</a>'
        note = by_tag.get(ref, {})
        # data-id는 뷰어 하이라이트(mark[data-ids])와 맞춰야 하므로 **원래 id**를 넣는다
        return (f'<a class="chat-cite chat-cite-note" data-kind="note" '
                f'data-id="{escape(str(note.get("id") or ref), quote=True)}" '
                f'data-row="{int(note.get("row", 0) or 0)}" data-n="{n}">{n}</a>')

    return _PH_RE.sub(chip, html)


# --- 답변 ---------------------------------------------------------------


def answer(wd: WorkDir, provider: Provider, question: str,
           history: list[dict] | None = None, include_notes: bool = True) -> dict:
    """질문 → 턴 dict (저장·HTTP 응답 공용). LLM 호출은 확장 1회 + 답변 1회.

    `include_notes=False`면 독자 메모를 색인·프롬프트 양쪽에서 제외한다.
    """
    q = (question or "").strip()[:MAX_QUESTION]
    chunks = chunks_of(wd, include_notes)
    by_row = {c.row: c for c in chunks}
    plan = expand_query(provider, q) if chunks else None
    rows = retrieve(chunks, q, plan, include_notes=include_notes) if chunks else []
    user = build_user_prompt(wd, chunks, rows, q, history, include_notes)
    # 프롬프트에 실제로 실린 메모만 인용을 허용한다 (태그 → 메모)
    notes = {note_tag(n["id"]): n
             for r in rows for n in (by_row[r].notes if r in by_row else [])} \
        if include_notes else {}
    raw = provider.complete(build_system(bool(notes)), user, max_tokens=ANSWER_TOKENS)
    clean, cited = resolve_citations(raw or "", rows, notes.keys())
    citations = []
    for i, ref in enumerate(cited):
        if isinstance(ref, str):
            n = notes[ref]
            c = by_row.get(n["row"])
            citations.append({"n": i + 1, "kind": "note", "id": n["id"], "row": n["row"],
                              "side": n["side"], "color": n["color"], "quote": n["quote"],
                              "note": n["note"], "heading": c.heading if c else "",
                              "en": c.en if c else "", "ko": c.ko if c else None})
            continue
        c = by_row.get(ref)
        if c is None:
            continue
        citations.append({"n": i + 1, "kind": "row", "row": ref, "heading": c.heading,
                          "en": c.en, "ko": c.ko})
    return {
        "id": f"t{int(time.time() * 1000):x}",
        "ts": time.time(),
        "question": q,
        "answer_md": clean,
        "answer_html": render_html(clean, cited, notes),
        "citations": citations,
        "retrieved": rows,
        "model": provider.model,
        "cost_usd": round(provider.cost(), 6),
        "error": None,
    }


# --- 기록 (paper.md4/chat.json) -----------------------------------------


def _norm_turn(raw: object) -> dict | None:
    """저장·전송할 수 있는 형태로 정리 (깨진 항목은 버린다)."""
    if not isinstance(raw, dict):
        return None
    q = str(raw.get("question") or "").strip()
    if not q:
        return None
    cites = []
    for c in raw.get("citations") or []:
        if not isinstance(c, dict) or "row" not in c:
            continue
        item = {"n": int(c.get("n") or 0), "kind": "row", "row": int(c["row"]),
                "heading": str(c.get("heading") or ""),
                "en": str(c.get("en") or ""),
                "ko": (str(c["ko"]) if c.get("ko") else None)}
        if c.get("kind") == "note":   # 독자 메모 근거 — 인용문·메모 본문까지 함께 남긴다
            item.update({"kind": "note", "id": str(c.get("id") or ""),
                         "side": str(c.get("side") or "en"),
                         "color": str(c.get("color") or "yellow"),
                         "quote": str(c.get("quote") or ""),
                         "note": str(c.get("note") or "")})
        cites.append(item)
    return {
        "id": str(raw.get("id") or f"t{int(time.time() * 1000):x}"),
        "ts": float(raw.get("ts") or time.time()),
        "question": q[:MAX_QUESTION],
        "answer_md": str(raw.get("answer_md") or ""),
        "answer_html": str(raw.get("answer_html") or ""),
        "citations": cites,
        "retrieved": [int(r) for r in (raw.get("retrieved") or []) if str(r).isdigit()],
        "model": str(raw.get("model") or ""),
        "cost_usd": float(raw.get("cost_usd") or 0.0),
        "error": raw.get("error") or None,
    }


def load(wd: WorkDir) -> list[dict]:
    """저장된 대화. 파일이 없거나 깨졌으면 빈 목록 (뷰어를 막지 않는다)."""
    path = wd.chat_json
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    turns = data.get("turns") if isinstance(data, dict) else data
    return [t for t in (_norm_turn(x) for x in (turns or [])) if t]


def _write(wd: WorkDir, turns: list[dict]) -> list[dict]:
    path = wd.chat_json
    if not turns:
        path.unlink(missing_ok=True)  # 빈 껍데기를 남기지 않는다
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": VERSION, "turns": turns}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)  # 원자적 교체 — 저장 중 종료돼도 반쪽 파일이 남지 않는다
    return turns


def append(wd: WorkDir, turn: dict) -> list[dict]:
    """턴 하나를 기록에 붙이고 전체 목록을 돌려준다 (동시 요청도 안전하게 락)."""
    clean = _norm_turn(turn)
    if clean is None:
        return load(wd)
    with _append_lock:
        turns = (load(wd) + [clean])[-MAX_TURNS:]
        return _write(wd, turns)


def clear(wd: WorkDir) -> None:
    """대화 기록 삭제."""
    with _append_lock:
        _write(wd, [])
