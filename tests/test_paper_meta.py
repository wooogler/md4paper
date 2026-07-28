"""논문 서지 추출·저장·로드 + 표시 헬퍼 (FakeProvider)."""

import json

from md4paper import paper_meta
from md4paper.ir import PaperMeta
from md4paper.llm import FakeProvider
from md4paper.workdir import WorkDir, rename_workdir


def test_authors_short():
    assert paper_meta.authors_short([]) == ""
    assert paper_meta.authors_short(["Minjae Lee"]) == "Minjae Lee"
    assert paper_meta.authors_short(["Minjae Lee", "Minsuk Kahng"]) == "Minjae Lee, Minsuk Kahng"
    assert paper_meta.authors_short(["A", "B", "C"]) == "A, B et al."


def test_front_text_includes_raw_and_frontmatter(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.raw_md.write_text("## Title\n\nauthor block here\n\n## Abstract\n...", encoding="utf-8")
    wd.frontmatter_txt.write_text("Proceedings of the CHI Conference 2025", encoding="utf-8")
    ft = paper_meta.front_text(wd)
    assert "author block here" in ft
    assert "Proceedings of the CHI Conference 2025" in ft


def test_extract_save_load_roundtrip(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.raw_md.write_text("## T\n\nJohn Doe, Jane Roe\n\n## Abstract\n", encoding="utf-8")
    meta = PaperMeta(title="Great Paper", authors=["John Doe", "Jane Roe"], year=2025, venue="CHI")
    fake = FakeProvider(parse_fn=lambda s, u, sc: meta, model="fake")

    got = paper_meta.extract(fake, paper_meta.front_text(wd))
    assert got.year == 2025 and got.venue == "CHI"

    paper_meta.save(wd, got)
    loaded = paper_meta.load(wd)
    assert loaded["title"] == "Great Paper"
    assert loaded["authors"] == ["John Doe", "Jane Roe"]
    assert loaded["year"] == 2025 and loaded["venue"] == "CHI"


def test_load_none_when_missing_or_broken(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.root.mkdir(parents=True)
    assert paper_meta.load(wd) is None  # 없음
    wd.paper_meta_json.write_text("{not json", encoding="utf-8")
    assert paper_meta.load(wd) is None  # 깨짐 → None


def test_folder_base_from_llm_short_title():
    m = PaperMeta(title="Continual Human-in-the-Loop Optimization of X",
                  short_title="ContinualHITLOptimization", authors=["Chen Liao", "Qian Yang"], year=2025)
    assert paper_meta.folder_base(m) == "2025_ContinualHITLOptimization_Liao"


def test_folder_base_falls_back_to_camel_when_no_short_title():
    # short_title이 비면 주 제목(콜론 앞)의 의미 단어를 CamelCase로 (규칙 폴백)
    m = PaperMeta(title="Data-Prompt Co-Evolution: Growing Test Sets", authors=["Minjae Lee"], year=2026)
    assert paper_meta.folder_base(m) == "2026_DataPromptCoEvolution_Lee"


def test_folder_base_omits_missing_pieces():
    assert paper_meta.folder_base(PaperMeta(title="Deep Nets", short_title="DeepNets")) == "DeepNets"
    assert paper_meta.folder_base(PaperMeta(title="", short_title="")) == ""  # 제목 없으면 리네임 스킵


def test_year_from_pdf_date():
    assert paper_meta._year_from_pdf_date("D:20260409012226+00'00'") == 2026
    assert paper_meta._year_from_pdf_date("2025-03-01") == 2025
    assert paper_meta._year_from_pdf_date("") is None
    assert paper_meta._year_from_pdf_date("D:18990101") is None  # 상식 범위 밖


def _seed_paper_folder(ws, stem, source_pdf):
    """<ws>/<stem>/{<stem>.pdf, <stem>.md4/extract/meta.json} 구조를 만든다 (업로드 후 상태 모사)."""
    container = ws / stem
    (container / f"{stem}.md4" / "extract").mkdir(parents=True)
    (container / f"{stem}.pdf").write_bytes(b"%PDF-1.4 fake")
    wd = WorkDir(container / f"{stem}.md4")
    wd.meta_json.write_text(json.dumps({"source": str(source_pdf)}), encoding="utf-8")
    return wd


def test_rename_workdir_moves_container_md4_pdf_and_updates_source(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    old_pdf = ws / "2505.12345" / "2505.12345.pdf"
    wd = _seed_paper_folder(ws, "2505.12345", old_pdf)

    new_wd = rename_workdir(wd, "2025_ContinualHITLOptimization_Liao", ws)

    base = ws / "2025_ContinualHITLOptimization_Liao"
    assert new_wd.root == base / "2025_ContinualHITLOptimization_Liao.md4"
    assert new_wd.root.is_dir()
    assert (base / "2025_ContinualHITLOptimization_Liao.pdf").exists()
    assert not (ws / "2505.12345").exists()  # 옛 폴더 사라짐
    # meta.json의 source가 새 PDF 경로로 갱신됨 (뷰어 안 깨지게)
    assert json.loads(new_wd.meta_json.read_text())["source"].endswith("2025_ContinualHITLOptimization_Liao.pdf")


def test_rename_workdir_uniquifies_on_collision(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "2025_Dup_Lee").mkdir()  # 이미 같은 이름 폴더 존재
    wd = _seed_paper_folder(ws, "paper", ws / "paper" / "paper.pdf")
    new_wd = rename_workdir(wd, "2025_Dup_Lee", ws)
    assert new_wd.root == ws / "2025_Dup_Lee (2)" / "2025_Dup_Lee (2).md4"
