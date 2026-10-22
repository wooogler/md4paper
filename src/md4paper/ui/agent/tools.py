"""에이전트가 부르는 tool — 논문·메모·라이브러리 읽기와 화면 이동.

모두 `RunContext[Deps]`를 받는 모듈 함수이고 `TOOLS`로 에이전트에 한꺼번에 건다. docstring은
**모델이 읽는 설명**이라 한국어로 무엇을 돌려주는지·인자가 무엇인지 분명히 적는다.

규칙:
- `token=None`이면 지금 화면의 논문. 모르는 토큰·열린 논문 없음은 예외 대신 안내 문자열을 돌려준다
  (tool이 예외를 던지면 run 전체가 끊긴다 — 모델이 `list_papers`로 다시 찾게 하는 편이 낫다).
- 파일 I/O는 동기라 `asyncio.to_thread`로 돌린다(서버 이벤트 루프를 막지 않게).
- 결과는 각 4000자 상한. 행을 돌려주는 tool은 행 앞에 `[rN]`(다른 논문은 `[tok:rN]`)을 붙이고,
  돌려준 행을 `deps.cited_rows`에 등록한다 — 답의 인용 마커는 이 집합 안의 것만 칩이 된다.
- 화면을 움직이는 tool(goto_row/open_paper)은 `deps.ui_actions`에 동작을 넣을 뿐이다. SSE 생성기가
  그것을 스트림에 끼워 보내고 브라우저가 실행한다.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from pydantic_ai import RunContext

from md4paper.ui.agent import retrieval
from md4paper.ui.agent.cites import note_tag, row_label
from md4paper.ui.agent.context import ScreenContext
from md4paper.workdir import WorkDir

RESULT_MAX = 4000     # tool 결과 하나의 글자 상한
ROW_CLIP = 900        # 검색 결과에서 한 행의 한쪽(EN/KO) 글자 상한
READ_ROWS_MAX = 30    # read_rows 한 번에 읽는 행 수 상한
SEARCH_K_MAX = 15


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


# --- 공통 ---------------------------------------------------------------


def _cap(text: str, limit: int = RESULT_MAX) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…(결과가 길어 잘림)"


def _clip(text: str | None, limit: int = ROW_CLIP) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit].rstrip() + " …"


def _resolve(deps: Deps, token: str | None) -> tuple[str, WorkDir] | str:
    """토큰 → (토큰, WorkDir). 실패하면 모델에게 보일 안내 문자열."""
    tok = (token or deps.current_token or "").strip()
    if not tok:
        return ("지금 화면에 열린 논문이 없습니다. list_papers로 논문을 찾아 token을 지정하세요.")
    wd = deps.wd_for(tok)
    if wd is None:
        return (f"알 수 없는 논문 token입니다: {tok}. list_papers로 token을 다시 확인하세요.")
    return tok, wd


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
    return _pack_rows(ctx.deps, tok, found, ROW_CLIP, f"검색 결과 {len(found)}개 (관련도 순):")


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
    return _pack_rows(ctx.deps, tok, picked, RESULT_MAX,
                      f"r{lo}–r{picked[-1].row} (전체 r0–r{len(chunks) - 1}):")


async def outline(ctx: RunContext[Deps], token: str | None = None) -> str:
    """논문의 섹션 목차를 행 번호와 함께 돌려준다 (어느 섹션이 몇 행에서 시작하는지).

    특정 섹션을 읽으려면 여기서 행 번호를 보고 read_rows로 읽는다.

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    got = _resolve(ctx.deps, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    chunks = (await asyncio.to_thread(retrieval.paper, wd)).chunks
    lines = [f"{'  ' * (c.head_level - 1)}- [{row_label(tok, c.row, ctx.deps.current_token)}] "
             f"{c.heading}" for c in chunks if c.head_level]
    if not lines:
        return "목차(헤더)를 찾지 못했습니다." if chunks else "이 논문에서 읽을 수 있는 본문이 없습니다."
    return _cap(f"목차 (전체 r0–r{len(chunks) - 1}):\n" + "\n".join(lines))


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
                      project: str | None = None, limit: int = 30) -> str:
    """사용자 라이브러리의 논문 목록(최근 수정순)과 각 논문의 token을 돌려준다.

    다른 논문을 읽거나(search_paper 등의 token 인자) 열 때(open_paper) 여기서 얻은 token을 쓴다.

    Args:
        query: 제목·저자·학회에 들어 있는 글자로 거르기 (대소문자 무시). 생략하면 전부.
        project: 컬렉션 id 또는 이름으로 거르기. 생략하면 전부.
        limit: 최대 개수 (기본 30, 최대 100).
    """
    from md4paper import config, projects
    from md4paper.workdir import recent_workdirs

    deps = ctx.deps
    ws = deps.workspace or config.resolve_workspace()
    limit = max(1, min(int(limit or 30), 100))
    q = (query or "").strip().lower()
    pj = (project or "").strip().lower()

    def work() -> tuple[list[dict], dict[str, str]]:
        names = {p["id"]: p["name"] for p in projects.all_projects()}
        rows = recent_workdirs(Path(ws), limit=500)
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
        return out, names

    rows, names = await asyncio.to_thread(work)
    if not rows:
        return "조건에 맞는 논문이 없습니다."
    lines = []
    for r in rows[:limit]:
        tok = deps.register_token(WorkDir(Path(r["root"])))
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
        if tok == deps.current_token:
            extra.append("← 지금 열린 논문")
        tail = f" · {' · '.join(extra)}" if extra else ""
        lines.append(f"- token {tok}: {r.get('title') or r.get('name')}{meta}{tail}")
    more = f"\n(그 밖에 {len(rows) - limit}편 더 있음)" if len(rows) > limit else ""
    return _cap(f"논문 {min(len(rows), limit)}편:\n" + "\n".join(lines) + more)


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
    ctx.deps.ui_actions.put_nowait({"kind": "goto_row", "token": tok, "row": int(row)})
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


TOOLS = [search_paper, read_rows, outline, list_note_tabs, read_notes, read_highlights,
         list_papers, list_projects, get_bibtex, goto_row, open_paper]
