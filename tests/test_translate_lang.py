"""번역 대상 언어 — 한국어 밖 언어로 번역, 언어별 파일·캐시, 옛 한국어 논문 호환."""

import json
from pathlib import Path

import pytest

from md4paper import config, langs, pipeline
from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.llm import FakeProvider
from md4paper.translate import engine, glossary
from md4paper.translate.style import style_instruction
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"
JA_GLOSSARY = GlossaryList(entries=[
    GlossaryEntry(term="attention", korean="アテンション", policy="transliterate"),
    GlossaryEntry(term="encoder", korean="エンコーダ", policy="translate"),
])


def _body(user: str) -> str:
    """유저 메시지의 번역할 본문 — 한국어·영어 머리 둘 다."""
    lines = user.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(("=== 번역할 본문", "=== Text to translate")):
            return "\n".join(lines[i + 1:])
    return user


def _recording(gloss: GlossaryList = JA_GLOSSARY):
    seen = {"system": [], "user": [], "parse": []}

    def complete(system: str, user: str) -> str:
        seen["system"].append(system)
        seen["user"].append(user)
        return _body(user)

    def parse(system: str, user: str, schema):  # noqa: ANN001
        seen["parse"].append(system)
        return gloss

    return seen, FakeProvider(complete_fn=complete, parse_fn=parse, model="gpt-5.6-luna")


@pytest.fixture
def paper(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_cite.md", wd)
    return wd


# --- 언어 코드·문체 ----------------------------------------------------------

def test_normalize_language_codes():
    assert langs.normalize("JA") == "ja"
    assert langs.normalize("zh-hans") == "zh-Hans"
    assert langs.normalize("zh_Hans") == "zh-Hans"
    assert langs.normalize("pt-br") == "pt-BR"
    for bad in ("", "japanese!", "../x", "ja/..", "j"):
        with pytest.raises(ValueError):
            langs.normalize(bad)


def test_korean_style_instructions_are_unchanged():
    """한국어 지시문은 번역 캐시의 옛 키에 들어간다 — 글자 그대로 고정."""
    assert style_instruction("해라체") == "문어체 해라체(~한다/~이다)로 번역한다. 논문 번역의 표준 문체다."
    assert style_instruction("합니다체") == "경어체 합니다체(~합니다/~입니다)로 번역한다."
    assert style_instruction("해요체") == "부드러운 해요체(~해요/~예요)로 번역한다."
    assert style_instruction("") == style_instruction("해라체")
    assert style_instruction("모르는 값") == style_instruction("해라체")
    assert style_instruction('custom:"짧게"') == "짧게"


def test_fit_style_only_replaces_other_languages_presets():
    # 한국어: 프리셋·custom·모르는 값 모두 그대로 (캐시 키가 바뀌면 안 된다)
    for s in ("해라체", "합니다체", 'custom:"x"', "옛날 값"):
        assert langs.fit_style("ko", s) == s
    # 언어를 바꿨을 때 남은 다른 언어 프리셋만 그 언어 기본으로
    assert langs.fit_style("ja", "해라체") == "である調"
    assert langs.fit_style("ja", "해라체", "です・ます調") == "です・ます調"
    assert langs.fit_style("ko", "である調") == "해라체"
    assert langs.fit_style("ja", "です・ます調") == "です・ます調"
    assert langs.fit_style("fr", "académico") == "academic"  # 프리셋 없는 언어


def test_unknown_language_still_gets_a_prompt():
    lang = langs.get("fr")
    assert lang.english == "French" and langs.style_names("fr") == ("academic",)
    assert "BCP 47 code 'sw'" in langs.get("sw").english


# --- 프롬프트 ---------------------------------------------------------------

def test_non_korean_prompt_names_language_and_style():
    gl = GlossaryList(entries=[*JA_GLOSSARY.entries, GlossaryEntry(term="Transformer", korean="", policy="keep"),
                               GlossaryEntry(term="decoder", korean="デコーダ", policy="병기-first-use")])
    p = engine.build_system_prompt("である調", gl, "T", "A", "- Intro", lang="ja")
    assert "into Japanese" in p and "である調" in p
    assert "- attention → アテンション (transliterate by sound)" in p
    assert "- Transformer → keep the English original" in p
    assert '- decoder → デコーダ (on first use write "デコーダ (decoder)")' in p
    assert "한국어" not in p and "번역" not in p


def test_korean_user_message_markers_are_unchanged():
    """한국어 유저 메시지 머리는 예전 글 그대로 (모델이 보는 글이 바뀌지 않게)."""
    seen, fake = _recording()
    engine.translate_chunk(fake, "sys", "Body.", context_tail="Prev.")
    assert seen["user"][0] == ("=== 앞 문맥 (직전 섹션 끝 · 참고용, 다시 출력하지 말 것) ===\nPrev.\n\n"
                               "=== 번역할 본문 (이 아래만 번역) ===\nBody.")
    engine.translate_chunk(fake, "sys", "Body.", context_tail="Prev.", lang="es")
    assert seen["user"][1].startswith("=== Preceding context") and "=== Text to translate" in seen["user"][1]


# --- 논문의 번역 언어 ---------------------------------------------------------

def test_new_paper_follows_global_language(paper):
    assert paper.lang == "ko" and paper.ko_md.name == "paper.ko.md"
    assert paper.cache_json.name == "cache.json" and paper.glossary_yaml.name == "glossary.yaml"
    assert paper.figures_ko_json.name == "figures-ko.json"
    config.set_section_value("translate", "target_lang", "ja")
    assert paper.lang == "ja"
    assert (paper.ko_md.name, paper.cache_json.name, paper.glossary_yaml.name, paper.figures_ko_json.name) == \
        ("paper.ja.md", "cache.ja.json", "glossary.ja.yaml", "figures-ja.json")


def test_invalid_global_language_falls_back_to_korean(paper):
    config.set_section_value("translate", "target_lang", "../evil")
    assert config.resolve_target_lang() == "ko" and paper.lang == "ko"


def test_translate_into_japanese_end_to_end(paper):
    config.set_section_value("translate", "target_lang", "ja")
    seen, fake = _recording()
    summary = pipeline.run_translate(paper, fake)
    assert summary["passthrough"] == 0
    out = paper.out
    assert (out / "paper.ja.md").exists() and not (out / "paper.ko.md").exists()
    assert (paper.translate / "cache.ja.json").exists() and not (paper.translate / "cache.json").exists()
    assert (paper.translate / "glossary.ja.yaml").exists() and not (paper.translate / "glossary.yaml").exists()
    st = paper.load_status()
    assert st["lang"] == "ja" and st["translate"]["lang"] == "ja" and st["translate"]["style"] == "である調"
    assert all("into Japanese" in s for s in seen["system"])
    assert "into Japanese" in seen["parse"][0] and "into Korean" not in seen["parse"][0]
    # 다시 돌리면 전부 캐시
    _seen2, fake2 = _recording()
    again = pipeline.run_translate(paper, fake2)
    assert again["cached"] == again["chunks"] and again["ok"] == 0


def test_translated_paper_keeps_its_language_when_global_changes(paper):
    _seen, fake = _recording()
    pipeline.run_translate(paper, fake)  # 한국어(기본)
    config.set_section_value("translate", "target_lang", "es")
    assert paper.lang == "ko" and paper.ko_md.exists()
    # status에 lang이 없던 옛 논문도 한국어 산출물이 있으면 한국어
    st = paper.load_status()
    st.pop("lang")
    paper.save_status(st)
    assert paper.lang == "ko"


def test_glossary_only_paper_keeps_language_when_global_changes(paper):
    config.set_section_value("translate", "target_lang", "ja")
    _seen, fake = _recording()
    pipeline.run_glossary(paper, fake)
    config.set_section_value("translate", "target_lang", "es")
    assert paper.lang == "ja" and paper.glossary_yaml.name == "glossary.ja.yaml"


def test_switching_language_keeps_old_files_and_cache(paper):
    _seen, fake = _recording()
    pipeline.run_translate(paper, fake)  # ko, 해라체
    ko_text = paper.ko_md.read_text(encoding="utf-8")
    _seen, fake = _recording()
    pipeline.run_translate(paper, fake, lang="ja")
    assert paper.lang == "ja" and paper.ko_md.name == "paper.ja.md"
    assert (paper.out / "paper.ko.md").read_text(encoding="utf-8") == ko_text
    assert paper.load_status()["translate"]["style"] == "である調"  # manifest의 해라체는 일본어 기본으로
    # 한국어로 되돌리면 옛 캐시를 그대로 쓴다
    _seen, fake = _recording()
    back = pipeline.run_translate(paper, fake, lang="ko")
    assert back["cached"] == back["chunks"] and back["ok"] == 0
    assert paper.load_status()["translate"]["style"] == "해라체"


def test_korean_input_hash_format_is_unchanged(paper):
    """옛 기록에서 모델을 되찾는 `recover_translate`가 `문체:모델` 해시를 쓴다 — 한국어는 그 꼴 그대로."""
    from md4paper.workdir import hash_text

    _seen, fake = _recording()
    pipeline.run_translate(paper, fake)
    assert paper.load_status()["translate"]["input_hash"] == hash_text("해라체:gpt-5.6-luna")


# --- 컬렉션 용어집 ------------------------------------------------------------

def test_collection_glossary_is_per_language(tmp_path, monkeypatch, paper):
    from md4paper import projects, workdir

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    pid = projects.create("Coll")["id"]
    workdir.set_projects(paper.root, [pid])
    glossary.save_collection(pid, GlossaryList(entries=[GlossaryEntry(term="encoder", korean="인코더")]))
    glossary.save_collection(pid, GlossaryList(entries=[GlossaryEntry(term="encoder", korean="エンコーダ")]),
                             "ja")
    assert glossary.collection_path(pid).name == f"{pid}.yaml"
    assert glossary.collection_path(pid, "ja").name == f"{pid}.ja.yaml"
    assert glossary.effective(paper)[0].entries[0].korean == "인코더"
    paper.set_lang("ja")
    assert glossary.effective(paper)[0].entries[0].korean == "エンコーダ"
    config.set_section_value("translate", "target_lang", "ja")
    assert glossary.load_collection(pid).entries[0].korean == "エンコーダ"  # 언어를 안 주면 전역 언어
    assert glossary.delete_collection(pid)
    assert not list((tmp_path / "cfg" / "glossary").glob(f"{pid}*"))


# --- 아직 한국어만인 기능 -------------------------------------------------------

def test_figure_translation_refuses_other_languages(paper):
    from md4paper import figure_translate

    paper.set_lang("ja")
    with pytest.raises(figure_translate.FigureTranslateError, match="한국어만"):
        figure_translate.translate(paper, "figure-1.png", FakeProvider())
    with pytest.raises(figure_translate.FigureTranslateError):
        figure_translate.translate_many(paper, FakeProvider())


# --- CLI ------------------------------------------------------------------

def test_cli_language_command_sets_global_language():
    from click.testing import CliRunner

    from md4paper.cli import cli

    r = CliRunner().invoke(cli, ["language", "zh-hans", "--style", "通俗"])
    assert r.exit_code == 0, r.output
    assert config.resolve_target_lang() == "zh-Hans" and config.resolve_style() == "通俗"
    assert config.load_config()["translate"]["zh-Hans_style"] == "通俗"
    r = CliRunner().invoke(cli, ["language"])
    assert "zh-Hans" in r.output and "简体中文" in r.output
    r = CliRunner().invoke(cli, ["language", "not a code"])
    assert r.exit_code != 0


def test_status_lang_survives_reconvert_stage_reset(paper):
    """mark_done이 하위 단계 기록을 지워도(재변환) 논문의 언어는 남는다 — 최상위 키라서."""
    paper.set_lang("es")
    paper.mark_done("cite", "x")
    assert paper.lang == "es"
    assert json.loads(paper.status_json.read_text(encoding="utf-8"))["lang"] == "es"
