"""저장 위치(라이브러리) — 변환한 논문이 쌓일 전역 폴더, 영어·한국어 따로."""

from pathlib import Path

import pytest

from md4paper import config, library
from md4paper.workdir import WorkDir


def _wd(tmp_path: Path, name: str = "2017_Attention_Vaswani",
        en: str | None = "# T\n\n![Figure 1](images/fig-01.png)\n",
        ko: str | None = "# 제목\n\n![그림 1](images/fig-01.png)\n") -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    if en is not None:
        wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    (wd.out_images / "fig-01.png").write_bytes(b"PNG-1")
    (wd.out_images / "fig-99.png").write_bytes(b"unreferenced")
    return wd


def test_unset_by_default(tmp_path):
    assert library.dir_for("en") is None and library.dir_for("ko") is None
    assert library.configured() is False
    assert library.auto_export(_wd(tmp_path)) == []  # 폴더 없으면 아무 데도 안 쓴다


def test_exports_en_and_ko_to_separate_folders(tmp_path):
    en_dir, ko_dir = tmp_path / "EN", tmp_path / "KO"
    config.set_library_dir("en", str(en_dir))
    config.set_library_dir("ko", str(ko_dir))
    wd = _wd(tmp_path)

    written = library.export_paper(wd)

    assert written == [en_dir / "2017_Attention_Vaswani.md", ko_dir / "2017_Attention_Vaswani.md"]
    # 이미지는 논문별 하위 폴더로 격리되고, 마크다운 참조도 거기로 고쳐진다
    img = en_dir / "images" / "2017_Attention_Vaswani" / "fig-01.png"
    assert img.read_bytes() == b"PNG-1"
    assert "](images/2017_Attention_Vaswani/fig-01.png)" in written[0].read_text(encoding="utf-8")
    assert "](images/2017_Attention_Vaswani/fig-01.png)" in written[1].read_text(encoding="utf-8")
    # 본문이 참조하지 않은 이미지는 복사하지 않는다
    assert not (en_dir / "images" / "2017_Attention_Vaswani" / "fig-99.png").exists()


def test_accumulates_papers_and_overwrites_same_paper(tmp_path):
    en_dir = tmp_path / "EN"
    config.set_library_dir("en", str(en_dir))
    first = _wd(tmp_path, "paper-a")
    second = _wd(tmp_path, "paper-b")
    library.export_paper(first)
    library.export_paper(second)

    assert {p.name for p in en_dir.glob("*.md")} == {"paper-a.md", "paper-b.md"}

    # 같은 논문을 다시 내보내면 새 파일이 아니라 덮어쓴다 (버전이 아니라 논문이 쌓인다)
    first.en_md.write_text("# 고친 제목\n", encoding="utf-8")
    library.export_paper(first)
    assert len(list(en_dir.glob("*.md"))) == 2
    assert (en_dir / "paper-a.md").read_text(encoding="utf-8").startswith("# 고친 제목")


def test_same_folder_for_both_languages_gets_lang_suffix(tmp_path):
    both = tmp_path / "Papers"
    config.set_library_dir("en", str(both))
    config.set_library_dir("ko", str(both))
    wd = _wd(tmp_path)

    written = library.export_paper(wd)

    # 한 폴더에 두 언어를 넣으면 이름이 겹치므로 .en/.ko로 구분한다
    assert [p.name for p in written] == ["2017_Attention_Vaswani.en.md", "2017_Attention_Vaswani.ko.md"]


def test_only_ko_folder_exports_only_translation(tmp_path):
    ko_dir = tmp_path / "KO"
    config.set_library_dir("ko", str(ko_dir))
    written = library.export_paper(_wd(tmp_path))
    assert [p.parent for p in written] == [ko_dir]


def test_skips_missing_markdown(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("ko", str(tmp_path / "KO"))
    wd = _wd(tmp_path, ko=None)  # 아직 번역 안 함
    written = library.export_paper(wd)
    assert [p.name for p in written] == ["2017_Attention_Vaswani.md"]
    assert not (tmp_path / "KO").exists()


def test_export_target_applied(tmp_path):
    """내보내기 형식(Obsidian) 설정이 라이브러리 사본에도 적용된다 — 위키 임베드 + 논문별 경로."""
    en_dir = tmp_path / "EN"
    config.set_library_dir("en", str(en_dir))
    config.set_section_value("output", "export_target", "obsidian")
    library.export_paper(_wd(tmp_path))
    md = (en_dir / "2017_Attention_Vaswani.md").read_text(encoding="utf-8")
    assert "![[images/2017_Attention_Vaswani/fig-01.png]]" in md


def test_auto_export_respects_switch(tmp_path):
    en_dir = tmp_path / "EN"
    config.set_library_dir("en", str(en_dir))
    wd = _wd(tmp_path)

    config.set_section_value("library", "auto", False)
    assert library.auto_export(wd) == []
    assert not en_dir.exists()

    config.set_section_value("library", "auto", True)
    assert library.auto_export(wd) == [en_dir / "2017_Attention_Vaswani.md"]


def test_auto_export_never_raises(tmp_path, monkeypatch):
    """자동 저장이 실패해도(권한 등) 변환·번역 흐름은 계속돼야 한다."""
    config.set_library_dir("en", str(tmp_path / "EN"))

    def boom(*a, **kw):
        raise PermissionError("읽기 전용 폴더")

    monkeypatch.setattr(library, "export", boom)
    assert library.auto_export(_wd(tmp_path)) == []


def test_export_many_counts(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    roots = [_wd(tmp_path, f"paper-{i}").root for i in range(3)]
    assert library.export_many(roots) == (3, 0)


def test_config_roundtrip_and_unset():
    config.set_library_dir("en", "~/Papers/EN")
    assert config.resolve_library_dir("en") == Path.home() / "Papers" / "EN"
    config.set_library_dir("en", None)
    assert config.resolve_library_dir("en") is None
    # 해제는 키 삭제 — "None" 문자열이 남으면 안 된다
    assert "None" not in (config.CONFIG_PATH.read_text(encoding="utf-8") if config.CONFIG_PATH.exists() else "")
    with pytest.raises(ValueError):
        config.resolve_library_dir("fr")


# --- 원본 PDF 사본 (md와 같은 기준명 → md에서 바로 찾아진다) ---
def _add_pdf(wd: WorkDir, tmp_path: Path) -> Path:
    import json

    pdf = wd.root.parent / f"{wd.root.stem}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    wd.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    return pdf


def test_export_pdf_alongside_markdown(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    wd = _wd(tmp_path, ko=None)
    _add_pdf(wd, tmp_path)

    written = library.export_paper(wd)

    assert tmp_path / "PDF" / "2017_Attention_Vaswani.pdf" in written
    assert (tmp_path / "PDF" / "2017_Attention_Vaswani.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()  # md·PDF 기준명 일치


def test_export_pdf_skips_when_no_pdf_or_no_dir(tmp_path):
    wd = _wd(tmp_path, ko=None)
    assert library.export_pdf(wd) is None  # 폴더 미설정
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    assert library.export_pdf(wd) is None  # .md 입력 등 원본 PDF 없음
    assert not (tmp_path / "PDF").exists()


def test_remove_stem_cleans_old_copies(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    wd = _wd(tmp_path, "old-name", ko=None)
    _add_pdf(wd, tmp_path)
    library.export_paper(wd)
    assert (tmp_path / "EN" / "old-name.md").exists()

    library.remove_stem("old-name")

    assert not (tmp_path / "EN" / "old-name.md").exists()
    assert not (tmp_path / "EN" / "images" / "old-name").exists()
    assert not (tmp_path / "PDF" / "old-name.pdf").exists()


# --- 논문 삭제 시 라이브러리 사본 정리 ---
def _exported(tmp_path: Path):
    """저장 위치를 설정하고 논문 하나를 실제로 내보낸 상태."""
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    wd = _wd(tmp_path, ko=None)
    _add_pdf(wd, tmp_path)
    library.export_paper(wd)
    assert (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()
    return wd, tmp_path / "ws"


def test_delete_workdir_also_clears_library_copies(tmp_path):
    """기본값 — 사본까지 지운다. 안 지우면 사용자 볼트에 고아 파일이 남는다."""
    from md4paper.workdir import delete_workdir

    wd, ws = _exported(tmp_path)
    assert delete_workdir(wd.root, ws) is True
    assert not wd.root.exists()
    assert not (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()
    assert not (tmp_path / "EN" / "images" / "2017_Attention_Vaswani").exists()
    assert not (tmp_path / "PDF" / "2017_Attention_Vaswani.pdf").exists()


def test_delete_workdir_can_keep_library_copies(tmp_path):
    """체크박스를 끈 경우 — 작업 폴더만 지우고 볼트는 건드리지 않는다."""
    from md4paper.workdir import delete_workdir

    wd, ws = _exported(tmp_path)
    assert delete_workdir(wd.root, ws, with_library=False) is True
    assert not wd.root.exists()
    assert (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()
    assert (tmp_path / "PDF" / "2017_Attention_Vaswani.pdf").exists()


def test_delete_workdir_rejected_leaves_library_alone(tmp_path):
    """안전 검사에 걸려 거부되면 사본도 그대로 — 삭제는 검사를 통과한 뒤에만 일어난다."""
    from md4paper.workdir import delete_workdir

    wd, _ = _exported(tmp_path)
    assert delete_workdir(wd.root, tmp_path / "other-ws") is False  # 작업 폴더 밖
    assert wd.root.exists()
    assert (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()


# --- 기존 논문 이름 정리 (이름 규칙 일괄 적용) ---
def test_apply_naming_renames_folder_pdf_and_library(tmp_path):
    import json

    from md4paper import paper_meta
    from md4paper.ir import PaperMeta

    ws = tmp_path / "ws"
    wd = _wd(tmp_path, "2412.01234v2", ko=None)  # arXiv 다운로드명 같은 원래 이름
    (wd.structure).mkdir(parents=True, exist_ok=True)
    wd.sections_yaml.write_text("title: T\nsections: []\n", encoding="utf-8")
    pdf = _add_pdf(wd, tmp_path)
    paper_meta.save(wd, PaperMeta(title="Attention Is All You Need", short_title="Attention",
                                  authors=["Ashish Vaswani"], year=2017))
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    library.export_paper(wd)  # 옛 이름으로 이미 내보낸 상태
    assert (tmp_path / "EN" / "2412.01234v2.md").exists()

    counts = paper_meta.apply_naming(ws)

    assert counts == {"renamed": 1, "unchanged": 0, "no_meta": 0}
    new_container = ws / "2017_Attention_Vaswani"
    assert (new_container / "2017_Attention_Vaswani.md4").is_dir()  # 폴더·.md4 리네임
    assert (new_container / "2017_Attention_Vaswani.pdf").exists()  # 작업 폴더의 PDF도
    assert not pdf.exists()
    # 저장 위치: 새 이름으로 다시 내보내고 옛 이름 사본은 청소
    assert (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()
    assert not (tmp_path / "EN" / "2412.01234v2.md").exists()
    assert (tmp_path / "PDF" / "2017_Attention_Vaswani.pdf").exists()
    assert not (tmp_path / "PDF" / "2412.01234v2.pdf").exists()
    # meta.json의 source 경로도 새 PDF를 가리킨다 (뷰어 PDF 대조가 안 깨지게)
    new_wd = WorkDir(new_container / "2017_Attention_Vaswani.md4")
    assert json.loads(new_wd.meta_json.read_text())["source"].endswith("2017_Attention_Vaswani.pdf")

    # 두 번째 실행은 아무것도 바꾸지 않는다 (안정적)
    assert paper_meta.apply_naming(ws) == {"renamed": 0, "unchanged": 1, "no_meta": 0}


def test_apply_naming_takes_name_held_by_upload_stub(tmp_path):
    """변환 안 된 업로드 잔여물이 이름을 점유해도 (2)를 붙이지 않고 그 폴더를 흡수한다.

    실사용에서 40편을 올린 뒤 변환 전에 중단 → 재업로드했더니 PDF만 든 껍데기가 이름을 막아
    변환 결과가 전부 '<이름> (2)'가 됐다. 그 상황을 그대로 재현한 회귀 테스트.
    """
    from md4paper import paper_meta
    from md4paper.ir import PaperMeta

    ws = tmp_path / "ws"
    stub = ws / "2017_Attention_Vaswani"  # 중단된 업로드가 남긴 껍데기 (PDF만)
    stub.mkdir(parents=True)
    (stub / "2017_Attention_Vaswani.pdf").write_bytes(b"%PDF old")

    wd = _wd(tmp_path, "2017_Attention_Vaswani (2)", ko=None)  # 재업로드로 만들어진 (2) 논문
    wd.sections_yaml.write_text("title: T\nsections: []\n", encoding="utf-8")
    _add_pdf(wd, tmp_path)
    paper_meta.save(wd, PaperMeta(title="Attention Is All You Need", short_title="Attention",
                                  authors=["Ashish Vaswani"], year=2017))

    assert paper_meta.apply_naming(ws) == {"renamed": 1, "unchanged": 0, "no_meta": 0}

    assert [p.name for p in ws.iterdir()] == ["2017_Attention_Vaswani"]  # (2) 사라짐
    container = ws / "2017_Attention_Vaswani"
    assert (container / "2017_Attention_Vaswani.md4").is_dir()
    # 껍데기가 남긴 옛 PDF는 변환에 쓴 같은 논문 PDF로 덮어써진다 (중복 방치 안 함)
    assert sorted(p.name for p in container.iterdir() if p.suffix == ".pdf") == \
        ["2017_Attention_Vaswani.pdf"]
    assert (container / "2017_Attention_Vaswani.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_rename_keeps_suffix_when_real_paper_holds_the_name(tmp_path):
    """진짜 논문이 그 이름을 쓰고 있으면 (2)는 그대로 — 같은 논문을 두 번 변환한 정상 케이스."""
    from md4paper.workdir import rename_workdir

    ws = tmp_path / "ws"
    taken = ws / "2017_Attention_Vaswani"
    (taken / "2017_Attention_Vaswani.md4").mkdir(parents=True)  # 변환된 논문이 이미 점유

    wd = _wd(tmp_path, "dup-upload", ko=None)
    new_wd = rename_workdir(wd, "2017_Attention_Vaswani", ws)
    assert new_wd.root.parent.name == "2017_Attention_Vaswani (2)"


def test_apply_naming_skips_papers_without_meta(tmp_path):
    from md4paper import paper_meta

    ws = tmp_path / "ws"
    wd = _wd(tmp_path, "no-meta-paper", ko=None)
    wd.sections_yaml.write_text("title: T\nsections: []\n", encoding="utf-8")
    assert paper_meta.apply_naming(ws) == {"renamed": 0, "unchanged": 0, "no_meta": 1}
    assert wd.root.is_dir()  # 이름 유지
