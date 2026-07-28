"""Anthropic 어댑터 — messages.parse 구조화 출력. thinking 비활성이 기본."""

from __future__ import annotations

from md4paper.llm.base import Provider, T


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str) -> None:
        super().__init__(api_key, model)
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def _track(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage.add(getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)

    def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        self._track(resp)
        return "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )

    def parse(self, system: str, user: str, schema: type[T], *, max_tokens: int = 4096) -> T:
        resp = self._client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        self._track(resp)
        parsed = resp.parsed_output
        if parsed is None:
            raise RuntimeError("Anthropic 구조화 출력 파싱 실패 (거부 또는 스키마 불일치)")
        return parsed
