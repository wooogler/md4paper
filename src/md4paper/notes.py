"""논문 메모 탭 — 왼쪽 레일 '메모' 패널의 여러 메모(요약·질문·아이디어 …).

탭 하나가 마크다운 파일 하나다. 첫 탭은 예전부터 쓰던 `notes.md`(작업 디렉토리 맨 위)를 그대로 쓰고,
더 만든 탭은 `notes/<이름>.md`에 둔다. 탭 이름과 순서는 `notes/_tabs.json`에 적는다 — 이 파일이 없으면
탭은 `notes.md` 하나("메모")뿐이라, 탭을 만들기 전의 논문·내보내기·도구는 아무것도 바뀌지 않는다.

상태는 파일뿐이다(PLAN.md: UI는 별도 데이터 모델을 갖지 않는다). 빈 탭은 파일 없이 목록에만 남는다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from md4paper.workdir import WorkDir

DEFAULT_NAME = "메모"
_MAIN_FILE = "notes.md"           # 첫 탭 — 탭을 도입하기 전부터 쓰던 자리
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_NAME = 40


def _dir(wd: WorkDir) -> Path:
    return wd.root / "notes"


def _index(wd: WorkDir) -> Path:
    return _dir(wd) / "_tabs.json"


def clean_name(name: str) -> str:
    """탭 이름 정리 — 파일 이름에 못 쓰는 글자를 빼고 공백을 하나로, 앞의 점을 떼고 40자까지."""
    n = _BAD.sub("", name or "")
    n = re.sub(r"\s+", " ", n).strip().lstrip(".").strip()
    return n[:_MAX_NAME].strip()


def _load(wd: WorkDir) -> list[dict]:
    """[{name, file}] — file은 작업 디렉토리 기준 상대 경로. 목록 파일이 없거나 깨졌으면 기본 탭 하나."""
    try:
        data = json.loads(_index(wd).read_text(encoding="utf-8"))
        tabs = [t for t in data.get("tabs", [])
                if isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("file"), str)]
    except (OSError, json.JSONDecodeError, AttributeError):
        tabs = []
    return tabs or [{"name": DEFAULT_NAME, "file": _MAIN_FILE}]


def _save(wd: WorkDir, tabs: list[dict]) -> None:
    _dir(wd).mkdir(parents=True, exist_ok=True)
    _write_atomic(_index(wd), json.dumps({"tabs": tabs}, ensure_ascii=False, indent=2) + "\n")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _path(wd: WorkDir, tab: dict) -> Path:
    p = (wd.root / tab["file"]).resolve()
    root = wd.root.resolve()
    return p if p.is_relative_to(root) else root / _MAIN_FILE  # 목록 파일이 밖을 가리키면 무시


def _find(tabs: list[dict], name: str) -> dict | None:
    return next((t for t in tabs if t["name"] == name), None)


def _unique(tabs: list[dict], name: str, skip: dict | None = None) -> str:
    taken = {t["name"] for t in tabs if t is not skip}
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def _new_file(wd: WorkDir, tabs: list[dict], name: str, skip: dict | None = None) -> str:
    """새 탭 파일 자리 `notes/<이름>.md` — 다른 탭이 쓰는 파일과 겹치지 않게."""
    used = {t["file"] for t in tabs if t is not skip}
    base = f"notes/{name}"
    rel, n = f"{base}.md", 2
    while rel in used or (wd.root / rel).exists():
        rel, n = f"{base} ({n}).md", n + 1
    return rel


# ---------------------------------------------------------------- 공개 API

def tab_names(wd: WorkDir) -> list[str]:
    return [t["name"] for t in _load(wd)]


def read(wd: WorkDir, name: str) -> str:
    tab = _find(_load(wd), name)
    if tab is None:
        return ""
    try:
        return _path(wd, tab).read_text(encoding="utf-8")
    except OSError:
        return ""


def write(wd: WorkDir, name: str, text: str) -> None:
    """탭 내용 저장 — 원자적으로. 비우면 파일만 지우고 탭은 목록에 남긴다(빈 껍데기 파일을 남기지 않게)."""
    tab = _find(_load(wd), name)
    if tab is None:
        return
    path = _path(wd, tab)
    if not text.strip():
        path.unlink(missing_ok=True)
        return
    _write_atomic(path, text)


def add(wd: WorkDir, name: str = "") -> str:
    """새 탭을 맨 뒤에 — 이름이 비거나 겹치면 '메모 2'처럼 번호를 붙인다. 붙은 이름을 돌려준다."""
    tabs = _load(wd)
    name = _unique(tabs, clean_name(name) or DEFAULT_NAME)
    tabs.append({"name": name, "file": _new_file(wd, tabs, name)})
    _save(wd, tabs)
    return name


def rename(wd: WorkDir, old: str, new: str) -> str:
    """탭 이름 바꾸기. 더 만든 탭은 파일도 새 이름으로 옮긴다(첫 탭의 notes.md는 그대로). 바뀐 이름 반환."""
    tabs = _load(wd)
    tab = _find(tabs, old)
    new = clean_name(new)
    if tab is None or not new or new == old:
        return old
    new = _unique(tabs, new, skip=tab)
    if tab["file"] != _MAIN_FILE:
        src = _path(wd, tab)
        rel = _new_file(wd, tabs, new, skip=tab)
        if src.exists():
            (wd.root / rel).parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, wd.root / rel)
        tab["file"] = rel
    tab["name"] = new
    _save(wd, tabs)
    return new


def remove(wd: WorkDir, name: str) -> list[str]:
    """탭과 그 파일을 지운다. 마지막 하나는 지우지 않는다(메모 자리가 없어지지 않게). 남은 이름 목록 반환."""
    tabs = _load(wd)
    tab = _find(tabs, name)
    if tab is None or len(tabs) <= 1:
        return [t["name"] for t in tabs]
    _path(wd, tab).unlink(missing_ok=True)
    tabs.remove(tab)
    _save(wd, tabs)
    return [t["name"] for t in tabs]


def combined(wd: WorkDir) -> str:
    """내보내기용 한 덩어리 — 내용 있는 탭이 하나면 그 글 그대로(예전 notes.md와 같은 모양),
    여럿이면 탭마다 `### 이름` 아래에 차례로."""
    parts = [(n, read(wd, n).strip("\n")) for n in tab_names(wd)]
    parts = [(n, t) for n, t in parts if t.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1] + "\n"
    return "\n\n".join(f"### {n}\n\n{t}" for n, t in parts) + "\n"
