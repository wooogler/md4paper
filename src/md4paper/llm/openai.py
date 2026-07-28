"""OpenAI 어댑터 — Responses API (신규 프로젝트 권장 표면). 프리픽스 캐싱은 자동."""

from __future__ import annotations

from md4paper.llm.base import Provider, T


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str | None, model: str) -> None:
        super().__init__(api_key, model)
        import openai

        self._client = openai.OpenAI(api_key=api_key)

    def _track(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage.add(getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)

    def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        resp = self._client.responses.create(
            model=self.model,
            instructions=system,
            input=user,
            max_output_tokens=max_tokens,
        )
        self._track(resp)
        return resp.output_text

    def parse(self, system: str, user: str, schema: type[T], *, max_tokens: int = 4096) -> T:
        resp = self._client.responses.parse(
            model=self.model,
            instructions=system,
            input=user,
            text_format=schema,
            max_output_tokens=max_tokens,
        )
        self._track(resp)
        parsed = resp.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI 구조화 출력 파싱 실패 (거부 또는 스키마 불일치)")
        return parsed
