"""화면 언어 — UI 문구를 한국어 밖 언어로 보여 주기.

**한국어 원문이 곧 키다.** 문구는 코드에 지금처럼 한국어로 쓰고 `t()`로 감싼다:

    ui.label(t("저장했습니다"))
    ui.notify(t("논문 {n}편을 번역합니다", n=3))

화면 언어가 한국어면 `t()`는 원문을 그대로 돌려준다 — 감싸기만으로는 동작도, 한국어 문구를 확인하는
테스트도 바뀌지 않는다. 다른 언어면 `locales/<lang>.json`({한국어 원문: 번역})에서 찾고, 없으면 한국어로
보인다(빈 값도 없는 것으로 친다). 자리표시는 `str.format` 이름 인자 — 번역문도 같은 이름을 써야 한다.
f-string은 원문이 매번 달라 키가 안 되므로 `t("…{n}…", n=…)` 꼴로 바꿔 감싼다.

**JS 문구**는 셸 페이지 head에 실린 `window.md4T(문구, {이름: 값})`로 — 같은 카탈로그를 쓴다(`head_html`).

**카탈로그 관리**는 `tools/i18n_catalog.py`: 코드의 `t("…")`·`md4T('…')` 리터럴을 모아 카탈로그에 빈 항목을
더하고 안 쓰는 항목을 알린다. 다른 세션들이 같은 파일을 고치고 있어 문구는 파일 하나씩 옮긴다.

화면 언어: config `[ui].language` > 번역 언어(`[translate].target_lang`)에 카탈로그가 있으면 그 언어 > 번역 언어가
한국어가 아니면 영어 > 한국어. 바꾸면 창을 새로 열어야(새로고침) 반영된다.
"""

from __future__ import annotations

import json
import re
import string
from functools import lru_cache
from pathlib import Path

SOURCE = "ko"  # 코드에 쓰는 원문 언어
LOCALES = Path(__file__).parent / "locales"
# Quasar(NiceGUI) 내장 문구 — 표 페이지·날짜 선택기 등
_QUASAR = {"ko": "ko-KR", "en": "en-US", "ja": "ja", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW", "es": "es",
           "fr": "fr", "de": "de-DE", "pt": "pt", "it": "it", "ru": "ru", "vi": "vi", "id": "id", "tr": "tr"}


def available() -> tuple[str, ...]:
    """고를 수 있는 화면 언어 — 한국어 + 카탈로그 파일이 있는 언어."""
    return (SOURCE, *sorted(p.stem for p in LOCALES.glob("*.json")))


def ui_lang() -> str:
    """지금 화면 언어 (모듈 설명의 순서). 설정 파일이 바뀌면(경로·mtime) 다시 정한다 — `t()`가 한 화면에
    수백 번 불리므로 매번 TOML을 읽지 않는다. 경로를 키에 넣는 건 테스트가 CONFIG_DIR을 바꾸기 때문."""
    from md4paper import config

    path = config.CONFIG_PATH
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    locs = tuple(sorted(p.name for p in LOCALES.glob("*.json")))
    return _ui_lang(str(path), stamp, locs)


@lru_cache(maxsize=8)
def _ui_lang(_path: str, _stamp: object, _locs: tuple) -> str:
    from md4paper import config

    cfg = config.load_config()
    want = str((cfg.get("ui") or {}).get("language") or "")
    have = available()
    if want in have:
        return want
    target = config.resolve_target_lang()
    if target in have:
        return target
    return "en" if target != SOURCE and "en" in have else SOURCE


@lru_cache(maxsize=None)
def _load(lang: str, stamp: float) -> dict[str, str]:
    path = LOCALES / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def catalog(lang: str | None = None) -> dict[str, str]:
    """{한국어 원문: 번역} — 빈 값은 빠진다. 파일을 고치면(mtime) 다시 읽는다."""
    lang = lang or ui_lang()
    if lang == SOURCE:
        return {}
    path = LOCALES / f"{lang}.json"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    return _load(lang, stamp)


def t(msg: str, /, **kw: object) -> str:
    """화면 문구 — 화면 언어의 번역(없으면 한국어 원문). kw가 있으면 `str.format`으로 채운다."""
    text = catalog().get(msg, msg)
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return msg.format(**kw)  # 번역문의 자리표시가 틀렸으면 원문으로 — 화면이 깨지는 것보다 낫다


def fields(msg: str) -> set[str]:
    """문구의 자리표시 이름들 — 카탈로그 검사(원문과 번역이 같은 이름을 쓰는지)에 쓴다."""
    try:
        return {f for _lit, f, _spec, _conv in string.Formatter().parse(msg) if f}
    except ValueError:
        return {"<깨진 자리표시>"}


def quasar_language() -> str:
    """`ui.run(language=…)` 값 — Quasar 내장 문구의 언어."""
    return _QUASAR.get(ui_lang(), "en-US")


_JS = """<script>
window.__md4I18n = %s;
window.md4T = function(s, kw){
  var r = (window.__md4I18n && window.__md4I18n[s]) || s;
  if (kw) r = r.replace(/\\{(\\w+)\\}/g, function(m, k){ return (k in kw) ? kw[k] : m; });
  return r;
};
</script>"""


def head_html() -> str:
    """셸 페이지 head에 실을 스크립트 — 화면 언어 카탈로그 + `md4T`."""
    return _JS % json.dumps(catalog(), ensure_ascii=False).replace("</", "<\\/")


# --- 카탈로그 관리 (tools/i18n_catalog.py가 쓴다) -------------------------------------

# t("…") / t('…') 와 JS md4T('…') — 리터럴 첫 인자만 (변수·f-string은 키가 될 수 없다)
_PY_CALL = re.compile(r"""(?:(?<![\w.])|(?<=i18n\.))t\(\s*(?P<q>"|')(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)\s*[,)]""")
# JS 문구는 파이썬 문자열 안이라 이스케이프가 두 겹 — 백슬래시 없는 리터럴만 받는다(문구에 쓸 일이 없다)
_JS_CALL = re.compile(r"""md4T\(\s*(?P<q>"|')(?P<s>[^"'\\\n]*)(?P=q)""")


def _unescape(s: str) -> str:
    try:
        return json.loads('"' + s.replace('"', '\\"').replace("\\'", "'") + '"')
    except ValueError:
        return s


def collect(root: Path | None = None) -> dict[str, list[str]]:
    """코드에서 감싼 문구들 → {원문: [쓰인 파일:줄]}. Python `t()`와 JS `md4T()` 리터럴."""
    root = root or Path(__file__).parent
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        if path.name == "i18n.py":
            continue
        text = path.read_text(encoding="utf-8")
        for rx in (_PY_CALL, _JS_CALL):
            for m in rx.finditer(text):
                s = m.group("s") if rx is _JS_CALL else _unescape(m.group("s"))
                line = text.count("\n", 0, m.start()) + 1
                found.setdefault(s, []).append(f"{path.relative_to(root.parent)}:{line}")
    return found


def problems(lang: str) -> list[str]:
    """카탈로그 검사 — 깨진 JSON, 원문과 번역의 자리표시 이름이 다른 항목. 빈 번역(아직 안 옮김)은 문제가 아니다."""
    path = LOCALES / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path.name}: 읽지 못함 ({e})"]
    if not isinstance(data, dict):
        return [f"{path.name}: 객체가 아님"]
    out = []
    for k, v in data.items():
        if not isinstance(v, str):
            out.append(f"{k!r}: 값이 문자열이 아님")
        elif v and fields(k) != fields(v):
            out.append(f"{k!r}: 자리표시가 다름 {sorted(fields(k))} ≠ {sorted(fields(v))}")
    return out


def sync(lang: str, *, prune: bool = False, root: Path | None = None) -> dict:
    """코드의 문구로 카탈로그를 맞춘다 — 없는 원문은 빈 값으로 더하고(기존 순서 뒤에), prune이면 안 쓰는 항목을 지운다.

    반환 {added, unused, total, translated}. 파일이 없으면 새로 만든다.
    """
    path = LOCALES / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    used = collect(root)
    added = [s for s in used if s not in data]
    unused = [k for k in data if k not in used]
    for s in added:
        data[s] = ""
    if prune:
        for k in unused:
            data.pop(k)
    LOCALES.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"added": added, "unused": unused, "total": len(data),
            "translated": sum(1 for v in data.values() if v)}
