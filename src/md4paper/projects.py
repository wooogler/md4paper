"""프로젝트 — 논문 묶음. 묶음마다 폴더 하나를 정하면 그 안이 저절로 정리된다.

한 사람이 동시에 여러 주제를 읽는다: 학위 서베이, 수업 과제, 진행 중인 연구. 전역 '저장 위치'
하나만 있으면 모든 논문이 한 폴더에 섞이고 홈 목록도 최근순 한 줄로만 쌓인다. 프로젝트는 그
두 가지를 함께 푼다:

- **분류**: 논문마다 소속 프로젝트를 적어 두고(그 논문의 status.json), 홈 목록을 프로젝트로
  걸러 보거나 프로젝트별 구역으로 묶어 본다.
- **저장 위치**: 프로젝트 폴더 **하나만** 고르면 종류별 자리가 그 안에 자동으로 잡힌다.

```
<프로젝트 폴더>/2017_Attention_Vaswani.md          ← 영어 마크다운은 루트에 (읽는 게 주로 이것)
<프로젝트 폴더>/images/2017_Attention_Vaswani/     ← 그림은 논문별 폴더로
<프로젝트 폴더>/ko/2017_Attention_Vaswani.md       ← 번역본
<프로젝트 폴더>/pdf/2017_Attention_Vaswani.pdf     ← 원본 PDF (md와 같은 기준명)
```

폴더를 안 고른 프로젝트(와 미분류 논문)는 **공통 저장 위치**(config `[library]`의 en/ko/pdf)를
쓴다 — 프로젝트를 쓰기 전에 잡아 둔 설정이 그대로 살아 있다.

저장 위치: `~/.config/md4paper/projects.json`. config.toml에 넣지 않은 이유는 우리 최소 TOML
직렬화기가 테이블 배열을 못 쓰기 때문이다 — 프로젝트는 개수가 늘어나는 목록이다.

```json
{"active": "p18f…", "projects": [
  {"id": "p18f…", "name": "튜터 챗봇 서베이", "created_at": 1.7e9, "root": "~/Vault/Tutor"}
]}
```

논문 쪽 배정은 `workdir.set_project`가 status.json에 쓴다. 프로젝트를 지워도 그 논문의 배정
문자열은 남지만, 없는 id는 어디서나 '미분류'로 취급한다(유령 구역이 생기지 않게).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from md4paper.config import CONFIG_DIR, LIBRARY_KINDS

PROJECTS_PATH = CONFIG_DIR / "projects.json"

KINDS = LIBRARY_KINDS  # ("en", "ko", "pdf") — 저장 위치 폴더 종류

# 프로젝트 폴더 안의 자리 — 영어 마크다운은 루트, 번역·PDF는 하위 폴더.
# (영어를 루트에 두는 이유: 노트 앱에서 폴더를 열면 논문 목록이 바로 보이는 게 자연스럽다.)
SUBDIR = {"en": "", "ko": "ko", "pdf": "pdf"}

# 목록 필터·배정에 쓰는 특수 값 (실제 프로젝트 id와 섞이지 않게 id에 못 쓰는 모양으로)
ALL = ""  # 모든 프로젝트 (필터 전용)
NONE = "__none__"  # 미분류 — 프로젝트에 넣지 않은 논문
NONE_LABEL = "미분류"
ALL_LABEL = "모든 프로젝트"

_MAX_NAME = 60

# 프로젝트마다 다르게 정할 수 있는 설정 — {키: (전역 config의 [섹션], 필드)}.
# **저장·출력에 관한 것만** 둔다. 프로젝트는 이미 '폴더 하나'가 정체성이라, 그 폴더에 무엇을
# 어떤 이름으로 쌓을지가 프로젝트마다 달라지는 게 자연스럽다(볼트마다 규칙이 다르다).
# 추출·번역 설정까지 여기로 내리면 프로젝트 설정 화면이 전역 설정 화면을 통째로 복제하게 되고,
# 지금 보는 값이 어디서 왔는지 헷갈린다.
SETTINGS = {
    "naming": ("output", "naming"),  # 파일·폴더 이름 규칙
    "export_target": ("output", "export_target"),  # 내보내기 형식 (범용/Notion/Obsidian)
    "bibtex": ("library", "bibtex"),  # references.bib도 함께 쌓을지
    "auto": ("library", "auto"),  # 변환·번역 후 자동 저장
    "bib_lookup": ("library", "bib_lookup"),  # 변환 직후 논문 API로 서지를 보강할지
}


def setting(pid: str | None, key: str):  # noqa: ANN201
    """이 프로젝트가 **따로 정해 둔** 값. 안 정했으면 None → 호출측이 전역 설정을 쓴다.

    '안 정함'과 '거짓으로 정함'을 구별해야 하므로 없으면 None을 준다 (False와 다르다).
    """
    if key not in SETTINGS:
        raise ValueError(f"프로젝트 설정이 아닙니다: {key} ({', '.join(SETTINGS)})")
    p = get(pid or "")
    if not p:
        return None
    value = (p.get("settings") or {}).get(key)
    return None if value is None else value


def set_setting(pid: str, key: str, value) -> bool:  # noqa: ANN001
    """프로젝트 설정 지정/해제. `value`가 None이면 해제(전역 설정을 따르게 된다)."""
    if key not in SETTINGS:
        raise ValueError(f"프로젝트 설정이 아닙니다: {key} ({', '.join(SETTINGS)})")
    data = load()
    for p in data["projects"]:
        if p["id"] != pid:
            continue
        current = dict(p.get("settings") or {})
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
        if current == (p.get("settings") or {}):
            return True
        p["settings"] = current
        _save(data)
        return True
    return False


def settings_of(pid: str | None) -> dict:
    """이 프로젝트가 따로 정해 둔 설정만 (전역을 따르는 항목은 들어 있지 않다)."""
    p = get(pid or "")
    return {k: v for k, v in (p.get("settings") or {}).items() if k in SETTINGS} if p else {}


def layout(root) -> dict[str, Path]:  # noqa: ANN001 — Path | str
    """폴더 하나 → 종류별 자리 {en, ko, pdf}. 프로젝트와 '한 폴더로 정리'가 같이 쓴다."""
    base = Path(str(root)).expanduser()
    return {k: (base / sub if sub else base) for k, sub in SUBDIR.items()}


def ensure_layout(root) -> None:  # noqa: ANN001 — Path | str
    """고른 폴더 안에 ko/·pdf/를 미리 만들어 준다 (실패해도 조용히 — 내보낼 때 다시 만든다)."""
    for path in layout(root).values():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return


def _blank() -> dict:
    return {"active": ALL, "projects": []}


def load() -> dict:
    """projects.json 전체 ({"active": id, "projects": [...]}). 없거나 깨졌으면 빈 구조."""
    if not PROJECTS_PATH.exists():
        return _blank()
    try:
        data = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    projects = [p for p in (data.get("projects") or []) if isinstance(p, dict) and p.get("id")]
    return {"active": str(data.get("active") or ALL), "projects": projects}


def _save(data: dict) -> None:
    """원자적 쓰기 — 앱 창을 여러 개 띄우면 같은 파일을 두 프로세스가 쓸 수 있다."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = PROJECTS_PATH.with_suffix(f".json.tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, PROJECTS_PATH)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def all_projects() -> list[dict]:
    """만든 순서대로 프로젝트 목록 — [{id, name, created_at, root}]."""
    return load()["projects"]


def get(pid: str) -> dict | None:
    """id로 프로젝트 하나 (없으면 None). 특수 값(ALL/NONE)도 None."""
    if not pid or pid == NONE:
        return None
    for p in all_projects():
        if p["id"] == pid:
            return p
    return None


def exists(pid: str) -> bool:
    return get(pid) is not None


def normalize(pid: str | None) -> str:
    """논문에 적힌 배정값 → 쓸 수 있는 값. 없는 프로젝트·특수 값은 모두 '' (미분류)."""
    return pid if pid and exists(pid) else ""


def name_of(pid: str | None) -> str:
    """사람이 읽을 이름. 미분류·없어진 프로젝트는 '미분류'."""
    p = get(pid or "")
    return p["name"] if p else NONE_LABEL


def _new_id(taken: set[str]) -> str:
    """짧고 안 겹치는 id. 이름을 쓰지 않는 이유는 이름을 바꿔도 배정이 살아 있어야 하기 때문."""
    base = f"p{int(time.time() * 1000):x}"
    pid, i = base, 2
    while pid in taken:
        pid = f"{base}-{i}"
        i += 1
    return pid


def clean_name(name: str) -> str:
    return " ".join(str(name or "").split())[:_MAX_NAME]


def create(name: str, root: str | None = None) -> dict:
    """프로젝트 만들기. 이름이 비면 '프로젝트 N'으로 붙여 준다. 반환: 만든 프로젝트."""
    data = load()
    label = clean_name(name) or f"프로젝트 {len(data['projects']) + 1}"
    proj = {
        "id": _new_id({p["id"] for p in data["projects"]}),
        "name": label,
        "created_at": time.time(),
        "root": str(root).strip() if root else "",
    }
    data["projects"].append(proj)
    _save(data)
    if proj["root"]:
        ensure_layout(proj["root"])
    return proj


def rename(pid: str, name: str) -> bool:
    """이름 바꾸기 (배정은 id로 하므로 논문은 그대로 따라온다)."""
    label = clean_name(name)
    if not label:
        return False
    data = load()
    for p in data["projects"]:
        if p["id"] == pid:
            if p.get("name") == label:
                return True
            p["name"] = label
            _save(data)
            return True
    return False


def delete(pid: str) -> bool:
    """프로젝트 삭제 — 폴더나 파일은 건드리지 않는다. 그 논문들은 미분류가 된다."""
    data = load()
    rest = [p for p in data["projects"] if p["id"] != pid]
    if len(rest) == len(data["projects"]):
        return False
    data["projects"] = rest
    if data["active"] == pid:
        data["active"] = ALL
    _save(data)
    return True


def root_of(pid: str) -> Path | None:
    """이 프로젝트의 폴더 (안 정했으면 None → 공통 저장 위치를 쓴다)."""
    p = get(pid)
    raw = str((p or {}).get("root") or "").strip()
    return Path(raw).expanduser() if raw else None


def set_root(pid: str, path: str | None) -> bool:
    """프로젝트 폴더 지정/해제. 지정하면 그 안에 ko/·pdf/ 자리를 만들어 둔다."""
    value = str(path).strip() if path else ""
    data = load()
    for p in data["projects"]:
        if p["id"] == pid:
            if str(p.get("root") or "") != value:
                p["root"] = value
                _save(data)
            if value:
                ensure_layout(value)
            return True
    return False


def dir_for(pid: str, which: str) -> Path | None:
    """이 프로젝트에서 그 종류가 갈 폴더 (폴더 미지정이면 None — 폴백은 library.dir_for)."""
    if which not in KINDS:
        raise ValueError(f"알 수 없는 종류: {which} (en|ko|pdf)")
    root = root_of(pid)
    return None if root is None else layout(root)[which]


def has_dirs(pid: str) -> bool:
    """이 프로젝트가 자기 폴더를 갖고 있는지."""
    return root_of(pid) is not None


def active() -> str:
    """홈에서 고른 프로젝트 — 목록 필터 + 새로 올린 논문의 소속. 없어진 id면 ''."""
    a = load()["active"]
    if a in (ALL, NONE):
        return a
    return a if exists(a) else ALL


def set_active(pid: str) -> None:
    """고른 프로젝트 기억 (다음 실행에도 같은 프로젝트를 보게)."""
    data = load()
    value = pid if pid in (ALL, NONE) or any(p["id"] == pid for p in data["projects"]) else ALL
    if data["active"] == value:
        return
    data["active"] = value
    _save(data)


def assign_target(pid: str | None = None) -> str:
    """새 논문을 넣을 프로젝트 — 고른 게 실제 프로젝트일 때만, 아니면 '' (미분류)."""
    a = active() if pid is None else pid
    return a if a and a not in (ALL, NONE) and exists(a) else ""


def options(*, with_all: bool = False, with_none: bool = True) -> dict[str, str]:
    """NiceGUI select용 {값: 라벨}. 프로젝트가 없어도 미분류 항목은 유지한다."""
    opts: dict[str, str] = {}
    if with_all:
        opts[ALL] = ALL_LABEL
    if with_none:
        opts[NONE] = NONE_LABEL
    for p in all_projects():
        opts[p["id"]] = p["name"]
    return opts
