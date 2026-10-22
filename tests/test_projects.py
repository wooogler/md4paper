"""프로젝트 — 논문 묶음, 그리고 묶음마다 정하는 폴더 하나.

폴더를 하나 정하면 종류별 자리(루트·ko/·pdf/)가 그 안에 자동으로 잡히고, 논문의 소속은
그 논문의 status.json에 적힌다. 논문 하나가 여러 프로젝트에 속할 수 있고(첫 항목 = 주 프로젝트),
사본은 폴더를 가진 소속 프로젝트마다 생긴다. 그런 소속이 없으면 공통 저장 위치를 쓴다.
"""

import json
from pathlib import Path

import pytest

from md4paper import config, library, projects, workdir
from md4paper.workdir import WorkDir


def _wd(tmp_path: Path, name: str = "2017_Attention_Vaswani", *,
        project: str | list[str] | None = None,
        ko: str | None = "# 제목\n\n![그림 1](images/fig-01.png)\n") -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text("# T\n\n![Figure 1](images/fig-01.png)\n", encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    (wd.out_images / "fig-01.png").write_bytes(b"PNG-1")
    wd.sections_yaml.write_text("title: T\nsections: []\n", encoding="utf-8")  # 목록에 잡히려면 필요
    if isinstance(project, list):
        workdir.set_projects(wd.root, project)
    elif project is not None:
        workdir.set_project(wd.root, project)
    return wd


def _add_pdf(wd: WorkDir) -> Path:
    pdf = wd.root.parent / f"{wd.root.stem}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    wd.meta_json.write_text(json.dumps({"source": str(pdf)}), encoding="utf-8")
    return pdf


def test_no_projects_until_you_make_one():
    assert projects.all_projects() == []
    assert projects.active() == projects.ALL  # 처음 화면은 '모든 프로젝트'
    assert projects.assign_target() == ""  # 새 논문은 미분류로 들어간다


def test_create_keeps_order_and_names_the_unnamed():
    first = projects.create("튜터 챗봇 서베이")
    second = projects.create("   ")  # 이름을 비워 두고 만든 경우

    assert [p["name"] for p in projects.all_projects()] == ["튜터 챗봇 서베이", "프로젝트 2"]
    assert first["id"] != second["id"]
    assert projects.get(first["id"])["name"] == "튜터 챗봇 서베이"
    assert projects.exists(first["id"]) and not projects.exists("p-없는-것")
    assert first["root"] == ""  # 폴더는 아직 안 정했다


def test_rename_does_not_lose_the_papers(tmp_path):
    """배정은 id로 하므로 이름을 바꿔도 그 논문들이 따라온다."""
    pid = projects.create("서베이")["id"]
    wd = _wd(tmp_path, project=pid)

    assert projects.rename(pid, "튜터 챗봇 서베이") is True
    assert projects.name_of(pid) == "튜터 챗봇 서베이"
    assert workdir.project_of(wd.root) == pid
    assert projects.rename(pid, "  ") is False  # 빈 이름으로는 못 바꾼다
    assert projects.rename("p-없는-것", "x") is False


def test_delete_removes_the_grouping_not_the_files(tmp_path):
    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    other = projects.create("수업 과제")["id"]
    only_here = _wd(tmp_path, "paper-a", project=pid)
    both = _wd(tmp_path, "paper-b", project=[pid, other])
    library.export_paper(only_here)
    exported = root / "paper-a.md"
    assert exported.exists()

    assert projects.delete(pid) is True

    assert projects.delete(pid) is False  # 두 번은 안 지워진다
    assert exported.exists()  # 폴더·파일은 사용자 것이라 건드리지 않는다
    assert only_here.root.is_dir()
    # 작업 폴더를 안 넘기면 논문에 적힌 id는 남지만, 없는 프로젝트라 어디서나 걸러 읽힌다
    assert workdir.projects_of(both.root) == [pid, other]
    assert library.projects_of(both) == [other]  # 다른 소속은 그대로 → 그쪽이 주 프로젝트가 된다
    assert library.project_of(both) == other
    assert library.project_of(only_here) == ""  # 이 프로젝트에만 속했던 논문은 미분류
    assert projects.name_of(pid) == projects.NONE_LABEL


def test_delete_with_the_workspace_detaches_the_papers(tmp_path):
    """작업 폴더를 넘기면 논문들에서 그 id를 실제로 뗀다 — 다른 소속은 남긴다."""
    pid = projects.create("서베이")["id"]
    other = projects.create("수업 과제")["id"]
    only_here = _wd(tmp_path, "paper-a", project=pid)
    both = _wd(tmp_path, "paper-b", project=[pid, other])
    hidden = _wd(tmp_path, "paper-c", project=pid)
    workdir.set_hidden(hidden.root, True)  # 숨긴 논문도 떼야 다시 보였을 때 유령 소속이 없다
    untouched = _wd(tmp_path, "paper-d", project=other)

    assert projects.delete(pid, tmp_path / "ws") is True

    assert workdir.projects_of(only_here.root) == []
    assert workdir.projects_of(both.root) == [other]
    assert workdir.projects_of(hidden.root) == []
    assert workdir.projects_of(untouched.root) == [other]


def test_one_folder_gives_each_kind_its_place(tmp_path):
    root = tmp_path / "Vault"
    pid = projects.create("서베이")["id"]

    assert projects.has_dirs(pid) is False
    assert projects.set_root(pid, str(root)) is True

    assert projects.root_of(pid) == root
    assert projects.has_dirs(pid) is True
    assert projects.layout(root) == {"en": root, "ko": root / "ko", "pdf": root / "pdf"}
    assert projects.dir_for(pid, "en") == root  # 영어 마크다운은 루트에 (폴더를 열면 목록이 보인다)
    assert projects.dir_for(pid, "ko") == root / "ko"
    assert projects.dir_for(pid, "pdf") == root / "pdf"
    # 고르는 순간 자리를 만들어 둔다 — 노트 앱에서 폴더를 열었을 때 빈 화면이 아니게
    assert root.is_dir() and (root / "ko").is_dir() and (root / "pdf").is_dir()

    assert projects.set_root(pid, None) is True  # 해제하면 공통 저장 위치로 돌아간다
    assert projects.root_of(pid) is None and projects.dir_for(pid, "en") is None
    assert projects.set_root("p-없는-것", str(root)) is False


def test_vanished_project_reads_as_unclassified():
    pid = projects.create("서베이")["id"]
    projects.delete(pid)

    assert projects.normalize(pid) == ""
    assert projects.normalize(projects.NONE) == ""
    assert projects.normalize(None) == ""
    assert projects.name_of(pid) == projects.NONE_LABEL
    assert projects.assign_target(pid) == ""
    assert projects.get(projects.NONE) is None


def test_active_project_is_remembered_and_forgotten_with_it():
    pid = projects.create("서베이")["id"]

    projects.set_active(pid)
    assert projects.active() == pid
    assert projects.assign_target() == pid  # 고른 프로젝트로 새 논문이 들어간다

    projects.set_active(projects.NONE)  # '미분류'만 보기
    assert projects.active() == projects.NONE
    assert projects.assign_target() == ""  # 미분류 필터는 배정 대상이 아니다

    projects.set_active(pid)
    projects.delete(pid)
    assert projects.active() == projects.ALL  # 지워진 프로젝트가 계속 골라져 있지 않게


def test_options_always_offer_unclassified():
    pid = projects.create("서베이")["id"]
    assert projects.options() == {projects.NONE: projects.NONE_LABEL, pid: "서베이"}
    assert projects.options(with_all=True) == {
        projects.ALL: projects.ALL_LABEL, projects.NONE: projects.NONE_LABEL, pid: "서베이"}
    assert projects.options(with_none=False) == {pid: "서베이"}


def test_project_folder_beats_the_shared_setting(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    own = projects.create("서베이", str(tmp_path / "Vault"))["id"]
    no_folder = projects.create("수업 과제")["id"]

    assert library.dir_for("en", own) == tmp_path / "Vault"
    assert library.dir_for("ko", own) == tmp_path / "Vault" / "ko"
    # 폴더를 안 정한 프로젝트와 미분류 논문은 공통 설정으로 폴백한다
    assert library.dir_for("en", no_folder) == tmp_path / "EN"
    assert library.dir_for("en", "") == tmp_path / "EN"
    assert library.dir_for("ko", no_folder) is None  # 공통 ko도 미설정이면 갈 곳이 없다


def test_assignment_is_not_work_so_recent_order_holds(tmp_path):
    """배정은 '작업'이 아니다 — 폴더 수정시각을 흔들면 최근순 목록에서 논문이 튀어 오른다."""
    older = _wd(tmp_path, "paper-a")
    newer = _wd(tmp_path, "paper-b")
    before = older.root.stat().st_mtime
    pid = projects.create("서베이")["id"]

    assert workdir.set_project(older.root, pid) is True

    assert workdir.project_of(older.root) == pid
    assert older.root.stat().st_mtime == before
    names = [r["name"] for r in workdir.recent_workdirs(tmp_path / "ws")]
    assert names.index(newer.root.stem) < names.index(older.root.stem)

    assert workdir.set_project(older.root, None) is True  # 미분류로 되돌리기
    assert workdir.project_of(older.root) == ""
    assert workdir.set_project(tmp_path / "ws" / "없는-논문.md4", pid) is False


def test_recent_workdirs_carries_the_memberships(tmp_path):
    first = projects.create("서베이")["id"]
    second = projects.create("수업 과제")["id"]
    _wd(tmp_path, "paper-a", project=first)
    _wd(tmp_path, "paper-b")
    _wd(tmp_path, "paper-c", project=[second, first])

    rows = {r["name"]: (r["projects"], r["project"])
            for r in workdir.recent_workdirs(tmp_path / "ws")}

    # `project`는 첫 항목(주 프로젝트) — 단일 소속만 아는 옛 호출자도 그대로 돌게
    assert rows == {"paper-a": ([first], first), "paper-b": ([], ""),
                    "paper-c": ([second, first], second)}


def test_export_paper_lands_in_the_project_layout(tmp_path):
    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    wd = _wd(tmp_path, project=pid)
    _add_pdf(wd)

    written = library.export_paper(wd)

    assert written == [
        root / "2017_Attention_Vaswani.md",
        root / "ko" / "2017_Attention_Vaswani.md",
        root / "pdf" / "2017_Attention_Vaswani.pdf",
    ]
    # 영어·번역이 다른 폴더로 가므로 이름이 겹치지 않아 언어 접미사가 붙지 않는다
    assert library.same_folder(pid) is False
    # 그림은 (영어 마크다운이 있는) 루트 아래 논문별 폴더로
    assert (root / "images" / "2017_Attention_Vaswani" / "fig-01.png").read_bytes() == b"PNG-1"
    assert "](images/2017_Attention_Vaswani/fig-01.png)" in written[0].read_text(encoding="utf-8")


def test_unclassified_paper_still_goes_to_the_shared_place(tmp_path):
    """프로젝트를 쓰기 전에 잡아 둔 공통 설정이 그대로 살아 있어야 한다."""
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    projects.create("서베이", str(tmp_path / "Vault"))  # 다른 프로젝트가 있어도 새어 나가지 않는다
    wd = _wd(tmp_path, ko=None)
    _add_pdf(wd)

    written = library.export_paper(wd)

    assert written == [tmp_path / "EN" / "2017_Attention_Vaswani.md",
                       tmp_path / "PDF" / "2017_Attention_Vaswani.pdf"]
    assert not (tmp_path / "Vault" / "2017_Attention_Vaswani.md").exists()


def test_auto_export_follows_the_paper_to_its_project(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    wd = _wd(tmp_path, project=pid, ko=None)

    assert library.auto_export(wd) == [root / "2017_Attention_Vaswani.md"]
    assert not (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()


def test_reassign_moves_the_copies_even_with_auto_off(tmp_path):
    """프로젝트 옮기기는 사용자가 직접 누른 동작이라 자동 저장 설정과 무관하게 사본을 옮긴다."""
    old_root, new_root = tmp_path / "Old", tmp_path / "New"
    old = projects.create("옛 서베이", str(old_root))["id"]
    new = projects.create("새 서베이", str(new_root))["id"]
    wd = _wd(tmp_path, project=old)
    _add_pdf(wd)
    library.export_paper(wd)
    assert (old_root / "2017_Attention_Vaswani.md").exists()
    config.set_section_value("library", "auto", False)

    written = library.reassign(wd, new)

    assert written == [
        new_root / "2017_Attention_Vaswani.md",
        new_root / "ko" / "2017_Attention_Vaswani.md",
        new_root / "pdf" / "2017_Attention_Vaswani.pdf",
    ]
    assert library.projects_of(wd) == [new]
    # '옮기기'는 소속을 그 하나로 바꾸는 것 — 빠진 프로젝트의 폴더에는 사본이 남지 않는다
    assert not (old_root / "2017_Attention_Vaswani.md").exists()
    assert not (old_root / "ko" / "2017_Attention_Vaswani.md").exists()
    assert not (old_root / "pdf" / "2017_Attention_Vaswani.pdf").exists()
    assert not (old_root / "images" / "2017_Attention_Vaswani").exists()
    assert library.reassign(wd, new) == []  # 같은 프로젝트로 다시 옮기라면 할 일이 없다


def test_reassign_to_unclassified_uses_the_shared_place(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    wd = _wd(tmp_path, project=pid, ko=None)
    library.export_paper(wd)

    assert library.reassign(wd, projects.NONE) == [tmp_path / "EN" / "2017_Attention_Vaswani.md"]
    assert library.project_of(wd) == ""
    assert not (root / "2017_Attention_Vaswani.md").exists()


def test_remove_stem_everywhere_sweeps_every_project_folder(tmp_path):
    """논문을 지울 때 — 여러 프로젝트에 속했거나 옮겨 다녔다면 여러 폴더에 사본이 있다."""
    config.set_library_dir("en", str(tmp_path / "EN"))
    config.set_library_dir("pdf", str(tmp_path / "PDF"))
    first = projects.create("A", str(tmp_path / "A"))["id"]
    second = projects.create("B", str(tmp_path / "B"))["id"]
    wd = _wd(tmp_path, ko=None)
    _add_pdf(wd)
    library.export_paper(wd)  # 공통 폴더로
    workdir.set_projects(wd.root, [first, second])
    library.export_paper(wd)  # 두 프로젝트 폴더 모두로
    assert (tmp_path / "A" / "2017_Attention_Vaswani.md").exists()
    assert (tmp_path / "B" / "2017_Attention_Vaswani.md").exists()

    library.remove_stem_everywhere("2017_Attention_Vaswani")

    for place in (tmp_path / "EN", tmp_path / "A", tmp_path / "B"):
        assert not (place / "2017_Attention_Vaswani.md").exists()
        assert not (place / "images" / "2017_Attention_Vaswani").exists()
    for pdf_dir in (tmp_path / "PDF", tmp_path / "A" / "pdf", tmp_path / "B" / "pdf"):
        assert not (pdf_dir / "2017_Attention_Vaswani.pdf").exists()


# --- 여러 프로젝트 소속 (N:M) ------------------------------------------------


def test_old_status_with_a_single_project_still_reads(tmp_path):
    """옛 형식(`project` 하나)으로 적힌 논문 — 그 하나의 소속으로 읽힌다."""
    pid = projects.create("서베이")["id"]
    wd = _wd(tmp_path)
    wd.save_status({"project": pid})

    assert workdir.projects_of(wd.root) == [pid]
    assert workdir.project_of(wd.root) == pid
    assert library.projects_of(wd) == [pid]
    assert [r["projects"] for r in workdir.recent_workdirs(tmp_path / "ws")] == [[pid]]


def test_memberships_keep_the_old_key_for_the_installed_app(tmp_path):
    """설치된 앱(옛 코드)이 같은 output/을 읽는다 — 옛 키 `project`엔 주 프로젝트를 둔다."""
    first = projects.create("서베이")["id"]
    second = projects.create("수업 과제")["id"]
    wd = _wd(tmp_path)

    assert workdir.set_projects(wd.root, [first, second, first]) is True  # 중복은 한 번만

    status = wd.load_status()
    assert status["projects"] == [first, second]
    assert status["project"] == first
    assert workdir.set_projects(wd.root, []) is True  # 미분류는 두 키 모두 없음
    assert "project" not in wd.load_status() and "projects" not in wd.load_status()
    assert workdir.set_projects(tmp_path / "ws" / "없는-논문.md4", [first]) is False


def test_the_old_app_moving_a_paper_wins_over_a_stale_list(tmp_path):
    """옛 앱은 `project`만 고친다 — 첫 항목과 어긋나면 그쪽이 최신이다."""
    first = projects.create("서베이")["id"]
    second = projects.create("수업 과제")["id"]
    third = projects.create("연구")["id"]
    wd = _wd(tmp_path, project=[first, second])

    wd.save_status({**wd.load_status(), "project": third})  # 옛 앱이 '옮기기'를 했다
    assert workdir.projects_of(wd.root) == [third]

    status = wd.load_status()
    status.pop("project")  # 옛 앱이 미분류로 되돌렸다
    wd.save_status(status)
    assert workdir.projects_of(wd.root) == []


def test_add_and_remove_keep_the_primary_first(tmp_path):
    first = projects.create("서베이")["id"]
    second = projects.create("수업 과제")["id"]
    wd = _wd(tmp_path, project=first)
    before = wd.root.stat().st_mtime

    assert workdir.add_to_project(wd.root, second) is True
    assert workdir.add_to_project(wd.root, first) is True  # 이미 속했으면 그대로
    assert workdir.projects_of(wd.root) == [first, second]  # 더한 건 뒤에 — 주 프로젝트는 그대로
    assert wd.root.stat().st_mtime == before  # 소속 바꾸기는 '작업'이 아니다

    assert workdir.remove_from_project(wd.root, first) is True
    assert workdir.projects_of(wd.root) == [second]  # 주를 빼면 다음 항목이 주가 된다
    assert workdir.project_of(wd.root) == second
    assert workdir.remove_from_project(wd.root, first) is True  # 없는 소속을 빼는 건 할 일이 없다


def test_normalize_many_drops_the_vanished_and_keeps_order():
    first = projects.create("서베이")["id"]
    second = projects.create("수업 과제")["id"]
    gone = projects.create("지울 것")["id"]
    projects.delete(gone)

    mixed = [second, gone, first, second, projects.NONE, ""]
    assert projects.normalize_many(mixed) == [second, first]
    assert projects.normalize_many(None) == []


def test_a_paper_in_two_projects_gets_a_copy_in_each_folder(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    a = projects.create("서베이", str(tmp_path / "A"))["id"]
    b = projects.create("연구", str(tmp_path / "B"))["id"]
    wd = _wd(tmp_path, project=[a, b], ko=None)

    written = library.export_paper(wd)

    assert written == [tmp_path / "A" / "2017_Attention_Vaswani.md",
                       tmp_path / "B" / "2017_Attention_Vaswani.md"]
    assert (tmp_path / "B" / "images" / "2017_Attention_Vaswani" / "fig-01.png").exists()
    assert not (tmp_path / "EN" / "2017_Attention_Vaswani.md").exists()  # 공통으로는 새지 않는다


def test_a_project_without_a_folder_adds_no_copy(tmp_path):
    """폴더를 가진 소속이 하나라도 있으면 그리로만 — 폴더 없는 소속 때문에 공통에 또 쌓이지 않게."""
    config.set_library_dir("en", str(tmp_path / "EN"))
    no_folder = projects.create("수업 과제")["id"]
    own = projects.create("서베이", str(tmp_path / "A"))["id"]
    wd = _wd(tmp_path, project=[no_folder, own], ko=None)

    assert library.export_targets(wd) == [own]
    assert library.export_paper(wd) == [tmp_path / "A" / "2017_Attention_Vaswani.md"]
    # 주 프로젝트(첫 항목)에 폴더가 없어도 자동 저장은 폴더 있는 소속으로 간다
    assert library.auto_export(wd) == [tmp_path / "A" / "2017_Attention_Vaswani.md"]


def test_each_copy_follows_its_own_folders_format(tmp_path, monkeypatch):
    """이름은 주 프로젝트 규칙 하나, 형식·BibTeX는 사본이 놓이는 폴더의 프로젝트 설정 — 볼트마다 앱이 다르다."""
    from md4paper import bibtex

    entry = "@article{Vaswani2017,\n  title = {Attention Is All You Need},\n  year = {2017}\n}\n"
    monkeypatch.setattr(bibtex, "entry_for", lambda wd: ("Vaswani2017", entry))
    a = projects.create("서베이", str(tmp_path / "A"))["id"]
    b = projects.create("연구", str(tmp_path / "B"))["id"]
    projects.set_setting(b, "export_target", "obsidian")
    projects.set_setting(a, "bibtex", False)
    wd = _wd(tmp_path, project=[a, b], ko=None)

    written = library.export_paper(wd)

    a_md = (tmp_path / "A" / "2017_Attention_Vaswani.md").read_text(encoding="utf-8")
    b_md = (tmp_path / "B" / "2017_Attention_Vaswani.md").read_text(encoding="utf-8")
    assert "](images/2017_Attention_Vaswani/fig-01.png)" in a_md  # A는 전역(범용) 형식
    assert "![[images/2017_Attention_Vaswani/fig-01.png]]" in b_md  # B는 Obsidian
    assert tmp_path / "B" / "references.bib" in written  # BibTeX도 폴더마다
    assert not (tmp_path / "A" / "references.bib").exists()


def test_two_projects_sharing_a_folder_write_once(tmp_path):
    """같은 폴더를 정한 두 프로젝트 — 한 번만 쓰고, 한쪽을 빼도 남은 쪽의 사본은 지우지 않는다."""
    a = projects.create("서베이", str(tmp_path / "Vault"))["id"]
    b = projects.create("연구", str(tmp_path / "Vault" / "."))["id"]  # 표기만 다른 같은 폴더
    wd = _wd(tmp_path, project=[a, b], ko=None)

    assert library.export_targets(wd) == [a]
    assert library.export_paper(wd) == [tmp_path / "Vault" / "2017_Attention_Vaswani.md"]

    assert library.set_memberships(wd, [b]) == []
    assert (tmp_path / "Vault" / "2017_Attention_Vaswani.md").exists()


def test_a_project_folder_equal_to_the_shared_place_keeps_its_copy(tmp_path):
    """미분류(공통)에서 공통과 같은 폴더의 프로젝트로 옮겨도 사본을 지웠다 다시 쓰지 않는다."""
    vault = tmp_path / "Vault"
    for which, spot in projects.layout(vault).items():
        config.set_library_dir(which, str(spot))
    pid = projects.create("서베이", str(vault))["id"]
    wd = _wd(tmp_path, ko=None)
    library.export_paper(wd)
    copy = vault / "2017_Attention_Vaswani.md"
    stamp = copy.stat().st_mtime_ns

    assert library.set_memberships(wd, [pid]) == []

    assert copy.exists() and copy.stat().st_mtime_ns == stamp
    assert library.projects_of(wd) == [pid]


def test_leaving_one_project_cleans_only_that_folder(tmp_path):
    a = projects.create("서베이", str(tmp_path / "A"))["id"]
    b = projects.create("연구", str(tmp_path / "B"))["id"]
    wd = _wd(tmp_path, project=[a, b], ko=None)
    _add_pdf(wd)
    library.export_paper(wd)
    config.set_section_value("library", "auto", False)  # 직접 누른 동작이라 자동 저장과 무관

    assert library.set_memberships(wd, [b]) == []  # 남는 폴더엔 새로 쓸 게 없다

    assert library.projects_of(wd) == [b]
    assert not (tmp_path / "A" / "2017_Attention_Vaswani.md").exists()
    assert not (tmp_path / "A" / "pdf" / "2017_Attention_Vaswani.pdf").exists()
    assert not (tmp_path / "A" / "images" / "2017_Attention_Vaswani").exists()
    assert (tmp_path / "B" / "2017_Attention_Vaswani.md").exists()
    assert (tmp_path / "B" / "pdf" / "2017_Attention_Vaswani.pdf").exists()


def test_joining_a_project_adds_a_copy_there(tmp_path):
    a = projects.create("서베이", str(tmp_path / "A"))["id"]
    b = projects.create("연구", str(tmp_path / "B"))["id"]
    wd = _wd(tmp_path, project=a, ko=None)
    library.export_paper(wd)

    assert library.set_memberships(wd, [a, b]) == [tmp_path / "B" / "2017_Attention_Vaswani.md"]

    assert (tmp_path / "A" / "2017_Attention_Vaswani.md").exists()  # 있던 사본은 그대로
    assert library.projects_of(wd) == [a, b]


def test_leaving_every_project_goes_back_to_the_shared_place(tmp_path):
    config.set_library_dir("en", str(tmp_path / "EN"))
    a = projects.create("서베이", str(tmp_path / "A"))["id"]
    b = projects.create("연구", str(tmp_path / "B"))["id"]
    wd = _wd(tmp_path, project=[a, b], ko=None)
    library.export_paper(wd)

    written = library.set_memberships(wd, [])

    assert written == [tmp_path / "EN" / "2017_Attention_Vaswani.md"]
    assert library.projects_of(wd) == []
    for root in (tmp_path / "A", tmp_path / "B"):
        assert not (root / "2017_Attention_Vaswani.md").exists()


def test_filters_and_counts_go_by_membership(tmp_path, monkeypatch):
    """여러 프로젝트에 속한 논문은 그 어느 프로젝트에도 잡히고, 편수도 프로젝트마다 센다."""
    from md4paper import cli, enrich

    ws = tmp_path / "ws"
    monkeypatch.setattr(config, "resolve_workspace", lambda: ws)
    a = projects.create("서베이")["id"]
    b = projects.create("연구")["id"]
    gone = projects.create("지울 것")["id"]
    _wd(tmp_path, "paper-a", project=[a, b])
    _wd(tmp_path, "paper-b", project=b)
    _wd(tmp_path, "paper-c", project=gone)  # 없어진 프로젝트에만 속한 논문은 미분류
    _wd(tmp_path, "paper-d")
    projects.delete(gone)

    def names(roots):
        return sorted(r.parent.name for r in roots)

    assert names(enrich.project_roots(a, ws)) == ["paper-a"]
    assert names(enrich.project_roots(b, ws)) == ["paper-a", "paper-b"]
    assert names(enrich.project_roots("", ws)) == ["paper-c", "paper-d"]
    assert names(cli._project_roots(b)) == ["paper-a", "paper-b"]
    assert names(cli._project_roots("")) == ["paper-c", "paper-d"]
    assert cli._paper_counts() == {a: 1, b: 2, "": 2}


# --- 프로젝트마다 다른 설정 (저장·출력) -------------------------------------


def test_a_project_without_overrides_follows_the_global_settings(tmp_path):
    """기본은 '전역 따름'이다 — 전역을 고치면 따로 정하지 않은 프로젝트가 저절로 따라온다."""
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    config.set_section_value("output", "export_target", "notion")

    assert projects.settings_of(pid) == {}
    assert projects.setting(pid, "export_target") is None  # '안 정함'
    assert config.resolve_export_target(pid) == "notion"  # 전역을 그대로 물려받는다


def test_an_override_wins_over_the_global_setting(tmp_path):
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    config.set_section_value("output", "export_target", "notion")
    projects.set_setting(pid, "export_target", "obsidian")

    assert config.resolve_export_target(pid) == "obsidian"
    assert config.resolve_export_target() == "notion"  # 전역은 그대로 (다른 프로젝트에 영향 없음)


def test_switching_a_setting_off_is_not_the_same_as_not_setting_it(tmp_path):
    """False와 '안 정함'이 구별돼야 한다 — 안 그러면 끈 것이 전역 켜짐으로 되살아난다."""
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    config.set_section_value("library", "bibtex", True)
    projects.set_setting(pid, "bibtex", False)

    assert config.resolve_library_bibtex(pid) is False
    assert config.resolve_library_bibtex() is True


def test_clearing_an_override_goes_back_to_the_global_setting(tmp_path):
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    config.set_section_value("library", "auto", True)
    projects.set_setting(pid, "auto", False)
    assert config.resolve_library_auto(pid) is False

    projects.set_setting(pid, "auto", None)

    assert projects.settings_of(pid) == {}
    assert config.resolve_library_auto(pid) is True


def test_a_bad_naming_override_falls_back_instead_of_breaking_names(tmp_path):
    """손으로 고친 projects.json에 이상한 규칙이 들어와도 파일명을 망가뜨리지 않는다."""
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    projects.set_setting(pid, "naming", "{nope}")

    assert config.resolve_naming_template(pid) == config.DEFAULT_NAMING


def test_settings_are_rejected_for_unknown_keys(tmp_path):
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    with pytest.raises(ValueError, match="프로젝트 설정이 아닙니다"):
        projects.set_setting(pid, "korean_style", "해요체")
    with pytest.raises(ValueError, match="프로젝트 설정이 아닙니다"):
        projects.setting(pid, "korean_style")


def test_a_deleted_project_setting_does_not_leak_to_papers(tmp_path):
    """없어진 프로젝트는 어디서나 '미분류'다 — 그 설정이 유령처럼 남으면 안 된다."""
    pid = projects.create("서베이", str(tmp_path / "V"))["id"]
    projects.set_setting(pid, "export_target", "obsidian")
    projects.delete(pid)

    assert projects.setting(pid, "export_target") is None
    assert config.resolve_export_target(pid) == config.resolve_export_target()
