"""헤더 이름별 사용자 선택 기억 (논문 간 학습).

같은 학회/포맷의 논문을 반복해 보면 "CCS Concepts", "Keywords", "ACM Reference Format" 같은
헤더가 계속 나온다. 사용자가 한 번 정한 처리(drop/skip/레벨)를 이름 기준으로 기억해 다음 논문에
자동 적용한다. **자동 계산값과 다른 선택(=실제 교정)만** 기억하고, 되돌리면 기억도 지운다.

저장 위치: ~/.config/md4paper/heading_prefs.json (작업 디렉토리가 아니라 전역)
"""

from __future__ import annotations

import json
import re

from md4paper.config import CONFIG_DIR

PREFS_PATH = CONFIG_DIR / "heading_prefs.json"

# 앞머리 번호(1 / 1.2 / II. / A) 제거 — 논문마다 번호는 달라도 이름은 같다
_LEADING_NUM_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[IVXLCDM]+|[A-Z])[.)]?\s+")
_PUNCT_RE = re.compile(r"[^\w\s가-힣]+")


def norm_key(text: str) -> str:
    """헤더 텍스트 → 기억 키 (번호·구두점·대소문자·공백 차이를 흡수)."""
    t = _LEADING_NUM_RE.sub("", text or "")
    t = _PUNCT_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def load() -> dict[str, object]:
    """{정규화된 헤더명: 레벨값} 반환."""
    if not PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def remember(text: str, level) -> None:  # noqa: ANN001 — LevelValue
    """헤더 이름에 대한 선택을 기억."""
    key = norm_key(text)
    if not key:
        return
    data = load()
    data[key] = level
    _save(data)


def forget(text: str) -> None:
    """기억 삭제 (사용자가 자동값으로 되돌린 경우)."""
    key = norm_key(text)
    data = load()
    if key in data:
        del data[key]
        _save(data)


def lookup(text: str):  # noqa: ANN201 — LevelValue | None
    """이 헤더 이름에 기억된 선택이 있으면 반환."""
    return load().get(norm_key(text))


def sync(sections) -> tuple[int, int]:  # noqa: ANN001 — list[ManifestSection]
    """매니페스트 저장 시 호출 — 교정은 기억하고, 자동값으로 되돌린 건 잊는다.

    반환: (기억한 수, 잊은 수)
    """
    data = load()
    learned = forgotten = 0
    for s in sections:
        key = norm_key(s.text)
        if not key or s.auto_level is None:
            continue
        if s.level != s.auto_level:  # 사용자가 고침 → 기억
            if data.get(key) != s.level:
                data[key] = s.level
                learned += 1
        elif key in data:  # 자동값으로 되돌림 → 기억 삭제
            del data[key]
            forgotten += 1
    if learned or forgotten:
        _save(data)
    return learned, forgotten
