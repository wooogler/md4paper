"""화면 언어 — t()·카탈로그·JS 주입·문구 수집."""

import json

from md4paper import config, i18n
from md4paper.i18n import t


def test_korean_screen_returns_source_text_unchanged():
    assert i18n.ui_lang() == "ko"
    assert t("최신 버전") == "최신 버전"
    assert t("새 버전이 있습니다 ({n}개 변경)", n=3) == "새 버전이 있습니다 (3개 변경)"
    assert t("카탈로그에 없는 문구") == "카탈로그에 없는 문구"


def test_english_screen_uses_catalog_and_falls_back_to_korean():
    config.set_section_value("ui", "language", "en")
    assert i18n.ui_lang() == "en"
    assert t("최신 버전") == "Up to date"
    assert t("새 버전이 있습니다 ({n}개 변경)", n=3) == "A new version is available (3 changes)"
    assert t("카탈로그에 없는 문구") == "카탈로그에 없는 문구"


def test_screen_language_follows_translation_language_when_unset():
    config.set_section_value("translate", "target_lang", "ja")  # 일본어 카탈로그는 아직 없다 → 영어
    assert i18n.ui_lang() == "en"
    config.set_section_value("translate", "target_lang", "ko")
    assert i18n.ui_lang() == "ko"
    config.set_section_value("ui", "language", "xx")  # 카탈로그 없는 값은 무시
    assert i18n.ui_lang() == "ko"


def test_broken_placeholder_in_translation_falls_back_to_source(monkeypatch):
    monkeypatch.setattr(i18n, "catalog", lambda lang=None: {"{n}편": "{m} papers"})
    assert t("{n}편", n=2) == "2편"


def test_shipped_catalogs_are_valid_and_cover_every_wrapped_string():
    """코드에서 t()/md4T()로 감싼 문구는 모든 카탈로그에 항목이 있어야 한다(빈 값은 괜찮다 — 한국어로 보인다).
    실패하면: uv run python tools/i18n_catalog.py sync <lang>"""
    used = i18n.collect()
    assert used, "감싼 문구가 하나도 없다 — 수집 정규식이 깨졌나"
    for lang in i18n.available()[1:]:
        assert i18n.problems(lang) == []
        data = json.loads((i18n.LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [s for s in used if s not in data]
        assert not missing, f"{lang}.json에 없는 문구 {len(missing)}개 (예: {missing[:3]}) — tools/i18n_catalog.py sync {lang}"


def test_problems_flag_placeholder_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "LOCALES", tmp_path)
    (tmp_path / "en.json").write_text(json.dumps({"{n}편": "{m} papers", "확인": "", "닫기": "Close"}),
                                      encoding="utf-8")
    assert len(i18n.problems("en")) == 1 and "자리표시" in i18n.problems("en")[0]


def test_collect_and_sync_find_python_and_js_strings(tmp_path, monkeypatch):
    pkg = tmp_path / "md4paper"
    (pkg / "ui").mkdir(parents=True)
    (pkg / "ui" / "a.py").write_text(
        'from md4paper.i18n import t\n'
        'x = t("저장했습니다")\n'
        'y = t("{n}편 번역", n=2)\n'
        'z = i18n.t(\'닫기\')\n'
        'q = obj.t("이건 아님")\n'
        'JS = """<script>el.title = md4T(\'다시 시도\');</script>"""\n', encoding="utf-8")
    found = i18n.collect(pkg)
    assert set(found) == {"저장했습니다", "{n}편 번역", "닫기", "다시 시도"}
    assert found["저장했습니다"] == ["md4paper/ui/a.py:2"]
    monkeypatch.setattr(i18n, "LOCALES", tmp_path / "locales")
    (tmp_path / "locales").mkdir()
    (tmp_path / "locales" / "en.json").write_text(json.dumps({"닫기": "Close", "옛 문구": "Old"}), encoding="utf-8")
    r = i18n.sync("en", root=pkg)
    assert set(r["added"]) == {"저장했습니다", "{n}편 번역", "다시 시도"} and r["unused"] == ["옛 문구"]
    r = i18n.sync("en", root=pkg, prune=True)
    data = json.loads((tmp_path / "locales" / "en.json").read_text(encoding="utf-8"))
    assert "옛 문구" not in data and data["닫기"] == "Close" and r["translated"] == 1


def test_head_html_carries_catalog_for_js():
    assert "md4T" in i18n.head_html() and "__md4I18n = {}" in i18n.head_html()  # 한국어 화면은 빈 사전
    config.set_section_value("ui", "language", "en")
    html = i18n.head_html()
    assert '"최신 버전": "Up to date"' in html and "</script>" in html and html.count("</script>") == 1


def test_quasar_language_follows_screen_language():
    assert i18n.quasar_language() == "ko-KR"
    config.set_section_value("ui", "language", "en")
    assert i18n.quasar_language() == "en-US"


def test_html_marks_are_translated_escaped_and_collected(tmp_path):
    code = '<button title="⦃닫기 (Esc)⦄">⦃닫기⦄</button>'
    assert i18n.html(code) == '<button title="닫기 (Esc)">닫기</button>'
    config.set_section_value("ui", "language", "en")
    import md4paper.i18n as m
    orig = m.catalog
    m.catalog = lambda lang=None: {"닫기 (Esc)": 'Close "Esc"', "닫기": "Close"}
    try:
        assert i18n.html(code) == '<button title="Close &quot;Esc&quot;">Close</button>'
    finally:
        m.catalog = orig
    pkg = tmp_path / "md4paper"
    pkg.mkdir()
    (pkg / "a.py").write_text('X = """<b>⦃굵게⦄</b>"""\n', encoding="utf-8")
    assert i18n.collect(pkg) == {"굵게": ["md4paper/a.py:1"]}


def test_collect_joins_implicitly_concatenated_literals(tmp_path):
    pkg = tmp_path / "md4paper"
    pkg.mkdir()
    (pkg / "a.py").write_text('x = t("첫 줄 "\n      "둘째 줄", n=1)\ny = N_("상수")\n', encoding="utf-8")
    assert set(i18n.collect(pkg)) == {"첫 줄 둘째 줄", "상수"}
