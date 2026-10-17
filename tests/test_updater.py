"""자동 업데이트 — 설치 출처 판독, GitHub 비교, 업데이트 스크립트."""

from __future__ import annotations

import json

import httpx

from md4paper import updater

OLD = "a" * 40
NEW = "b" * 40


def _direct_url(**vcs) -> str:
    return json.dumps({"url": "https://github.com/wooogler/md4paper",
                       "vcs_info": {"vcs": "git", "commit_id": OLD, **vcs}})


def test_source_from_direct_url():
    src = updater.source_from_direct_url(_direct_url())
    assert (src.owner, src.repo, src.commit, src.ref) == ("wooogler", "md4paper", OLD, None)
    assert src.spec == "md4paper[ui,native] @ git+https://github.com/wooogler/md4paper"
    branch = updater.source_from_direct_url(_direct_url(requested_revision="dev"))
    assert branch.spec.endswith("md4paper@dev")


def test_source_ignores_non_git_installs():
    assert updater.source_from_direct_url(None) is None
    assert updater.source_from_direct_url(json.dumps({"url": "file:///x", "dir_info": {"editable": True}})) is None
    other = json.dumps({"url": "https://gitlab.com/a/b", "vcs_info": {"vcs": "git", "commit_id": OLD}})
    assert updater.source_from_direct_url(other) is None


def _client(head: str, compare: dict | None = None, compare_status: int = 200) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if "/commits/" in req.url.path:
            assert req.url.path.endswith("/commits/HEAD")
            return httpx.Response(200, text=head)
        if "/compare/" in req.url.path:
            return httpx.Response(compare_status, json=compare or {})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_up_to_date():
    src = updater.source_from_direct_url(_direct_url())
    assert updater.check(src, client=_client(OLD)) is None


def test_check_finds_update_with_messages():
    src = updater.source_from_direct_url(_direct_url())
    compare = {"status": "ahead", "ahead_by": 2, "commits": [
        {"commit": {"message": "첫 변경\n\n본문"}}, {"commit": {"message": "둘째 변경"}}]}
    upd = updater.check(src, client=_client(NEW, compare))
    assert upd.latest == NEW and upd.ahead == 2
    assert upd.messages == ["둘째 변경", "첫 변경"]  # 최근 것 먼저


def test_check_update_even_if_compare_fails():
    src = updater.source_from_direct_url(_direct_url())
    upd = updater.check(src, client=_client(NEW, compare_status=404))
    assert upd is not None and upd.ahead == 0 and upd.messages == []


def test_check_installed_ahead_is_not_update():
    src = updater.source_from_direct_url(_direct_url())
    assert updater.check(src, client=_client(NEW, {"status": "behind"})) is None


def test_posix_script_waits_reinstalls_relaunches(tmp_path):
    src = updater.source_from_direct_url(_direct_url())
    s = updater.posix_script(src, "/u/uv", 4242, tmp_path / "log", relaunch=True)
    assert "kill -0 4242" in s
    assert "/u/uv tool install --force 'md4paper[ui,native] @ git+https://github.com/wooogler/md4paper'" in s
    assert s.index("kill -0") < s.index("tool install") < s.index(" app ||")


def test_version_label_has_version():
    from md4paper import __version__

    assert updater.version_label().startswith(f"v{__version__}")


def test_env_root_follows_app_bundle_symlink(tmp_path, monkeypatch):
    """맥 앱 번들로 띄우면 sys.prefix가 번들이다 — pyvenv.cfg 링크를 따라 실제 uv tool 폴더로."""
    tool = tmp_path / "tools" / "md4paper"
    tool.mkdir(parents=True)
    (tool / "pyvenv.cfg").write_text("home = /x\n")
    contents = tmp_path / "md4paper.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "pyvenv.cfg").symlink_to(tool / "pyvenv.cfg")
    monkeypatch.setattr(updater.sys, "prefix", str(contents))
    assert updater.env_root() == tool.resolve()
    assert updater.tool_exe().parent.parent == tool.resolve()


def test_skip_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_skip_path", lambda: tmp_path / "cfg" / "update.json")
    assert updater.skipped() == ""
    updater.set_skipped(NEW)
    assert updater.skipped() == NEW
    updater.set_skipped(None)
    assert updater.skipped() == ""
