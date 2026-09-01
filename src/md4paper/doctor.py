"""환경 점검 — 흔한 설치 문제를 사전 진단한다."""

from __future__ import annotations

import importlib.util
import sys

from md4paper import config


def _check(label: str, ok: bool, detail: str = "", optional: bool = False) -> tuple[str, bool, str, bool]:
    """optional=True인 항목은 미충족이어도 '설치 실패'가 아니다(예: 보조 프로바이더 키)."""
    return (label, ok, detail, optional)


def run_checks() -> list[tuple[str, bool, str, bool]]:
    checks: list[tuple[str, bool, str, bool]] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(_check("Python >= 3.11", py_ok, sys.version.split()[0]))

    for mod in ("click", "pydantic", "ruamel.yaml", "markdown_it", "pypdfium2"):
        spec = importlib.util.find_spec(mod)
        checks.append(_check(f"모듈 {mod}", spec is not None, "" if spec else "미설치"))

    from md4paper.extract import DEFAULT_BACKEND, available_backends

    backends = available_backends()
    checks.append(
        _check(
            f"추출 백엔드 ({DEFAULT_BACKEND})",
            DEFAULT_BACKEND in backends,
            "사용 가능" if backends else "없음 — `uv sync`로 설치",
        )
    )

    # 웹 UI·앱 창은 선택 extra다 — 없어도 CLI 변환은 된다.
    ui_ok = importlib.util.find_spec("nicegui") is not None
    checks.append(_check("웹 UI (nicegui)", ui_ok,
                         "설치됨" if ui_ok else "미설치 — `uv sync --extra ui`", optional=True))
    native_ok = importlib.util.find_spec("webview") is not None
    checks.append(_check("앱 창 (pywebview)", native_ok,
                         "설치됨" if native_ok else "미설치(선택) — `uv sync --extra ui --extra native`",
                         optional=True))

    from md4paper import launcher

    where = launcher.default_location()
    checks.append(_check("앱 아이콘 등록", where.exists(),
                         str(where) if where.exists() else "없음(선택) — `md4paper app`", optional=True))

    ks = config.key_status()
    provider = config.resolve_provider()
    prov_ok = ks.get(provider, False)
    checks.append(
        _check(
            f"LLM 키: 기본 프로바이더 '{provider}'",
            prov_ok,
            "설정됨" if prov_ok else f"미설정 — env {config.ENV_VARS[provider]} 또는 `md4paper keys set {provider}`",
            optional=True,  # 키 없이도 변환(PDF→마크다운)은 동작한다 — 번역·인용만 막힌다
        )
    )
    for p in config.PROVIDERS:
        if p != provider:
            checks.append(
                _check(f"LLM 키: {p}", ks.get(p, False),
                       "설정됨" if ks.get(p) else "미설정(선택)", optional=True)
            )

    return checks
