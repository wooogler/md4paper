"""하위 컬렉션 — 컬렉션 안의 컬렉션 (projects.json의 parent).

상위를 고르면 하위 논문까지 보이고, 폴더·설정은 가장 가까운 조상 것을 물려받는다.
지우면 하위는 한 칸 위로 올라가고, 순환·깨진 parent는 맨 위로 읽는다.
"""

import json

from narani import library, projects, workdir
from narani.workdir import WorkDir


def _wd(tmp_path, name, pids):
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text("# T\n", encoding="utf-8")
    wd.sections_yaml.write_text("title: T\nsections: []\n", encoding="utf-8")
    workdir.set_projects(wd.root, pids)
    return wd


def test_tree_orders_children_under_their_parent():
    a = projects.create("A")["id"]
    b = projects.create("B")["id"]
    a1 = projects.create("A1", parent=a)["id"]
    a1x = projects.create("A1x", parent=a1)["id"]
    a2 = projects.create("A2", parent=a)["id"]
    assert [(p["id"], d) for p, d in projects.tree()] == [(a, 0), (a1, 1), (a1x, 2), (a2, 1), (b, 0)]
    assert projects.descendants(a) == [a1, a1x, a2]
    assert projects.ancestors(a1x) == [a1, a]
    assert projects.path_name(a1x) == "A / A1 / A1x"
    assert list(projects.options().values()) == ["미분류", "A", "A / A1", "A / A1 / A1x", "A / A2", "B"]


def test_create_under_unknown_parent_goes_to_top():
    p = projects.create("고아", parent="p-없음")
    assert p["parent"] == ""


def test_set_parent_refuses_cycles():
    a = projects.create("A")["id"]
    b = projects.create("B", parent=a)["id"]
    c = projects.create("C", parent=b)["id"]
    assert projects.set_parent(a, c) is False  # 자기 하위 아래로는 못 간다
    assert projects.set_parent(a, a) is False
    assert projects.parent_of(a) == ""
    assert projects.set_parent(c, "") is True  # 맨 위로
    assert projects.parent_of(c) == ""
    assert projects.set_parent(c, a) is True
    assert projects.ancestors(c) == [a]


def test_broken_parent_in_file_reads_as_top_level():
    a = projects.create("A")["id"]
    b = projects.create("B", parent=a)["id"]
    data = json.loads(projects.PROJECTS_PATH.read_text(encoding="utf-8"))
    for p in data["projects"]:  # 손으로 고쳐 순환을 만든다: A ↔ B
        if p["id"] == a:
            p["parent"] = b
    projects.PROJECTS_PATH.write_text(json.dumps(data), encoding="utf-8")
    ids = [p["id"] for p, _ in projects.tree()]
    assert sorted(ids) == sorted([a, b])  # 둘 다 목록에 남는다 (순환으로 사라지지 않게)


def test_delete_promotes_children_one_level():
    a = projects.create("A")["id"]
    b = projects.create("B", parent=a)["id"]
    c = projects.create("C", parent=b)["id"]
    assert projects.delete(b) is True
    assert projects.parent_of(c) == a


def test_child_inherits_folder_and_settings(tmp_path):
    top = projects.create("SCALE", str(tmp_path / "vault"))["id"]
    projects.set_setting(top, "export_target", "obsidian")
    kid = projects.create("리뷰", parent=top)["id"]
    assert projects.root_of(kid) is None  # 자기 폴더는 없다
    assert projects.folder_of(kid) == tmp_path / "vault"
    assert projects.inherited_from(kid)["id"] == top
    assert projects.has_dirs(kid)
    assert projects.setting(kid, "export_target") == "obsidian"
    projects.set_setting(kid, "export_target", "notion")  # 따로 정하면 그 값이 이긴다
    assert projects.setting(kid, "export_target") == "notion"
    assert projects.setting(top, "export_target") == "obsidian"


def test_child_paper_is_copied_into_inherited_folder_once(tmp_path):
    vault = tmp_path / "vault"
    top = projects.create("SCALE", str(vault))["id"]
    kid = projects.create("리뷰", parent=top)["id"]
    wd = _wd(tmp_path, "2020_X_Kim", [top, kid])
    # 상위·하위 둘 다 소속이어도 폴더가 같으니 사본은 한 벌
    assert library.export_targets(wd) == [top]
    out = library.export_paper(wd)
    assert vault / "2020_X_Kim.md" in out


def test_moving_out_of_the_parent_keeps_copy_when_folder_is_inherited(tmp_path):
    vault = tmp_path / "vault"
    top = projects.create("SCALE", str(vault))["id"]
    kid = projects.create("리뷰", parent=top)["id"]
    wd = _wd(tmp_path, "2020_X_Kim", [top])
    library.export_paper(wd)
    library.set_memberships(wd, [kid])  # 상위에서 하위로 — 같은 폴더라 사본은 그대로
    assert (vault / "2020_X_Kim.md").exists()
