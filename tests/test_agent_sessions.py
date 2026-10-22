"""에이전트 세션 저장 — CRUD, 원자적 저장, Conversation 왕복, 상한 잘라내기."""

from __future__ import annotations

import json
import shutil

import pytest

pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from pydantic_ai import Conversation, DeferredToolRequests  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage  # noqa: E402

from md4paper.ui.agent import sessions  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_agent_dir():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)


def _exchange(i: int) -> list:
    """질문 하나 = 사용자 요청 → tool 호출 → tool 결과 → 답 (4개 메시지)."""
    return [
        ModelRequest(parts=[UserPromptPart(f"q{i}")]),
        ModelResponse(parts=[ToolCallPart("search_paper", {"query": f"q{i}"},
                                          tool_call_id=f"c{i}")]),
        ModelRequest(parts=[ToolReturnPart("search_paper", f"r{i}", tool_call_id=f"c{i}")]),
        ModelResponse(parts=[TextPart(f"a{i}")]),
    ]


def test_create_list_load_rename_delete():
    a = sessions.create()
    b = sessions.create(title="두 번째", model="openai:gpt-6-luna")
    assert a.id.startswith("s") and a.id != b.id
    assert (sessions.sessions_dir() / f"{a.id}.json").exists()
    ids = [m["id"] for m in sessions.list_sessions()]
    assert set(ids) == {a.id, b.id}
    assert sessions.load(b.id).title == "두 번째" and sessions.load(b.id).model.startswith("openai")

    renamed = sessions.rename(a.id, "  새 이름 ")
    assert renamed.title == "새 이름"
    assert sessions.list_sessions()[0]["id"] == a.id      # 방금 고친 것이 맨 위
    assert sessions.rename("s000000", "x") is None

    assert sessions.delete(a.id) is True
    assert sessions.load(a.id) is None and sessions.delete(a.id) is False
    assert [m["id"] for m in sessions.list_sessions()] == [b.id]


def test_bad_ids_never_touch_the_filesystem():
    for bad in ("../x", "s../../etc", "", "abc", "s12/34"):
        assert sessions.load(bad) is None
        assert sessions.delete(bad) is False


def test_save_is_atomic_and_leaves_no_tmp():
    s = sessions.create()
    s.turns.append({"id": "t1", "role": "user", "text": "안녕"})
    sessions.save(s)
    files = sorted(p.name for p in sessions.agent_dir().rglob("*"))
    assert not any(n.endswith(".tmp") for n in files)
    data = json.loads((sessions.sessions_dir() / f"{s.id}.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["turns"][0]["text"] == "안녕"
    assert isinstance(data["conversation"], dict) and "messages" in data["conversation"]


def test_index_rebuilds_when_missing_or_broken():
    s = sessions.create(title="남는 세션")
    sessions.index_path().write_text("{망가짐", encoding="utf-8")
    assert [m["id"] for m in sessions.list_sessions()] == [s.id]
    sessions.index_path().unlink()
    assert [m["title"] for m in sessions.list_sessions()] == ["남는 세션"]


def test_conversation_roundtrip_with_usage_and_pending():
    s = sessions.create()
    call = ToolCallPart("write_note", {"text": "x"}, tool_call_id="w1")
    s.conversation = Conversation(
        messages=_exchange(0) + [ModelRequest(parts=[UserPromptPart("메모 써 줘")]),
                                 ModelResponse(parts=[call])],
        usage=RunUsage(input_tokens=120, output_tokens=30, requests=3),
        deferred_tool_requests=DeferredToolRequests(approvals=[call]))
    sessions.touch_paper(s, "0123456789ab")
    sessions.touch_paper(s, "fedcba987654")
    sessions.touch_paper(s, "0123456789ab")
    s.pending_token = "0123456789ab"
    sessions.save(s)

    back = sessions.load(s.id)
    assert len(back.conversation.messages) == 6
    assert back.conversation.conversation_id == s.conversation.conversation_id
    assert back.conversation.usage.input_tokens == 120
    # 승인 카드용 한 줄 설명과, 승인을 요청한 때 화면의 논문이 함께 돈다
    assert back.pending == {"calls": [{"id": "w1", "name": "write_note", "args": {"text": "x"},
                                       "description": "'메모' 탭 끝에 1줄 추가"}],
                            "token": "0123456789ab"}
    assert back.paper_tokens == ["fedcba987654", "0123456789ab"]
    assert sessions.list_sessions()[0]["last_paper"] == "0123456789ab"


def test_papers_map_roundtrip_and_old_files_without_it(tmp_path, monkeypatch):
    s = sessions.create()
    sessions.touch_paper(s, "0123456789ab", tmp_path / "a.md4")
    sessions.touch_paper(s, "fedcba987654")                  # 경로를 모르면 목록만 갱신
    sessions.remember_papers(s, {"aaaaaaaaaaaa": tmp_path / "b.md4", "": tmp_path / "x"})
    sessions.save(s)
    back = sessions.load(s.id)
    assert back.papers == {"0123456789ab": str((tmp_path / "a.md4").resolve()),
                           "aaaaaaaaaaaa": str((tmp_path / "b.md4").resolve())}
    assert back.paper_tokens == ["0123456789ab", "fedcba987654"]

    # 옛 세션 파일 — paper_tokens만 있고 papers가 없다
    path = sessions.sessions_dir() / f"{s.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["papers"]
    path.write_text(json.dumps(data), encoding="utf-8")
    old = sessions.load(s.id)
    assert old.papers == {} and old.paper_tokens == ["0123456789ab", "fedcba987654"]

    # 상한을 넘으면 오래된 것부터 버리고, 다시 본 것은 뒤(최근)로 간다
    monkeypatch.setattr(sessions, "MAX_PAPERS", 2)
    sessions.remember_papers(old, {"t1": tmp_path / "1", "t2": tmp_path / "2"})
    sessions.remember_papers(old, {"t1": tmp_path / "1", "t3": tmp_path / "3"})
    assert list(old.papers) == ["t1", "t3"]


def test_trim_cuts_whole_turns_at_user_boundaries(monkeypatch):
    s = sessions.create()
    msgs = []
    for i in range(5):
        msgs += _exchange(i)
        s.turns += [{"id": f"u{i}", "role": "user", "text": f"q{i}"},
                    {"id": f"a{i}", "role": "assistant", "text": f"a{i}"}]
    s.conversation = Conversation(messages=msgs)
    sessions.trim(s, limit=2)
    kept = s.conversation.messages
    assert len(kept) == 8
    assert isinstance(kept[0], ModelRequest) and kept[0].parts[0].content == "q3"
    assert [t["id"] for t in s.turns] == ["u3", "a3", "u4", "a4"]

    # save()도 같은 상한을 건다
    monkeypatch.setattr(sessions, "MAX_TURNS", 1)
    s2 = sessions.create()
    s2.conversation = Conversation(messages=msgs)
    sessions.save(s2)
    assert len(sessions.load(s2.id).conversation.messages) == 4


def test_default_title_is_first_40_chars():
    assert sessions.default_title("  짧은   질문 ") == "짧은 질문"
    long = "가" * 50
    assert sessions.default_title(long) == "가" * 40 + "…"
