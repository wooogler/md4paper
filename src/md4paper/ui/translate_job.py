"""번역 작업 — 뷰어의 '번역 실행'과 홈의 일괄 번역이 같은 작업 함수를 쓴다.

작업(`start`)은 `jobs` 레지스트리에 `translate` 종류로 올라 탭을 옮겨도 끝까지 돈다(§jobs). 뷰어에서
하나를 띄우든 홈에서 80편을 띄우든 **같은 문**(`gate`, 기본 2편)을 지난다 — 한 편 안에서도 섹션을
병렬로 부르므로 편수까지 다 풀면 API 동시 호출이 불어나 속도 제한에 걸린다. 문 앞에서 기다리는 작업은
`job.detail["queued"]`가 켜져 홈 작업 카드에 '대기 중'으로 보인다.

`plan`은 홈 일괄 번역 확인 대화상자의 숫자다 — 고른 논문을 번역 없음 / 전체 재번역(캐시 못 씀) / 낡은
섹션 있음 / 최신으로 나누고 대략 비용을 어림한다(LLM 호출 없음; `glossary.impact`와 같은 식).

nicegui를 import하지 않는다 — 테스트가 asyncio.run으로 바로 돌린다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from md4paper import config, library
from md4paper.ui import jobs
from md4paper.workdir import WorkDir

_GATE: jobs.Gate | None = None


def gate() -> jobs.Gate:
    """번역 작업의 문 — 처음 쓸 때 설정(`[translate] batch_workers`)으로 만든다. 서버 수명 동안 하나."""
    global _GATE  # noqa: PLW0603 — 프로세스 전역 상한
    if _GATE is None:
        _GATE = jobs.Gate(config.resolve_translate_batch_workers())
    return _GATE


def reset(limit: int | None = None) -> None:
    """테스트용 — 문을 새로 만든다(limit을 주면 그 값으로)."""
    global _GATE  # noqa: PLW0603
    _GATE = jobs.Gate(limit) if limit is not None else None


def ahead(job: jobs.Job) -> int:
    """대기 중인 작업 앞에 기다리는 번역 수 — 기다리는 중이 아니면 -1 (홈 작업 카드의 '앞에 n개')."""
    return gate().ahead(job)


def start(wd: WorkDir, provider, *, title: str = "", style: str | None = None,  # noqa: ANN001
          figures: bool = False) -> jobs.Job | None:
    """이 논문의 번역을 백그라운드로 띄운다 — 이미 번역 중이면 None.

    지금 용어집(effective)·설정으로 `UIController.translate`(바뀐 유닛만 새로, 나머지는 캐시) → (figures면)
    아직 번역하지 않은 그림의 글자 번역(§figure_translate) → 저장 위치로 자동 저장 → 완료 문구. 워커
    스레드에서 화면 없이 완결된다. style이 None이면 논문 설정의 문체. 이벤트 루프 안(UI 핸들러)에서 불러야 한다.
    """
    from md4paper.ui.controller import UIController

    def work(job: jobs.Job) -> dict:
        def on_progress(d: int, t: int, status: dict) -> None:
            job.progress(d, t)
            job.detail["status"] = status

        ctrl = UIController(wd)  # 문을 지난 뒤에 읽는다 — 기다리는 사이 고친 설정·용어집도 따라온다
        summary = ctrl.translate(provider, style, on_progress)
        figs = None
        if figures:
            from md4paper import figure_translate

            job.detail["figures"] = True
            figs = figure_translate.translate_many(wd, provider, on_progress=lambda d, t: job.progress(d, t))
            summary["figures"] = figs
        saved = library.auto_export(wd)  # 저장 위치로 자동 저장
        msg = f"번역 완료 (≈${summary['cost_usd'] + (figs['cost_usd'] if figs else 0.0):.4f})"
        if figs and (figs["done"] or figs["failed"]):
            msg += f" · 그림 {figs['done']}개 글자 번역" + (f"(실패 {len(figs['failed'])}개)" if figs["failed"] else "")
        if saved:
            msg += f" · 저장 위치에 {len(saved)}개 파일 저장"
        if summary.get("passthrough"):
            msg += f" · {summary['passthrough']}개 섹션은 아래 표시(구조 검증 실패)"
        if summary.get("kept"):
            msg += f" · 체크 해제 {summary['kept']}개 구간은 지난 번역 유지"
        job.message = msg
        return summary

    return jobs.start(wd.root, "translate", "번역", work, title=title, gate=gate())


def default_model(wd: WorkDir) -> str:
    """'provider/model' — 이 논문을 마지막에 번역한 모델(기록이 없으면 input_hash로 되찾은 것), 그것도
    없으면 전역 기본 (뷰어 드롭다운 기본값과 같다)."""
    from md4paper import pipeline

    last = pipeline.last_translate_model(wd) or pipeline.recover_translate_model(wd)
    if last is not None:
        return "/".join(last)
    prov = config.resolve_provider()
    return f"{prov}/{config.resolve_model(prov)}"


def provider_for(wd: WorkDir, choice: str | None = None):  # noqa: ANN201 — Provider
    """그 논문의 번역 모델로 프로바이더를 만든다 — 키가 없으면 RuntimeError(호출자가 그 논문을 건너뛴다)."""
    p_name, _, m_name = (choice or default_model(wd)).partition("/")
    return config.build_provider(p_name, m_name or None)


def estimate_usd(model: str, chars: int) -> float:
    """원문 글자 수 → 대략 비용. 입력 ≈ 글자/3.5 토큰, 출력 ≈ 입력×1.3 (`glossary.impact`와 같은 식)."""
    from md4paper.llm.base import Usage, cost_usd
    from md4paper.translate import glossary

    inp = int(chars / glossary._CHARS_PER_TOKEN)  # noqa: SLF001 — 영향 미리보기와 숫자를 맞춘다
    return cost_usd(model, Usage(input_tokens=inp, output_tokens=int(inp * glossary._OUT_RATIO)))  # noqa: SLF001


def plan(roots: list[Path | str], model_for: Callable[[WorkDir], str] | None = None) -> dict:
    """일괄 번역 전 미리보기 — 고른 논문을 네 묶음으로 나누고 대략 비용을 센다 (LLM 호출 없음).

    - fresh: 번역(ko.md)이 없다 — en.md 전체 글자로 비용 어림.
    - full: 번역은 있는데 보관된 번역(캐시)을 통째로 못 쓴다(캐시 파일 없음, 지난 번역 뒤 논문 용어집·
      모델·문체가 바뀐 옛 캐시) — 다음 실행이 처음부터 다시 번역하므로 번역 대상 유닛 전체 글자로 어림,
      섹션 수. 원문이 바뀐 게 아니라 stale에는 넣지 않는다(`apply.retranslate_units`).
    - stale: 번역이 있고 낡은 유닛이 있다(원문·용어집이 바뀜) — 낡은 유닛 원문 글자로 어림, 섹션 수.
    - current: 번역이 최신이다 — 기본은 건너뛴다(다시 돌려도 캐시를 써서 비용이 거의 0).
    - skipped: 원문(en.md)·구조가 없어 번역할 수 없다.

    model_for(wd) → 'provider/model'(기본: 지난 번역 모델 > 전역 기본). 항목: {root, title, model,
    manual_ko(번역문을 직접 고쳤는지), chars, est_cost_usd, sections(full·stale만), figures(글자 번역 안 한
    그림 수), est_figures_usd}. 합계: cost_fresh, cost_full, full_sections, cost_stale, stale_sections,
    figures(그림 수), cost_figures — 그림 숫자는 대화상자에서 '그림 속 글자도 번역'을 켰을 때만 쓴다.
    """
    from md4paper import figure_translate
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply
    from md4paper.workdir import paper_title

    pick = model_for or default_model
    out: dict = {"fresh": [], "full": [], "stale": [], "current": [], "skipped": [],
                 "cost_fresh": 0.0, "cost_full": 0.0, "full_sections": 0, "cost_stale": 0.0, "stale_sections": 0,
                 "figures": 0, "cost_figures": 0.0}
    for root in roots:
        wd = WorkDir(Path(root))
        item: dict = {"root": wd.root, "title": paper_title(wd.root),
                      "manual_ko": bool(wd.load_status().get("manual_edit_ko"))}  # 다시 쓰면 고친 번역이 사라진다
        if not wd.en_md.exists():
            out["skipped"].append({**item, "reason": "영어 원문 없음"})
            continue
        try:
            manifest = manifest_io.load(wd)
        except (OSError, ValueError):
            out["skipped"].append({**item, "reason": "구조(sections.yaml)를 읽지 못함"})
            continue
        choice = pick(wd)
        model = choice.partition("/")[2] or choice
        item["model"] = choice
        nfig = len(figure_translate.todo(wd))
        item["figures"], item["est_figures_usd"] = nfig, figure_translate.estimate_usd(model, nfig)
        out["figures"] += nfig
        out["cost_figures"] += item["est_figures_usd"]
        if not wd.ko_md.exists():
            chars = len(wd.en_md.read_text(encoding="utf-8"))
            cost = estimate_usd(model, chars)
            out["fresh"].append({**item, "chars": chars, "est_cost_usd": cost})
            out["cost_fresh"] += cost
            continue
        units, full = apply.retranslate_units(wd, manifest)
        if not units:
            out["current"].append({**item, "chars": 0, "est_cost_usd": 0.0})
            continue
        sids: list[str] = []
        for u in units:
            sids.extend(s for s in u["sids"] if s not in sids)
        chars = sum(len(u["source"]) for u in units)
        cost = estimate_usd(model, chars)
        kind = "full" if full else "stale"
        out[kind].append({**item, "chars": chars, "est_cost_usd": cost, "sections": len(sids)})
        out[f"cost_{kind}"] += cost
        out[f"{kind}_sections"] += len(sids)
    return out
