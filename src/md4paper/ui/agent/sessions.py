"""에이전트 대화 세션 저장 — 전역(논문을 가로지르는) 세션, 파일 하나에 세션 하나.

세션은 홈·여러 논문을 오가며 이어지므로 논문 폴더(`.md4/chat.json`)가 아니라 설정 폴더에 둔다:

```
CONFIG_DIR/agent/sessions/<sid>.json   ← 세션 본문
CONFIG_DIR/agent/sessions.json         ← 목록 (id, 제목, 갱신 시각, 마지막 논문 …)
```

본문에는 두 가지 기록이 함께 있다:
- `conversation` — Pydantic AI의 `Conversation`을 그대로 직렬화한 것. 모델에 다시 넣을 메시지,
  누적 usage, **승인 대기 중인 tool 호출**(deferred_tool_requests)까지 담겨 앱을 다시 켜도 이어진다.
- `turns` — 화면에 그릴 말풍선(질문 원문, 렌더된 답 HTML, 인용, 비용). 메시지 목록에서 다시 만들
  수도 있지만 인용 칩 HTML은 답을 낸 그때의 허용 집합으로만 정확히 만들 수 있어 따로 둔다.

저장은 tmp→replace로 원자적이고 프로세스 안에서는 락을 건다. 여러 창이 같은 세션에 쓰면
마지막 저장이 이긴다(손상만 막는다). 상한(MAX_TURNS)을 넘으면 오래된 턴을 요약 없이 버린다.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from md4paper import config

VERSION = 1
MAX_TURNS = 200       # 사용자 질문 기준 상한 — 넘으면 앞부분을 잘라낸다 (요약하지 않는다)
TITLE_CHARS = 40      # 기본 제목 = 첫 질문 앞 40자

_lock = threading.RLock()
_SID_RE = re.compile(r"^s[0-9a-f]{6,16}(?:-\d{1,3})?$")   # 경로로 쓰이므로 형식을 엄격히


def agent_dir() -> Path:
    # 지연 계산 — 테스트가 CONFIG_DIR를 바꿔도 따라가게
    return Path(config.CONFIG_DIR) / "agent"


def sessions_dir() -> Path:
    return agent_dir() / "sessions"


def index_path() -> Path:
    return agent_dir() / "sessions.json"


def memory_path() -> Path:
    """장기 메모리 파일 (사람이 읽고 고치는 마크다운). 시스템 지시문에 통째로 들어간다."""
    return agent_dir() / "memory.md"


def _empty_conversation():  # noqa: ANN202 — pydantic_ai.Conversation (지연 import)
    from pydantic_ai import Conversation

    return Conversation()


@dataclass
class Session:
    id: str
    title: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    model: str = ""                                   # "openai:gpt-6-luna" — 마지막 턴의 모델
    paper_tokens: list[str] = field(default_factory=list)   # 다룬 논문들 (마지막 = 최근)
    conversation: object = field(default_factory=_empty_conversation)  # pydantic_ai.Conversation
    turns: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cost_usd": 0.0})

    @property
    def pending(self) -> dict | None:
        """승인 대기 중인 tool 호출 — `{"calls": [{id, name, args}]}` 또는 None."""
        req = getattr(self.conversation, "deferred_tool_requests", None)
        if req is None:
            return None
        calls = [{"id": p.tool_call_id, "name": p.tool_name, "args": p.args_as_dict()}
                 for p in [*req.approvals, *req.calls]]
        return {"calls": calls} if calls else None

    def meta(self) -> dict:
        """목록에 실을 요약."""
        return {"id": self.id, "title": self.title, "created": self.created,
                "updated": self.updated, "model": self.model,
                "last_paper": self.paper_tokens[-1] if self.paper_tokens else None,
                "turns": sum(1 for t in self.turns if t.get("role") == "user"),
                "cost_usd": round(float(self.usage.get("cost_usd", 0.0)), 6)}


# --- (역)직렬화 ---------------------------------------------------------


def _to_dict(s: Session) -> dict:
    from pydantic_ai import ConversationTypeAdapter

    conv = json.loads(ConversationTypeAdapter.dump_json(s.conversation))
    return {"version": VERSION, "id": s.id, "title": s.title, "created": s.created,
            "updated": s.updated, "model": s.model, "paper_tokens": s.paper_tokens,
            "conversation": conv, "turns": s.turns, "usage": s.usage}


def _from_dict(data: dict) -> Session:
    from pydantic_ai import ConversationTypeAdapter

    raw = data.get("conversation")
    # JSON 경로로 되살린다 — bytes 필드(base64)는 dump_json/validate_json 짝으로만 그대로 돈다
    conv = (ConversationTypeAdapter.validate_json(json.dumps(raw)) if isinstance(raw, dict)
            else _empty_conversation())
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return Session(
        id=str(data["id"]), title=str(data.get("title") or ""),
        created=float(data.get("created") or time.time()),
        updated=float(data.get("updated") or time.time()),
        model=str(data.get("model") or ""),
        paper_tokens=[str(t) for t in (data.get("paper_tokens") or []) if t],
        conversation=conv,
        turns=[t for t in (data.get("turns") or []) if isinstance(t, dict)],
        usage={"input": int(usage.get("input") or 0), "output": int(usage.get("output") or 0),
               "cost_usd": float(usage.get("cost_usd") or 0.0)},
    )


def _write_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)   # 원자적 교체 — 저장 중 종료돼도 반쪽 파일이 남지 않는다


def _path(sid: str) -> Path | None:
    return sessions_dir() / f"{sid}.json" if _SID_RE.match(sid or "") else None


# --- 목록 ---------------------------------------------------------------


def _read_index() -> list[dict] | None:
    try:
        data = json.loads(index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get("sessions") if isinstance(data, dict) else None
    return [m for m in items if isinstance(m, dict) and m.get("id")] if isinstance(items, list) \
        else None


def _rebuild_index() -> list[dict]:
    """목록 파일이 없거나 깨졌으면 세션 파일들로 다시 만든다."""
    metas = []
    for p in sorted(sessions_dir().glob("s*.json")) if sessions_dir().is_dir() else []:
        s = load(p.stem)
        if s is not None:
            metas.append(s.meta())
    _write_index(metas)
    return metas


def _write_index(metas: list[dict]) -> None:
    metas = sorted(metas, key=lambda m: -float(m.get("updated") or 0))
    _write_atomic(index_path(), {"version": VERSION, "sessions": metas})


def _upsert_index(meta: dict | None, drop: str | None = None) -> None:
    metas = _read_index()
    if metas is None:
        metas = _rebuild_index()
    key = (meta or {}).get("id") or drop
    metas = [m for m in metas if m.get("id") != key]
    if meta is not None:
        metas.append(meta)
    _write_index(metas)


def list_sessions() -> list[dict]:
    """세션 요약 목록 — 최근 갱신순."""
    with _lock:
        metas = _read_index()
        if metas is None:
            metas = _rebuild_index() if sessions_dir().is_dir() else []
        return sorted(metas, key=lambda m: -float(m.get("updated") or 0))


# --- CRUD ---------------------------------------------------------------


def new_id() -> str:
    """`s<ms hex>` — 같은 밀리초에 둘이 생기면 `-2`, `-3`을 붙인다."""
    base = f"s{int(time.time() * 1000):x}"
    sid, i = base, 2
    while (sessions_dir() / f"{sid}.json").exists():
        sid, i = f"{base}-{i}", i + 1
    return sid


def create(title: str | None = None, model: str | None = None) -> Session:
    """새 빈 세션을 만들어 저장한다."""
    with _lock:
        s = Session(id=new_id(), title=(title or "").strip()[:200], model=model or "")
        save(s)
        return s


def load(sid: str) -> Session | None:
    """세션 하나. 없거나 깨졌으면 None."""
    path = _path(sid)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _from_dict(data) if isinstance(data, dict) and data.get("id") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(session: Session) -> Session:
    """잘라내기 → 원자적 저장 → 목록 갱신."""
    path = _path(session.id)
    if path is None:
        raise ValueError(f"잘못된 세션 id: {session.id!r}")
    with _lock:
        trim(session)
        session.updated = time.time()
        _write_atomic(path, _to_dict(session))
        _upsert_index(session.meta())
    return session


def rename(sid: str, title: str) -> Session | None:
    with _lock:
        s = load(sid)
        if s is None:
            return None
        s.title = (title or "").strip()[:200]
        return save(s)


def delete(sid: str) -> bool:
    with _lock:
        path = _path(sid)
        if path is None:
            return False
        existed = path.exists()
        path.unlink(missing_ok=True)
        _upsert_index(None, drop=sid)
        return existed


def touch_paper(session: Session, token: str | None) -> None:
    """이 세션이 다룬 논문 목록에 토큰을 맨 뒤(= 최근)로 올린다 (저장은 호출자가)."""
    if not token:
        return
    session.paper_tokens = [t for t in session.paper_tokens if t != token] + [token]


def default_title(text: str) -> str:
    """첫 질문으로 만든 기본 제목 (공백 정리, 앞 40자)."""
    t = " ".join((text or "").split())
    return t if len(t) <= TITLE_CHARS else t[:TITLE_CHARS].rstrip() + "…"


def turn_id() -> str:
    return f"t{int(time.time() * 1000):x}"


# --- 상한 ---------------------------------------------------------------


def _user_starts(messages: list) -> list[int]:
    """사용자 질문으로 시작하는 요청 메시지의 위치들 (턴 경계)."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return [i for i, m in enumerate(messages)
            if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)]


def trim(session: Session, limit: int | None = None) -> None:
    """질문이 limit개를 넘으면 오래된 턴을 통째로 버린다 (메시지·표시 기록 양쪽).

    메시지는 **사용자 질문 경계에서만** 자른다 — tool 호출을 잘라 놓고 그 결과만 남기면
    공급자 API가 기록을 거부한다. 승인 대기 중인 호출은 맨 끝에 있으니 살아남는다.
    """
    limit = MAX_TURNS if limit is None else limit
    conv = session.conversation
    msgs = list(getattr(conv, "messages", []) or [])
    starts = _user_starts(msgs)
    if len(starts) > limit:
        conv.messages = msgs[starts[len(starts) - limit]:]
    users = [i for i, t in enumerate(session.turns) if t.get("role") == "user"]
    if len(users) > limit:
        session.turns = session.turns[users[len(users) - limit]:]
