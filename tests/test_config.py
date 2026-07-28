"""config 키 저장·마스킹·삭제 (테스트용 임시 CONFIG_DIR은 conftest가 지정)."""

from md4paper import config


def test_key_save_preview_and_delete(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.set_key("openai", "sk-proj-ABCDEFGHIJKLMNOP1234")

    info = config.stored_key_preview("openai")
    assert info is not None and info["source"] == "file"
    # 앞 6 + 뒤 4만 남기고 가운데는 가림
    assert info["masked"].startswith("sk-pro") and info["masked"].endswith("1234")
    assert "ABCDEFGHIJKLMNOP" not in info["masked"]
    assert config.resolve_key("openai") == "sk-proj-ABCDEFGHIJKLMNOP1234"

    # 원클릭 삭제 → 사라짐, 두 번째 삭제는 False
    assert config.delete_key("openai") is True
    assert config.stored_key_preview("openai") is None
    assert config.delete_key("openai") is False


def test_env_key_is_preview_source_and_not_file_deletable(monkeypatch):
    config.delete_key("gemini")  # 파일 키 정리
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyABCDEFGHIJKLMNOPQR")
    info = config.stored_key_preview("gemini")
    assert info is not None and info["source"] == "env"
    assert config.delete_key("gemini") is False  # env 키는 파일 삭제로 못 지움


# --- 홈 'Global 설정' — 변환·번역 기본값을 config.toml에 저장/복원 ---
def test_global_settings_roundtrip_bool_and_list():
    # bool·list 값이 TOML 네이티브로 저장돼 다시 읽힌다 (문자열 "True"로 뭉개지지 않음)
    config.set_section_value("translate", "translate_headers", False)
    config.set_section_value("translate", "translate_references", True)
    config.set_section_value("output", "caption_style", "blockquote")
    config.set_section_value("cite", "parts", ["authoryear", "short"])
    config.set_section_value("cite", "reference_links", False)
    config.set_section_value("extract", "ocr", True)

    assert config.resolve_translate_headers() is False
    assert config.resolve_translate_references() is True
    assert config.resolve_caption_style() == "blockquote"
    assert config.resolve_citation_parts() == ["authoryear", "short"]
    assert config.resolve_reference_links() is False
    assert config.resolve_ocr() is True


def test_global_settings_defaults_when_unset():
    # 아무것도 저장 안 된 상태의 안전한 기본값
    config.set_section_value("output", "caption_style", "nonsense")  # 잘못된 값 → 기본으로 폴백
    assert config.resolve_caption_style() == "bold-italic"
