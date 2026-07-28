"""LLM 프로바이더 어댑터 — Anthropic / OpenAI / Gemini 공통 인터페이스.

번역·citation 파싱·용어집 추출이 이 얇은 어댑터를 공유한다. 세 SDK 모두 pydantic 모델을
직접 받아 파싱 인스턴스를 반환하므로 어댑터는 complete/parse 두 메서드짜리 Protocol이다.
"""

from md4paper.llm.base import (
    FakeProvider,
    Provider,
    Usage,
    cost_usd,
    get_provider,
)

__all__ = ["FakeProvider", "Provider", "Usage", "cost_usd", "get_provider"]
