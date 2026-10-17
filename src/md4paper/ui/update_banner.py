"""새 버전 알림 — 앱을 켤 때 GitHub을 한 번 보고, 새 커밋이 있으면 오른쪽 아래에 띄운다.

검사는 서버가 뜰 때 한 번(백그라운드 스레드), 결과는 서버 수명 state["update"]에 둔다. 배너는 셸
페이지에 붙어 탭을 옮겨도 남고, '나중에'를 누르면 이번 실행 동안만 숨는다 — 다음에 켤 때 다시 본다.
'업데이트'를 누르면 떼어 낸 스크립트가 앱 종료를 기다려 다시 깔고 다시 켠다(§updater).
"""

from __future__ import annotations

import threading

from md4paper import updater
from md4paper.ui import jobs

_CSS = """
.md4-update{position:fixed;right:16px;bottom:16px;z-index:6000;max-width:360px;
  background:#fff;border:1px solid #c7d2fe;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.14);
  padding:12px 14px;font-size:13px}
.md4-update ul{margin:6px 0 0 16px;padding:0;color:#6b7280;font-size:12px;max-height:120px;overflow:auto}
"""


def start_check(state: dict) -> None:
    """서버 시작 시 한 번 — 결과를 state["update"]에 (없거나 실패하면 False)."""
    src = updater.installed_source()
    state["update_src"] = src
    if src is None:
        state["update"] = False
        return

    def work() -> None:
        try:
            state["update"] = updater.check(src) or False
        except Exception as e:  # noqa: BLE001 — 오프라인·API 한도 등은 조용히 넘긴다
            print(f"업데이트 확인 실패: {e}")
            state["update"] = False

    threading.Thread(target=work, name="md4paper-update-check", daemon=True).start()


def install(state: dict) -> None:
    """셸 페이지가 부른다 — 결과가 나올 때까지 기다렸다가 새 버전이 있으면 배너를 그린다."""
    from nicegui import app, ui

    ui.add_css(_CSS)
    box = ui.element("div")

    def apply() -> None:
        busy = list(jobs.running()) or state.get("queue")
        if busy:
            ui.notify("진행 중인 변환·번역이 끝난 뒤에 업데이트하세요.", type="warning")
            return
        try:
            log = updater.spawn_update(state["update_src"])
        except Exception as e:  # noqa: BLE001
            ui.notify(f"업데이트를 시작하지 못했습니다: {e}", type="negative")
            return
        print(f"업데이트 시작 — 로그: {log}")
        ui.notify("업데이트를 받는 중입니다. 앱이 잠시 닫혔다가 다시 열립니다.", type="info")
        ui.timer(1.2, app.shutdown, once=True)

    def dismiss() -> None:
        state["update_dismissed"] = True
        box.clear()
        box.classes(remove="md4-update")

    def render() -> None:
        upd = state.get("update")
        if upd is None:
            return  # 아직 확인 중
        timer.deactivate()
        if not upd or state.get("update_dismissed"):
            return
        box.classes("md4-update")
        with box:
            n = f" ({upd.ahead}개 변경)" if upd.ahead else ""
            ui.label(f"새 버전이 있습니다{n}").classes("font-bold")
            if upd.messages:
                with ui.element("ul"):
                    for m in upd.messages[:8]:
                        with ui.element("li"):
                            ui.label(m)
            with ui.row().classes("justify-end gap-1 mt-2 w-full"):
                ui.button("나중에", on_click=dismiss).props("flat dense no-caps")
                ui.button("업데이트하고 다시 시작", icon="system_update", on_click=apply) \
                    .props("dense unelevated no-caps color=indigo")

    timer = ui.timer(1.0, render)
    render()
