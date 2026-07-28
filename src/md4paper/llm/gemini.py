"""Gemini 어댑터 — google-genai 통합 SDK. response_schema로 구조화 출력, 암묵 캐싱 기본.

키는 클라이언트에 명시 전달한다(GOOGLE_API_KEY 우선순위 함정 회피).
"""

from __future__ import annotations

from md4paper.llm.base import Provider, T


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str | None, model: str) -> None:
        super().__init__(api_key, model)
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def _track(self, resp) -> None:
        u = getattr(resp, "usage_metadata", None)
        if u is not None:
            self.usage.add(
                getattr(u, "prompt_token_count", 0) or 0,
                getattr(u, "candidates_token_count", 0) or 0,
            )

    def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        self._track(resp)
        return resp.text or ""

    def parse(self, system: str, user: str, schema: type[T], *, max_tokens: int = 4096) -> T:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=max_tokens,
            ),
        )
        self._track(resp)
        parsed = resp.parsed
        if parsed is None:
            raise RuntimeError("Gemini 구조화 출력 파싱 실패 (거부 또는 스키마 불일치)")
        return parsed  # type: ignore[return-value]
