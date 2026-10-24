"""한 논문 = 탭 하나 — 같은 작업 폴더 이름의 다른 경로(복사본)는 새 탭이 아니라 그 자리를 바꾼다."""

from __future__ import annotations

from pathlib import Path

from narani.ui import tabstore


def _paper(base: Path, name: str) -> Path:
    root = base / name / f"{name}.md4"
    (root / "structure").mkdir(parents=True)
    (root / "structure" / "sections.yaml").write_text("sections: []\n", encoding="utf-8")
    return root


def test_opening_a_copy_replaces_the_tab_instead_of_adding(tmp_path):
    a = _paper(tmp_path / "ws", "2018_Peerception_AUTHORS")
    b = _paper(tmp_path / "ws", "2026_Other")
    copy = _paper(tmp_path / "scratch", "2018_Peerception_AUTHORS")
    tabstore.open_tab(a)
    tabstore.open_tab(b)
    tabstore.open_tab(copy)
    tabs = tabstore.load()
    assert [p.name for p in tabs] == ["2018_Peerception_AUTHORS.md4", "2026_Other.md4"]  # 자리 그대로, 하나만
    assert tabs[0] == copy.resolve()
    tabstore.open_tab(a)
    assert tabstore.load()[0] == a.resolve()


def test_existing_duplicates_collapse_preferring_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    real = _paper(ws, "2018_Peerception_AUTHORS")
    c1 = _paper(tmp_path / "s1", "2018_Peerception_AUTHORS")
    c2 = _paper(tmp_path / "s2", "2018_Peerception_AUTHORS")
    other = _paper(ws, "2026_Other")
    monkeypatch.setattr("narani.config.resolve_workspace", lambda: ws)
    tabstore._write([str(c1.resolve()), str(other.resolve()), str(c2.resolve()), str(real.resolve())])
    tabs = tabstore.load()
    assert tabs == [real.resolve(), other.resolve()]
