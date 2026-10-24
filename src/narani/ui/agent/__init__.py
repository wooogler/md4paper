"""에이전트 창 백엔드 — 전역 세션, 여러 논문을 읽는 tool, 스트리밍 턴, 다중 논문 인용.

뷰어 챗봇(`ui/chat.py`, 논문당 단발 RAG)을 대신한다. 하네스는 Pydantic AI(openai·anthropic·gemini
네이티브, tool 호출·스트리밍·대화 직렬화·승인 중단/재개). 화면(`ui/agent_panel.py`)과는 SSE 라우트
(`routes.register_routes`)로만 만난다.
"""

from narani.ui.agent.agent import (
    DEFAULT_LIMITS,
    SYSTEM,
    build_agent,
    build_model,
    default_agent,
    make_agent,
    memory_text,
    model_label,
    run_turn,
)
from narani.ui.agent.cites import note_tag, render_html, resolve_citations, row_label
from narani.ui.agent.context import ScreenContext, to_prompt
from narani.ui.agent.routes import model_options, readiness, register_routes
from narani.ui.agent.sessions import Session
from narani.ui.agent.tools import TOOLS, Deps, review_url, token_of

__all__ = [
    "DEFAULT_LIMITS", "SYSTEM", "TOOLS", "Deps", "ScreenContext", "Session",
    "build_agent", "build_model", "default_agent", "make_agent", "memory_text", "model_label",
    "model_options", "note_tag", "readiness", "register_routes", "render_html",
    "resolve_citations", "review_url", "row_label", "run_turn", "to_prompt", "token_of",
]
