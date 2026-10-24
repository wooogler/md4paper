"""옛 이름(md4paper) 시절 폴더를 새 이름으로 옮긴다 — 처음 켤 때 한 번.

앱 이름이 md4paper에서 Narani로 바뀌면서 설정 폴더(`~/.config/md4paper`)와 기본 작업 폴더
(`~/md4paper`)도 새 이름을 따른다. 옛 설치본에서 넘어온 사용자가 키·컬렉션·열린 탭·Drive 연결과
논문을 잃지 않도록, 새 폴더가 아직 없을 때만 옛 폴더를 통째로 옮긴다(같은 디스크라 이름만 바뀌고
파일 권한 — Drive 토큰의 0600 — 도 그대로다).

작업 폴더를 옮기면 설정 파일(projects.json·open_tabs.json·config.toml 등)과 논문의 meta.json에
적힌 절대 경로가 옛 폴더를 가리키므로, 그 앞부분만 새 폴더로 고쳐 쓴다.

`NARANI_CONFIG_DIR`로 설정 폴더를 따로 정했으면(테스트·개발) 아무것도 하지 않는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from narani import config

LEGACY_CONFIG_DIR = Path.home() / ".config" / "md4paper"
LEGACY_WORKSPACE_ROOT = Path.home() / "md4paper"
WORKSPACE_ROOT = Path(config.FALLBACK_WORKSPACE).expanduser().parent  # ~/narani

_REWRITE_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}


def _movable(old: Path, new: Path) -> bool:
    return old.is_dir() and not old.is_symlink() and not new.exists() and not new.is_symlink()


def _rewrite_paths(root: Path, old: Path, new: Path, *, names: set[str] | None = None) -> int:
    """root 아래 설정·메타 파일의 `old` 경로 앞부분을 `new`로. 고친 파일 수.

    JSON·TOML은 윈도 경로의 역슬래시를 `\\\\`로 적으므로 그 형태도 함께 바꾼다.
    """
    pairs = [(str(old), str(new))]
    esc_old, esc_new = json.dumps(str(old))[1:-1], json.dumps(str(new))[1:-1]
    if esc_old != str(old):
        pairs.append((esc_old, esc_new))
    changed = 0
    for path in root.rglob("*"):
        if path.suffix not in _REWRITE_SUFFIXES or not path.is_file():
            continue
        if names is not None and path.name not in names:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out = text
        for a, b in pairs:
            # 경로의 앞부분일 때만 — `~/md4paper-old` 같은 이웃 폴더는 건드리지 않는다
            for tail in ("/", "\\", '"', "'"):
                out = out.replace(a + tail, b + tail)
        if out != text:
            path.write_text(out, encoding="utf-8")
            changed += 1
    return changed


def migrate() -> list[str]:
    """옮긴 것을 사람이 읽을 문장으로 돌려준다(없으면 빈 목록). 실패해도 앱은 계속 뜬다."""
    if os.environ.get("NARANI_CONFIG_DIR"):
        return []
    done: list[str] = []
    try:
        if _movable(LEGACY_CONFIG_DIR, config.CONFIG_DIR):
            config.CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
            os.rename(LEGACY_CONFIG_DIR, config.CONFIG_DIR)
            done.append(f"설정 폴더를 옮겼습니다: {LEGACY_CONFIG_DIR} → {config.CONFIG_DIR}")
        if _movable(LEGACY_WORKSPACE_ROOT, WORKSPACE_ROOT):
            os.rename(LEGACY_WORKSPACE_ROOT, WORKSPACE_ROOT)
            done.append(f"작업 폴더를 옮겼습니다: {LEGACY_WORKSPACE_ROOT} → {WORKSPACE_ROOT}")
            if config.CONFIG_DIR.is_dir():
                _rewrite_paths(config.CONFIG_DIR, LEGACY_WORKSPACE_ROOT, WORKSPACE_ROOT)
            _rewrite_paths(WORKSPACE_ROOT, LEGACY_WORKSPACE_ROOT, WORKSPACE_ROOT, names={"meta.json"})
    except OSError as e:
        done.append(f"옛 md4paper 폴더를 옮기지 못했습니다: {e}")
    return done
