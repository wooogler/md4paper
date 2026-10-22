"""에이전트 테스트 공용 — 임시 논문 WorkDir, 토큰 레지스트리, 대본대로 움직이는 FunctionModel.

네트워크 없이 Pydantic AI 에이전트를 돌리려고 `FunctionModel`을 쓴다. `run_stream_events`는
스트리밍 경로라 `stream_function`이 꼭 있어야 한다(없으면 AssertionError).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from md4paper.workdir import WorkDir

EN = """# Bespoke Bots

We study how instructors customize chatbots.

## 2 Method

We interviewed 18 instructors at three universities.

Interviews lasted 45 minutes each.

## 3 Results

Instructors wanted control over the tone of generated answers.
"""

KO = """# 맞춤형 봇

우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다.

## 2 방법

세 개 대학의 교수자 18명을 인터뷰했다.

인터뷰는 각각 45분씩 진행했다.

## 3 결과

교수자들은 생성된 답변의 어투를 통제하고 싶어 했다.
"""


def make_wd(base: Path, name: str = "p", en: str = EN, ko: str | None = KO,
            title: str = "Bespoke Bots") -> WorkDir:
    """뷰어가 여는 것과 같은 모양의 작업 디렉토리 (list_papers가 찾도록 sections.yaml 포함)."""
    wd = WorkDir(base / f"{name}.md4")
    wd.ensure()
    wd.out.mkdir(parents=True, exist_ok=True)
    wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.structure.mkdir(parents=True, exist_ok=True)
    wd.sections_yaml.write_text(f"title: {title}\n", encoding="utf-8")
    return wd


def item(aid: str, row: int, quote: str, note: str = "", side: str = "en",
         color: str = "yellow") -> dict:
    """annotations.json 항목 하나 (v2 — 앵커 목록 구조)."""
    return {"id": aid, "color": color, "note": note,
            "anchors": [{"side": side, "row": row, "start": 0, "end": len(quote),
                         "quote": quote, "prefix": "", "suffix": ""}]}


def write_notes(wd: WorkDir, items: list[dict]) -> None:
    wd.annotations_json.write_text(json.dumps({"items": items}), encoding="utf-8")


class Registry:
    """app의 state["wd_by_token"] 흉내 — 등록한 논문만 찾는다 (폴백 없음)."""

    def __init__(self, *wds: WorkDir) -> None:
        self.by_token: dict[str, WorkDir] = {}
        for wd in wds:
            self.register(wd)

    def register(self, wd: WorkDir) -> str:
        from md4paper.ui.agent.tools import token_of

        tok = token_of(wd.root)
        self.by_token[tok] = wd
        return tok

    def wd_for(self, token: str) -> WorkDir | None:
        return self.by_token.get(token)


# --- 대본 모델 ----------------------------------------------------------
# 대본의 한 단계 = tool 호출 목록 [(이름, 인자)] 또는 최종 답 문자열.
# 앞 tool 결과를 봐야 하는 단계는 messages → (목록 | 문자열) 함수로 준다.

Step = list[tuple[str, dict]] | str | Callable[[list], "list[tuple[str, dict]] | str"]


def _step_index(messages: list) -> int:
    """이번 질문(마지막 사용자 메시지) 뒤로 모델이 몇 번 답했는지 = 대본의 몇 번째 단계인지."""
    last_user = max((i for i, m in enumerate(messages) if isinstance(m, ModelRequest)
                     and any(isinstance(p, UserPromptPart) for p in m.parts)), default=0)
    return sum(1 for m in messages[last_user:] if isinstance(m, ModelResponse))


def tool_returns(messages: list) -> list[str]:
    """지금까지의 tool 결과 문자열들 (대본에서 인용할 행을 고를 때)."""
    out = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if getattr(p, "part_kind", "") == "tool-return":
                    out.append(str(p.content))
    return out


def scripted_model(steps: list[Step], model_name: str = "scripted",
                   seen: list | None = None) -> FunctionModel:
    """대본대로 움직이는 모델. `seen`을 주면 호출마다 모델이 받은 AgentInfo를 쌓는다."""

    def pick(messages: list) -> list[tuple[str, dict]] | str:
        step = steps[min(_step_index(messages), len(steps) - 1)]
        return step(messages) if callable(step) else step

    def call_id(messages: list, k: int) -> str:
        return f"c{_step_index(messages)}_{k}"

    def function(messages: list, info: AgentInfo) -> ModelResponse:
        step = pick(messages)
        if isinstance(step, list):
            return ModelResponse(parts=[ToolCallPart(n, a, tool_call_id=call_id(messages, k))
                                        for k, (n, a) in enumerate(step)])
        return ModelResponse(parts=[TextPart(step)])

    async def stream_function(messages: list, info: AgentInfo):  # noqa: ANN202
        if seen is not None:
            seen.append(info)
        step = pick(messages)
        if isinstance(step, list):
            yield {k: DeltaToolCall(name=n, json_args=json.dumps(a),
                                    tool_call_id=call_id(messages, k))
                   for k, (n, a) in enumerate(step)}
            return
        half = len(step) // 2
        yield step[:half]
        yield step[half:]

    return FunctionModel(function, stream_function=stream_function, model_name=model_name)
