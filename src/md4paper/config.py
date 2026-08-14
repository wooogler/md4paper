"""전역 설정과 LLM 프로바이더/키 해석.

키·기본 프로바이더·기본 모델은 작업 디렉토리가 아니라 ~/.config/md4paper/config.toml (0600)에 둔다.
해석 순서:
  프로바이더/모델: CLI 플래그 > config.toml > 내장 기본
  키: env 변수 > config.toml
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("MD4PAPER_CONFIG_DIR", Path.home() / ".config" / "md4paper"))
CONFIG_PATH = CONFIG_DIR / "config.toml"

PROVIDERS = ("openai", "anthropic", "gemini")

# 제공사별 모델 — 토큰당 가격 오름차순(저렴 → 비쌈). 기본값은 가장 저렴한 tier(GPT luna 급).
# 각 사의 최신 세대만 싣는다. gemini 최저가 티어는 3.1-flash-lite($0.25/$1.5)가 더 싸지만
# 최신인 3.5-flash-lite를 쓴다 — 구형도 --model로는 계속 고를 수 있다(PRICING에 단가 유지).
MODEL_TIERS = {
    "openai": ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"),
    "gemini": ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.1-pro-preview"),
}
# 내장 기본값 (사용자 선호: openai / gpt-5.6-luna). 각 제공사의 가장 저렴한 tier를 기본으로.
DEFAULT_PROVIDER = "openai"
DEFAULT_MODELS = {p: tiers[0] for p, tiers in MODEL_TIERS.items()}
ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
DEFAULT_KOREAN_STYLE = "해라체"
DEFAULT_FLAVOR = "standard"  # 이미지 임베딩 뷰어 프로파일 (standard|obsidian|notion|html)


def load_config() -> dict:
    """config.toml을 읽어 dict 반환 (없으면 빈 dict)."""
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def resolve_provider(cli_provider: str | None = None) -> str:
    if cli_provider:
        return cli_provider
    cfg = load_config()
    return cfg.get("default_provider", DEFAULT_PROVIDER)


def resolve_model(provider: str, cli_model: str | None = None) -> str:
    if cli_model:
        return cli_model
    cfg = load_config()
    models = cfg.get("models", {})
    return models.get(provider, DEFAULT_MODELS.get(provider, ""))


def resolve_key(provider: str) -> str | None:
    """키 조회: env 변수 우선 → config.toml."""
    env = os.environ.get(ENV_VARS[provider])
    if env:
        return env
    cfg = load_config()
    return cfg.get("keys", {}).get(provider)


def resolve_korean_style() -> str:
    cfg = load_config()
    return cfg.get("translate", {}).get("korean_style", DEFAULT_KOREAN_STYLE)


def _as_bool(val, default: bool) -> bool:  # noqa: ANN001
    if val is None:
        return default
    return str(val).strip().lower() not in ("false", "0", "no", "off", "")


def resolve_translate_headers() -> bool:
    """섹션 제목도 번역할지: config [translate].translate_headers > True."""
    return _as_bool(load_config().get("translate", {}).get("translate_headers"), True)


def resolve_translate_references() -> bool:
    """참고문헌 섹션 번역 여부: config [translate].translate_references > False."""
    return _as_bool(load_config().get("translate", {}).get("translate_references"), False)


_CAPTION_STYLES = ("bold-italic", "blockquote", "italic")


def resolve_caption_style() -> str:
    """그림·표 캡션 스타일: config [output].caption_style > bold-italic."""
    val = str(load_config().get("output", {}).get("caption_style", "bold-italic"))
    return val if val in _CAPTION_STYLES else "bold-italic"


def resolve_ocr() -> bool:
    """스캔 PDF용 OCR 기본값: config [extract].ocr > False."""
    return _as_bool(load_config().get("extract", {}).get("ocr"), False)


def resolve_translate_workers() -> int:
    """섹션 병렬 번역 동시 스레드 수 (config [translate].workers, 기본 4). 1~12로 제한."""
    cfg = load_config()
    try:
        n = int(cfg.get("translate", {}).get("workers", 4))
    except (TypeError, ValueError):
        n = 4
    return max(1, min(n, 12))



def resolve_runin_mode() -> str:
    return load_config().get("structure", {}).get("runin_headings", "off")


FALLBACK_WORKSPACE = "~/md4paper/output"


def _project_root() -> Path | None:
    """소스 체크아웃(src 레이아웃)에서 실행 중이면 저장소 루트, 아니면 None."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def default_workspace() -> Path:
    """기본 작업 폴더 — 저장소에서 실행하면 <프로젝트>/output, 아니면 ~/md4paper/output."""
    root = _project_root()
    return root / "output" if root else Path(FALLBACK_WORKSPACE).expanduser()


def resolve_workspace() -> Path:
    """업로드 파일·작업 디렉토리를 모아둘 폴더. config [output].workspace > 기본 output 폴더.

    소스 코드 폴더나 홈 루트가 논문별 디렉토리로 어지럽혀지지 않도록 output 폴더 하나에 모은다.
    """
    raw = load_config().get("output", {}).get("workspace")
    if not raw:
        return default_workspace()
    return Path(str(raw)).expanduser()


# --- 라이브러리(결과물을 쌓아둘 폴더) — 영어·한국어 마크다운, 원본 PDF를 각각 따로 지정할 수 있다 ---
LIBRARY_WHICH = ("en", "ko")  # 마크다운 언어
LIBRARY_KINDS = ("en", "ko", "pdf")  # 저장 위치 폴더 종류 (마크다운 2 + 원본 PDF)


def _check_kind(which: str) -> str:
    if which not in LIBRARY_KINDS:
        raise ValueError(f"알 수 없는 종류: {which} (en|ko|pdf)")
    return which


def resolve_library_dir(which: str) -> Path | None:
    """변환한 논문의 결과물이 쌓일 폴더: config [library].en_dir / ko_dir / pdf_dir. 미설정이면 None.

    작업 폴더(workspace)와는 다르다 — 작업 폴더는 원본·중간 파일이 든 작업장이고,
    여기는 결과 마크다운(과 원본 PDF 사본)만 모아 노트 앱(Obsidian 등)에 그대로 쓰는 곳이다.
    """
    raw = load_config().get("library", {}).get(f"{_check_kind(which)}_dir")
    return Path(str(raw)).expanduser() if raw else None


def set_library_dir(which: str, path: str | None) -> None:
    """라이브러리 폴더 설정/해제 (None이나 빈 문자열이면 해제)."""
    value = str(path).strip() if path else ""
    set_section_value("library", f"{_check_kind(which)}_dir", value or None)


def resolve_library_auto() -> bool:
    """변환·번역이 끝나면 라이브러리 폴더에 자동으로 쌓을지: config [library].auto > True.

    폴더를 지정했다는 것 자체가 '거기 모으고 싶다'는 뜻이므로 기본은 켜짐. 끄면 수동 내보내기만.
    """
    return _as_bool(load_config().get("library", {}).get("auto"), True)


# --- 파일 이름 규칙 — 논문 폴더·PDF·저장 위치 사본의 기준명 템플릿 ---
DEFAULT_NAMING = "{year}_{title}_{author}"
NAMING_PLACEHOLDERS = ("{year}", "{title}", "{author}", "{venue}")
_NAMING_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # 파일명 금지 문자 (Windows 포함 교집합)


def naming_template_error(template: str) -> str | None:
    """이름 규칙으로 쓸 수 없으면 이유(한국어 문장), 괜찮으면 None."""
    t = (template or "").strip()
    if not t:
        return "규칙이 비어 있습니다."
    if not any(p in t for p in NAMING_PLACEHOLDERS):
        return "자리표시자({year}·{title}·{author}·{venue})가 하나도 없습니다 — 모든 논문이 같은 이름이 됩니다."
    rest = t
    for p in NAMING_PLACEHOLDERS:
        rest = rest.replace(p, "")
    if _NAMING_UNSAFE.search(rest):
        return '파일 이름에 쓸 수 없는 문자가 있습니다 (< > : " / \\ | ? * 등).'
    return None


def resolve_enrich_mailto() -> str:
    """서지 API polite pool에 쓸 연락 이메일: config [enrich].mailto. 없으면 빈 문자열.

    OpenAlex/Crossref는 mailto를 붙이면 더 넉넉한 풀로 보내준다(필수는 아님).
    """
    return str(load_config().get("enrich", {}).get("mailto") or "").strip()


def resolve_naming_template() -> str:
    """논문 파일·폴더 이름 규칙: config [output].naming > 기본 {year}_{title}_{author}.

    저장된 값이 잘못됐으면(수동 편집 등) 조용히 기본 규칙으로 폴백한다.
    """
    raw = str(load_config().get("output", {}).get("naming") or "").strip()
    return raw if raw and naming_template_error(raw) is None else DEFAULT_NAMING


def resolve_flavor(cli_flavor: str | None = None) -> str:
    """이미지 플레이버: CLI 플래그 > config [output].flavor > 내장 기본. (조립은 항상 canonical)"""
    if cli_flavor:
        return cli_flavor
    cfg = load_config()
    return cfg.get("output", {}).get("flavor", DEFAULT_FLAVOR)


_EXPORT_TARGETS = ("universal", "notion", "obsidian")


def resolve_export_target() -> str:
    """마크다운 내보내기 형식: config [output].export_target > universal(범용).

    en.md는 범용으로 저장하고 다운로드 시 이 값으로 변환한다(이미지 임베드·인용 표기). 마지막 선택을 기억."""
    val = str(load_config().get("output", {}).get("export_target", "universal"))
    return val if val in _EXPORT_TARGETS else "universal"


# 옛 단일 스타일 → parts 매핑 (하위호환)
_STYLE_TO_PARTS = {"keep": ["number"], "authoryear": ["authoryear"], "short": ["short"]}


def style_to_parts(style: str) -> list[str]:
    return _STYLE_TO_PARTS.get(style, ["number"])


def resolve_citation_parts() -> list[str]:
    """인용 표기 요소: config [cite].parts(list) > [cite].style(single) > [number]."""
    cite = load_config().get("cite", {})
    if isinstance(cite.get("parts"), list) and cite["parts"]:
        return [str(p) for p in cite["parts"]]
    if cite.get("style"):
        return style_to_parts(str(cite["style"]))
    return ["number"]


def resolve_reference_links() -> bool:
    """참고문헌 DOI/arXiv 하이퍼링크 여부: config [cite].reference_links > True."""
    val = load_config().get("cite", {}).get("reference_links", True)
    return str(val).lower() not in ("false", "0", "no")


_AUTHOR_PARTS = ("email", "affiliation")


def resolve_author_parts() -> list[str]:
    """저자 블록에 표시할 요소(이름은 항상): config [output].author_parts(list) > 이메일·소속 둘 다."""
    val = load_config().get("output", {}).get("author_parts")
    if isinstance(val, list):
        return [str(p) for p in val if str(p) in _AUTHOR_PARTS]
    return list(_AUTHOR_PARTS)


def build_provider(cli_provider: str | None = None, cli_model: str | None = None):
    """설정을 해석해 LLM 프로바이더 인스턴스를 만든다. 키 없으면 오류."""
    from md4paper.llm import get_provider

    provider = resolve_provider(cli_provider)
    model = resolve_model(provider, cli_model)
    key = resolve_key(provider)
    if not key:
        raise RuntimeError(
            f"{provider} API 키가 없습니다. env {ENV_VARS[provider]} 또는 "
            f"`md4paper keys set {provider}`로 설정하세요."
        )
    return get_provider(provider, api_key=key, model=model)


def validate_provider_key(provider: str, model: str, key: str) -> tuple[bool, str]:
    """키·모델로 아주 작은 실호출을 시도해 연결 가능 여부를 즉시 확인.

    번역·인용을 오래 돌린 뒤에야 401을 보는 사고를 막는다.
    반환: (성공?, 사람이 읽을 메시지). 실패 메시지는 SDK 오류를 그대로 전달(키 오타 vs 모델 없음 구분).
    """
    from md4paper.llm import get_provider

    key = (key or "").strip()
    if not key:
        return False, "키가 비어 있습니다."
    try:
        prov = get_provider(provider, api_key=key, model=model)
        prov.complete("You are a connection test.", "ping", max_tokens=1)
    except Exception as e:  # noqa: BLE001 — SDK별 예외를 그대로 사용자에게 요약
        msg = str(e).strip().splitlines()[0] if str(e).strip() else e.__class__.__name__
        return False, msg[:200]
    return True, "연결 성공"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_value(v) -> str:  # noqa: ANN001
    """스칼라/불리언/정수/리스트를 TOML 리터럴로 직렬화 (문자열은 따옴표)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(f'"{_toml_escape(str(x))}"' for x in v) + "]"
    return f'"{_toml_escape(str(v))}"'


def _dump_toml(cfg: dict) -> str:
    """평면 + 1단계 테이블 구조만 지원하는 최소 TOML 직렬화기."""
    lines: list[str] = []
    for key, val in cfg.items():
        if isinstance(val, dict):
            continue
        lines.append(f"{key} = {_toml_value(val)}")
    for key, val in cfg.items():
        if not isinstance(val, dict):
            continue
        lines.append("")
        lines.append(f"[{key}]")
        for k, v in val.items():
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def set_key(provider: str, key: str) -> None:
    """config.toml에 프로바이더 키 저장 (파일 권한 0600)."""
    if provider not in PROVIDERS:
        raise ValueError(f"알 수 없는 프로바이더: {provider}")
    cfg = load_config()
    cfg.setdefault("keys", {})[provider] = key
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_dump_toml(cfg), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def delete_key(provider: str) -> bool:
    """config.toml에 저장된 프로바이더 키를 삭제. 삭제되면 True (env 변수 키는 못 지움)."""
    cfg = load_config()
    keys = cfg.get("keys", {})
    if provider not in keys:
        return False
    del keys[provider]
    cfg["keys"] = keys
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_dump_toml(cfg), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)
    return True


def _mask_key(key: str) -> str:
    """키를 앞 6자 + 뒤 4자만 남기고 가린다 (어느 키인지 식별만 가능하게)."""
    k = key.strip()
    if len(k) <= 12:
        return "•" * len(k)
    return f"{k[:6]}…{k[-4:]}"


def stored_key_preview(provider: str) -> dict | None:
    """저장된 키의 출처와 마스킹 미리보기. 없으면 None.

    반환: {"source": "env"|"file", "masked": "sk-pro…ab12"}
    env 변수 키는 이 앱이 지울 수 없으므로 출처를 구분해 알려준다.
    """
    env = os.environ.get(ENV_VARS[provider])
    if env:
        return {"source": "env", "masked": _mask_key(env)}
    k = load_config().get("keys", {}).get(provider)
    if k:
        return {"source": "file", "masked": _mask_key(k)}
    return None


def set_section_value(section: str, field: str, value) -> None:  # noqa: ANN001
    """config.toml의 [section].field 갱신 (예: [output].workspace, [translate].korean_style).

    value는 str뿐 아니라 bool/int/list도 가능 (TOML 네이티브로 직렬화).
    value=None이면 그 키를 삭제한다(설정 해제) — "None" 문자열이 저장되지 않도록."""
    cfg = load_config()
    if value is None:
        if field not in cfg.get(section, {}):
            return
        del cfg[section][field]
    else:
        cfg.setdefault(section, {})[field] = value
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_dump_toml(cfg), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def set_default(field: str, value: str) -> None:
    """default_provider 등 최상위 설정 갱신."""
    cfg = load_config()
    cfg[field] = value
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_dump_toml(cfg), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def key_status() -> dict[str, bool]:
    """프로바이더별 키 설정 여부 (값은 노출하지 않음)."""
    return {p: resolve_key(p) is not None for p in PROVIDERS}
