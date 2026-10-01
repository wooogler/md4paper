"""백그라운드 작업 — 번역·레이아웃 수정·참고문헌 파싱이 탭을 옮겨도 끝까지 돈다.

NiceGUI 이벤트 핸들러는 그 페이지(클라이언트)에 묶여 있다. 논문 탭·홈 이동은 **페이지 이동**이라,
핸들러 안에서 `await run.io_bound(...)`로 돌리던 작업은 워커 스레드는 계속 돌아도 그 뒤의 마무리
(미리보기 갱신·자동 저장·완료 알림)가 사라진 페이지에서 실행되다 깨지고, 돌아온 페이지는 진행 중인
줄도 모른 채 버튼을 다시 열어 둔다 — 사용자 눈에는 '취소된' 것으로 보인다.

그래서 작업 자체는 서버 수명 레지스트리에 올려 이벤트 루프의 태스크로 돌리고, 페이지는 그 상태를
**지켜보기만** 한다. 페이지가 사라져도 작업은 끝까지 가고, 같은 논문을 다시 열면 진행 바가 그 자리에
다시 붙는다. 그래서 작업 함수(work)는 **화면 없이도 완결**돼야 한다 — 결과 파일 저장·자동 내보내기는
work 안에서 하고, 화면 갱신만 페이지가 한다.

논문(root)·종류(kind)마다 하나씩만 돈다 — 같은 논문의 번역을 두 창에서 동시에 누르면 둘째는 거절된다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Job:
    root: str  # 논문 작업 폴더 (resolve된 문자열)
    kind: str  # translate | layout | cite | terms
    label: str  # 사람이 읽는 이름 ("번역", "레이아웃 수정" …)
    title: str = ""  # 논문 제목 (헤더의 진행 목록용)
    status: str = "running"  # running | done | failed
    done: int = 0
    total: int = 0
    detail: dict = field(default_factory=dict)  # 종류별 진행 정보 (예: 번역의 섹션별 상태)
    result: Any = None
    message: str = ""  # 끝났을 때 보여 줄 한 줄 (work가 채운다)
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    seen: bool = False  # 완료를 어느 화면이든 한 번 알렸는가 — 돌아온 페이지가 다시 알리지 않게

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def fraction(self) -> float:
        return (self.done / self.total) if self.total else 0.0

    def progress(self, done: int, total: int) -> None:
        """워커 스레드에서 부르는 진행 콜백 — 숫자만 바꾼다(화면은 페이지 타이머가 그린다)."""
        self.done, self.total = done, total


_JOBS: dict[tuple[str, str], Job] = {}
_TASKS: set[asyncio.Task] = set()  # 태스크가 도중에 GC되지 않게 참조를 쥐고 있는다

_KEEP_FINISHED_S = 3600  # 끝난 작업은 한 시간 뒤 잊는다 (돌아와서 결과 알림을 볼 여유)


def _key(root: Path | str) -> str:
    return str(Path(root).resolve())


def get(root: Path | str, kind: str) -> Job | None:
    """그 논문의 그 종류 작업 — 도는 중이거나 최근에 끝난 것."""
    return _JOBS.get((_key(root), kind))


def is_running(root: Path | str, kind: str) -> bool:
    job = get(root, kind)
    return job is not None and job.running


def running(root: Path | str | None = None) -> list[Job]:
    """도는 중인 작업 — root를 주면 그 논문 것만. 시작 순."""
    k = _key(root) if root is not None else None
    return sorted((j for j in _JOBS.values() if j.running and (k is None or j.root == k)),
                  key=lambda j: j.started)


def unseen_finished(root: Path | str) -> list[Job]:
    """그 논문에서 끝났지만 아직 아무 화면도 알리지 않은 작업 (자리를 비운 사이 끝난 것)."""
    k = _key(root)
    return [j for j in _JOBS.values() if j.root == k and not j.running and not j.seen]


def start(root: Path | str, kind: str, label: str, work: Callable[[Job], Any],
          title: str = "") -> Job | None:
    """작업을 백그라운드로 띄운다. 같은 논문·종류가 이미 돌고 있으면 None.

    work(job)는 워커 스레드에서 돈다 — job.progress()로 진행을, job.message로 완료 문구를 남기고,
    반환값은 job.result가 된다. 이벤트 루프 안(UI 핸들러)에서 불러야 한다.
    """
    _prune()
    k = (_key(root), kind)
    cur = _JOBS.get(k)
    if cur is not None and cur.running:
        return None
    job = Job(root=k[0], kind=kind, label=label, title=title)
    _JOBS[k] = job
    task = asyncio.get_running_loop().create_task(_run(job, work), name=f"md4paper {kind} {k[0]}")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job


async def _run(job: Job, work: Callable[[Job], Any]) -> None:
    try:
        job.result = await asyncio.to_thread(work, job)
        job.status = "done"
    except Exception as ex:  # noqa: BLE001 — API 오류 등은 그대로 화면에 보여 준다
        job.status, job.error = "failed", str(ex) or type(ex).__name__
    finally:
        job.finished = time.time()


def _prune() -> None:
    now = time.time()
    for k, j in list(_JOBS.items()):
        if not j.running and now - j.finished > _KEEP_FINISHED_S:
            del _JOBS[k]


def reset() -> None:
    """테스트용 — 레지스트리를 비운다 (도는 태스크는 건드리지 않는다)."""
    _JOBS.clear()
