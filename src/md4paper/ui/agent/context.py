"""화면 맥락 스냅숏 — 사용자가 지금 무엇을 보고 있는지를 에이전트에 알린다.

서버는 run 도중 브라우저에 물을 수 없으므로, 클라이언트(`window.__md4Context()`)가 매 메시지에
스냅숏을 실어 보내고 서버는 그것을 시스템 지시문 끝의 짧은 "현재 화면" 블록으로 바꾼다.
그래서 "이 문단 설명해 줘", "선택한 문장" 같은 지시어가 통한다.

모든 필드는 비어도 된다(홈 화면, 구버전 클라이언트). 블록은 300토큰 안팎으로 자른다 —
매 턴 지시문에 붙으니 길면 그대로 비용이 된다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUOTE_MAX = 300      # 선택 인용 상한 (글자)
ROWS_MAX = 60        # 보이는 행 목록 상한
PROMPT_MAX = 900     # 블록 전체 상한 (글자) — 한국어 기준 대략 300토큰 안팎


class _Loose(BaseModel):
    # 클라이언트가 필드를 더 보내도 거절하지 않는다 (프론트가 먼저 바뀌어도 서버가 깨지지 않게)
    model_config = ConfigDict(extra="ignore")


class PaperRef(_Loose):
    token: str | None = None
    title: str | None = None


class Selection(_Loose):
    side: str | None = None      # "en" | "ko"
    row: int | None = None
    quote: str | None = None

    @field_validator("quote")
    @classmethod
    def _clip_quote(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v if len(v) <= QUOTE_MAX else v[:QUOTE_MAX].rstrip() + "…"


class NoteTabs(_Loose):
    open: list[str] = Field(default_factory=list)
    focus: str | None = None


class ProjectRef(_Loose):
    id: str | None = None
    name: str | None = None


class ScreenContext(_Loose):
    """클라이언트가 보내는 화면 스냅숏 (설계 노트의 JSON 형태)."""

    screen: str | None = None          # "review" | "home" | "window"(단독 창)
    paper: PaperRef | None = None
    lang: str | None = None            # "en" | "ko" | "both"
    visible_rows: list[int] = Field(default_factory=list)
    top_row: int | None = None
    selection: Selection | None = None
    note_tabs: NoteTabs | None = None
    left_panel: str | None = None
    project: ProjectRef | None = None

    @field_validator("visible_rows")
    @classmethod
    def _cap_rows(cls, v: list[int]) -> list[int]:
        return sorted(set(int(r) for r in v if int(r) >= 0))[:ROWS_MAX]

    @property
    def token(self) -> str | None:
        """화면의 논문 토큰 (없으면 None)."""
        return (self.paper.token or None) if self.paper else None


_SCREEN_KO = {"review": "논문 뷰어", "home": "홈(논문 목록)",
              "window": "따로 띄운 도우미 창 — 논문 본문·화면 이동은 메인 창에 있다"}
_LANG_KO = {"en": "원문만", "ko": "번역만", "both": "원문·번역 나란히"}
_SIDE_KO = {"en": "원문", "ko": "번역"}


def _ranges(rows: list[int]) -> str:
    """[3,4,5,9] → 'r3–r5, r9' (연속 구간을 묶어 짧게)."""
    out: list[str] = []
    start = prev = None
    for r in rows:
        if start is None:
            start = prev = r
        elif r == prev + 1:
            prev = r
        else:
            out.append(f"r{start}" if start == prev else f"r{start}–r{prev}")
            start = prev = r
    if start is not None:
        out.append(f"r{start}" if start == prev else f"r{start}–r{prev}")
    return ", ".join(out)


def to_prompt(ctx: ScreenContext | None) -> str:
    """스냅숏 → 지시문에 덧붙일 한국어 블록. 알릴 것이 없으면 빈 문자열."""
    if ctx is None:
        return ""
    lines: list[str] = []
    if ctx.screen:
        lines.append(f"- 화면: {_SCREEN_KO.get(ctx.screen, ctx.screen)}")
    if ctx.paper and (ctx.paper.token or ctx.paper.title):
        title = (ctx.paper.title or "").strip()[:120] or "(제목 없음)"
        if ctx.paper.token:
            # 앞 턴에서 다룬 논문의 token을 대화 기록에서 집어 쓰는 일이 있었다 — 지금 것을 못 박는다
            lines.append(f"- 열린 논문 = '이 논문': {title} (token {ctx.paper.token}). '이 논문'·"
                         "'여기'는 이 논문이다 — tool의 token 인자를 비우면 이 논문을 읽고, "
                         "인용은 [[rN]].")
        else:
            lines.append(f"- 열린 논문: {title}")
    if ctx.lang:
        lines.append(f"- 보기: {_LANG_KO.get(ctx.lang, ctx.lang)}")
    if ctx.visible_rows:
        top = f" (맨 위 r{ctx.top_row})" if ctx.top_row is not None else ""
        lines.append(f"- 화면에 보이는 행: {_ranges(ctx.visible_rows)}{top}")
    elif ctx.top_row is not None:
        lines.append(f"- 화면 맨 위 행: r{ctx.top_row}")
    sel = ctx.selection
    if sel and sel.quote:
        where = _SIDE_KO.get(sel.side or "", "")
        row = f" r{sel.row}" if sel.row is not None else ""
        head = f"{where}{row}".strip()
        lines.append(f"- 사용자가 선택한 글{f' ({head})' if head else ''}: \"{sel.quote}\"")
    if ctx.note_tabs and (ctx.note_tabs.open or ctx.note_tabs.focus):
        tabs = ", ".join(t[:40] for t in ctx.note_tabs.open[:8])
        focus = f" — 보고 있는 탭: {ctx.note_tabs.focus[:40]}" if ctx.note_tabs.focus else ""
        lines.append(f"- 열린 메모 탭: {tabs or '(없음)'}{focus}")
    if ctx.left_panel:
        lines.append(f"- 왼쪽 패널: {ctx.left_panel}")
    if ctx.project and (ctx.project.name or ctx.project.id):
        lines.append(f"- 보고 있는 컬렉션: {ctx.project.name or ctx.project.id}")
    if not lines:
        return ""
    block = "## 현재 화면 (사용자가 보고 있는 것)\n" + "\n".join(lines)
    return block if len(block) <= PROMPT_MAX else block[:PROMPT_MAX].rstrip() + "…"
