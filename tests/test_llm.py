"""LLM 어댑터 베이스 테스트 — 비용, 팩토리, fake 프로바이더 (실제 API 호출 없음)."""

import pytest
from pydantic import BaseModel

from md4paper.llm import FakeProvider, Usage, cost_usd, get_provider


class Out(BaseModel):
    value: int


def test_cost_usd():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # gpt-5.6-luna = $1/$6
    assert cost_usd("gpt-5.6-luna", u) == pytest.approx(7.0)
    # 미등록 모델은 0
    assert cost_usd("unknown-model", u) == 0.0


def test_get_provider_dispatch():
    # 실제 네트워크 호출 없이 인스턴스 생성만 확인 (더미 키)
    for name, model in [("openai", "gpt-5.6-luna"), ("anthropic", "claude-sonnet-5"), ("gemini", "gemini-3.1-pro")]:
        p = get_provider(name, api_key="dummy", model=model)
        assert p.name == name and p.model == model
    with pytest.raises(ValueError):
        get_provider("nonexistent")


def test_fake_provider_parse():
    fake = FakeProvider(parse_fn=lambda s, u, schema: schema(value=42))
    out = fake.parse("sys", "user", Out)
    assert out.value == 42
    assert fake.usage.input_tokens > 0


def test_fake_provider_complete():
    fake = FakeProvider(complete_fn=lambda s, u: f"echo:{u}")
    assert fake.complete("sys", "hello") == "echo:hello"
