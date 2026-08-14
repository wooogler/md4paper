"""레이아웃 자동 수정 — 청킹 라운드트립·내용 보존 검증·구조 재구축·되돌리기."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from md4paper import pipeline, relayout
from md4paper.ir import Flavor, Manifest, ManifestSection
from md4paper.llm.base import FakeProvider
from md4paper.ui.controller import UIController
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"
_ATX = re.compile(r"^#{1,6}\s+\S")

# 실제 깨짐 패턴 — 헤더가 두 줄로 쪼개짐 + 인라인 수식이 풀어헤쳐짐
BROKEN = "## 2\n\n## Background\n\nThe input is x t . The AI suggests h ( x t ) .\n"
FIXED = "## 2 Background\n\nThe input is $x_t$. The AI suggests $h(x_t)$.\n"


@pytest.fixture
def wd(tmp_path):
    w = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "broken_layout.md", w)
    return w


def _provider(fn):
    """user 프롬프트(= 청크)를 받아 수정본을 돌려주는 가짜 프로바이더."""
    return FakeProvider(complete_fn=lambda system, user: fn(user))


# --- 청킹 ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["sample_arxiv.md", "sample_ieee.md", "broken_layout.md"])
def test_split_roundtrip(name):
    raw = (CORPUS / name).read_text(encoding="utf-8")
    parts = relayout.split_for_fix(raw, target_chars=300)
    assert parts
    # 합치면 원문 그대로 — 못 고친 청크를 원문으로 되돌려 놓을 수 있어야 한다
    assert "\n".join(parts) == "\n".join(raw.splitlines())


def test_split_never_ends_a_chunk_on_a_heading():
    raw = (CORPUS / "broken_layout.md").read_text(encoding="utf-8")
    for part in relayout.split_for_fix(raw, target_chars=120)[:-1]:
        solid = [ln for ln in part.split("\n") if ln.strip()]
        # 쪼개진 헤더가 청크 경계로 갈라지면 어느 쪽에서도 합칠 수 없다
        assert not (solid and _ATX.match(solid[-1])), part


def test_split_empty():
    assert relayout.split_for_fix("") == []


# --- 내용 보존 검증 --------------------------------------------------------


def test_check_passes_layout_only_fix():
    assert relayout.check(BROKEN, FIXED) == []


def test_check_rejects_dropped_content():
    dropped = "## 2 Background\n\nThe input is $x_t$.\n"  # 뒷문장이 통째로 사라짐
    assert relayout.check(BROKEN, dropped)


def test_check_rejects_added_content():
    padded = FIXED + "\n" + "This paragraph was invented by the model. " * 20
    assert relayout.check(BROKEN, padded)


def test_check_rejects_empty():
    assert relayout.check(BROKEN, "   ") == ["빈 응답"]


def test_content_key_ignores_markup():
    assert relayout.content_key("## 2\n\n## Background") == relayout.content_key("## 2 Background")
    assert relayout.content_key("x t .") == relayout.content_key("$x_t$.")


# --- 청크 수정 -------------------------------------------------------------


def test_fix_chunk_protects_images():
    """이미지는 센티넬로 가려져 LLM에 노출되지 않고, 결과에 원본 그대로 돌아온다."""
    src = "![](images/fig-01.jpeg)\n\nFigure 1: A caption.\n"
    seen = {}

    def fn(user: str) -> str:
        seen["user"] = user
        return user.replace("Figure 1: A caption.", "**Figure 1:** *A caption.*")

    out, status, problems = relayout.fix_chunk(_provider(fn), "sys", src)
    assert "images/fig-01.jpeg" not in seen["user"]  # 가려짐
    assert "![](images/fig-01.jpeg)" in out and status == "fixed" and problems == []


def test_fix_chunk_keeps_original_when_content_lost():
    out, status, problems = relayout.fix_chunk(_provider(lambda u: "## 2 Background\n"), "sys", BROKEN)
    assert out == BROKEN and status == "kept" and problems  # 원문 그대로 + 이유


def test_fix_chunk_unwraps_code_fence():
    out, status, _ = relayout.fix_chunk(_provider(lambda u: f"```markdown\n{FIXED.strip()}\n```"), "sys", BROKEN)
    assert not out.lstrip().startswith("```") and "## 2 Background" in out and status == "fixed"


def test_fix_chunk_retries_then_keeps():
    calls = []

    def fn(user: str) -> str:
        calls.append(user)
        return "gone"

    out, status, _ = relayout.fix_chunk(_provider(fn), "sys", BROKEN)
    assert len(calls) == 2 and out == BROKEN and status == "kept"  # 한 번 재시도 후 포기


def test_fix_markdown_preserves_chunk_seams():
    raw = (CORPUS / "broken_layout.md").read_text(encoding="utf-8")
    fixed, summary = relayout.fix_markdown(raw, _provider(lambda u: u), workers=1)
    assert fixed == raw and summary["fixed"] == 0 and summary["unchanged"] == summary["chunks"]


def test_build_system_prompt_appends_user_instructions():
    prompt = relayout.build_system_prompt("머리글 줄을 지워줘")
    assert "머리글 줄을 지워줘" in prompt and prompt.startswith(relayout.build_system_prompt())


# --- 전체 실행: 구조 재구축 -------------------------------------------------


def _merge_split_heading(text: str) -> str:
    """가짜 LLM — 쪼개진 헤더만 합친다 (실제 수정과 같은 종류의 변경)."""
    return text.replace("## 2\n\n## Background", "## 2 Background")


def test_run_rebuilds_section_tree(wd):
    ctrl = UIController(wd)
    assert any(s.text == "2" for s in ctrl.manifest.sections)  # 깨진 상태: '2'가 헤더 하나

    summary = ctrl.fix_layout(_provider(_merge_split_heading))

    assert summary["changed"] and summary["fixed"] >= 1
    assert "## 2 Background" in wd.raw_md.read_text(encoding="utf-8")
    texts = [s.text for s in ctrl.manifest.sections]
    assert "2 Background" in texts and "2" not in texts  # 섹션 트리가 고친 문서를 따라간다
    assert "# 2 Background" in wd.en_md.read_text(encoding="utf-8")
    # 일괄 레벨 조정 그룹도 새 구조 기준 (번호 있는 헤더로 잡힘)
    assert any(g["scheme"] == "dotted-arabic" for g in ctrl.level_groups())


def test_run_keeps_manifest_object_identity(wd):
    """UI 클로저가 붙들고 있는 manifest 객체가 그대로 갱신돼야 섹션 트리가 새 구조를 그린다."""
    ctrl = UIController(wd)
    before = ctrl.manifest
    ctrl.fix_layout(_provider(_merge_split_heading))
    assert ctrl.manifest is before
    assert "2 Background" in [s.text for s in before.sections]


def test_run_inherits_settings_and_user_levels(wd):
    ctrl = UIController(wd)
    ctrl.set_setting("flavor", "obsidian")
    ctrl.set_setting("korean_style", "합니다체")
    ctrl.set_setting("citation_parts", ["number", "short"])
    intro = next(s for s in ctrl.manifest.sections if s.text.startswith("1 Introduction"))
    ctrl.set_level(intro.id, "drop")  # 자동값과 다른 사용자 선택
    refs = next(s for s in ctrl.manifest.sections if s.text == "References")
    refs.translate = False
    ctrl.save()

    ctrl.fix_layout(_provider(_merge_split_heading))

    assert ctrl.manifest.flavor is Flavor.OBSIDIAN
    assert ctrl.manifest.korean_style == "합니다체"
    assert ctrl.manifest.citation_parts == ["number", "short"]
    assert next(s for s in ctrl.manifest.sections if s.text.startswith("1 Introduction")).level == "drop"
    assert next(s for s in ctrl.manifest.sections if s.text == "References").translate is False


def test_inherit_carries_settings_and_user_choices():
    """구조는 새로 잡되 문서 설정·사용자 교정·번역 제외는 헤더 이름으로 이어받는다 (prefs와 무관하게)."""
    old = Manifest(
        title="옛 제목", korean_style="합니다체", flavor=Flavor.OBSIDIAN,
        citation_parts=["number", "short"], translate_headers=False,
        sections=[
            ManifestSection(id="h_0000", text="Background", line=0, level="drop", auto_level=2),
            ManifestSection(id="h_0001", text="References", line=9, level=1, auto_level=1, translate=False),
        ])
    new = Manifest(title="새 제목", sections=[
        ManifestSection(id="h_0000", text="2 Background", line=0, level=2, auto_level=2),
        ManifestSection(id="h_0001", text="References", line=9, level=1, auto_level=1),
    ])

    relayout.inherit(new, old)

    assert new.title == "새 제목"  # 제목은 고친 문서 기준 (구조에서 다시 뽑는다)
    assert new.korean_style == "합니다체" and new.flavor is Flavor.OBSIDIAN
    assert new.citation_parts == ["number", "short"] and new.translate_headers is False
    assert new.sections[0].level == "drop"  # 번호가 새로 붙어도 같은 이름으로 이어받는다
    assert new.sections[1].translate is False


def test_custom_prompt_reaches_the_model(wd):
    seen: list[str] = []

    def fn(system: str, user: str) -> str:
        seen.append(system)
        return _merge_split_heading(user)

    UIController(wd).fix_layout(FakeProvider(complete_fn=fn), "표는 손대지 마")
    assert seen and all("표는 손대지 마" in s for s in seen)


def test_run_noop_when_llm_changes_nothing(wd):
    ctrl = UIController(wd)
    before = wd.raw_md.read_text(encoding="utf-8")
    summary = ctrl.fix_layout(_provider(lambda u: u))
    assert summary["changed"] is False
    assert wd.raw_md.read_text(encoding="utf-8") == before
    assert not ctrl.can_undo_layout_fix()  # 안 바뀌었으면 스냅샷도 안 남긴다


def test_undo_restores_previous_state(wd):
    ctrl = UIController(wd)
    raw_before = wd.raw_md.read_text(encoding="utf-8")
    yaml_before = wd.sections_yaml.read_text(encoding="utf-8")

    ctrl.fix_layout(_provider(_merge_split_heading))
    assert ctrl.can_undo_layout_fix()

    assert ctrl.undo_layout_fix() is True
    assert wd.raw_md.read_text(encoding="utf-8") == raw_before
    assert wd.sections_yaml.read_text(encoding="utf-8") == yaml_before
    assert "2" in [s.text for s in ctrl.manifest.sections]
    assert not ctrl.can_undo_layout_fix()  # 스냅샷은 한 번 쓰면 소비된다
    assert ctrl.undo_layout_fix() is False


def test_run_requires_raw_md(tmp_path):
    empty = WorkDir(tmp_path / "empty.md4")
    with pytest.raises(relayout.LayoutFixError):
        relayout.run(empty, _provider(lambda u: u))


def test_layout_fix_plan(wd):
    plan = UIController(wd).layout_fix_plan()
    assert plan["chunks"] >= 1 and plan["chars"] > 0
