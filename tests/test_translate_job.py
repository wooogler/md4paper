"""공용 번역 작업(§ui/translate_job.py) — 뷰어·홈 일괄 번역이 같은 작업 함수와 같은 동시 실행 문을 쓴다.

- start: jobs 레지스트리에 translate 작업을 올리고, 문(gate)이 두 편까지만 들여보낸다. 기다리는 작업은
  `detail["queued"]`가 켜진다(홈 작업 카드의 '대기 중 · 앞에 n개'). 기다리기는 이벤트 루프에서 —
  스레드를 붙잡지 않는다.
- plan: 일괄 번역 확인 대화상자의 네 묶음(번역 없음 · 전체 재번역(캐시 못 씀) · 낡은 섹션 있음 · 최신)과
  대략 비용.
"""

import asyncio
import threading
from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.ir import GlossaryEntry
from md4paper.llm import FakeProvider
from md4paper.ui import controller, jobs, translate_job
from md4paper.ui.controller import UIController
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"


@pytest.fixture(autouse=True)
def _fresh_gate():
    translate_job.reset(2)
    yield
    translate_job.reset()


def test_start_runs_at_most_two_and_marks_the_rest_queued(tmp_path, monkeypatch):
    release = threading.Event()
    entered: list[str] = []

    class FakeCtrl:
        def __init__(self, wd):
            self.wd = wd

        def translate(self, provider, style, on_progress):
            entered.append(self.wd.root.name)
            on_progress(1, 2, {"s1": "done"})
            release.wait(5)
            return {"cost_usd": 0.0123, "kept": 0}

    monkeypatch.setattr(controller, "UIController", FakeCtrl)
    monkeypatch.setattr(translate_job.library, "auto_export", lambda wd: [])
    wds = [WorkDir(tmp_path / f"p{i}.md4") for i in range(3)]

    async def go():
        js = [translate_job.start(wd, object(), title=f"논문 {i}") for i, wd in enumerate(wds)]
        assert all(j is not None and j.kind == "translate" for j in js)
        assert translate_job.start(wds[0], object()) is None  # 같은 논문은 하나만 (기다리는 중이어도)
        for _ in range(300):
            if len(entered) == 2 and js[2].detail.get("queued"):
                break
            await asyncio.sleep(0.01)
        assert sorted(entered) == ["p0.md4", "p1.md4"]
        assert js[2].detail.get("queued") and translate_job.ahead(js[2]) == 0
        assert not js[0].detail.get("queued") and translate_job.ahead(js[0]) == -1
        assert js[0].detail["status"] == {"s1": "done"} and js[0].total == 2  # 뷰어 진행 바가 읽는 값
        assert [j.kind for j in jobs.running()] == ["translate"] * 3  # 기다리는 작업도 '도는 중'
        release.set()
        while any(j.running for j in js):
            await asyncio.sleep(0.01)
        return js

    js = asyncio.run(go())
    assert [j.status for j in js] == ["done"] * 3 and len(entered) == 3
    assert js[0].message == "번역 완료 (≈$0.0123)"
    assert "queued" not in js[2].detail
    assert translate_job.gate().active == 0 and translate_job.gate().waiting == []


def test_gate_releases_on_failure(tmp_path, monkeypatch):
    class Boom:
        def __init__(self, wd):
            pass

        def translate(self, *a):
            raise RuntimeError("API 키 없음")

    monkeypatch.setattr(controller, "UIController", Boom)

    async def go():
        job = translate_job.start(WorkDir(tmp_path / "a.md4"), object())
        while job.running:
            await asyncio.sleep(0.01)
        return job

    job = asyncio.run(go())
    assert job.status == "failed" and "API 키 없음" in job.error
    assert translate_job.gate().active == 0  # 실패해도 자리를 돌려준다


def test_viewer_uses_shared_translate_job():
    """뷰어 '번역 실행'도 같은 작업 함수·같은 문을 지난다 (작업 함수를 옮겼지 복사하지 않았다)."""
    import inspect

    from md4paper.ui import app

    src = inspect.getsource(app.build)
    body = src.split("def _start_translate(", 1)[1].split("def attach_translate(", 1)[0]
    assert "translate_job.start(ctrl.wd, prov, title=_title, style=style)" in body
    assert "library.auto_export" not in body


def _translated(tmp_path, name: str) -> WorkDir:
    wd = WorkDir(tmp_path / f"{name}.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", wd)
    c = UIController(wd)
    c.save_glossary([GlossaryEntry(term="attention", korean="어텐션")])
    c.translate(FakeProvider(complete_fn=lambda s, u: u, model="gpt-6-luna"), "해라체")
    return wd


def test_plan_buckets_and_estimates(tmp_path):
    fresh = WorkDir(tmp_path / "fresh.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", fresh)
    cur = _translated(tmp_path, "cur")
    stale = _translated(tmp_path, "stale")
    UIController(stale).save_glossary([GlossaryEntry(term="attention", korean="주의 집중")])  # 용어 하나 바꿈
    empty = tmp_path / "empty.md4"
    empty.mkdir()

    p = translate_job.plan([fresh.root, cur.root, stale.root, empty],
                           model_for=lambda wd: "openai/gpt-6-luna")
    assert [x["root"] for x in p["fresh"]] == [fresh.root]
    assert [x["root"] for x in p["current"]] == [cur.root]
    assert [x["root"] for x in p["stale"]] == [stale.root]
    assert [x["root"] for x in p["skipped"]] == [empty]
    st = p["stale"][0]
    assert 0 < st["sections"] == p["stale_sections"]
    assert 0 < st["chars"] < len(stale.en_md.read_text(encoding="utf-8"))  # 바뀐 유닛만
    assert p["fresh"][0]["chars"] == len(fresh.en_md.read_text(encoding="utf-8"))
    assert p["cost_fresh"] == pytest.approx(translate_job.estimate_usd("gpt-6-luna", p["fresh"][0]["chars"]))
    assert p["cost_fresh"] > p["cost_stale"] > 0
    assert p["current"][0]["est_cost_usd"] == 0.0 and p["fresh"][0]["model"] == "openai/gpt-6-luna"
    assert not any(x["manual_ko"] for x in p["fresh"] + p["stale"] + p["current"])


def test_plan_counts_unusable_cache_as_full_retranslation(tmp_path):
    cur = _translated(tmp_path, "cur")
    nocache = _translated(tmp_path, "nocache")
    nocache.cache_json.unlink()  # 번역은 있는데 보관된 번역이 없다 — 다음 실행이 처음부터 다시 번역

    p = translate_job.plan([cur.root, nocache.root], model_for=lambda wd: "openai/gpt-6-luna")
    assert [x["root"] for x in p["current"]] == [cur.root]
    assert [x["root"] for x in p["full"]] == [nocache.root]
    assert p["stale"] == [] and p["stale_sections"] == 0 and p["cost_stale"] == 0.0  # stale에는 안 넣는다
    f = p["full"][0]
    assert 0 < f["sections"] == p["full_sections"]
    assert 0 < f["chars"] <= len(nocache.en_md.read_text(encoding="utf-8"))
    assert f["est_cost_usd"] == p["cost_full"] == pytest.approx(translate_job.estimate_usd("gpt-6-luna", f["chars"]))
    assert p["cost_full"] > 0


def test_default_model_follows_last_translation(tmp_path):
    wd = _translated(tmp_path, "a")
    assert translate_job.default_model(wd).endswith("/gpt-6-luna")
