"""에이전트가 부르는 tool — 논문·메모·라이브러리 읽기와 화면 이동.

모두 `RunContext[Deps]`를 받는 모듈 함수이고 `TOOLS`로 에이전트에 한꺼번에 건다. docstring은
**모델이 읽는 설명**이라 한국어로 무엇을 돌려주는지·인자가 무엇인지 분명히 적는다.

규칙:
- `token=None`이면 지금 화면의 논문. 모르는 토큰·열린 논문 없음은 예외 대신 안내 문자열을 돌려준다
  (tool이 예외를 던지면 run 전체가 끊긴다 — 모델이 `list_papers`로 다시 찾게 하는 편이 낫다).
- 파일 I/O는 동기라 `asyncio.to_thread`로 돌린다(서버 이벤트 루프를 막지 않게).
- 결과는 각 4000자 상한(목록인 list_papers만 12000자). 행을 돌려주는 tool은 행 앞에 `[rN]`
  (다른 논문은 `[tok:rN]`)을 붙이고, 돌려준 행을 `deps.cited_rows`에 등록한다 — 답의 인용
  마커는 이 집합 안의 것만 칩이 된다.
- 화면을 움직이는 tool(goto_row/open_paper)은 `deps.ui_actions`에 동작을 넣을 뿐이다. SSE 생성기가
  그것을 스트림에 끼워 보내고 브라우저가 실행한다.
- 비용이 드는 tool(retranslate_sections·run_glossary)은 `jobtools.py`에 있다 — 승인 뒤 서버 수명
  작업을 띄우고 바로 돌아온다. 에이전트에는 `TOOLS + jobtools.TOOLS`로 함께 건다.
- 쓰기 tool(write_note·add_highlight·set_project_membership·remember)은 `requires_approval` —
  모델이 부르면 run이 승인 대기로 끝나고, 사용자가 승인한 뒤의 run에서야 실제로 실행된다.
  승인 카드의 한 줄 설명은 `DESCRIBE`(tool 이름 → args로 문장 만들기). 실행한 뒤에는
  `{"kind": "refresh", "what": …}` 화면 동작을 넣어 뷰어가 메모·하이라이트·목록을 다시 읽게 한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from pydantic_ai import RunContext, Tool

from md4paper.ui.agent import retrieval, sessions
from md4paper.ui.agent.cites import note_tag, row_label
from md4paper.ui.agent.context import ScreenContext
from md4paper.workdir import WorkDir

RESULT_MAX = 4000     # tool 결과 하나의 글자 상한
ROW_CLIP = 900        # 검색 결과에서 한 행의 한쪽(EN/KO) 글자 상한
READ_ROWS_MAX = 30    # read_rows 한 번에 읽는 행 수 상한
SEARCH_K_MAX = 15
TITLE_CLIP = 60       # 결과 첫 줄의 논문 제목 상한 (요약 200자 안에 행 번호가 남게)
LIST_DEFAULT = 100    # list_papers 기본 개수
LIST_LIMIT_MAX = 300  # list_papers 개수 상한
LIST_MAX = 12000      # list_papers 결과 글자 상한 — 목록은 한 줄이 짧아 따로 넉넉히 (약 3~4천 토큰)


def token_of(root: Path) -> str:
    """워크디렉토리 토큰 — app.wd_token과 같은 규칙(경로 sha1 앞 12자리)."""
    return hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]


def review_url(root: Path) -> str:
    """리뷰 화면 주소 — app._review_url과 같은 규칙 (`/review?wd=<절대 경로>`)."""
    return f"/review?wd={quote(str(Path(root).resolve()))}"


@dataclass
class Deps:
    """run 하나 동안 tool이 공유하는 것들 (턴마다 새로 만든다)."""

    wd_for: Callable[[str], WorkDir | None]          # 토큰 → WorkDir (모르면 None, 폴백 없음)
    current_token: str | None = None                  # 지금 화면의 논문 (홈이면 None)
    ctx: ScreenContext = field(default_factory=ScreenContext)
    cited_rows: set[tuple[str, int]] = field(default_factory=set)   # 인용 허용 (token, row)
    cited_notes: set[str] = field(default_factory=set)              # 인용 허용 메모 id
    ui_actions: asyncio.Queue = field(default_factory=asyncio.Queue)
    register_token: Callable[[WorkDir], str] = lambda wd: token_of(wd.root)
    note_refs: dict[str, dict] = field(default_factory=dict)   # 메모 태그 → {id,row,token,…}
    workspace: Path | None = None    # list_papers가 훑을 작업 폴더 (None이면 config 기본)
    # 이번 run에서 새로 등록한 token → 작업 폴더. run이 끝나면 세션 `papers`에 합쳐 저장한다 —
    # 서버를 다시 켜도 대화 기록 속 token을 routes가 다시 등록할 수 있게.
    papers: dict[str, Path] = field(default_factory=dict)
    # 비용 tool(jobtools)이 이번 run에 띄운 백그라운드 작업 기록 — run_turn이 세션 `jobs`에 합친다
    jobs: list[dict] = field(default_factory=list)
    # 작업용 LLM 공장 (purpose "translate"|"glossary", WorkDir) → Provider. None이면 앱 설정
    # (jobtools.default_job_provider). 테스트는 FakeProvider를 돌려주는 함수를 끼운다.
    build_job_provider: Callable | None = None
    job_notice: str = ""     # 지시문에 실을 백그라운드 작업 소식 (run_turn이 run 전에 한 번 만든다)

    def register(self, wd: WorkDir) -> str:
        """`register_token`을 부르고 그 token의 경로를 `papers`에 적는다 (tool은 이것을 쓴다)."""
        tok = self.register_token(wd)
        self.papers[tok] = Path(wd.root)
        return tok


# --- 공통 ---------------------------------------------------------------


def _cap(text: str, limit: int = RESULT_MAX) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…(결과가 길어 잘림)"


def _clip(text: str | None, limit: int = ROW_CLIP) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit].rstrip() + " …"


_CURRENT = object()   # _resolve의 기본 논문 = 지금 화면


def _resolve(deps: Deps, token: str | None,
             default: object = _CURRENT) -> tuple[str, WorkDir] | str:
    """토큰 → (토큰, WorkDir). 실패하면 모델에게 보일 안내 문자열.

    token이 비면 default(기본: 지금 화면의 논문). 쓰기 tool은 승인을 요청한 때의 논문을 넘긴다.
    """
    fallback = deps.current_token if default is _CURRENT else default
    tok = (token or fallback or "").strip()
    if not tok:
        return ("지금 화면에 열린 논문이 없습니다. list_papers로 논문을 찾아 token을 지정하세요.")
    wd = deps.wd_for(tok)
    if wd is None:
        return (f"알 수 없는 논문 token입니다: {tok}. list_papers로 token을 다시 확인하세요.")
    return tok, wd


async def _paper_line(deps: Deps, tok: str, wd: WorkDir) -> str:
    """결과 첫 줄 — 어느 논문을 읽었는지. 모델이 '이 논문'을 맞게 골랐는지 스스로 확인하게."""
    from md4paper.workdir import paper_title

    title = " ".join((await asyncio.to_thread(paper_title, Path(wd.root))).split())
    title = title if len(title) <= TITLE_CLIP else title[:TITLE_CLIP].rstrip() + "…"
    where = "지금 화면의 논문" if tok == deps.current_token else f"다른 논문, token {tok}"
    return f"논문: {title} ({where})\n"


def _note_desc(n: dict) -> str:
    """메모 한 건의 설명 — `(원문, 노랑): "인용문" — 메모 본문`."""
    where = retrieval.SIDE_KO.get(n.get("side", "en"), "원문")
    color = retrieval.COLOR_KO.get(n.get("color", ""), n.get("color", ""))
    body = " ".join(str(n.get("note", "")).split()) or "(메모 없이 하이라이트만)"
    return f'({where}, {color}): "{_clip(n.get("quote", ""), 300)}" — {body}'


def _note_line(n: dict) -> str:
    return f"  독자 메모 [{note_tag(n['id'])}] {_note_desc(n)}"


def _register_note(deps: Deps, tok: str, n: dict) -> None:
    deps.cited_notes.add(str(n["id"]))
    deps.note_refs[note_tag(n["id"])] = {**n, "token": tok}


def _row_block(c: retrieval.Chunk, label: str, clip: int = ROW_CLIP) -> str:
    head = f" ({c.heading})" if c.heading else ""
    block = f"[{label}]{head}\nEN: {_clip(c.en, clip)}"
    if c.ko:
        block += f"\nKO: {_clip(c.ko, clip)}"
    for f in c.figures:   # 그림 설명 — 저자가 PDF에 단 대체 텍스트 (이미지 줄만으로는 그림 내용을 모른다)
        block += f"\n그림 설명(PDF): {_clip(f, clip)}"
    for n in c.notes:
        block += "\n" + _note_line(n)
    return block


def _pack_rows(deps: Deps, tok: str, chunks: list[retrieval.Chunk], clip: int,
               header: str) -> str:
    """행 블록들을 상한 안에서 이어 붙이고, **실제로 실은** 행·메모만 인용 허용에 등록한다."""
    out = header
    shown = 0
    for c in chunks:
        block = _row_block(c, row_label(tok, c.row, deps.current_token), clip)
        if len(out) + len(block) + 2 > RESULT_MAX and shown:
            out += f"\n\n…(상한에 걸려 여기서 멈춤 — 다음은 r{c.row}부터)"
            break
        out += "\n\n" + block
        shown += 1
        deps.cited_rows.add((tok, c.row))
        for n in c.notes:
            _register_note(deps, tok, n)
    return _cap(out)


# --- 논문 읽기 ----------------------------------------------------------


async def search_paper(ctx: RunContext[Deps], query: str, token: str | None = None,
                       k: int = 8) -> str:
    """논문 본문(원문·번역)과 독자 메모에서 질문과 관련된 문단을 찾는다 (BM25 키워드 검색).

    결과의 각 문단 앞 `[rN]`(다른 논문이면 `[token:rN]`)이 인용 번호다. 답에서 그 문단을 근거로
    쓸 때는 `[[rN]]`처럼 대괄호 두 겹으로 인용한다. 키워드 검색이라 영어 용어·동의어로 바꿔 여러 번
    불러도 좋다.

    Args:
        query: 찾을 내용. 질문 문장 그대로 또는 핵심 키워드(영어·한국어 모두 가능).
        token: 논문 token. 생략하면 지금 화면의 논문.
        k: 돌려받을 문단 수 (1~15, 기본 8).
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    k = max(1, min(int(k or 8), SEARCH_K_MAX))

    def work() -> list[retrieval.Chunk]:
        p = retrieval.paper(wd)
        by_row = p.by_row()
        return [by_row[r] for r in retrieval.retrieve(p.chunks, query, k=k, index=p.index)
                if r in by_row]

    found = await asyncio.to_thread(work)
    if not found:
        return "이 논문에서 읽을 수 있는 본문이 없습니다 (변환이 끝나지 않았을 수 있음)."
    head = await _paper_line(ctx.deps, tok, wd)
    return _pack_rows(ctx.deps, tok, found, ROW_CLIP, f"{head}검색 결과 {len(found)}개 (관련도 순):")


async def read_rows(ctx: RunContext[Deps], start: int, end: int | None = None,
                    token: str | None = None) -> str:
    """논문의 정렬 행을 번호 범위로 그대로 읽는다 (원문 EN + 번역 KO).

    outline이나 search_paper에서 본 행 번호 주변을 더 읽을 때 쓴다. 한 번에 30행까지.
    행 앞 `[rN]`이 인용 번호다(답에서는 `[[rN]]`).

    Args:
        start: 첫 행 번호 (0부터).
        end: 마지막 행 번호(포함). 생략하면 start 한 행만.
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    lo = max(0, int(start))
    hi = int(end) if end is not None else lo
    if hi < lo:
        lo, hi = hi, lo
    hi = min(hi, lo + READ_ROWS_MAX - 1)
    chunks = (await asyncio.to_thread(retrieval.paper, wd)).chunks
    picked = [c for c in chunks if lo <= c.row <= hi]
    if not picked:
        return f"행 범위 밖입니다. 이 논문의 행은 r0–r{len(chunks) - 1}입니다." if chunks \
            else "이 논문에서 읽을 수 있는 본문이 없습니다."
    head = await _paper_line(ctx.deps, tok, wd)
    return _pack_rows(ctx.deps, tok, picked, RESULT_MAX,
                      f"{head}r{lo}–r{picked[-1].row} (전체 r0–r{len(chunks) - 1}):")


async def outline(ctx: RunContext[Deps], token: str | None = None) -> str:
    """논문의 섹션 목차를 행 번호·섹션 id와 함께 돌려준다 (어느 섹션이 몇 행에서 시작하는지).

    각 줄은 `- [r12] h_0004 Introduction` 꼴이다. 특정 섹션을 읽으려면 행 번호를 보고 read_rows로
    읽고, 섹션을 다시 번역할 때(retranslate_sections)는 섹션 id(`h_0004`)를 넘긴다.

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    chunks = (await asyncio.to_thread(retrieval.paper, wd)).chunks
    sids = await asyncio.to_thread(section_ids, wd, chunks)
    lines = [f"{'  ' * (c.head_level - 1)}- [{row_label(tok, c.row, ctx.deps.current_token)}] "
             f"{sids[c.row] + ' ' if c.row in sids else ''}{c.heading}"
             for c in chunks if c.head_level]
    if not lines:
        return "목차(헤더)를 찾지 못했습니다." if chunks else "이 논문에서 읽을 수 있는 본문이 없습니다."
    head = await _paper_line(ctx.deps, tok, wd)
    note = " — 각 줄: [행 번호] 섹션 id 제목" if sids else ""
    return _cap(f"{head}목차 (전체 r0–r{len(chunks) - 1}){note}:\n" + "\n".join(lines))


def _norm_head(text: str) -> str:
    return " ".join((text or "").split()).lower()


def section_ids(wd: WorkDir, chunks: list[retrieval.Chunk]) -> dict[int, str]:
    """outline의 헤더 행 번호 → `out/sections.map.json`의 섹션 id (retranslate_sections가 받는 id).

    둘 다 en.md의 헤더 줄에서 나오므로(코드 펜스 안 # 제외) 개수가 같으면 순서대로 짝짓는다.
    다르면(재조립 중이거나 손으로 고친 en.md) 제목 글자가 같은 것을 문서 순서로 찾는다.
    """
    try:
        smap = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    except (OSError, ValueError):
        return {}
    smap = sorted((e for e in smap if e.get("id")), key=lambda e: e.get("out_line", 0))
    heads = [c for c in chunks if c.head_level]
    if len(heads) == len(smap):
        return {c.row: str(e["id"]) for c, e in zip(heads, smap)}
    out: dict[int, str] = {}
    j = 0
    for c in heads:
        k = j
        while k < len(smap) and _norm_head(smap[k].get("text", "")) != _norm_head(c.heading):
            k += 1
        if k < len(smap):
            out[c.row] = str(smap[k]["id"])
            j = k + 1
    return out


# --- 메모·하이라이트 ----------------------------------------------------


async def list_note_tabs(ctx: RunContext[Deps], token: str | None = None) -> str:
    """사용자가 이 논문에 써 둔 메모 탭 이름과 길이를 돌려준다.

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    from md4paper import notes

    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    _tok, wd = got

    def work() -> list[tuple[str, int]]:
        return [(n, len(notes.read(wd, n))) for n in notes.tab_names(wd)]

    tabs = await asyncio.to_thread(work)
    if not tabs:
        return "메모 탭이 없습니다."
    return _cap("메모 탭:\n" + "\n".join(f"- {n} ({size}자)" for n, size in tabs))


async def read_notes(ctx: RunContext[Deps], name: str | None = None,
                     token: str | None = None) -> str:
    """사용자가 쓴 메모 탭의 내용(마크다운)을 읽는다. 논문 본문이 아니라 **사용자의 글**이다.

    Args:
        name: 메모 탭 이름. 생략하면 내용 있는 모든 탭을 이어서.
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    from md4paper import notes

    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    _tok, wd = got

    def work() -> str:
        if not name:
            return notes.combined(wd)
        names = notes.tab_names(wd)
        if name not in names:
            return f"'{name}' 탭이 없습니다. 있는 탭: {', '.join(names) or '(없음)'}"
        return notes.read(wd, name)

    text = await asyncio.to_thread(work)
    return _cap(text) if text.strip() else "메모가 비어 있습니다."


async def read_highlights(ctx: RunContext[Deps], token: str | None = None) -> str:
    """사용자가 논문에 그은 하이라이트와 그 메모를 문서 순서로 돌려준다.

    각 줄의 `[a…]`가 메모 인용 번호(답에서는 `[[a…]]`), `[rN]`이 그 하이라이트가 걸린 행이다.
    하이라이트는 논문 내용이 아니라 사용자가 표시한 것임을 답에서 구분해 말한다.

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    chunks = (await asyncio.to_thread(retrieval.paper, wd)).chunks
    found = [(c, n) for c in chunks for n in c.notes]
    if not found:
        return "이 논문에는 하이라이트·메모가 없습니다."
    lines: list[str] = []
    size = 0
    for c, n in found:
        label = row_label(tok, c.row, ctx.deps.current_token)
        line = f"- [{note_tag(n['id'])}] [{label}] {_note_desc(n)}"
        if size + len(line) > RESULT_MAX - 200:
            lines.append(f"…(상한에 걸려 {len(found) - len(lines)}개 생략)")
            break
        lines.append(line)
        size += len(line) + 1
        ctx.deps.cited_rows.add((tok, c.row))
        _register_note(ctx.deps, tok, n)
    return _cap(f"하이라이트 {len(found)}개:\n" + "\n".join(lines))


# --- 라이브러리 ---------------------------------------------------------


async def list_papers(ctx: RunContext[Deps], query: str | None = None,
                      project: str | None = None, limit: int = LIST_DEFAULT) -> str:
    """사용자 라이브러리의 논문 목록(최근 수정순)과 각 논문의 token을 돌려준다.

    다른 논문을 읽거나(search_paper 등의 token 인자) 열 때(open_paper) 여기서 얻은 token을 쓴다.
    결과 첫머리에 **라이브러리 전체 편수·조건에 맞는 편수·실제로 보인 편수·컬렉션별 편수**가 있다.
    "몇 편이냐"는 그 숫자로 답하고, 목록 줄 수를 세지 않는다(목록은 잘릴 수 있다).
    찾는 논문이 목록에 없으면 query나 project로 좁혀 다시 부른다.

    Args:
        query: 제목·저자·학회에 들어 있는 글자로 거르기 (대소문자 무시). 생략하면 전부.
        project: 컬렉션 id 또는 이름으로 거르기. 생략하면 전부.
        limit: 최대 개수 (기본 100, 최대 300). 결과 글자 상한에 걸리면 그보다 적게 보인다.
    """
    from md4paper import config, projects
    from md4paper.workdir import recent_workdirs

    deps = ctx.deps
    ws = deps.workspace or config.resolve_workspace()
    limit = max(1, min(int(limit or LIST_DEFAULT), LIST_LIMIT_MAX))
    q = (query or "").strip().lower()
    pj = (project or "").strip().lower()

    def work() -> tuple[int, list[dict], dict[str, str]]:
        names = {p["id"]: p["name"] for p in projects.all_projects()}
        # 상한 없이 다 센다 — 앞 몇백 편에서 자르면 '전체 편수'가 틀린다
        rows = recent_workdirs(Path(ws), limit=100_000)
        out = []
        for r in rows:
            ids = [i for i in r.get("projects") or [] if i in names]
            if pj and not any(i.lower() == pj or names[i].lower() == pj for i in ids):
                continue
            hay = " ".join([str(r.get("title") or ""), " ".join(r.get("authors") or []),
                            str(r.get("venue") or "")]).lower()
            if q and q not in hay:
                continue
            out.append({**r, "projects": ids})
        return len(rows), out, names

    total, rows, names = await asyncio.to_thread(work)
    if not rows:
        return (f"조건에 맞는 논문이 없습니다 (라이브러리 전체 {total}편)." if q or pj
                else "라이브러리에 논문이 없습니다.")

    # 머리 — 모델이 목록 줄 수를 세서 편수를 답하지 않게 숫자를 먼저 못 박는다
    counts: dict[str, int] = {}
    loose = 0
    for r in rows:
        loose += not r["projects"]
        for i in r["projects"]:
            counts[i] = counts.get(i, 0) + 1
    by_pj = [f"{names[i]} {n}" for i, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    if loose:
        by_pj.append(f"미분류 {loose}")
    cond = ", ".join(b for b in (f"query '{query.strip()}'" if q else "",
                                 f"project '{project.strip()}'" if pj else "") if b)

    lines: list[str] = []
    size = 0
    for r in rows[:limit]:
        authors = ", ".join((r.get("authors") or [])[:2])
        if len(r.get("authors") or []) > 2:
            authors += " 외"
        bits = [b for b in (str(r.get("year") or ""), authors) if b]
        meta = f" ({'; '.join(bits)})" if bits else ""
        extra = []
        if r["projects"]:
            extra.append("컬렉션: " + ", ".join(names[i] for i in r["projects"]))
        if r.get("has_ko"):
            extra.append("번역 있음")
        tok = deps.register(WorkDir(Path(r["root"])))
        if tok == deps.current_token:
            extra.append("← 지금 열린 논문")
        tail = f" · {' · '.join(extra)}" if extra else ""
        line = f"- token {tok}: {r.get('title') or r.get('name')}{meta}{tail}"
        if size + len(line) + 1 > LIST_MAX - 600 and lines:   # 600 = 머리·꼬리 자리
            break
        lines.append(line)
        size += len(line) + 1

    shown = len(lines)
    if cond:
        head = (f"라이브러리 전체 {total}편 중 조건({cond})에 맞는 논문 {len(rows)}편, "
                f"그중 최근 수정순 {shown}편을 보입니다.")
    else:
        head = f"라이브러리 전체 {total}편 중 최근 수정순 {shown}편을 보입니다."
    head += f"\n컬렉션별 ({'조건에 맞는 논문 기준' if cond else '전체'}): {', '.join(by_pj)}"
    tail = ""
    if shown < len(rows):
        tail = (f"\n\n(목록 밖에 {len(rows) - shown}편 더 있음 — 찾는 논문이 없으면 query(제목·저자·"
                f"학회 글자)나 project(컬렉션 이름·id)로 좁혀 다시 부르세요.)")
    return _cap(head + "\n\n" + "\n".join(lines) + tail, LIST_MAX)


async def list_projects(ctx: RunContext[Deps]) -> str:
    """컬렉션(논문 묶음) 목록과 각 컬렉션의 논문 수를 돌려준다. 한 논문이 여러 컬렉션에 속할 수 있다."""
    from md4paper import config, projects
    from md4paper.workdir import recent_workdirs

    ws = ctx.deps.workspace or config.resolve_workspace()

    def work() -> str:
        items = projects.all_projects()
        counts: dict[str, int] = {}
        loose = 0
        known = {p["id"] for p in items}
        for r in recent_workdirs(Path(ws), limit=100_000):
            ids = [i for i in r.get("projects") or [] if i in known]
            loose += not ids
            for i in ids:
                counts[i] = counts.get(i, 0) + 1
        if not items:
            return f"컬렉션이 없습니다 (논문 {loose}편은 모두 미분류)."
        lines = [f"- id {p['id']}: {p['name']} — {counts.get(p['id'], 0)}편" for p in items]
        return "컬렉션:\n" + "\n".join(lines) + f"\n- (미분류) — {loose}편"

    return _cap(await asyncio.to_thread(work))


async def get_bibtex(ctx: RunContext[Deps], token: str | None = None) -> str:
    """논문의 BibTeX 항목을 돌려준다 (저장된 서지 정보로 만든다, 네트워크 조회 없음).

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    from md4paper import bibtex

    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    _tok, wd = got
    entry = await asyncio.to_thread(bibtex.entry_for, wd)
    if entry is None:
        return "이 논문의 서지 정보가 없거나 BibTeX 검증에 실패했습니다."
    key, text = entry
    return _cap(f"인용 키: {key}\n```bibtex\n{text.strip()}\n```")


# --- 화면 이동 ----------------------------------------------------------


async def goto_row(ctx: RunContext[Deps], row: int, token: str | None = None) -> str:
    """사용자 화면을 논문의 특정 행으로 스크롤하고 잠깐 강조한다.

    다른 논문의 행이면 그 논문을 연 뒤 이동한다. 사용자가 "거기 보여 줘"처럼 위치를 원할 때 쓴다.

    Args:
        row: 이동할 행 번호.
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    n = len((await asyncio.to_thread(retrieval.paper, wd)).chunks)
    if not n:
        return "이 논문에서 읽을 수 있는 본문이 없습니다."
    if not 0 <= int(row) < n:
        return f"행 범위 밖입니다 (r0–r{n - 1})."
    # url도 싣는다 — 브라우저는 토큰만으로 다른 논문의 주소(`/review?wd=<경로>`)를 만들 수 없다
    ctx.deps.ui_actions.put_nowait({"kind": "goto_row", "token": tok, "row": int(row),
                                    "url": review_url(wd.root)})
    where = "" if tok == ctx.deps.current_token else " (그 논문을 열고)"
    return f"화면을{where} r{int(row)}로 옮겼습니다."


async def open_paper(ctx: RunContext[Deps], token: str, lang: str | None = None) -> str:
    """사용자 화면에 다른 논문을 연다 (뷰어로 이동). token은 list_papers에서 얻는다.

    Args:
        token: 열 논문의 token.
        lang: 보기 언어 힌트 — "en"(원문), "ko"(번역), "both"(나란히). 생략하면 그대로.
    """
    from md4paper.workdir import paper_title

    wd = ctx.deps.wd_for((token or "").strip())
    if wd is None:
        return f"알 수 없는 논문 token입니다: {token}. list_papers로 token을 다시 확인하세요."
    action = {"kind": "open_paper", "token": token.strip(), "url": review_url(wd.root)}
    if lang in ("en", "ko", "both"):
        action["lang"] = lang
    ctx.deps.ui_actions.put_nowait(action)
    title = await asyncio.to_thread(paper_title, Path(wd.root))
    return f"'{title}' 논문을 열었습니다."


# --- 쓰기 (사용자 승인 뒤 실행) ----------------------------------------
# 모델이 부르면 run은 승인 대기로 끝나고(DeferredToolRequests), 여기 함수는 사용자가 승인한 뒤의
# run에서야 돈다. 그사이 사용자가 다른 논문으로 옮겼을 수 있으므로 token을 비워 부른 호출은
# '승인을 요청했을 때 화면의 논문'(재개 때 tool_call_metadata로 들어온다)에 쓴다.

NOTE_MAX = 20000       # write_note 한 번에 쓰는 글 상한
MEMORY_LINE_MAX = 500  # remember 한 줄 상한
MEMORY_WARN = 4000     # agent.MEMORY_MAX와 같은 값 — 넘으면 앞부분(오래된 기억)이 지시문에 안 들어간다
ANCHOR_CTX = 40        # 하이라이트 앵커의 앞뒤 문맥 길이 — 뷰어 JS의 CTX와 같게
# 뷰어가 셀을 그리는 markdown2 extras (app.py의 `_md = [...]`, ui.markdown(enb, extras=_md))
VIEW_EXTRAS = "fenced-code-blocks tables latex"


def _write_target(ctx: RunContext[Deps], token: str | None) -> tuple[str, WorkDir] | str:
    """쓰기 tool의 대상 논문 — token이 없으면 승인을 요청한 때 화면의 논문(없으면 지금 화면)."""
    meta = ctx.tool_call_metadata if isinstance(ctx.tool_call_metadata, dict) else {}
    if "token" in meta:
        return _resolve(ctx.deps, token, default=meta["token"])
    return _resolve(ctx.deps, token)


def _refresh(deps: Deps, what: str, token: str | None, **extra: object) -> None:
    """뷰어에 다시 읽으라고 알린다 — 열린 화면이 들고 있던 옛 목록으로 파일을 덮지 않게."""
    deps.ui_actions.put_nowait({"kind": "refresh", "what": what, "token": token, **extra})


def _lines(text: str) -> int:
    return sum(1 for ln in (text or "").splitlines() if ln.strip())


async def write_note(ctx: RunContext[Deps], name: str, text: str, token: str | None = None,
                     mode: str = "append") -> str:
    """사용자의 메모 탭에 글을 쓴다. **사용자가 승인해야 실행된다.**

    사용자가 메모로 남겨 달라고 할 때만 쓴다. 탭이 없으면 그 이름으로 새로 만든다.
    마크다운으로 쓰고, 논문 행을 가리킬 때는 인용 칩 대신 "r12"처럼 평문으로 적는다.

    Args:
        name: 메모 탭 이름 (list_note_tabs로 확인). 없는 이름이면 새 탭을 만든다.
        text: 쓸 내용 (마크다운).
        token: 논문 token. 생략하면 지금 화면의 논문.
        mode: "append"(기본, 탭 끝에 빈 줄을 두고 덧붙임) 또는 "replace"(탭 내용을 통째로 바꿈 —
            사용자가 바꿔 달라고 분명히 말했을 때만).
    """
    from md4paper import notes

    if mode not in ("append", "replace"):
        return f"mode는 'append' 또는 'replace'여야 합니다 (받은 값: {mode!r})."
    body = (text or "").strip()
    if not body:
        return "쓸 내용이 비어 있어 아무것도 쓰지 않았습니다."
    if len(body) > NOTE_MAX:
        return f"내용이 너무 깁니다 ({len(body)}자, 상한 {NOTE_MAX}자). 나눠서 쓰세요."
    got = _write_target(ctx, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    want = notes.clean_name(name) or notes.DEFAULT_NAME

    def work() -> tuple[str, str, bool]:
        created = want not in notes.tab_names(wd)
        tab = notes.add(wd, want) if created else want
        old, stamp = notes.read_stamped(wd, tab)
        new = body + "\n" if mode == "replace" or not old.strip() \
            else old.rstrip() + "\n\n" + body + "\n"
        # stamp로 읽은 뒤 바깥(편집기·다른 창)에서 바뀌었으면 덮지 않는다. 통째로 바꿀 때는
        # 덮기 전 내용을 notes/.trash/에 한 벌 남긴다(keep_old).
        notes.write(wd, tab, new, expect=stamp, keep_old=(mode == "replace"))
        return tab, new, created

    try:
        tab, _new, created = await asyncio.to_thread(work)
    except notes.Conflict:
        return (f"'{want}' 탭이 방금 바깥에서 바뀌어 쓰지 않았습니다. read_notes로 다시 읽은 뒤 "
                "사용자에게 다시 쓸지 물어보세요.")
    _refresh(ctx.deps, "notes", tok, tab=tab)
    n = _lines(body)
    if created:
        return f"새 메모 탭 '{tab}'을 만들어 {n}줄을 썼습니다."
    if mode == "replace":
        return f"'{tab}' 탭 내용을 새 글({n}줄)로 바꿨습니다 (이전 내용은 휴지통에 한 벌 남김)."
    return f"'{tab}' 탭 끝에 {n}줄을 덧붙였습니다."


class _TextOf(HTMLParser):
    """HTML → textContent (태그를 버리고 글자만, 엔티티는 풀어서)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_data(self, data: str) -> None:
        self.out.append(data)


def cell_text(md: str) -> str:
    """뷰어 셀의 평문 — 하이라이트 오프셋은 마크다운 원문이 아니라 **렌더된 셀의 textContent** 기준이다.

    뷰어와 같은 렌더러(NiceGUI ui.markdown = markdown2, 같은 extras)로 HTML을 만들고 태그를 걷는다.
    뷰어 쪽의 수식 표식·이미지 주소 바꾸기 같은 차이는 남지만, 뷰어는 오프셋이 안 맞으면
    quote로 다시 찾으므로(app.py resolve) 문구만 같으면 붙는다.
    """
    try:
        from nicegui.elements.markdown import prepare_content

        html = prepare_content(md or "", VIEW_EXTRAS)
    except Exception:  # noqa: BLE001 — 렌더러가 없으면 원문으로 (quote 재탐색이 받쳐 준다)
        return md or ""
    p = _TextOf()
    p.feed(html)
    p.close()
    return "".join(p.out)


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """평문에서 quote의 [start, end) — 그대로 없으면 공백 차이(줄바꿈·연속 공백)를 무시하고 찾는다."""
    q = (quote or "").strip()
    if not q:
        return None
    i = text.find(q)
    if i >= 0:
        return i, i + len(q)
    flat: list[str] = []
    where: list[int] = []      # flat의 각 글자가 원문 몇 번째 글자인지
    prev_space = True
    for k, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            flat.append(" ")
            prev_space = True
        else:
            flat.append(ch)
            prev_space = False
        where.append(k)
    qf = " ".join(q.split())
    j = "".join(flat).find(qf)
    if j < 0:
        return None
    return where[j], where[j + len(qf) - 1] + 1


def _anno_id() -> str:
    """뷰어 JS의 uid()와 같은 꼴 — 'a' + 밀리초(36진) + 무작위 5자."""
    n, digits = int(time.time() * 1000), "0123456789abcdefghijklmnopqrstuvwxyz"
    ms = ""
    while n:
        n, r = divmod(n, 36)
        ms = digits[r] + ms
    return "a" + ms + "".join(secrets.choice(digits) for _ in range(5))


async def add_highlight(ctx: RunContext[Deps], row: int, side: str, quote: str, note: str = "",
                        color: str = "yellow", token: str | None = None) -> str:
    """논문의 한 문장(구절)에 하이라이트를 긋고 메모를 단다. **사용자가 승인해야 실행된다.**

    사용자가 표시해 달라고 할 때만 쓴다. quote는 read_rows·search_paper로 읽은 그 행의 글을
    **그대로 복사**해야 위치를 찾는다(한 문장 정도, 수식·기호를 바꾸지 말 것).

    Args:
        row: 행 번호 (결과의 [rN]의 N).
        side: "en"(원문) 또는 "ko"(번역).
        quote: 하이라이트할 문구 — 그 행의 원문/번역에 들어 있는 글 그대로.
        note: 하이라이트에 붙일 메모 (선택).
        color: "yellow"(기본), "green", "blue", "pink", "purple".
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    from md4paper.ui import annotations

    if side not in annotations.SIDES:
        return "side는 'en'(원문) 또는 'ko'(번역)여야 합니다."
    if color not in annotations.COLORS:
        return f"color는 {', '.join(annotations.COLORS)} 중 하나여야 합니다."
    if not (quote or "").strip():
        return "quote가 비어 있습니다 — 하이라이트할 문구를 그대로 넣으세요."
    got = _write_target(ctx, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    chunks = (await asyncio.to_thread(retrieval.paper, wd)).chunks
    c = next((x for x in chunks if x.row == int(row)), None)
    if c is None:
        return (f"행 범위 밖입니다 (r0–r{len(chunks) - 1})." if chunks
                else "이 논문에서 읽을 수 있는 본문이 없습니다.")
    src = c.en if side == "en" else c.ko
    if not (src or "").strip():
        return f"r{c.row}에는 번역이 없습니다. side='en'으로 원문에 그으세요."
    plain = await asyncio.to_thread(cell_text, src)
    span = locate(plain, quote)
    where = retrieval.SIDE_KO[side]
    if span is None:
        return (f"r{c.row} {where}에서 그 문구를 찾지 못했습니다. read_rows로 그 행을 읽고 "
                "글을 그대로 복사해 다시 부르세요.")
    s, e = span
    item = {"id": _anno_id(), "color": color, "note": (note or "").strip(),
            "anchors": [{"side": side, "row": c.row, "start": s, "end": e, "quote": plain[s:e],
                         "prefix": plain[max(0, s - ANCHOR_CTX):s],
                         "suffix": plain[e:e + ANCHOR_CTX]}],
            "created": time.time()}

    def work() -> None:
        items = annotations.load(wd)
        items.append(item)
        annotations.save(wd, items, backup=True)

    await asyncio.to_thread(work)
    n = retrieval._note_of(item)
    if n is not None:    # 방금 그은 표시도 답에서 [[a…]]로 인용할 수 있게
        _register_note(ctx.deps, tok, n)
    _refresh(ctx.deps, "highlights", tok, id=item["id"])
    return (f"r{c.row} {where}에 하이라이트를 그었습니다 [{note_tag(item['id'])}]: "
            f"\"{_clip(plain[s:e], 120)}\"")


def _find_project(key: str) -> dict | None:
    """컬렉션 id 또는 이름(대소문자 무시) → 컬렉션."""
    from md4paper import projects

    k = (key or "").strip()
    items = projects.all_projects()
    return (next((p for p in items if p["id"] == k), None)
            or next((p for p in items if p["name"].lower() == k.lower()), None))


async def set_project_membership(ctx: RunContext[Deps], token: str, project_id: str,
                                 add: bool = True) -> str:
    """논문을 컬렉션에 넣거나 뺀다 (한 논문은 여러 컬렉션에 속할 수 있다). **사용자가 승인해야 실행된다.**

    Args:
        token: 논문 token (list_papers에서). 빈 문자열이면 지금 화면의 논문.
        project_id: 컬렉션 id 또는 이름 (list_projects에서).
        add: True면 넣기, False면 빼기.
    """
    from md4paper import library

    got = _write_target(ctx, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    proj = await asyncio.to_thread(_find_project, project_id)
    if proj is None:
        return f"'{project_id}' 컬렉션이 없습니다. list_projects로 id를 확인하세요."
    pid, pname = proj["id"], proj["name"]

    def work() -> str | None:
        cur = library.projects_of(wd)
        if add == (pid in cur):
            return None
        new = [*cur, pid] if add else [i for i in cur if i != pid]
        library.set_memberships(wd, new)   # 저장 폴더의 사본도 소속에 맞춰 따라간다
        return pid

    changed = await asyncio.to_thread(work)
    if changed is None:
        return f"이미 '{pname}' 컬렉션에 {'들어 있습니다' if add else '없습니다'}."
    _refresh(ctx.deps, "library", tok)
    return f"논문을 '{pname}' 컬렉션{'에 넣었습니다' if add else '에서 뺐습니다'}."


async def remember(ctx: RunContext[Deps], text: str) -> str:
    """사용자에 대해 앞으로도 기억할 것을 장기 메모리(memory.md)에 한 줄 적는다. **사용자가 승인해야 실행된다.**

    사용자가 기억해 달라고 하거나, 앞으로의 대화에 계속 쓸 선호·배경(연구 분야, 답 형식 취향 등)을
    밝혔을 때만 쓴다. 한 번 읽고 말 논문 내용은 적지 않는다. 다음 질문부터 지시문에 들어간다.

    Args:
        text: 기억할 내용 한 줄 (짧게).
    """
    line = " ".join((text or "").split())[:MEMORY_LINE_MAX]
    if not line:
        return "기억할 내용이 비어 있습니다."
    path = sessions.memory_path()

    def work() -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        sep = "" if not old or old.endswith("\n") else "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{sep}- {date.today().isoformat()} {line}\n")
        return len(old) + len(sep) + len(line) + 14

    size = await asyncio.to_thread(work)
    msg = f"기억해 두었습니다: {line}"
    if size > MEMORY_WARN:
        msg += (f"\n(memory.md가 {MEMORY_WARN}자를 넘어 앞부분(오래된 기억)은 지시문에 들어가지 "
                "않습니다 — 사용자에게 memory.md를 정리하라고 알려 주세요.)")
    return msg


# --- 승인 카드 설명 -----------------------------------------------------

_COLOR_ADJ = {"yellow": "노란", "green": "초록", "blue": "파란", "pink": "분홍", "purple": "보라"}


def _short(text: object, limit: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _on_paper(args: dict) -> str:
    tok = str(args.get("token") or "").strip()
    return f" (논문 {tok})" if tok else ""


def _describe_write_note(args: dict) -> str:
    name = str(args.get("name") or "").strip() or "메모"
    n = _lines(str(args.get("text") or ""))
    if args.get("mode") == "replace":
        return f"'{name}' 탭 내용을 새 글({n}줄)로 바꿈{_on_paper(args)}"
    return f"'{name}' 탭 끝에 {n}줄 추가{_on_paper(args)}"


def _describe_add_highlight(args: dict) -> str:
    side = retrieval.SIDE_KO.get(str(args.get("side") or "en"), "원문")
    color = _COLOR_ADJ.get(str(args.get("color") or "yellow"), "")
    out = f"r{args.get('row')} {side}에 {color} 하이라이트: \"{_short(args.get('quote'), 50)}\""
    if str(args.get("note") or "").strip():
        out += f" + 메모 \"{_short(args.get('note'), 40)}\""
    return out + _on_paper(args)


def _describe_membership(args: dict) -> str:
    key = str(args.get("project_id") or "")
    try:
        proj = _find_project(key)
    except Exception:  # noqa: BLE001 — 설명 한 줄 때문에 승인 카드가 깨지면 안 된다
        proj = None
    name = proj["name"] if proj else key
    verb = "에 넣기" if args.get("add", True) else "에서 빼기"
    return f"논문을 컬렉션 '{name}'{verb}{_on_paper(args)}"


def _describe_remember(args: dict) -> str:
    return f"기억해 두기 (memory.md): \"{_short(args.get('text'), 80)}\""


DESCRIBE: dict[str, Callable[[dict], str]] = {
    "write_note": _describe_write_note,
    "add_highlight": _describe_add_highlight,
    "set_project_membership": _describe_membership,
    "remember": _describe_remember,
}


def describe_call(name: str, args: dict, root: Path | str | None = None) -> str:
    """승인 카드에 보일 사람용 한 줄. 모르는 tool은 이름만.

    root는 그 호출이 쓸 논문의 작업 폴더(세션 `papers`에서 찾은 것) — 비용 tool은 그 논문을 읽어
    섹션 제목·모델을 보인다. 없으면 인자만으로 짧게.
    """
    from md4paper.ui.agent import jobtools

    a = args if isinstance(args, dict) else {}
    try:
        if name in jobtools.DESCRIBE:
            return jobtools.DESCRIBE[name](a, Path(root) if root else None)
        fn = DESCRIBE.get(name)
        return fn(a) if fn is not None else f"{name} 실행"
    except Exception:  # noqa: BLE001 — 깨진 인자여도 카드는 뜬다
        return f"{name} 실행"


WRITE_TOOLS = [write_note, add_highlight, set_project_membership, remember]

TOOLS = [search_paper, read_rows, outline, list_note_tabs, read_notes, read_highlights,
         list_papers, list_projects, get_bibtex, goto_row, open_paper,
         *(Tool(fn, requires_approval=True) for fn in WRITE_TOOLS)]

# 컬렉션 용어집 tool은 따로 모듈에 — 위의 Deps·_find_project·_refresh 등이 정의된 뒤라 순환 import가 풀린다
from md4paper.ui.agent import glossary_tools  # noqa: E402

TOOLS = [*TOOLS, *glossary_tools.TOOLS]
