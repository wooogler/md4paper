"""일괄 번역 확인 대화상자 — 홈의 '번역 (N편)'과 컬렉션 용어집의 '낡은 섹션 재번역'이 같이 쓴다.

여러 편을 한꺼번에 번역하면 돈이 든다. 그래서 띄우기 전에 고른 논문을 세 묶음(번역 없음 · 낡은 섹션
있음 · 최신)으로 세어 대략 비용과 함께 보여 주고, 확인을 받아야 논문마다 번역 작업을 띄운다
(`translate_job.start` — 동시에 두 편씩, 나머지는 대기). 숫자는 `translate_job.plan`이 센다(LLM 없음).

모델은 논문마다 그 논문을 지난번에 번역한 모델(없으면 전역 기본) — 뷰어 드롭다운 기본값과 같다.
키가 없는 제공사의 논문은 건너뛰고 알린다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from md4paper.ui import jobs, translate_job
from md4paper.workdir import WorkDir


def _money(x: float) -> str:
    return f"≈ ${x:.2f}" if x >= 0.01 else ("≈ $0" if x <= 0 else "< $0.01")


def open_batch_translate(roots: list[Path | str], *, on_started: Callable[[int], None] | None = None) -> None:
    """확인 대화상자를 연다 — 지금 `with` 문맥(홈의 host 등) 안에 짓는다.

    on_started(n)은 작업을 띄운 뒤 불린다(n = 띄운 편수) — 호출측이 작업 카드·선택을 정리한다.
    """
    from nicegui import run, ui

    roots = [Path(r) for r in roots]
    st: dict = {"plan": None, "redo": False}

    def targets() -> list[dict]:
        p = st["plan"] or {}
        return [*p.get("fresh", []), *p.get("stale", []), *(p.get("current", []) if st["redo"] else [])]

    def launch() -> None:
        started, busy, nokey = 0, [], []
        for item in targets():
            wd = WorkDir(item["root"])
            if jobs.is_running(wd.root, "translate") or jobs.is_running(wd.root, "reconvert"):
                busy.append(item["title"])
                continue
            try:
                prov = translate_job.provider_for(wd, item.get("model"))
            except RuntimeError:
                nokey.append(item["title"])
                continue
            if translate_job.start(wd, prov, title=item["title"]) is None:
                busy.append(item["title"])
            else:
                started += 1
        dlg.close()
        if started:
            ui.notify(f"번역 {started}편 시작 — 동시에 {translate_job.gate().limit}편씩, 나머지는 대기열에서 "
                      "차례로 돕니다. 화면을 옮겨도 계속됩니다.", type="positive")
        if busy:
            ui.notify(f"이미 번역·변환 중이라 건너뜀 {len(busy)}편: " + ", ".join(t[:30] for t in busy[:3])
                      + (" …" if len(busy) > 3 else ""), type="info")
        if nokey:
            ui.notify(f"번역 모델의 API 키가 없어 건너뜀 {len(nokey)}편 — ⚙ 설정의 AI에서 키를 넣으세요.",
                      type="warning")
        if on_started is not None:
            on_started(started)

    @ui.refreshable
    def body() -> None:
        p = st["plan"]
        if p is None:
            with ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm")
                ui.label(f"{len(roots)}편 살펴보는 중…").classes("text-sm text-gray-500")
            return
        fresh, stale, cur, skip = p["fresh"], p["stale"], p["current"], p["skipped"]

        def line(icon: str, color: str, text: str, sub: str = "", tip: list[dict] | None = None) -> None:
            with ui.row().classes("items-start gap-2 no-wrap w-full"):
                ui.icon(icon, size="18px").classes(color + " q-mt-xs")
                with ui.column().classes("gap-0 min-w-0"):
                    lbl = ui.label(text).classes("text-sm")
                    if tip:
                        lbl.tooltip(" · ".join(t["title"][:40] for t in tip[:12]) + (" …" if len(tip) > 12 else ""))
                    if sub:
                        ui.label(sub).classes("text-xs text-gray-500")

        line("fiber_new", "text-primary", f"번역 없음 {len(fresh)}편",
             f"처음부터 번역 {_money(p['cost_fresh'])}" if fresh else "", fresh)
        line("update", "text-orange-7", f"낡은 섹션 있음 {len(stale)}편 (섹션 {p['stale_sections']}개)",
             f"바뀐 섹션만 다시 번역 {_money(p['cost_stale'])} — 나머지는 지난 번역(캐시)" if stale else "", stale)
        line("check_circle", "text-green-7", f"최신 {len(cur)}편",
             ("다시 번역합니다 — 캐시를 써서 비용은 거의 0" if st["redo"] else "건너뜁니다") if cur else "", cur)
        if skip:
            line("block", "text-grey-6", f"번역할 수 없음 {len(skip)}편",
                 " · ".join(f"{s['title'][:30]} ({s['reason']})" for s in skip[:3]), skip)
        if cur:
            ui.checkbox("최신인 논문도 다시 번역", value=st["redo"],
                        on_change=lambda e: (st.update(redo=e.value), body.refresh())).props("dense") \
                .tooltip("설정·문체를 바꾼 뒤 결과 파일을 다시 쓰고 싶을 때 — 바뀐 곳이 없으면 캐시로 채워집니다")
        manual = [t for t in targets() if t.get("manual_ko")]
        if manual:
            ui.label(f"⚠ 번역문을 직접 고친 {len(manual)}편은 새 결과로 다시 쓰여 고친 내용이 사라집니다.") \
                .classes("text-xs text-orange-9")
        n = len(targets())
        total = p["cost_fresh"] + p["cost_stale"]
        ui.label(f"합계 {n}편 · {_money(total)} (대략 — 원문 글자 수로 어림) · 동시에 "
                 f"{translate_job.gate().limit}편씩").classes("text-xs text-gray-500 q-mt-xs")
        with ui.row().classes("justify-end gap-2 w-full q-mt-sm"):
            ui.button("취소", on_click=dlg.close).props("flat dense no-caps")
            ui.button(f"번역 시작 ({n}편)", icon="translate", on_click=launch) \
                .props("unelevated dense no-caps color=secondary" + ("" if n else " disable"))

    with ui.dialog() as dlg, ui.card().classes("gap-2").style("width:520px; max-width:94vw"):
        ui.label(f"{len(roots)}편 번역").classes("text-base font-bold")
        ui.label("고른 논문을 지금 용어집(컬렉션 용어집 포함)·설정으로 번역합니다. 모델은 논문마다 지난번에 "
                 "번역한 모델(없으면 기본 모델)입니다.").classes("text-xs text-gray-600")
        body()

        async def load() -> None:
            plan = await run.io_bound(translate_job.plan, roots)
            if dlg.is_deleted:
                return
            st["plan"] = plan
            body.refresh()

        ui.timer(0.05, load, once=True)
    dlg.on("hide", dlg.delete)  # 열 때마다 새로 짓는다 — 닫히면 치운다
    dlg.open()
