"""preview — 홈 미리보기 패널의 초록·대표 이미지·메모 앞부분."""

import json
import os

import pytest

from md4paper import preview
from md4paper.workdir import WorkDir

ABS = ("We present a system that helps students revise essays with feedback from a language model. "
       "In a study with forty participants, the system improved revision quality while keeping "
       "students in control of their own writing, and we discuss implications for design.")
INTRO = ("Writing is hard. Students often struggle to revise their drafts, and instructors rarely "
         "have time to give detailed feedback on every essay they receive in a large course.")
OLD = 1_000_000_000  # 폴더 수정시각을 과거로 박아 두고 변하지 않는지 본다


def _wd(tmp_path, en: str | None = None, ko: str | None = None) -> WorkDir:
    wd = WorkDir(tmp_path / "paper.md4")
    wd.ensure()
    if en is not None:
        wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    return wd


def _pin_mtime(wd: WorkDir) -> None:
    os.utime(wd.root, (OLD, OLD))


# --- 초록 추출 -------------------------------------------------------------------

def test_bold_abstract_marker_preferred_over_longer_paragraph():
    """'Abstract' 표시 뒤 문단을 고른다 — 앞의 더 긴 문단(저자 소개 등)보다 우선."""
    longer = "Some unrelated front text without a period " * 10
    md = f"# Title\n\n{longer}\n\n**Abstract**\n\n{ABS}\n\n# 1 Introduction\n\n{INTRO}\n"
    assert preview.extract_abstract(md) == ABS


def test_inline_abstract_lead_is_stripped():
    md = f"# Title\n\n**Abstract.** {ABS}\n\n## 1. Introduction\n\n{INTRO}\n"
    assert preview.extract_abstract(md) == ABS


def test_no_marker_picks_longest_paragraph_before_numbered_section():
    md = (f"# Title\n\nJane Doe\nUniversity of Somewhere\njane@uni.edu\n\n{ABS}\n\n"
          f"# 1 Introduction\n\n{INTRO} {INTRO} {INTRO}\n")
    assert preview.extract_abstract(md) == ABS


def test_unnumbered_introduction_heading_also_ends_region():
    md = f"# Title\n\n{ABS}\n\n## INTRODUCTION\n\n{INTRO} {INTRO} {INTRO}\n"
    assert preview.extract_abstract(md) == ABS


def test_skips_figures_captions_tables_and_acm_blocks():
    """그림·캡션·표·CCS/키워드/ACM 형식·저작권 문단은 아무리 길어도 초록이 아니다."""
    long_caption = "> **Figure 1:** " + "The interface shows many panels and buttons. " * 10
    acm = "Jane Doe. 2024. Title. In Proceedings of CHI. ACM, New York, NY, USA, " * 5
    table = "| a | b |\n|---|---|\n| " + "cell " * 80 + "| x |"
    md = (f"# Title\n\n![Figure 1](images/figure-1.png)\n{long_caption}\n\n{ABS}\n\n{table}\n\n"
          f"## CCS Concepts\n\n" + "Human-centered computing → Empirical studies in HCI; " * 6 + "\n\n"
          f"## ACM Reference Format:\n\n{acm}\n\n"
          "Permission to make digital or hard copies of all or part of this work " * 5 + "\n\n"
          f"# 1 Introduction\n\n{INTRO}\n")
    assert preview.extract_abstract(md) == ABS


def test_multi_paragraph_abstract_joined_until_heading():
    p2 = "Our second paragraph reports results from a deployment with two hundred students in class."
    md = f"# Title\n\n# Abstract\n\n{ABS}\n\n{p2}\n\n# Keywords\n\nwriting, AI\n\n# 1 Introduction\n\n{INTRO}\n"
    assert preview.extract_abstract(md) == f"{ABS} {p2}"


def test_structured_abstract_subheadings_are_crossed():
    md = (f"# Title\n\n# Abstract\n\n## Background\n\n{ABS}\n\n## Methods\n\n"
          "We ran a randomized experiment with forty students over two weeks.\n\n"
          f"# Keywords\n\nwriting\n\n# 1. Introduction\n\n{INTRO}\n")
    got = preview.extract_abstract(md)
    assert got.startswith(ABS) and got.endswith("over two weeks.")


def test_elsevier_spaced_abstract_takes_single_paragraph():
    """'A B S T R A C T' 판은 초록 뒤에 헤딩 없이 서론이 붙는다 → 첫 문단만."""
    md = f"# Title\n\n## A R T I C L E I N F O\n\n## A B S T R A C T\n\n{ABS}\n\n{INTRO}\n\n# 1. Background\n\n{INTRO}\n"
    assert preview.extract_abstract(md) == ABS


def test_abstract_after_misordered_introduction():
    """추출 순서가 꼬여 '# ABSTRACT'가 '# 1 INTRODUCTION' 뒤에 와도 찾는다."""
    md = f"# Title\n\nJane Doe jane@uni.edu\n\n# 1 INTRODUCTION\n\n{INTRO}\n\n# ABSTRACT\n\n{ABS}\n\n# CCS CONCEPTS\n\nx\n"
    assert preview.extract_abstract(md) == ABS


def test_markdown_stripped_but_inline_math_kept():
    md = ("# Title\n\n# Abstract\n\nWe study **bold** and *italic* claims[^1] with citations "
          "[[8, Doe 2020, A Title](#ref-8)] and [Smith, 2019](#ref-3), plus a bound $O(n*k)$ "
          "that holds <sup class=\"md-fn\"><a href=\"#fn-1\">1</a></sup> for every input we tried "
          "in the experiments described in this paper, across all of the settings.\n\n# 1 Intro\n")
    got = preview.extract_abstract(md)
    assert "**" not in got and "*italic*" not in got and "[^1]" not in got and "<sup" not in got
    assert "citations [8] and Smith, 2019" in got
    assert "$O(n*k)$" in got
    assert "bold and italic claims" in got


def test_korean_abstract_and_glued_heading():
    """한국어 '# 초록'·'# 1 서론', 그리고 빈 줄 없이 붙은 '## CCS 개념:' 헤딩은 떼어 낸다."""
    ko_abs = ("본 연구는 언어 모델의 피드백으로 학생들이 에세이를 고쳐 쓰도록 돕는 시스템을 제시한다. "
              "마흔 명이 참여한 연구에서 시스템은 학생이 글의 주도권을 유지한 채 수정의 질을 높였으며, "
              "우리는 이 결과가 설계에 주는 함의를 논의한다. 또한 교사가 피드백의 수준을 조절할 수 있는 "
              "인터페이스를 함께 설계하고, 수업 현장에 적용할 때의 한계도 정리한다.")
    md = f"# 제목\n\n# 초록\n\n{ko_abs}\n## CCS 개념: · 인간 중심 컴퓨팅\n\n# 1 서론\n\n긴 서론\n"
    assert preview.extract_abstract(md) == ko_abs
    md2 = f"# 제목\n\n김철수\n서울대학교\n\n{ko_abs}\n\n# 1 서론\n\n짧은 서론.\n"
    assert preview.extract_abstract(md2) == ko_abs


def test_no_abstract_returns_empty():
    md = "# Title\n\nJane Doe\n\n## Introduction\n\n" + INTRO * 5
    assert preview.extract_abstract(md) == ""


# --- abstract_of: 캐시 -------------------------------------------------------------

def test_abstract_of_caches_in_preview_json_and_keeps_folder_mtime(tmp_path):
    wd = _wd(tmp_path, en=f"# T\n\n# Abstract\n\n{ABS}\n\n# 1 Intro\n\n{INTRO}\n")
    _pin_mtime(wd)
    assert preview.abstract_of(wd) == ABS
    data = json.loads(wd.preview_json.read_text(encoding="utf-8"))
    assert data["abstract"] == ABS and data["abstract_src"]
    assert wd.root.stat().st_mtime == OLD  # 홈 최근순 정렬을 흔들지 않는다
    assert not wd.paper_meta_json.exists()  # 서지 파일은 만들지 않는다


def test_abstract_of_reuses_cache(tmp_path):
    wd = _wd(tmp_path, en=f"# T\n\n# Abstract\n\n{ABS}\n\n# 1 Intro\n")
    preview.abstract_of(wd)
    data = json.loads(wd.preview_json.read_text(encoding="utf-8"))
    data["abstract"] = "SENTINEL"
    wd.preview_json.write_text(json.dumps(data), encoding="utf-8")
    assert preview.abstract_of(wd) == "SENTINEL"  # md가 그대로면 다시 뽑지 않는다


def test_abstract_of_reextracts_when_md_changes(tmp_path):
    wd = _wd(tmp_path, en=f"# T\n\n# Abstract\n\n{ABS}\n\n# 1 Intro\n")
    preview.abstract_of(wd)
    data = json.loads(wd.preview_json.read_text(encoding="utf-8"))
    data["abstract"] = "SENTINEL"
    wd.preview_json.write_text(json.dumps(data), encoding="utf-8")
    new_abs = ABS.replace("forty", "four hundred and forty")
    wd.en_md.write_text(f"# T\n\n# Abstract\n\n{new_abs}\n\n# 1 Intro\n", encoding="utf-8")
    st = wd.en_md.stat()
    os.utime(wd.en_md, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert preview.abstract_of(wd) == new_abs


def test_abstract_of_ko_cached_separately(tmp_path):
    ko_abs = "본 연구는 학생들의 글쓰기 수정을 돕는 시스템을 제시하고 그 효과를 검증한다. " * 3
    wd = _wd(tmp_path, en=f"# T\n\n# Abstract\n\n{ABS}\n\n# 1 Intro\n",
             ko=f"# 제목\n\n# 초록\n\n{ko_abs}\n\n# 1 서론\n")
    assert preview.abstract_of(wd, "ko") == ko_abs.strip()
    assert preview.abstract_of(wd, "en") == ABS
    data = json.loads(wd.preview_json.read_text(encoding="utf-8"))
    assert data["abstract_ko"] == ko_abs.strip() and data["abstract"] == ABS


def test_abstract_of_missing_md(tmp_path):
    wd = _wd(tmp_path)
    assert preview.abstract_of(wd) == ""
    assert preview.abstract_of(wd, "ko") == ""
    assert not wd.preview_json.exists()


# --- thumbnail_png ---------------------------------------------------------------

def _png(path):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), "white").save(path)
    return path


@pytest.fixture
def pdf(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 260)
    path = tmp_path / "src.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _with_source(wd: WorkDir, src) -> None:
    wd.meta_json.write_text(json.dumps({"source": str(src)}), encoding="utf-8")


def test_thumbnail_prefers_lowest_numbered_figure(tmp_path, pdf):
    wd = _wd(tmp_path)
    _with_source(wd, pdf)
    for name in ("figure-12.png", "figure-2.png", "figure-1-2.png", "figure-C.png", "image-1.png"):
        _png(wd.out_images / name)
    assert preview.thumbnail_png(wd) == wd.out_images / "figure-1-2.png"
    _png(wd.out_images / "figure-1.png")
    assert preview.thumbnail_png(wd) == wd.out_images / "figure-1.png"
    assert not wd.thumb_png.exists()  # 그림이 있으면 PDF는 렌더하지 않는다


def test_thumbnail_falls_back_to_pdf_page_and_caches(tmp_path, pdf):
    wd = _wd(tmp_path)
    _png(wd.out_images / "image-1.png")  # 'image-*'(로고 등)는 대표 그림으로 치지 않는다
    _with_source(wd, pdf)
    _pin_mtime(wd)
    got = preview.thumbnail_png(wd)
    assert got == wd.thumb_png and got.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert wd.root.stat().st_mtime == OLD
    first = got.stat().st_mtime_ns
    assert preview.thumbnail_png(wd) == got and got.stat().st_mtime_ns == first  # 다시 렌더하지 않는다


def test_thumbnail_none_without_figure_or_pdf(tmp_path):
    wd = _wd(tmp_path)
    assert preview.thumbnail_png(wd) is None


def test_thumbnail_none_for_broken_pdf(tmp_path):
    pytest.importorskip("pypdfium2")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    wd = _wd(tmp_path)
    _with_source(wd, bad)
    assert preview.thumbnail_png(wd) is None
    assert not wd.thumb_png.exists()


# --- notes_excerpt ---------------------------------------------------------------

def test_notes_excerpt_strips_markdown_and_truncates(tmp_path):
    wd = _wd(tmp_path)
    wd.notes_md.write_text("# 읽을 거리\n\n- **핵심**: [링크 글](https://x.y) 참고\n\n" + "가" * 400,
                           encoding="utf-8")
    got = preview.notes_excerpt(wd, limit=50)
    assert got.startswith("읽을 거리 핵심: 링크 글 참고")
    assert "#" not in got and "**" not in got and "https" not in got
    assert len(got) == 51 and got.endswith("…")


def test_notes_excerpt_missing(tmp_path):
    assert preview.notes_excerpt(_wd(tmp_path)) == ""
