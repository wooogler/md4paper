"""옛 이름(md4paper) 폴더 옮기기 — 설정·작업 폴더 이동과 그 안의 절대 경로 고쳐 쓰기."""

import json
import os
import stat

import pytest

from narani import config, legacy


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("NARANI_CONFIG_DIR", raising=False)
    monkeypatch.setattr(legacy, "LEGACY_CONFIG_DIR", tmp_path / ".config" / "md4paper")
    monkeypatch.setattr(legacy, "LEGACY_WORKSPACE_ROOT", tmp_path / "md4paper")
    monkeypatch.setattr(legacy, "WORKSPACE_ROOT", tmp_path / "narani")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".config" / "narani")
    return tmp_path


def test_moves_config_and_workspace_and_rewrites_paths(home):
    old_cfg = home / ".config" / "md4paper"
    old_cfg.mkdir(parents=True)
    old_ws = home / "md4paper"
    paper = old_ws / "output" / "2020_X_Kim"
    (paper / "x.md4" / "extract").mkdir(parents=True)
    (paper / "x.md4" / "extract" / "meta.json").write_text(
        json.dumps({"source": str(paper / "x.pdf")}), encoding="utf-8")
    (old_cfg / "open_tabs.json").write_text(json.dumps({"tabs": [str(paper / "x.md4")]}), encoding="utf-8")
    (old_cfg / "config.toml").write_text(f'[output]\nworkspace = "{old_ws / "output"}"\n', encoding="utf-8")
    token = old_cfg / "google_drive.json"
    token.write_text("{}", encoding="utf-8")
    os.chmod(token, 0o600)
    # 이웃 폴더(옛 이름으로 시작하는 다른 폴더)는 건드리지 않는다
    (old_cfg / "projects.json").write_text(json.dumps({"root": str(home / "md4paper-old")}), encoding="utf-8")

    done = legacy.migrate()

    assert len(done) == 2
    new_cfg, new_ws = config.CONFIG_DIR, home / "narani"
    assert not old_cfg.exists() and not old_ws.exists()
    assert stat.S_IMODE((new_cfg / "google_drive.json").stat().st_mode) == 0o600
    new_paper = new_ws / "output" / "2020_X_Kim"
    assert json.loads((new_cfg / "open_tabs.json").read_text())["tabs"] == [str(new_paper / "x.md4")]
    assert str(new_ws / "output") in (new_cfg / "config.toml").read_text()
    meta = json.loads((new_paper / "x.md4" / "extract" / "meta.json").read_text())
    assert meta["source"] == str(new_paper / "x.pdf")
    assert json.loads((new_cfg / "projects.json").read_text())["root"] == str(home / "md4paper-old")


def test_leaves_existing_new_folders_alone(home):
    (home / ".config" / "md4paper").mkdir(parents=True)
    config.CONFIG_DIR.mkdir(parents=True)
    assert legacy.migrate() == []
    assert (home / ".config" / "md4paper").exists()


def test_skipped_when_config_dir_is_overridden(home, monkeypatch):
    (home / ".config" / "md4paper").mkdir(parents=True)
    monkeypatch.setenv("NARANI_CONFIG_DIR", str(home / "elsewhere"))
    assert legacy.migrate() == []
    assert (home / ".config" / "md4paper").exists()
