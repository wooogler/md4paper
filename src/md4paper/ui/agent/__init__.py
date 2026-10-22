"""에이전트 창 백엔드 — 전역 세션, 여러 논문을 읽는 tool, 스트리밍 턴, 다중 논문 인용.

뷰어 챗봇(`ui/chat.py`, 논문당 단발 RAG)을 대신한다. 하네스는 Pydantic AI(openai·anthropic·gemini
네이티브, tool 호출·스트리밍·대화 직렬화·승인 중단/재개). 화면(`ui/agent_panel.py`)과는 SSE 라우트
(`routes.register_routes`)로만 만난다.
"""
