"""헤더 이름별 선택 학습 — 논문 간 유지 (프리셋 대신)."""


import pytest

from md4paper import prefs
from md4paper.ir import ManifestSection
from md4paper.structure import build
from md4paper.workdir import WorkDir


@pytest.fixture(autouse=True)
def clean_prefs():
    """각 테스트는 빈 선호 저장소에서 시작."""
    if prefs.PREFS_PATH.exists():
        prefs.PREFS_PATH.unlink()
    yield
    if prefs.PREFS_PATH.exists():
        prefs.PREFS_PATH.unlink()


def test_norm_key_ignores_numbering_and_punctuation():
    # 논문마다 번호는 달라도 이름은 같다
    assert prefs.norm_key("1 Introduction") == prefs.norm_key("2 Introduction")
    assert prefs.norm_key("CCS Concepts") == prefs.norm_key("ccs  concepts")
    assert prefs.norm_key("ACM Reference Format:") == prefs.norm_key("ACM Reference Format")
    assert prefs.norm_key("III. Method") == prefs.norm_key("2 Method")


def test_sync_remembers_only_corrections():
    secs = [
        ManifestSection(id="a", text="CCS Concepts", line=0, level="drop", auto_level=1),  # 교정
        ManifestSection(id="b", text="1 Introduction", line=1, level=1, auto_level=1),  # 그대로
    ]
    learned, forgotten = prefs.sync(secs)
    assert learned == 1 and forgotten == 0
    assert prefs.lookup("CCS Concepts") == "drop"
    assert prefs.lookup("1 Introduction") is None  # 자동값과 같으면 기억 안 함


def test_sync_forgets_when_reverted():
    prefs.remember("Keywords", "drop")
    assert prefs.lookup("Keywords") == "drop"
    # 사용자가 자동값으로 되돌림
    secs = [ManifestSection(id="a", text="Keywords", line=0, level=1, auto_level=1)]
    learned, forgotten = prefs.sync(secs)
    assert forgotten == 1
    assert prefs.lookup("Keywords") is None


def test_build_applies_remembered_choice(tmp_path):
    """다음 논문에서 같은 이름의 헤더에 기억된 선택이 자동 적용된다."""
    prefs.remember("CCS Concepts", "drop")

    raw = "# Some Paper\n\n## CCS Concepts\n\nsome concepts text\n\n## 1 Introduction\n\nbody\n"
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    m = build.build(raw, wd)

    ccs = next(s for s in m.sections if s.text == "CCS Concepts")
    assert ccs.level == "drop"  # 기억된 선택 적용
    # 자동 계산값은 따로 보존 (번호 감지 실패 → 추출기 레벨 ## = 2)
    assert ccs.auto_level == 2
    assert ccs.auto_level != ccs.level  # 교정으로 기록됨
    intro = next(s for s in m.sections if s.text == "1 Introduction")
    assert intro.level == 1  # 기억 없는 헤더는 자동값


def test_remembered_choice_survives_different_numbering(tmp_path):
    # "3 Acknowledgments"에서 정한 걸 "5 Acknowledgments"에도 적용
    prefs.remember("3 Acknowledgments", "drop")
    raw = "# T\n\n## 5 Acknowledgments\n\nthanks\n"
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    m = build.build(raw, wd)
    ack = next(s for s in m.sections if "Acknowledgments" in s.text)
    assert ack.level == "drop"
