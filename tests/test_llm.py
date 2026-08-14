"""LLM 어댑터 베이스 테스트 — 비용, 팩토리, fake 프로바이더 (실제 API 호출 없음)."""

import pytest
from pydantic import BaseModel

from md4paper import config
from md4paper.llm import FakeProvider, Usage, cost_usd, get_provider
from md4paper.llm.base import PRICING


class Out(BaseModel):
    value: int


def test_cost_usd():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # gpt-5.6-luna = $0.2/$1.2
    assert cost_usd("gpt-5.6-luna", u) == pytest.approx(1.4)
    # 미등록 모델은 0
    assert cost_usd("unknown-model", u) == 0.0


def test_get_provider_dispatch():
    # 실제 네트워크 호출 없이 인스턴스 생성만 확인 (더미 키)
    for name, model in [
        ("openai", "gpt-5.6-luna"),
        ("anthropic", "claude-sonnet-5"),
        ("gemini", "gemini-3.1-pro-preview"),
    ]:
        p = get_provider(name, api_key="dummy", model=model)
        assert p.name == name and p.model == model
    with pytest.raises(ValueError):
        get_provider("nonexistent")


def test_get_provider_defaults_match_config():
    # 모델 미지정 시 기본값은 config 한 곳에서만 와야 한다 (예전엔 어긋나 있었다)
    for name, model in config.DEFAULT_MODELS.items():
        assert get_provider(name, api_key="dummy").model == model


def test_model_tiers_are_priced_and_ascending():
    # UI가 tier 순서를 '저렴 → 비쌈'으로 표시하고, 미등록 모델은 비용이 0으로 나온다
    for provider, tiers in config.MODEL_TIERS.items():
        costs = []
        for model in tiers:
            assert model in PRICING, f"{provider}: {model} 단가 미등록"
            inp, out = PRICING[model]
            costs.append((inp, out))
        assert costs == sorted(costs), f"{provider}: tier가 가격 오름차순이 아니다 — {tiers}"


def test_fake_provider_parse():
    fake = FakeProvider(parse_fn=lambda s, u, schema: schema(value=42))
    out = fake.parse("sys", "user", Out)
    assert out.value == 42
    assert fake.usage.input_tokens > 0


def test_fake_provider_complete():
    fake = FakeProvider(complete_fn=lambda s, u: f"echo:{u}")
    assert fake.complete("sys", "hello") == "echo:hello"
