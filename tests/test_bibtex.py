"""쌓이는 references.bib — 논문 하나에 항목 하나, 그대로 복사해 붙이도록.

문자열을 손으로 만들지 않는다는 게 이 모듈의 핵심이라, 여기서 확인하는 것도 두 가지다:
만든 항목을 되읽어도 뜻이 같은가(`verify`), 그리고 **사용자가 손으로 넣은 항목·주석이 남는가**.
"""

import json
from pathlib import Path

from md4paper import bibtex, config, library, projects
from md4paper.workdir import WorkDir

_META = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": 2017,
         "venue": "Advances in Neural Information Processing Systems"}


def _wd(tmp_path: Path, name: str = "2017_Attention_Vaswani", meta: dict | None = None) -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text("# T\n", encoding="utf-8")
    if meta is not None:
        wd.paper_meta_json.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return wd


def _entry(meta: dict, key: str = "k") -> tuple[dict, str]:
    fields = bibtex.fields_from_meta(meta, key)
    return fields, bibtex.render(fields)


def test_entry_kind_follows_the_venue():
    assert bibtex.entry_kind("Advances in Neural Information Processing Systems") == "article"
    assert bibtex.entry_kind("Proceedings of the 2024 CHI Conference") == "inproceedings"
    assert bibtex.entry_kind("CHI '24 Extended Abstracts") == "inproceedings"
    assert bibtex.entry_kind("ACM Symposium on User Interface Software") == "inproceedings"
    assert bibtex.entry_kind("Nature Machine Intelligence") == "article"
    assert bibtex.entry_kind("arXiv preprint arXiv:1706.03762") == "misc"
    assert bibtex.entry_kind("") == "misc"  # venue를 모르면 종류를 단정하지 않는다


def test_conference_and_journal_get_the_right_field():
    conf, _ = _entry({**_META, "venue": "Proceedings of NeurIPS"})
    assert conf["ENTRYTYPE"] == "inproceedings" and conf["booktitle"] == "Proceedings of NeurIPS"
    assert "journal" not in conf

    art, _ = _entry({**_META, "venue": "Nature Machine Intelligence"})
    assert art["ENTRYTYPE"] == "article" and art["journal"] == "Nature Machine Intelligence"

    pre, text = _entry({**_META, "venue": "arXiv preprint arXiv:1706.03762v5"})
    assert pre["ENTRYTYPE"] == "misc" and pre["eprint"] == "1706.03762"
    assert pre["archiveprefix"] == "arXiv"
    assert bibtex.verify(text, pre) == (True, "확인됨")


def test_special_characters_leave_as_safe_latex():
    """제목에 &·%·_·악센트가 흔하다 — 이스케이프가 틀리면 몇 주 뒤 LaTeX 컴파일이 깨진다."""
    fields, text = _entry({**_META, "title": "Über Cats & Dogs: 50% of _hidden_ states",
                           "authors": ["Émile Borel"]})

    assert r"\&" in text and r"\%" in text and r"\_" in text
    assert "Über" not in text  # 악센트는 LaTeX 표기로 바뀐다 (원문자로 새지 않는다)
    assert r"{\'E}mile" in text
    # 되읽어도 필드가 그대로다 — 우리가 만든 문자열을 우리가 다시 읽어 확인한다
    assert bibtex.verify(text, fields) == (True, "확인됨")
    assert bibtex.parse(text)[0]["title"] == fields["title"]


def test_title_is_braced_so_capitals_survive():
    fields, text = _entry(_META)
    assert fields["title"] == "{Attention Is All You Need}"
    assert "title   = {{Attention Is All You Need}}" in text  # 스타일이 소문자로 내리지 못하게


def test_authors_are_last_name_first_joined_by_and():
    fields, _ = _entry({**_META, "authors": ["Ashish Vaswani", "Noam Shazeer", "Plato", "  "]})
    # 한 덩어리 이름(성·이름으로 쪼갤 수 없는 것)은 중괄호로 감싼다 — BibTeX가 이니셜로 줄이지 않게
    assert fields["author"] == "Vaswani, Ashish and Shazeer, Noam and {Plato}"
    # 중간 이름은 앞쪽에 붙는다 (성만 뒤로)
    assert _entry({**_META, "authors": ["Donald E. Knuth"]})[0]["author"] == "Knuth, Donald E."


def test_korean_names_and_titles_survive_intact():
    """bibtexparser 1.4의 변환 표는 한글 일부를 수학 기호로 바꾼다 — 한글은 그대로 나가야 한다."""
    fields, text = _entry({**_META, "title": "한국어 논문 제목 & 검증",
                           "authors": ["김영희", "Émile Durkheim"], "venue": "한국HCI학회"})
    assert "한국어 논문 제목" in fields["title"] and "\\math" not in text
    assert fields["author"] == "{김영희} and Durkheim, {\\'E}mile"  # 한글은 그대로, 악센트만 변환
    assert fields["journal"] == "한국HCI학회"
    assert "\\&" in fields["title"]  # LaTeX 특수문자는 여전히 이스케이프된다
    assert bibtex.verify(text, fields)[0]


def test_non_ascii_stems_get_distinct_keys():
    """비ASCII를 그냥 지우면 서로 다른 논문이 같은 키가 되어 앞 항목이 조용히 갈아치워진다."""
    a, b = bibtex.cite_key("2024_가논문_이"), bibtex.cite_key("2024_나논문_김")
    assert a != b
    assert a == bibtex.cite_key("2024_가논문_이")  # 같은 이름이면 항상 같은 키 (안정적)
    for key in (a, b):
        assert key.isascii() and " " not in key and "," not in key


def test_doi_drops_the_url_prefix():
    fields, text = _entry({**_META, "doi": " https://doi.org/10.1145/3411764.3445234 "})
    assert fields["doi"] == "10.1145/3411764.3445234"
    assert "doi     = {10.1145/3411764.3445234}" in text


def test_cite_key_is_the_paper_stem():
    assert bibtex.cite_key("2017_Attention_Vaswani") == "2017_Attention_Vaswani"
    assert bibtex.cite_key("2024 Tutor Chatbot, Lee") == "2024TutorChatbotLee"  # 키에 못 쓰는 문자
    assert bibtex.cite_key("").startswith("ref-")  # 이름이 없으면 최소한 유일한 키를


def test_sync_appends_a_new_entry_and_replaces_ours_in_place(tmp_path):
    path = tmp_path / "references.bib"
    _, first = _entry(_META, "2017_Attention_Vaswani")
    _, other = _entry({**_META, "title": "BERT"}, "2019_BERT_Devlin")

    assert bibtex.sync(path, "2017_Attention_Vaswani", first) == path
    assert bibtex.sync(path, "2019_BERT_Devlin", other) == path
    assert bibtex.keys_in(path) == ["2017_Attention_Vaswani", "2019_BERT_Devlin"]

    # 서지를 보강해 다시 내보내도 항목이 늘지 않는다 — 같은 자리에서 최신이 된다
    _, richer = _entry({**_META, "authors": ["Ashish Vaswani", "Noam Shazeer"]},
                       "2017_Attention_Vaswani")
    assert bibtex.sync(path, "2017_Attention_Vaswani", richer) == path

    text = path.read_text(encoding="utf-8")
    assert bibtex.keys_in(path) == ["2017_Attention_Vaswani", "2019_BERT_Devlin"]  # 순서까지 그대로
    assert "Shazeer, Noam" in text
    assert "\n}\n\n@article{2019_BERT_Devlin" in text  # 항목 사이 빈 줄 유지


def test_hand_written_entries_and_comments_survive(tmp_path):
    """이 설계의 핵심 — 우리가 쓰는 건 우리 키의 블록뿐이다. 파일을 통째로 다시 쓰지 않는다."""
    path = tmp_path / "references.bib"
    mine = "% 손으로 넣은 항목 — 건드리지 말 것\n@book{knuth1984,\n  title = {The TeXbook}\n}\n"
    path.write_text(mine, encoding="utf-8")
    _, entry = _entry(_META, "2017_Attention_Vaswani")

    bibtex.sync(path, "2017_Attention_Vaswani", entry)
    bibtex.sync(path, "2017_Attention_Vaswani", entry)  # 두 번째 내보내기에도 살아남아야 한다

    text = path.read_text(encoding="utf-8")
    assert "% 손으로 넣은 항목 — 건드리지 말 것" in text
    assert "@book{knuth1984," in text and "{The TeXbook}" in text
    assert bibtex.keys_in(path) == ["knuth1984", "2017_Attention_Vaswani"]


def test_remove_takes_only_that_entry(tmp_path):
    path = tmp_path / "references.bib"
    _, ours = _entry(_META, "2017_Attention_Vaswani")
    path.write_text("@book{knuth1984,\n  title = {The TeXbook}\n}\n", encoding="utf-8")
    bibtex.sync(path, "2017_Attention_Vaswani", ours)

    assert bibtex.remove(path, "2017_Attention_Vaswani") is True

    assert bibtex.keys_in(path) == ["knuth1984"]  # 남의 항목은 그대로
    assert bibtex.remove(path, "2017_Attention_Vaswani") is False  # 없는 항목은 지울 게 없다
    assert bibtex.remove(tmp_path / "없는파일.bib", "knuth1984") is False


def test_keys_in_is_empty_for_missing_file(tmp_path):
    assert bibtex.keys_in(tmp_path / "없는파일.bib") == []


def test_entry_for_needs_a_title(tmp_path):
    assert bibtex.entry_for(_wd(tmp_path)) is None  # paper_meta.json 없음 (서지 추출 전)
    assert bibtex.entry_for(_wd(tmp_path, "no-title", {"title": "  ", "year": 2017})) is None

    key, text = bibtex.entry_for(_wd(tmp_path, "2017_Attention_Vaswani", _META))
    assert key == "2017_Attention_Vaswani"  # 인용 키 = 마크다운·PDF와 같은 기준명
    assert text.startswith("@article{2017_Attention_Vaswani,")


def test_export_paper_stacks_the_bib_in_the_project_root(tmp_path):
    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    first = _wd(tmp_path, "2017_Attention_Vaswani", _META)
    first.save_status({"project": pid})
    second = _wd(tmp_path, "2019_BERT_Devlin", {**_META, "title": "BERT", "year": 2019})
    second.save_status({"project": pid})

    assert library.bib_path(pid) == root / "references.bib"
    assert root / "references.bib" in library.export_paper(first)
    library.export_paper(second)

    # 마크다운이 놓이는 폴더에 함께 쌓인다 — 노트 앱에서 그 폴더만 열어도 인용이 갖춰진다
    assert bibtex.keys_in(root / "references.bib") == ["2017_Attention_Vaswani", "2019_BERT_Devlin"]


def test_bib_goes_next_to_the_shared_markdown(tmp_path):
    """프로젝트 폴더가 없으면 공통 폴더 — 영어 마크다운을 놓는 곳을 우선한다."""
    assert library.bib_path() is None  # 쌓을 곳이 없으면 아무것도 하지 않는다
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    assert library.bib_path() == tmp_path / "PDF" / "references.bib"
    config.set_library_dir("en", str(tmp_path / "EN"))
    assert library.bib_path() == tmp_path / "EN" / "references.bib"

    wd = _wd(tmp_path, "2017_Attention_Vaswani", _META)
    assert tmp_path / "EN" / "references.bib" in library.export_paper(wd)


def test_bibtex_switch_off_writes_no_bib(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    wd = _wd(tmp_path, "2017_Attention_Vaswani", _META)
    config.set_section_value("library", "bibtex", False)

    assert library.export_bib(wd) is None
    assert library.export_paper(wd) == [tmp_path / "EN" / "2017_Attention_Vaswani.md"]
    assert not (tmp_path / "EN" / "references.bib").exists()

    config.set_section_value("library", "bibtex", True)
    assert library.export_bib(wd) == tmp_path / "EN" / "references.bib"


def test_export_bib_skips_papers_without_bibliography(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    wd = _wd(tmp_path, "2412.01234v2")  # 서지 추출 전 — 조용히 건너뛴다
    assert library.export_bib(wd) is None
    assert not (tmp_path / "EN" / "references.bib").exists()
