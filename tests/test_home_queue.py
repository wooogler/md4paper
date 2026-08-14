"""홈 변환 대기열 — save_source + 순차 워커 (서버 없이 run.io_bound 사용)."""

import asyncio
from pathlib import Path

from md4paper.ui import app as home
from md4paper.workdir import WorkDir


def test_save_source_writes_to_stem_folder(tmp_path):
    src = home.save_source(b"hello", "paper.pdf", tmp_path)
    assert src == tmp_path / "paper" / "paper.pdf"
    assert src.read_bytes() == b"hello"


def _mark_converted(src: Path) -> None:
    """이 업로드가 변환까지 끝난 것처럼 <이름>.md4를 만들어 둔다 (껍데기가 아님)."""
    (src.parent / f"{src.stem}.md4").mkdir()


def test_save_source_creates_new_folder_on_reupload(tmp_path):
    # 이미 '변환된' 논문과 같은 PDF를 다시 올리면 덮어쓰지 않고 '<이름> (N)' 고유 폴더 → 새 항목
    first = home.save_source(b"v1", "paper.pdf", tmp_path)
    _mark_converted(first)
    second = home.save_source(b"v2", "paper.pdf", tmp_path)
    _mark_converted(second)
    third = home.save_source(b"v3", "paper.pdf", tmp_path)
    assert first == tmp_path / "paper" / "paper.pdf"
    assert second == tmp_path / "paper (2)" / "paper.pdf"
    assert third == tmp_path / "paper (3)" / "paper.pdf"
    # 원본이 보존됨(덮어쓰지 않음) → 서로 다른 워크디렉토리 경로가 됨
    assert first.read_bytes() == b"v1" and second.read_bytes() == b"v2"
    assert first.parent != second.parent != third.parent


def test_save_source_reuses_unconverted_upload_stub(tmp_path):
    """변환되지 않은 업로드 잔여물(.md4 없음)은 재사용한다 — 끊긴 업로드가 이름을 점유하면 안 된다.

    업로드는 원본을 즉시 저장하고 변환은 큐에서 도는데, 그 사이에 서버를 끄면 PDF만 남는다.
    예전엔 이 껍데기 때문에 다시 올릴 때마다 '(2)'가 붙어 파일명이 영영 더러워졌다.
    """
    first = home.save_source(b"v1", "paper.pdf", tmp_path)  # 변환 전에 중단된 업로드
    second = home.save_source(b"v2", "paper.pdf", tmp_path)  # 다시 올림

    assert second == first  # 같은 폴더를 재사용 (2)가 붙지 않음
    assert second.read_bytes() == b"v2"
    assert [p.name for p in tmp_path.iterdir()] == ["paper"]


def _fake_wd(src_path: Path, meta: str = "{}") -> WorkDir:
    wd = WorkDir(src_path.parent / f"{src_path.stem}.md4")
    wd.extract.mkdir(parents=True, exist_ok=True)
    wd.meta_json.write_text(meta, encoding="utf-8")
    return wd


def _item(tmp_path, name):
    src = home.save_source(b"x", name, tmp_path)
    return {"name": name, "src_path": str(src), "pages": 0, "backend": "docling", "ocr": False,
            "status": "pending", "error": "", "garbled": 0, "since": 0.0, "done_at": 0.0}


def test_queue_processes_in_order_and_isolates_failure(tmp_path, monkeypatch):
    calls = []

    def fake_convert(src_path, backend, ocr, flavor="standard"):
        src_path = Path(src_path)
        calls.append(src_path.name)
        if "bad" in src_path.name:
            raise RuntimeError("boom")
        return _fake_wd(src_path)

    monkeypatch.setattr(home, "convert_source", fake_convert)
    monkeypatch.setattr(home, "_has_llm_key", lambda: False)  # cite/glossary 스킵

    state = {"queue": [_item(tmp_path, "a.pdf"), _item(tmp_path, "bad.pdf"), _item(tmp_path, "c.pdf")],
             "worker_running": False}
    asyncio.run(home._process_queue(state))

    assert calls == ["a.pdf", "bad.pdf", "c.pdf"]  # 순차 처리, 실패해도 다음 진행
    statuses = {it["name"]: it["status"] for it in state["queue"]}
    assert statuses == {"a.pdf": "done", "bad.pdf": "failed", "c.pdf": "done"}
    assert state["queue"][1]["error"] == "boom"
    assert not state["worker_running"]  # 끝나면 워커 플래그 해제
    assert state["queue"][0]["wd_root"].endswith("a.md4")


def test_queue_runs_cite_then_glossary_when_key_present(tmp_path, monkeypatch):
    phases = []
    monkeypatch.setattr(home, "convert_source",
                        lambda src, b, o, flavor="standard": _fake_wd(Path(src)))
    monkeypatch.setattr(home, "_has_llm_key", lambda: True)
    monkeypatch.setattr(home, "_auto_cite", lambda wd: phases.append("cite"))
    monkeypatch.setattr(home, "_auto_glossary", lambda wd: phases.append("glossary"))
    monkeypatch.setattr(home, "_auto_metadata", lambda wd: phases.append("meta"))

    item = _item(tmp_path, "p.pdf")
    state = {"queue": [item], "worker_running": False, "upload_dir": tmp_path}
    asyncio.run(home._process_queue(state))
    assert phases == ["cite", "glossary", "meta"]
    assert item["status"] == "done"


def test_queue_exports_to_library_when_folder_set(tmp_path, monkeypatch):
    """변환이 끝나면 전역 '저장 위치' 폴더에 결과 마크다운이 쌓인다."""
    from md4paper import config

    en_dir = tmp_path / "EN"
    config.set_library_dir("en", str(en_dir))

    def fake_convert(src, backend, ocr, flavor="standard"):
        wd = _fake_wd(Path(src))
        wd.out.mkdir(parents=True, exist_ok=True)
        wd.en_md.write_text("# Paper\n", encoding="utf-8")
        return wd

    monkeypatch.setattr(home, "convert_source", fake_convert)
    monkeypatch.setattr(home, "_has_llm_key", lambda: False)

    item = _item(tmp_path, "p.pdf")
    asyncio.run(home._process_queue({"queue": [item], "worker_running": False}))

    assert item["status"] == "done"
    assert (en_dir / "p.md").read_text(encoding="utf-8").startswith("# Paper")


def test_worker_guard_skips_when_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(home, "convert_source",
                        lambda src, b, o, flavor="standard": _fake_wd(Path(src)))
    item = _item(tmp_path, "p.pdf")
    state = {"queue": [item], "worker_running": True}  # 이미 다른 워커가 돎
    asyncio.run(home._process_queue(state))
    assert item["status"] == "pending"  # 아무것도 안 함 (하나만 돌도록)


def test_recent_workdirs_reads_paper_meta(tmp_path):
    from md4paper import pipeline
    from md4paper.workdir import recent_workdirs

    corpus = Path(__file__).parent / "corpus" / "sample_arxiv.md"
    wd = WorkDir(tmp_path / "paper" / "paper.md4")
    pipeline.convert(corpus, wd)
    wd.paper_meta_json.write_text(
        '{"title":"My Paper","authors":["A B","C D"],"year":2025,"venue":"CHI"}', encoding="utf-8")
    r = recent_workdirs(tmp_path)[0]
    assert r["title"] == "My Paper" and r["year"] == 2025 and r["venue"] == "CHI"
    assert r["authors"] == ["A B", "C D"]


def test_garbled_warning_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(home, "convert_source",
                        lambda src, b, o, flavor="standard": _fake_wd(Path(src), '{"garbled_chars": 7}'))
    monkeypatch.setattr(home, "_has_llm_key", lambda: False)
    item = _item(tmp_path, "p.pdf")
    asyncio.run(home._process_queue({"queue": [item], "worker_running": False}))
    assert item["garbled"] == 7


def test_hidden_paper_leaves_files_and_can_be_restored(tmp_path):
    """'목록에서 숨기기'는 파일을 지우지 않고 표시만 없앤다 — 언제든 되돌릴 수 있다."""
    from md4paper import pipeline
    from md4paper.workdir import WorkDir, recent_workdirs, set_hidden

    wd = WorkDir(tmp_path / "p" / "p.md4")
    pipeline.convert(Path(__file__).parent / "corpus" / "sample_arxiv.md", wd)
    assert [r["hidden"] for r in recent_workdirs(tmp_path)] == [False]

    assert set_hidden(wd.root) is True
    assert recent_workdirs(tmp_path) == []  # 기본 목록에서 사라짐
    hidden = recent_workdirs(tmp_path, include_hidden=True)
    assert len(hidden) == 1 and hidden[0]["hidden"] is True
    assert wd.en_md.exists() and wd.root.is_dir()  # 파일은 그대로 (삭제와 다른 점)

    assert set_hidden(wd.root, False) is True
    assert [r["hidden"] for r in recent_workdirs(tmp_path)] == [False]


def test_set_hidden_rejects_missing_workdir(tmp_path):
    from md4paper.workdir import set_hidden

    assert set_hidden(tmp_path / "없는논문.md4") is False


def test_recent_workdirs_opened_flag(tmp_path):
    """리뷰를 연 논문은 opened=True (status.json의 opened_at) — 미열람 표시용."""
    from md4paper import pipeline
    from md4paper.ui.app import _mark_opened
    from md4paper.workdir import WorkDir, recent_workdirs

    corpus = Path(__file__).parent / "corpus" / "sample_arxiv.md"
    wd = WorkDir(tmp_path / "p" / "p.md4")
    pipeline.convert(corpus, wd)

    assert recent_workdirs(tmp_path)[0]["opened"] is False  # 변환 직후엔 미열람
    _mark_opened(wd)
    assert recent_workdirs(tmp_path)[0]["opened"] is True
    _mark_opened(wd)  # 중복 호출 안전 (opened_at 유지)
    assert recent_workdirs(tmp_path)[0]["opened"] is True
