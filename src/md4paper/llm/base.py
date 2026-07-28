"""프로바이더 공통 인터페이스 + 비용 추적 + 팩토리.

각 어댑터는 complete(자유 텍스트)와 parse(pydantic 구조화 출력)를 구현하고 usage를 누적한다.
프로바이더별 노브(캐싱, thinking)는 어댑터 안에 격리된다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# $/1M 토큰 (input, output) — 2026-07 기준. 인트로 가격/변동 있으니 재검증 대상.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (1.0, 6.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-3.1-pro": (2.0, 12.0),
    "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.1-flash-lite": (0.1, 0.4),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # 섹션 병렬 번역 시 여러 스레드가 동시에 누적하므로 락으로 보호
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, inp: int, out: int) -> None:
        with self._lock:
            self.input_tokens += inp
            self.output_tokens += out


def cost_usd(model: str, usage: Usage) -> float:
    """모델·토큰 사용량 → 대략 비용(USD). 미등록 모델은 0."""
    price = PRICING.get(model)
    if not price:
        return 0.0
    inp, out = price
    return usage.input_tokens / 1_000_000 * inp + usage.output_tokens / 1_000_000 * out


class Provider:
    """모든 어댑터의 베이스. usage 누적 + 비용 계산 제공."""

    name = "base"

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.usage = Usage()

    def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        raise NotImplementedError

    def parse(self, system: str, user: str, schema: type[T], *, max_tokens: int = 4096) -> T:
        raise NotImplementedError

    def cost(self) -> float:
        return cost_usd(self.model, self.usage)


class FakeProvider(Provider):
    """테스트용 — 실제 네트워크 호출 없이 미리 정한 응답을 반환한다."""

    name = "fake"

    def __init__(
        self,
        complete_fn: Callable[[str, str], str] | None = None,
        parse_fn: Callable[[str, str, type[BaseModel]], BaseModel] | None = None,
        model: str = "fake-model",
    ) -> None:
        super().__init__(api_key=None, model=model)
        self._complete_fn = complete_fn
        self._parse_fn = parse_fn

    def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        if self._complete_fn is None:
            raise RuntimeError("FakeProvider에 complete_fn이 설정되지 않았습니다.")
        self.usage.add(len(system) + len(user), 0)
        return self._complete_fn(system, user)

    def parse(self, system: str, user: str, schema: type[T], *, max_tokens: int = 4096) -> T:
        if self._parse_fn is None:
            raise RuntimeError("FakeProvider에 parse_fn이 설정되지 않았습니다.")
        self.usage.add(len(system) + len(user), 0)
        return self._parse_fn(system, user, schema)  # type: ignore[return-value]


def get_provider(name: str, api_key: str | None = None, model: str | None = None) -> Provider:
    """프로바이더 이름 → 어댑터 인스턴스 (어댑터 모듈을 지연 임포트)."""
    if name == "openai":
        from md4paper.llm.openai import OpenAIProvider

        return OpenAIProvider(api_key, model or "gpt-5.6-luna")
    if name == "anthropic":
        from md4paper.llm.anthropic import AnthropicProvider

        return AnthropicProvider(api_key, model or "claude-sonnet-5")
    if name == "gemini":
        from md4paper.llm.gemini import GeminiProvider

        return GeminiProvider(api_key, model or "gemini-3.1-pro")
    raise ValueError(f"알 수 없는 프로바이더: {name}")
