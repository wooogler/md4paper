"""미리보기에서 서지 편집 — 사용자 수정이 이기고(보강·재추출이 덮지 않음), .bib·이름이 따라온다."""

import json
import os
from pathlib import Path

import pytest

from md4paper import bibsource, bibtex, config, enrich, paper_meta, preview, projects
from md4paper.ir import PaperMeta, StoredPaperMeta
from md4paper.workdir import WorkDir, set_projects

OLD = 1_000_000_000  # 폴더 수정시각을 과거로 박아 두고 변하지 않는지 본다


def _wd(tmp_path: Path, name: str = "2412.01234v2", meta: dict | None = None) -> WorkDir:
    """<ws>/<이름>/<이름>.md4 구조 (rename_workdir가 기대하는 모양)."""
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    if meta is not None:
        paper_meta.save(wd, StoredPaperMeta(**meta))
    return wd


def _pin(wd: WorkDir) -> None:
    os.utime(wd.root, (OLD, OLD))


ATTN = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017, "venue": "NeurIPS", "short_title": "Attention"}


# --- 저장 형식 ------------------------------------------------------------------

def test_old_paper_meta_without_user_fields_still_reads(tmp_path):
    wd = _wd(tmp_path)
    wd.paper_meta_json.write_text(json.dumps({"title": "Old", "doi": "10.1/x"}), encoding="utf-8")
    got = paper_meta.update_fields(wd, {"year": 2001})
    assert got["user_fields"] == ["year"] and got["doi"] == "10.1/x" and got["title"] == "Old"


# --- update_fields: 검증 ----------------------------------------------------------

def test_update_fields_normalizes_values(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    got = paper_meta.update_fields(wd, {
        "title": "  Attention   Is All\nYou Need!  ",
        "authors": [" Ashish  Vaswani ", "", "  "],
        "year": "2018",
        "doi": "https://doi.org/10.5555/ABC.def",
        "short_title": 'Att/en tion?:X',
        "venue": "  NeurIPS  2017 ",
    })
    assert got["title"] == "Attention Is All You Need!"
    assert got["authors"] == ["Ashish Vaswani"]
    assert got["year"] == 2018
    assert got["doi"] == "10.5555/ABC.def"  # 접두만 떼고 대소문자는 그대로
    assert got["short_title"] == "AttentionX"
    assert got["venue"] == "NeurIPS 2017"
    assert paper_meta.update_fields(wd, {"doi": "doi: 10.1/Q"})["doi"] == "10.1/Q"
    assert paper_meta.update_fields(wd, {"year": None})["year"] is None


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"year": 1799}, {"year": 2101}, {"year": "nineteen"}, {"year": 2017.5},
    {"abstract": "x"},
])
def test_update_fields_rejects_bad_values_without_writing(tmp_path, changes):
    wd = _wd(tmp_path, meta=ATTN)
    before = wd.paper_meta_json.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        paper_meta.update_fields(wd, {"venue": "CHI", **changes})  # 앞의 좋은 칸도 저장되지 않는다
    assert wd.paper_meta_json.read_text(encoding="utf-8") == before


def test_update_fields_accumulates_user_fields_in_order(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    paper_meta.update_fields(wd, {"year": 2018})
    paper_meta.update_fields(wd, {"title": "New Title", "year": 2019})
    got = paper_meta.load(wd)
    assert got["user_fields"] == ["year", "title"]  # 집합처럼, 처음 고친 순서대로
    assert got["year"] == 2019 and got["title"] == "New Title"


def test_update_fields_same_value_does_not_write_or_mark(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    os.utime(wd.paper_meta_json, (OLD, OLD))
    got = paper_meta.update_fields(wd, {"title": "Attention Is All You Need", "year": 2017})
    assert got["user_fields"] == [] and got["year"] == 2017
    assert wd.paper_meta_json.stat().st_mtime == OLD  # 파일을 다시 쓰지 않았다


def test_update_fields_starts_from_empty_when_no_meta(tmp_path):
    wd = _wd(tmp_path)
    got = paper_meta.update_fields(wd, {"title": "Hand Typed"})
    assert got["title"] == "Hand Typed" and got["user_fields"] == ["title"]
    assert paper_meta.load(wd)["title"] == "Hand Typed"


def test_update_fields_keeps_folder_mtime(tmp_path):
    """paper_meta.json·bib_source.json을 새로 만들어도(폴더 mtime이 바뀌는 경우) 되돌린다."""
    wd = _wd(tmp_path)
    _pin(wd)
    before = wd.root.stat().st_mtime_ns
    paper_meta.update_fields(wd, {"title": "Hand Typed"})
    assert wd.root.stat().st_mtime_ns == before


# --- update_fields: 출판 기록·BibTeX -------------------------------------------------

RECORD = {"title": "Attention is All you Need", "authors": ["A. Vaswani", "N. Shazeer"],
          "author_latex": "Vaswani, A. and Shazeer, N.", "year": 2017,
          "venue": "Advances in Neural Information Processing Systems", "kind": "inproceedings",
          "doi": "10.5555/3295222.3295349", "pages": "5998--6008", "meta_source": "s2+crossref"}


def test_update_fields_reflects_into_bib_source_and_bibtex(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    bibsource.save(wd, RECORD)

    paper_meta.update_fields(wd, {"title": "Attention Is All You Need",  # 그대로 → 기록도 그대로
                                  "authors": ["Ashish Vaswani", "Noam M. Shazeer"],
                                  "venue": "NeurIPS 2017", "short_title": "Attn"})

    rec = bibsource.load(wd)
    assert rec["authors"] == ["Ashish Vaswani", "Noam M. Shazeer"]
    assert "author_latex" not in rec  # 출판사 저자 표기가 사용자 값을 이기지 않게
    assert rec["venue"] == "NeurIPS 2017"
    assert rec["title"] == RECORD["title"]  # 고치지 않은 칸은 기록 값 그대로
    assert rec["pages"] == "5998--6008" and "short_title" not in rec
    _key, entry = bibtex.entry_for(wd)
    assert "Shazeer, Noam M." in entry and "NeurIPS 2017" in entry


# --- 보강·재추출이 사용자 칸을 지킨다 ---------------------------------------------------

def test_enrich_skips_user_fields(tmp_path, monkeypatch):
    found = {"title": ATTN["title"], "year": 2017, "venue": "NeurIPS",
             "doi": "10.1/online", "source": "openalex"}
    monkeypatch.setattr(enrich, "lookup", lambda *a, **k: dict(found))
    wd = _wd(tmp_path, meta={**ATTN, "year": None, "venue": ""})
    paper_meta.update_fields(wd, {"year": None, "doi": ""})  # 값은 같지만 → 표시도 안 된다
    paper_meta.update_fields(wd, {"venue": "Mine"})
    paper_meta.update_fields(wd, {"venue": ""})  # 일부러 비운 venue

    assert enrich.enrich_workdir(wd) == ["year"]  # venue는 사용자 칸이라 비어 있어도 채우지 않는다
    got = paper_meta.load(wd)
    assert got["venue"] == "" and got["year"] == 2017 and got["user_fields"] == ["venue"]
    assert got["doi"] == "10.1/online"

    meta = {**ATTN, "year": None, "doi": "", "user_fields": ["year", "doi"]}
    out, filled = enrich.enrich_meta(meta)
    assert filled == [] and out["doi"] == ""  # 사용자 DOI 칸도 지킨다


def test_boost_skips_user_fields_and_record_follows_user(tmp_path, monkeypatch):
    from md4paper.cite import resolve as cite_resolve

    monkeypatch.setattr(enrich, "lookup", lambda *a, **k: {
        "title": "My Title", "year": 1999, "venue": "Online Venue", "doi": "", "source": "openalex"})
    monkeypatch.setattr(bibsource, "fetch", lambda *a, **k: dict(RECORD))
    monkeypatch.setattr(cite_resolve, "update_workdir", lambda *a, **k: {})
    wd = _wd(tmp_path, meta={**ATTN, "venue": ""})
    paper_meta.update_fields(wd, {"title": "My Title", "year": None})

    got = enrich.boost_workdir(wd)

    assert got["fields"] == ["venue"]  # 연도는 사용자가 비웠다
    meta = paper_meta.load(wd)
    assert meta["year"] is None and meta["title"] == "My Title"
    rec = bibsource.load(wd)  # 처음 받는 기록도 사용자 값을 따른다
    assert rec["title"] == "My Title" and rec["year"] is None
    assert rec["venue"] == RECORD["venue"]


def test_refetch_overlays_user_values_and_fills_doi(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(title, **kw):  # noqa: ANN001, ANN202
        calls.append(title)
        return dict(RECORD)

    monkeypatch.setattr(bibsource, "fetch", fake_fetch)
    wd = _wd(tmp_path, meta=ATTN)
    bibsource.save(wd, {**RECORD, "title": "stale"})
    paper_meta.update_fields(wd, {"authors": ["Ashish Vaswani"], "year": 2018})
    _pin(wd)
    before = wd.root.stat().st_mtime_ns

    rec = bibsource.refetch(wd)

    assert calls == [ATTN["title"]]  # 받아 둔 기록이 있어도 새로 받는다
    assert rec["title"] == RECORD["title"]
    assert rec["authors"] == ["Ashish Vaswani"] and "author_latex" not in rec
    assert rec["year"] == 2018
    assert bibsource.load(wd) == rec
    assert paper_meta.load(wd)["doi"] == RECORD["doi"]  # '서지 ✓'가 보는 DOI
    assert wd.root.stat().st_mtime_ns == before

    monkeypatch.setattr(bibsource, "fetch", lambda *a, **k: None)
    assert bibsource.refetch(wd) is None
    assert bibsource.load(wd) == rec  # 못 찾으면 받아 둔 기록을 지우지 않는다


def test_refetch_keeps_old_values_missing_from_new_record(tmp_path, monkeypatch):
    """다시 받은 기록에 venue·pages가 빠져도 옛 값이 남아 .bib의 booktitle이 사라지지 않는다."""
    sparse = {"title": RECORD["title"], "authors": [], "year": 2017, "venue": "",
              "kind": "inproceedings", "doi": "", "retracted": False, "meta_source": "s2"}
    monkeypatch.setattr(bibsource, "fetch", lambda *a, **k: dict(sparse))
    wd = _wd(tmp_path, meta=ATTN)
    bibsource.save(wd, {**RECORD, "retracted": True})

    rec = bibsource.refetch(wd)

    assert rec["venue"] == RECORD["venue"] and rec["pages"] == "5998--6008"
    assert rec["doi"] == RECORD["doi"]
    assert rec["authors"] == RECORD["authors"]  # 저자가 비면 옛 저자·표기를 함께 남긴다
    assert rec["author_latex"] == RECORD["author_latex"]
    assert rec["retracted"] is False and rec["meta_source"] == "s2"  # 값이 있는 칸은 새 기록이 이긴다
    _key, entry = bibtex.entry_for(wd)
    assert "Advances in Neural Information Processing Systems" in entry


def test_refetch_new_authors_do_not_borrow_old_author_latex(tmp_path, monkeypatch):
    monkeypatch.setattr(bibsource, "fetch", lambda *a, **k: {
        "title": RECORD["title"], "authors": ["Ashish Vaswani", "Noam Shazeer"], "venue": "NeurIPS"})
    wd = _wd(tmp_path, meta=ATTN)
    bibsource.save(wd, RECORD)

    rec = bibsource.refetch(wd)

    assert rec["authors"] == ["Ashish Vaswani", "Noam Shazeer"] and "author_latex" not in rec
    assert rec["venue"] == "NeurIPS" and rec["pages"] == "5998--6008"


class _Provider:
    def __init__(self, meta: PaperMeta):
        self.meta = meta

    def parse(self, system, text, model, max_tokens=1024):  # noqa: ANN001, ANN201
        return self.meta.model_copy()


def test_reextract_keeps_user_fields_and_stored_extras(tmp_path):
    wd = _wd(tmp_path, meta={**ATTN, "doi": "10.1/x", "meta_source": "crossref"})
    wd.raw_md.write_text("# Attention\n\nAshish Vaswani\n", encoding="utf-8")
    paper_meta.update_fields(wd, {"title": "User Title", "short_title": "UserShort"})
    extracted = PaperMeta(title="LLM Title", authors=["A. V."], year=2016,
                          venue="Journal Title", short_title="LLMShort")

    got = paper_meta.reextract(wd, _Provider(extracted))

    assert got["title"] == "User Title" and got["short_title"] == "UserShort"
    assert got["authors"] == ["A. V."] and got["year"] == 2016
    assert got["venue"] == ""  # 템플릿 자리표시자는 extract가 비운다
    assert got["doi"] == "10.1/x" and got["user_fields"] == ["title", "short_title"]
    assert paper_meta.load(wd) == got


def test_reextract_without_text_raises(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    with pytest.raises(ValueError):
        paper_meta.reextract(wd, _Provider(PaperMeta(title="x")))


# --- 이름 규칙 -------------------------------------------------------------------

def test_naming_base_for_uses_primary_collection_rule(tmp_path):
    assert paper_meta.naming_base_for(_wd(tmp_path, "empty")) == ""
    wd = _wd(tmp_path, meta=ATTN)
    assert paper_meta.naming_base_for(wd) == "2017_Attention_Vaswani"
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    projects.set_setting(pid, "naming", "{author}-{year}")
    set_projects(wd.root, [pid])
    assert paper_meta.naming_base_for(wd) == "Vaswani-2017"


def test_rename_to_naming_moves_folder_and_cleans_old_copies(tmp_path):
    ws = tmp_path / "ws"
    wd = _wd(tmp_path, meta=ATTN)
    wd.en_md.write_text("# T\n", encoding="utf-8")
    pdf = wd.root.parent / f"{wd.root.stem}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    wd.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    from md4paper import library

    library.export_paper(wd)
    assert (tmp_path / "EN" / "2412.01234v2.md").exists()

    paper_meta.update_fields(wd, {"year": 2018})
    assert wd.root.stem == "2412.01234v2"  # 서지를 고쳐도 이름은 저절로 바뀌지 않는다
    new_wd = paper_meta.rename_to_naming(wd, ws)

    assert new_wd.root == (ws / "2018_Attention_Vaswani" / "2018_Attention_Vaswani.md4").resolve()
    assert (tmp_path / "EN" / "2018_Attention_Vaswani.md").exists()
    assert not (tmp_path / "EN" / "2412.01234v2.md").exists()
    assert (tmp_path / "PDF" / "2018_Attention_Vaswani.pdf").exists()
    assert not (tmp_path / "PDF" / "2412.01234v2.pdf").exists()
    assert paper_meta.rename_to_naming(new_wd, ws) is new_wd  # 이미 그 이름 → 그대로


def test_rename_to_naming_without_meta_returns_same(tmp_path):
    wd = _wd(tmp_path, "no-meta")
    assert paper_meta.rename_to_naming(wd, tmp_path / "ws") is wd
    assert wd.root.is_dir()


# --- 초록 -----------------------------------------------------------------------

ABS = ("We present a system that helps students revise essays with feedback from a language model. "
       "In a study with forty participants, the system improved revision quality while keeping "
       "students in control of their own writing, and we discuss implications for design.")


def test_set_abstract_wins_over_md_changes_and_keeps_mtime(tmp_path):
    wd = _wd(tmp_path)
    wd.en_md.write_text(f"# T\n\n**Abstract**\n\n{ABS}\n\n# 1 Introduction\n\nBody.\n", encoding="utf-8")
    assert preview.abstract_of(wd, "en") == ABS
    _pin(wd)
    before = wd.root.stat().st_mtime_ns

    preview.set_abstract(wd, "en", "  My own summary.  ")
    preview.set_abstract(wd, "ko", "내가 쓴 초록")  # 한국어 md가 없어도 사용자 값은 보인다

    assert wd.root.stat().st_mtime_ns == before
    wd.en_md.write_text(f"# T\n\n**Abstract**\n\n{ABS} More.\n", encoding="utf-8")  # md가 바뀌어도
    assert preview.abstract_of(wd, "en") == "My own summary."
    assert preview.abstract_of(wd, "ko") == "내가 쓴 초록"
    cache = json.loads(wd.preview_json.read_text(encoding="utf-8"))
    assert cache["abstract_src"] == "user" and cache["abstract_ko_src"] == "user"
    with pytest.raises(ValueError):
        preview.set_abstract(wd, "fr", "x")
