"""에이전트 만들기와 한 턴 돌리기 — Pydantic AI 위에 얹은 얇은 층.

- `build_agent`: 설정의 [chat] 제공사·모델과 키로 Agent를 만든다. 키는 env에 기대지 않고 명시
  주입한다(앱이 config.toml에서 읽은 키를 그대로 쓰게). 출력은 `str | DeferredToolRequests` —
  2단계에서 승인 필요한 쓰기 tool이 붙으면 run이 승인 대기로 끝날 수 있다.
- `run_turn`: run 하나를 스트리밍으로 돌리며 **SSE 이벤트 dict**를 낸다(routes가 그대로 흘려보낸다).
  끝나면 답의 인용 마커를 tool이 실제로 돌려준 행으로 걸러 칩 HTML로 렌더하고, 세션에 대화와
  표시용 턴을 기록해 저장한다.

턴 사용량 상한(UsageLimits)은 **턴마다** 센다: Pydantic AI는 `conversation=`의 누적 usage에서
세기 시작하므로, run에는 usage를 비운 대화를 넘기고 끝난 뒤 세션 누적에 더한다. 토큰 수 상한은
두지 않는다 — 요청마다 대화 전체가 들어가 run 안의 입력 토큰 합이 금세 커지므로(8문답 세션의
tool 4개 턴이 44.8k), 대신 모델에 보내는 기록을 창으로 자르고(`window_messages`) 돈은 세션 비용
상한(config [agent].session_cost_cap, routes가 턴 전에 확인)으로 묶는다.
비용은 genai-prices가 아니라 기존 `llm.base.cost_usd`(프로젝트 PRICING)로 — 번역 비용 표시와 같은 표.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from dataclasses import replace
from collections.abc import AsyncIterator
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")   # pydantic_ai import 전에 (Logfire 배너 끔)

from pydantic_ai import (  # noqa: E402
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    UsageLimits,
)
from pydantic_ai.capabilities import Hooks  # noqa: E402
from pydantic_ai.exceptions import UsageLimitExceeded  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)
from pydantic_ai.run import AgentRunResultEvent  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from md4paper import config  # noqa: E402
from md4paper.llm.base import Usage, cost_usd  # noqa: E402
from md4paper.ui.agent import retrieval, sessions  # noqa: E402
from md4paper.ui.agent.cites import note_tag, render_html, resolve_citations  # noqa: E402
from md4paper.ui.agent.context import PaperRef, to_prompt  # noqa: E402
from md4paper.ui.agent.tools import TOOLS, Deps, review_url  # noqa: E402
from md4paper.workdir import paper_title  # noqa: E402

DEFAULT_LIMITS = UsageLimits(request_limit=12, tool_calls_limit=20)
HISTORY_TURNS = 8      # 모델에 보내는 최근 사용자 질문 수 (세션 파일에는 전부 남는다)
FULL_RESULT_TURNS = 2  # 최근 몇 질문의 tool 결과는 줄이지 않는다
OLD_RESULT_MAX = 1500  # 그보다 앞선 턴의 tool 결과가 이보다 길면 줄인다
OLD_RESULT_KEEP = 300  # 줄일 때 남기는 앞부분 (어느 논문·무슨 결과였는지 알아볼 만큼)
OLD_RESULT_NOTE = "…(앞선 턴의 tool 결과, 생략)"
MEMORY_MAX = 4000      # memory.md를 지시문에 넣는 글자 상한
SUMMARY_MAX = 200      # tool_result 이벤트 요약 길이
CITE_TEXT_MAX = 1200   # 턴 기록에 남기는 인용 문단 길이

SYSTEM = """너는 md4paper의 논문 읽기 도우미다. 사용자는 PDF를 마크다운으로 바꾼 학술 논문을
영어 원문과 한국어 번역으로 나란히 읽고, 하이라이트·메모를 남기고, 논문을 컬렉션으로 묶어 관리한다.

할 수 있는 것: tool로 논문 본문을 찾고 읽기(search_paper, read_rows, outline), 사용자의 메모 탭과
하이라이트 읽기(list_note_tabs, read_notes, read_highlights), 라이브러리의 논문·컬렉션 목록
(list_papers, list_projects), BibTeX(get_bibtex), 사용자 화면 옮기기(goto_row, open_paper).
쓰기(사용자가 승인해야 실행됨): 메모 탭에 쓰기(write_note), 하이라이트 긋기(add_highlight),
논문을 컬렉션에 넣고 빼기(set_project_membership), 사용자에 대해 기억하기(remember).
컬렉션 용어집: 컬렉션 안에서 논문마다 어긋난 번역어 읽기(glossary_conflicts), 컬렉션 용어집
읽기(read_collection_glossary), 다음 번역 때 다시 번역될 범위·대략 비용(translation_impact),
컬렉션 용어집에 쓰기(write_collection_glossary — 승인 필요).

규칙:
- 논문 내용에 관한 답은 **tool로 읽은 문단에만** 근거한다. 읽지 않은 내용은 추측하지 말고,
  찾지 못했으면 찾지 못했다고 분명히 말한다. 필요하면 검색어를 바꿔 다시 찾는다.
- 인용: 근거로 쓴 문장 끝에 tool 결과의 번호를 대괄호 두 겹으로 붙인다 — 지금 논문의 행은
  `[[r12]]`, 다른 논문의 행은 `[[0123456789ab:r12]]`(token:행), 독자 메모는 `[[a…]]`.
  tool이 돌려준 번호만 쓴다. `[12]`나 각주 같은 다른 형식은 쓰지 않는다 — 논문 자체의
  참고문헌 번호와 헷갈린다.
- 독자 메모·하이라이트는 논문 내용이 아니라 **사용자가 직접 남긴 글**이다. "메모에 따르면",
  "표시해 둔 문장은"처럼 논문 본문과 구분해 말한다.
- 사용자가 쓴 언어로 답한다 (한국어로 물으면 한국어로).
- 간결하게. 마크다운을 쓸 수 있고 수식은 `$...$`로 쓴다.
- "이 문단", "선택한 문장", "여기" 같은 말은 아래 '현재 화면' 블록으로 해석한다.
- "이 논문"은 언제나 '현재 화면' 블록의 열린 논문이다. 화면의 논문은 턴마다 바뀔 수 있다 —
  화면이 바뀌었으면 새 논문이 '이 논문'이니, 앞 턴에서 열거나 다룬 논문과 혼동하지 말고
  지금 논문을 읽을 때는 token 인자를 비운다. tool 결과 첫 줄의 논문 제목으로 맞는지 확인한다.
- 화면에 열린 논문이 없으면(홈) list_papers로 찾고, 다른 논문은 tool에 token을 넘긴다.
- 논문이 몇 편인지는 list_papers·list_projects 결과 첫머리의 숫자로 답한다. 목록은 잘려 있을 수
  있으니 목록 줄 수를 세지 않는다.
- 화면 이동(goto_row, open_paper)은 사용자가 보여 달라고 하거나 이동을 원할 때만 한다.
- 쓰기 tool은 사용자가 그렇게 해 달라고 할 때만 부른다. 부르면 사용자에게 승인 카드가 뜬다.
  사용자가 거절하면 같은 쓰기를 다시 시도하지 말고 거절된 것을 받아들인다.
- add_highlight의 quote는 read_rows·search_paper로 읽은 그 행의 글을 그대로 복사한다.
- 컬렉션 용어 통일을 부탁받으면 glossary_conflicts로 어긋난 용어를 읽고, **답에서 통일안(용어 →
  번역어·policy와 고른 이유 한 줄)을 표로 제안**한 뒤 write_collection_glossary를 한 번에 부른다
  (항목마다 부르지 않는다). 사용자가 특정 표기를 정했으면 그대로. translation_impact로 다시 번역될
  범위·비용을 먼저 알려 준다. 컬렉션 용어집은 소속 논문의 용어집보다 우선한다."""


MEMORY_CUT_NOTE = "…(앞부분 생략)"


def memory_text() -> str:
    """장기 메모리(`CONFIG_DIR/agent/memory.md`) — 없으면 빈 문자열, 길면 **뒤** 4000자.

    `remember`는 끝에 덧붙이므로 최근 기억이 꼬리에 있다. 자르는 자리는 줄 경계 — 잘린 첫 줄은
    통째로 버린다(반쪽 문장이 사실처럼 읽히지 않게). 한 줄이 4000자를 넘으면 글자로 자른다.
    """
    try:
        text = sessions.memory_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > MEMORY_MAX:
        cut = len(text) - MEMORY_MAX
        tail = text[cut:]
        if text[cut - 1] != "\n":              # 줄 한가운데서 잘렸으면 그 줄을 버린다
            nl = tail.find("\n")
            tail = tail[nl + 1:] if nl >= 0 else tail
        text = MEMORY_CUT_NOTE + "\n" + tail.lstrip("\n")
    return "## 사용자에 대해 기억해 둔 것 (memory.md)\n" + text


async def _dynamic(ctx: RunContext[Deps]) -> str:
    """턴마다 다시 만드는 지시문 — 메모리 + 현재 화면. 기록에 쌓이지 않고 매 run 새로 붙는다.

    화면 맥락에 논문 제목이 없으면(구버전 클라이언트 등) 서버가 채운다 — '이 논문'을 token과
    제목 둘 다로 못 박아야 모델이 앞 턴의 논문과 헷갈리지 않는다. 채운 것은 deps.ctx에 남겨
    같은 run의 다음 요청에서 다시 읽지 않는다.
    """
    deps = ctx.deps
    sc = deps.ctx
    tok = deps.current_token
    if tok and not (sc.paper and (sc.paper.title or "").strip()):
        wd = deps.wd_for(tok)
        if wd is not None:
            title = await asyncio.to_thread(paper_title, Path(wd.root))
            deps.ctx = sc = sc.model_copy(update={"paper": PaperRef(token=tok, title=title)})
    return "\n\n".join(p for p in (memory_text(), to_prompt(sc)) if p)


# --- 모델에 보내는 기록 창 ---------------------------------------------
# Pydantic AI 2.59에는 `Agent(history_processors=…)`가 없다(→ `ProcessHistory` 능력). 그런데
# ProcessHistory는 처리 결과를 `ctx.messages`(= run의 영속 기록)에 되써서 잘린 기록이 그대로
# 세션 파일에 저장된다. 그래서 `Hooks(before_model_request=…)`로 **이번 요청에 실을 목록만**
# 바꾼다 — `request_context.messages`는 요청 전용 얕은 사본이라 새 목록을 대입해도 기록은 그대로다.
# (append/extend는 기록에도 되써지니 쓰지 않는다. 메시지·부분 객체는 기록과 같은 것이므로 고칠 때는
# dataclasses.replace로 새로 만든다.)


def _shrink(msg: ModelRequest) -> ModelRequest:
    """긴 tool 결과를 앞부분 + 생략 표시로 줄인 새 요청 (원래 객체는 건드리지 않는다)."""
    parts = []
    changed = False
    for p in msg.parts:
        if (isinstance(p, ToolReturnPart) and isinstance(p.content, str)
                and len(p.content) > OLD_RESULT_MAX):
            p = replace(p, content=p.content[:OLD_RESULT_KEEP].rstrip() + "\n" + OLD_RESULT_NOTE)
            changed = True
        parts.append(p)
    return replace(msg, parts=parts) if changed else msg


def window_messages(messages: list, turns: int = HISTORY_TURNS,
                    keep_full: int = FULL_RESULT_TURNS) -> list:
    """모델에 보낼 기록 — 최근 `turns`개 사용자 질문만, 그보다 앞선 tool 결과는 줄여서.

    자르는 자리는 사용자 질문 경계(`sessions.is_turn_start`)뿐이다 — tool 호출과 그 결과가
    갈라지면 공급자가 거부한다. 지금 질문(승인 재개면 승인을 기다리던 호출이 든 턴)은 맨 끝
    경계 뒤에 통째로 있으므로 잘리지 않는다. 최근 `keep_full`개 질문의 tool 결과는 그대로 둔다.
    """
    starts = [i for i, m in enumerate(messages) if sessions.is_turn_start(m)]
    cut = starts[-turns] if turns > 0 and len(starts) > turns else 0
    msgs = list(messages[cut:])
    kept = [i - cut for i in starts if i >= cut]
    full_from = kept[-keep_full] if keep_full > 0 and len(kept) >= keep_full else 0
    return [_shrink(m) if i < full_from and isinstance(m, ModelRequest) else m
            for i, m in enumerate(msgs)]


def _window_hook(ctx: RunContext[Deps], request_context):  # noqa: ANN001, ANN202 — ModelRequestContext
    # 상수는 부를 때 읽는다 (테스트가 HISTORY_TURNS를 바꿔 볼 수 있게)
    request_context.messages = window_messages(list(request_context.messages),
                                               HISTORY_TURNS, FULL_RESULT_TURNS)
    return request_context


def make_agent(model) -> Agent[Deps, str | DeferredToolRequests]:  # noqa: ANN001
    """모델(Model 인스턴스)에 tool·지시문을 얹은 Agent. 테스트는 여기에 FunctionModel을 넣는다."""
    return Agent(model, deps_type=Deps, output_type=[str, DeferredToolRequests],
                 instructions=[SYSTEM, _dynamic], tools=TOOLS,
                 capabilities=[Hooks(before_model_request=_window_hook)])


def build_model(provider_name: str, model_name: str, api_key: str):  # noqa: ANN201 — Model
    """제공사 이름(config.PROVIDERS) → Pydantic AI 모델. OpenAI는 Responses API."""
    if provider_name == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIResponsesModel(model_name, provider=OpenAIProvider(api_key=api_key))
    if provider_name == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
    if provider_name == "gemini":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
    raise ValueError(f"알 수 없는 프로바이더: {provider_name}")


def build_agent(provider_name: str, model_name: str,
                api_key: str | None) -> Agent[Deps, str | DeferredToolRequests]:
    """제공사·모델·키로 에이전트를 만든다. 키가 없으면 RuntimeError(안내 문구)."""
    if not api_key:
        raise RuntimeError(
            f"{provider_name} API 키가 없습니다. env {config.ENV_VARS.get(provider_name, '')} 또는 "
            f"`md4paper keys set {provider_name}`로 설정하세요.")
    return make_agent(build_model(provider_name, model_name, api_key))


def default_agent() -> Agent[Deps, str | DeferredToolRequests]:
    """설정의 [chat] 선택(없으면 전역 기본)과 그 제공사 키로 만든 에이전트."""
    provider, model = config.resolve_chat_choice()
    return build_agent(provider, model, config.resolve_key(provider))


_SYSTEM_TO_PROVIDER = {"openai": "openai", "anthropic": "anthropic", "google": "gemini"}


def model_label(agent: Agent) -> str:
    """세션에 적을 모델 표기 — `제공사:모델`(config.PROVIDERS 이름), 모르는 모델은 모델 이름만."""
    model = agent.model
    name = str(getattr(model, "model_name", "") or "")
    prov = _SYSTEM_TO_PROVIDER.get(str(getattr(model, "system", "") or ""))
    return f"{prov}:{name}" if prov else name


# --- 한 턴 --------------------------------------------------------------


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": data}


def _drain(deps: Deps) -> list[dict]:
    out = []
    while True:
        try:
            out.append(_event("ui_action", deps.ui_actions.get_nowait()))
        except asyncio.QueueEmpty:
            return out


def _args(part) -> dict:  # noqa: ANN001 — ToolCallPart
    try:
        return part.args_as_dict()
    except Exception:  # noqa: BLE001 — 깨진 JSON 인자도 이벤트는 내보낸다
        return {"_raw": str(part.args)[:500]}


def _summary(part) -> str:  # noqa: ANN001 — ToolReturnPart | RetryPromptPart
    content = getattr(part, "content", "")
    text = content if isinstance(content, str) else str(content)
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX].rstrip() + "…"


def _err_text(e: BaseException) -> str:
    if isinstance(e, UsageLimitExceeded):
        return f"이번 질문의 사용량 상한에 걸려 멈췄습니다 ({e}). 질문을 좁혀 다시 물어 주세요."
    msg = (str(e).strip().splitlines() or [""])[0] or e.__class__.__name__
    return msg[:200]


def _seed_from_turns(session: sessions.Session, deps: Deps) -> None:
    """앞선 턴들이 인용한 행·메모도 이번 답에서 다시 인용할 수 있게 허용 집합에 넣는다.

    ("아까 그 문단 다시 설명해 줘" — 그 문단은 이번 run의 tool 결과가 아니라 대화 기록에 있다.)
    """
    for t in session.turns:
        for c in t.get("citations") or []:
            tok = str(c.get("token") or "")
            if not tok:
                continue
            if c.get("kind") == "note" and c.get("id"):
                deps.cited_notes.add(str(c["id"]))
                deps.note_refs.setdefault(note_tag(c["id"]), dict(c))
            elif c.get("kind") == "row":
                deps.cited_rows.add((tok, int(c.get("row") or 0)))


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= CITE_TEXT_MAX else text[:CITE_TEXT_MAX].rstrip() + " …"


def _citations(refs: list, deps: Deps) -> list[dict]:
    """참조 목록 → 턴에 남길 인용 정보 (행 문단 원문·번역, 메모 내용)."""
    papers: dict[str, dict[int, retrieval.Chunk]] = {}
    urls: dict[str, str] = {}

    def rows_of(tok: str) -> dict[int, retrieval.Chunk]:
        if tok not in papers:
            wd = deps.wd_for(tok)
            papers[tok] = retrieval.paper(wd).by_row() if wd is not None else {}
        return papers[tok]

    def url_of(tok: str) -> str:
        """칩이 다른 논문을 가리킬 때 창이 옮겨 갈 주소 (토큰만으로는 경로를 못 만든다)."""
        if tok and tok not in urls:
            wd = deps.wd_for(tok)
            urls[tok] = review_url(wd.root) if wd is not None else ""
        return urls.get(tok, "")

    out = []
    for i, ref in enumerate(refs):
        if isinstance(ref, tuple):
            tok, row = ref
            c = rows_of(tok).get(row)
            out.append({"n": i + 1, "kind": "row", "token": tok, "row": row, "url": url_of(tok),
                        "heading": c.heading if c else "", "en": _clip(c.en) if c else "",
                        "ko": _clip(c.ko) if c else None})
            continue
        n = deps.note_refs.get(ref, {})
        ntok = str(n.get("token") or "")
        out.append({"n": i + 1, "kind": "note", "token": ntok, "url": url_of(ntok),
                    "id": str(n.get("id") or ref), "row": int(n.get("row") or 0),
                    "side": str(n.get("side") or "en"), "color": str(n.get("color") or "yellow"),
                    "quote": str(n.get("quote") or ""), "note": str(n.get("note") or "")})
    return out


async def run_turn(agent: Agent, session: sessions.Session, user_text: str | None, deps: Deps,
                   *, usage_limits: UsageLimits | None = None,
                   deferred_tool_results: DeferredToolResults | None = None,
                   ) -> AsyncIterator[dict]:
    """한 턴(또는 승인 뒤 재개)을 돌리며 SSE 이벤트 dict를 낸다. 끝나면 세션을 저장한다.

    이벤트: text{delta} · tool_call{id,name,args} · tool_result{id,summary} · ui_action{kind,…}
    · approval_needed{calls} · done{turn_id,html,citations,usage,cost_usd,session_cost_usd,pending}
    · error{message}. 오류가 나면 error 하나로 끝나고 done은 오지 않는다.
    """
    text = (user_text or "").strip() or None
    label = model_label(agent)
    model_name = getattr(agent.model, "model_name", "") or ""
    prior_usage = session.conversation.usage
    # 턴 상한을 이번 run만으로 세도록 usage를 비운 대화를 넘긴다 (끝나고 누적에 더한다)
    conv_in = dataclasses.replace(session.conversation, usage=RunUsage())
    _seed_from_turns(session, deps)
    cur = deps.wd_for(deps.current_token) if deps.current_token else None
    sessions.touch_paper(session, deps.current_token, cur.root if cur is not None else None)
    if text and not session.title:
        session.title = sessions.default_title(text)
    user_turn = ({"id": sessions.turn_id(), "ts": time.time(), "role": "user", "text": text,
                  "html": "", "citations": [], "usage": None, "cost_usd": 0.0,
                  "token": deps.current_token} if text else None)

    result = None
    after_tool = False
    try:
        async with agent.run_stream_events(
                text, deps=deps, conversation=conv_in, usage_limits=usage_limits or DEFAULT_LIMITS,
                deferred_tool_results=deferred_tool_results) as events:
            async for ev in events:
                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                    if ev.part.content:
                        yield _event("text", {"delta": ("\n\n" if after_tool else "")
                                              + ev.part.content})
                        after_tool = False
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                    if ev.delta.content_delta:
                        yield _event("text", {"delta": ev.delta.content_delta})
                elif isinstance(ev, FunctionToolCallEvent):
                    yield _event("tool_call", {"id": ev.part.tool_call_id,
                                               "name": ev.part.tool_name,
                                               "args": _args(ev.part)})
                    after_tool = True
                elif isinstance(ev, FunctionToolResultEvent):
                    yield _event("tool_result", {"id": ev.part.tool_call_id,
                                                 "summary": _summary(ev.part)})
                elif isinstance(ev, AgentRunResultEvent):
                    result = ev.result
                for a in _drain(deps):
                    yield a
        for a in _drain(deps):
            yield a
        if result is None:
            raise RuntimeError("에이전트가 결과 없이 끝났습니다.")
    except Exception as e:  # noqa: BLE001 — SSE 도중이라 예외 대신 error 이벤트로 알린다
        msg = _err_text(e)
        if user_turn:
            session.turns.append(user_turn)
            session.turns.append({"id": sessions.turn_id(), "ts": time.time(),
                                  "role": "assistant", "text": "", "html": "", "citations": [],
                                  "usage": None, "cost_usd": 0.0, "model": label, "error": msg})
            sessions.remember_papers(session, deps.papers)
            await asyncio.to_thread(sessions.save, session)
        yield _event("error", {"message": msg})
        return

    used = result.usage
    turn_usage = {"input": int(used.input_tokens or 0), "output": int(used.output_tokens or 0)}
    cost = cost_usd(model_name, Usage(input_tokens=turn_usage["input"],
                                      output_tokens=turn_usage["output"]))
    conv = result.conversation
    conv.usage = prior_usage + conv.usage
    session.conversation = conv
    session.model = label or session.model
    session.usage = {"input": session.usage.get("input", 0) + turn_usage["input"],
                     "output": session.usage.get("output", 0) + turn_usage["output"],
                     "cost_usd": round(float(session.usage.get("cost_usd", 0.0)) + cost, 6)}
    if user_turn:
        session.turns.append(user_turn)

    output = result.output
    if isinstance(output, DeferredToolRequests):
        # token 없이 부른 쓰기는 승인 뒤에도 이 논문에 쓴다 (routes가 재개 때 metadata로 넘긴다)
        session.pending_token = deps.current_token
        sessions.remember_papers(session, deps.papers)
        await asyncio.to_thread(sessions.save, session)
        yield _event("approval_needed", session.pending or {"calls": []})
        yield _event("done", {"turn_id": None, "html": "", "citations": [], "usage": turn_usage,
                              "cost_usd": round(cost, 6),
                              "session_cost_usd": session.usage["cost_usd"], "pending": True})
        return

    clean, refs = resolve_citations(str(output or ""), deps.cited_rows, deps.cited_notes,
                                    current=deps.current_token)
    html = render_html(clean, refs, deps.note_refs, current=deps.current_token)
    citations = await asyncio.to_thread(_citations, refs, deps)
    turn = {"id": sessions.turn_id(), "ts": time.time(), "role": "assistant", "text": clean,
            "html": html, "citations": citations, "usage": turn_usage,
            "cost_usd": round(cost, 6), "model": label}
    session.turns.append(turn)
    session.pending_token = None
    sessions.remember_papers(session, deps.papers)
    await asyncio.to_thread(sessions.save, session)
    yield _event("done", {"turn_id": turn["id"], "html": html, "citations": citations,
                          "usage": turn_usage, "cost_usd": turn["cost_usd"],
                          "session_cost_usd": session.usage["cost_usd"], "pending": False})
