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

`papers`는 대화에 나온 논문 token → 작업 폴더 절대 경로. 서버의 token 등록부(`wd_by_token`)는
메모리라 재시작하면 비는데, 대화 기록에는 앞 턴의 token이 그대로 남아 모델이 그것으로 tool을
부른다. 턴을 시작할 때 routes가 이 표로 등록부를 다시 채운다. (옛 파일은 `paper_tokens`만 있고
`papers`가 없다 — 빈 표로 읽고, 다음 턴부터 채워진다.)
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
MAX_PAPERS = 500      # papers(token → 경로) 상한 — list_papers가 한 번에 수백 편을 등록할 수 있다
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
    # 대화에 나온 token → 작업 폴더 절대 경로 (화면의 논문·list_papers 결과). 재시작 뒤 재등록용.
    papers: dict[str, str] = field(default_factory=dict)
    conversation: object = field(default_factory=_empty_conversation)  # pydantic_ai.Conversation
    turns: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cost_usd": 0.0})
    # 승인을 기다리는 호출이 생길 때 화면에 열려 있던 논문. 승인은 사용자가 다른 논문으로 옮긴
    # 뒤에 올 수 있으므로, token 없이 불린 쓰기 tool은 재개 때 이 논문에 쓴다(지금 화면이 아니라).
    pending_token: str | None = None
    # 비용 tool이 띄운 백그라운드 작업 — `{id, kind, job_kind, token, root, label, title, detail,
    # started, done, status, progress, result, error, message, notified, turn_id, …}`.
    # 결과는 `sync_jobs`가 서버의 작업 레지스트리(ui/jobs.py)와 대조해 채운다.
    jobs: list[dict] = field(default_factory=list)

    @property
    def pending(self) -> dict | None:
        """승인 대기 중인 tool 호출 — `{"calls": [{id, name, args, description}], "token"}` 또는 None.

        description은 승인 카드에 보일 사람용 한 줄(`tools.describe_call`), token은 `pending_token`.
        """
        req = getattr(self.conversation, "deferred_tool_requests", None)
        if req is None:
            return None
        from md4paper.ui.agent.tools import describe_call

        calls = []
        for p in [*req.approvals, *req.calls]:
            args = p.args_as_dict()
            # 그 호출이 쓸 논문 — token 인자, 없으면 승인을 요청한 때 화면의 논문 (재개 때와 같은 규칙)
            tok = str(args.get("token") or "").strip() or self.pending_token
            calls.append({"id": p.tool_call_id, "name": p.tool_name, "args": args,
                          "description": describe_call(p.tool_name, args,
                                                       self.papers.get(tok) if tok else None)})
        return {"calls": calls, "token": self.pending_token} if calls else None

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
            "papers": s.papers, "conversation": conv, "turns": s.turns, "usage": s.usage,
            "pending_token": s.pending_token, "jobs": s.jobs}


def _from_dict(data: dict) -> Session:
    from pydantic_ai import ConversationTypeAdapter

    raw = data.get("conversation")
    # JSON 경로로 되살린다 — bytes 필드(base64)는 dump_json/validate_json 짝으로만 그대로 돈다
    conv = (ConversationTypeAdapter.validate_json(json.dumps(raw)) if isinstance(raw, dict)
            else _empty_conversation())
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    papers = data.get("papers") if isinstance(data.get("papers"), dict) else {}
    return Session(
        id=str(data["id"]), title=str(data.get("title") or ""),
        created=float(data.get("created") or time.time()),
        updated=float(data.get("updated") or time.time()),
        model=str(data.get("model") or ""),
        paper_tokens=[str(t) for t in (data.get("paper_tokens") or []) if t],
        papers={str(t): str(p) for t, p in papers.items() if t and p},
        conversation=conv,
        turns=[t for t in (data.get("turns") or []) if isinstance(t, dict)],
        usage={"input": int(usage.get("input") or 0), "output": int(usage.get("output") or 0),
               "cost_usd": float(usage.get("cost_usd") or 0.0)},
        pending_token=str(data.get("pending_token") or "") or None,
        jobs=[j for j in (data.get("jobs") or []) if isinstance(j, dict) and j.get("id")],
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


def touch_paper(session: Session, token: str | None, root: Path | str | None = None) -> None:
    """이 세션이 다룬 논문 목록에 토큰을 맨 뒤(= 최근)로 올린다 (저장은 호출자가).

    root를 주면 `papers`에도 경로를 적는다 — 재시작 뒤 이 token을 다시 등록할 수 있게.
    """
    if not token:
        return
    session.paper_tokens = [t for t in session.paper_tokens if t != token] + [token]
    if root is not None:
        remember_papers(session, {token: root})


def remember_papers(session: Session, papers: dict[str, Path | str]) -> None:
    """token → 작업 폴더 경로를 `papers`에 합친다. 새로 본 것이 뒤(= 최근)로 가고, 상한을 넘으면
    오래된 것부터 버린다 (저장은 호출자가)."""
    for tok, root in papers.items():
        if not tok or not root:
            continue
        session.papers.pop(tok, None)
        session.papers[tok] = str(Path(root).expanduser().resolve())
    while len(session.papers) > MAX_PAPERS:
        session.papers.pop(next(iter(session.papers)))


def default_title(text: str) -> str:
    """첫 질문으로 만든 기본 제목 (공백 정리, 앞 40자)."""
    t = " ".join((text or "").split())
    return t if len(t) <= TITLE_CHARS else t[:TITLE_CHARS].rstrip() + "…"


def turn_id() -> str:
    return f"t{int(time.time() * 1000):x}"


# --- 상한 ---------------------------------------------------------------


def is_turn_start(message: object) -> bool:
    """대화를 여기서 잘라도 되는 사용자 질문 메시지인가 — 요청의 **첫 부분**이 사용자 질문.

    승인 대기를 무시하고 새 질문을 보내면 요청 하나가 `[tool 결과(거절), 사용자 질문]`이 된다.
    그 요청은 앞 응답의 tool 호출에 답하는 것이라 경계가 아니다 — 거기서 자르면 tool 결과만
    남고 호출이 사라져 공급자 API가 기록을 거부한다(Pydantic AI는 조용히 결과를 버린다).
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return (isinstance(message, ModelRequest) and bool(message.parts)
            and isinstance(message.parts[0], UserPromptPart))


def _user_starts(messages: list) -> list[int]:
    """사용자 질문으로 시작하는 요청 메시지의 위치들 (턴 경계)."""
    return [i for i, m in enumerate(messages) if is_turn_start(m)]


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


# --- 백그라운드 작업 ----------------------------------------------------
# 비용 tool(jobtools)이 띄운 작업은 서버 메모리의 레지스트리(ui/jobs.py)에만 진행·결과가 있다.
# 세션 기록은 (논문, 종류, 시작 시각)으로 그 작업을 다시 찾아 상태를 옮겨 적는다. 레지스트리는 끝난
# 작업을 한 시간 뒤 잊고 서버를 다시 켜면 비므로, 못 찾은 미완료 기록은 '알 수 없음'으로 닫는다.

MAX_JOBS = 50             # 세션에 남기는 작업 기록 상한 (오래된 것부터 버린다)
LOST_JOB = "서버가 다시 시작됐거나 오래돼 작업 결과를 알 수 없습니다."


def _job_result(rec: dict, job) -> dict:  # noqa: ANN001 — ui.jobs.Job
    """레지스트리 작업 결과 → 세션에 남길 짧은 요약 (번역: 섹션 수·비용, 용어집: 항목 수·비용)."""
    res = job.result
    if rec.get("kind") == "translate" and isinstance(res, dict):
        return {"sections": int(res.get("sections") or len(rec.get("sections") or [])),
                "translated": int(res.get("ok", 0)) + int(res.get("retried", 0)),
                "failed": int(res.get("passthrough", 0)),
                "cost_usd": round(float(res.get("cost_usd") or 0.0), 6)}
    if rec.get("kind") == "glossary":
        return {"entries": int(res or 0),
                "cost_usd": round(float(job.detail.get("cost_usd") or 0.0), 6)}
    return {"cost_usd": 0.0}


def sync_jobs(session: Session) -> list[dict]:
    """미완료 작업 기록을 레지스트리와 맞춘다 (저장은 호출자가). 이번에 끝난 기록들을 돌려준다.

    진행(progress)은 매번 옮겨 적지만 '바뀜'으로 치지 않는다 — 폴링마다 세션 파일을 다시 쓰지 않게.
    끝나는 순간(done false→true) 결과 비용을 세션 누적 비용에 **한 번** 더한다. 같은 세션을 두 곳
    (턴·폴링)이 각자 읽어 고쳐도, 늦게 저장한 쪽이 이 전이를 자기 사본에서 다시 하므로 한 번만 남는다.
    """
    from md4paper.ui import jobs as registry

    finished: list[dict] = []
    for rec in session.jobs:
        if rec.get("done"):
            continue
        job = registry.get(rec.get("root") or "", rec.get("job_kind") or rec.get("kind") or "")
        if job is None or abs(float(job.started) - float(rec.get("started") or 0)) > 1e-3:
            rec.update(done=True, status="failed", error=LOST_JOB)
            finished.append(rec)
            continue
        rec["progress"] = [int(job.done), int(job.total)]
        if job.running:
            continue
        rec.update(done=True, status=job.status, message=job.message or "")
        if job.status == "done":
            rec["result"] = _job_result(rec, job)
            cost = float(rec["result"].get("cost_usd") or 0.0)
            if cost:
                session.usage["cost_usd"] = round(float(session.usage.get("cost_usd") or 0.0) + cost, 6)
        else:
            rec["error"] = job.error or "알 수 없는 오류"
        finished.append(rec)
    return finished


def add_jobs(session: Session, records: list[dict], turn_id: str | None) -> None:
    """run이 띄운 작업 기록을 세션에 붙인다 — 어느 턴 뒤에 그릴지(turn_id)를 적어서."""
    for rec in records:
        rec["turn_id"] = turn_id
        session.jobs.append(rec)
    if len(session.jobs) > MAX_JOBS:
        session.jobs = session.jobs[-MAX_JOBS:]
