"""저장소 어댑터 — 앱 폴더 안의 상대 이름(`index.json`, `papers/….json`) ↔ 바이트.

폰 리더(§phone.web)에도 같은 모양의 어댑터가 있다. 여기서 정한 이름과 형식이 둘 사이의 약속이다.
LocalStore는 시험·미리보기용(그리고 동기화 폴더에 두고 싶을 때). Google Drive 어댑터가 같은 세 메서드를 갖는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class Store(Protocol):
    def read(self, name: str) -> bytes | None: ...
    def write(self, name: str, data: bytes, mime: str = "application/json") -> None: ...
    def delete(self, name: str) -> None: ...


class LocalStore:
    """로컬 폴더를 앱 폴더로 — 쓰기는 임시 파일에 쓴 뒤 바꿔치기(읽는 쪽이 반쯤 쓴 파일을 보지 않게)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"앱 폴더 밖의 이름: {name}")
        return p

    def read(self, name: str) -> bytes | None:
        try:
            return self._path(name).read_bytes()
        except FileNotFoundError:
            return None

    def write(self, name: str, data: bytes, mime: str = "application/json") -> None:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass
