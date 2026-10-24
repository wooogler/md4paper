"""열린 논문 탭 — 브라우저 탭처럼, 논문을 열면 생기고 ×를 누를 때까지 남는다.

고정(pin)과는 별개다. 고정은 홈 목록에서 관심 논문을 맨 위에 모아 보는 표시(status.json의
pinned_at)이고, 탭은 '지금 오가며 읽는 논문들'이다. 둘을 한 개념으로 묶어 두면 탭을 하나 닫으려고
고정을 풀어야 하고, 잠깐 열어 본 논문은 다른 논문으로 넘어가는 순간 탭에서 사라진다.

저장 위치: `~/.config/narani/open_tabs.json` — 앱을 다시 띄워도 열어 둔 탭이 그대로 있다.

**한 논문 = 탭 하나.** 같은 논문(작업 폴더 이름이 같은 것 — 시험용 복사본·옮긴 폴더 포함)을 다른
경로로 또 열면 새 탭을 만들지 않고 그 자리의 탭을 새 경로로 바꾼다. 같은 이름의 탭이 여럿이면
메모·하이라이트가 어느 쪽에 쓰였는지 알 수 없게 되기 때문이다(실제로 시험 서버가 연 복사본 탭에
메모가 저장돼 원본에서 사라진 것처럼 보인 일이 있었다). 이미 겹쳐 저장된 목록은 읽을 때 하나로
줄인다 — 작업 폴더 안의 것을 남기고, 둘 다 밖이면 나중에 연 것을 남긴다.
창이 여러 개여도 탭 목록은 하나를 같이 쓴다(한 사람의 '열어 둔 논문들'이므로).

```json
{"tabs": ["/Users/me/narani/attention/attention.md4", "..."]}
```
"""

from __future__ import annotations

import json
from pathlib import Path

from narani.config import CONFIG_DIR

TABS_PATH = CONFIG_DIR / "open_tabs.json"


def _key(root: Path | str) -> str:
    return str(Path(root).expanduser().resolve())


def _valid(path: str) -> bool:
    return (Path(path) / "structure" / "sections.yaml").exists()


def paper_id(root: Path | str) -> str:
    """같은 논문인지 가르는 열쇠 — 작업 폴더 이름(예: 2018_Peerception_AUTHORS.md4)."""
    return Path(root).name


def _in_workspace(path: str) -> bool:
    try:
        from narani.config import resolve_workspace

        return Path(path).is_relative_to(resolve_workspace().expanduser().resolve())
    except Exception:  # noqa: BLE001 — 작업 폴더를 못 정하면 우선순위만 잃는다
        return False


def _dedupe(tabs: list[str]) -> list[str]:
    """같은 논문이 여러 칸이면 하나로 — 첫 자리를 지키고, 작업 폴더 안의 경로 > 나중에 연 경로."""
    out: list[str] = []
    at: dict[str, int] = {}
    for t in tabs:
        pid = paper_id(t)
        if pid not in at:
            at[pid] = len(out)
            out.append(t)
            continue
        cur = out[at[pid]]
        if _in_workspace(t) or not _in_workspace(cur):
            out[at[pid]] = t
    return out


def _read() -> list[str]:
    try:
        data = json.loads(TABS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tabs = data.get("tabs") if isinstance(data, dict) else None
    if not isinstance(tabs, list):
        return []
    return list(dict.fromkeys(t for t in tabs if isinstance(t, str)))


def _write(tabs: list[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TABS_PATH.write_text(json.dumps({"tabs": tabs}, ensure_ascii=False, indent=1), encoding="utf-8")


def load() -> list[Path]:
    """열린 탭 — 연 순서대로. 지워졌거나 옮겨진 논문은 조용히 뺀다."""
    raw = _read()
    tabs = _dedupe([t for t in raw if _valid(t)])
    if tabs != raw:
        _write(tabs)
    return [Path(t) for t in tabs]


def is_open(root: Path | str) -> bool:
    return _key(root) in _read()


def open_tab(root: Path | str) -> None:
    """논문을 탭으로 연다 — 이미 열려 있으면 자리를 그대로 둔다(탭이 튀지 않게). 같은 논문이 다른
    경로로 열려 있으면 새 탭 대신 그 자리를 이 경로로 바꾼다(한 논문 = 탭 하나)."""
    k = _key(root)
    tabs = _read()
    if k in tabs:
        return
    pid = paper_id(k)
    same = [i for i, t in enumerate(tabs) if paper_id(t) == pid]
    if same:
        tabs[same[0]] = k
        tabs = [t for i, t in enumerate(tabs) if i == same[0] or paper_id(t) != pid]
        _write(tabs)
        return
    _write([*tabs, k])


def close_tab(root: Path | str) -> Path | None:
    """탭을 닫는다. 반환: 닫은 탭 다음으로 보여 줄 탭(오른쪽 이웃, 없으면 왼쪽) — 없으면 None."""
    k = _key(root)
    tabs = [t for t in _read() if _valid(t) or t == k]
    if k not in tabs:
        return None
    i = tabs.index(k)
    tabs.remove(k)
    _write(tabs)
    if not tabs:
        return None
    return Path(tabs[min(i, len(tabs) - 1)])


def rename_tab(old: Path | str, new: Path | str) -> None:
    """논문 폴더가 옮겨졌을 때(이름 정리) 탭이 따라가게 한다 — 자리는 그대로."""
    ko, kn = _key(old), _key(new)
    tabs = _read()
    if ko not in tabs:
        return
    tabs = [kn if t == ko else t for t in tabs]
    _write(list(dict.fromkeys(tabs)))
