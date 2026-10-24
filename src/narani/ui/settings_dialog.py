"""홈의 설정 대화상자 둘 — 전역 설정(사이드바 ⚙)과 프로젝트 하나의 설정(새로 만들기 = 편집).

첫 화면에 설정 패널을 늘어놓으면 매일 쓰는 목록이 자리를 잃는다. 설정은 처음 한 번 정하고 가끔
고치는 것이라 대화상자로 뺐고, 그 안에서도 **자주 쓰는 것만 앞에** 둔다:

- **AI** — 서비스·모델·키·연결 테스트 (번역·참고문헌 정리를 쓰려면 먼저 여기)
- **저장 위치** — 공통 폴더 하나 고르기, 자동 저장, 작업 폴더. 영어·한국어·PDF를 따로 보내는 칸은 접어 둔다
- **변환·번역** — 번역 언어·문체·섹션 제목 번역·내보내기 형식·OCR
- **고급** — 표기 세부(캡션·소제목·저자·인용·참고문헌), BibTeX, 서지 보강, 이름 규칙

설정 키와 config 함수는 예전 홈 왼쪽 패널과 같다 — 자리만 옮겼다. 상수(_PROVIDER_NAME 등)는
논문 화면도 쓰므로 app.py에 그대로 두고 함수 안에서 지연 import 한다(app.py가 이 모듈을 홈을
그릴 때 지연 import 하므로 순환은 없다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from narani.i18n import N_, t
from narani import config, i18n, library, projects
from narani.ui import desktop

_LIB_LABEL = {"en": N_("영어 마크다운"), "ko": N_("번역 마크다운"), "pdf": N_("PDF 원본")}

CSS = """
/* 설정 대화상자 — 왼쪽 세로 탭 | 오른쪽 내용(그 안에서만 스크롤). 뷰어 사이드바와 같은 색 토큰. */
.md4-setdlg { width: 760px; max-width: 94vw; padding: 0; gap: 0; overflow: hidden; }
.md4-setdlg-head { padding: 12px 14px 10px 18px; border-bottom: 1px solid #e6e4e0; }
.md4-setdlg-body { height: min(620px, 80vh); align-items: stretch; }
.md4-settabs { flex: 0 0 150px; background: #f4f3f1; border-right: 1px solid #e6e4e0; padding-top: 6px; }
.md4-settabs .q-tab { justify-content: flex-start; min-height: 38px; padding: 0 14px; color: #6b6862; }
.md4-settabs .q-tab__content { flex-direction: row; gap: 8px; }
.md4-settabs .q-tab__label { font-size: 13.5px; }
.md4-settabs .q-tab--active { color: #2383e2; background: rgba(35,131,226,.13); }
.md4-settabs .q-tab__indicator { display: none; }
.md4-setpanels { flex: 1 1 auto; min-width: 0; }
.md4-setpanels > .q-panel { overflow-y: auto; }
.md4-setpanels .q-tab-panel { padding: 16px 20px 24px; display: flex; flex-direction: column; gap: 10px; }
.md4-setsub { font-size: 11px; font-weight: 700; letter-spacing: .05em; color: #9a968e; margin-top: 6px; }
@media (prefers-color-scheme: dark) {
  .md4-setdlg-head { border-bottom-color: #333; }
  .md4-settabs { background: #1b1b1b; border-right-color: #333; }
  .md4-settabs .q-tab { color: #a8a8a8; }
  .md4-settabs .q-tab--active { background: rgba(90,150,230,.22); color: #7bb7ff; }
}
"""


def _can_pick() -> bool:
    from narani.ui import folder_dialog

    return folder_dialog.available() or desktop.active()  # 앱 창은 자체 대화상자를 갖는다


def _no_dialog(btn) -> None:  # noqa: ANN001 — ui.button
    btn.disable()
    btn.tooltip(t("이 환경에선 폴더 대화상자를 열 수 없습니다 — 경로를 직접 입력하세요"))


def any_library_dirs() -> bool:
    """내보낼 곳이 하나라도 있는지 — 공통 저장 위치 또는 폴더를 정한 프로젝트.

    프로젝트마다 폴더를 정해 쓰면 공통 설정은 비어 있을 수 있다. 그때 '폴더를 먼저 지정하세요'로
    막으면 실제로는 갈 곳이 있는 논문까지 못 내보낸다.
    """
    return library.configured() or any(projects.has_dirs(p["id"]) for p in projects.all_projects())


# ====================================================================================
# 전역 설정
# ====================================================================================
def build_settings(state: dict, on_change: Callable[[], None]) -> Callable[[str | None], None]:
    """전역 설정 대화상자를 짓고 여는 함수를 돌려준다 — `opener("ai")`처럼 탭을 골라 연다.

    홈을 그릴 때 한 번만 짓고 닫아도 지우지 않는다. 서지 보강은 몇 분씩 돌며 진행 막대를 갱신하는데,
    열 때마다 새로 지으면 대화상자를 닫은 사이 그 막대가 사라진 위젯을 건드리게 된다.
    on_change는 목록에 보이는 것(작업 폴더·이름·서지)이 바뀌었을 때 홈이 다시 훑도록 부른다.
    """
    from nicegui import run, ui

    from narani.ui.app import (
        _CAPSTYLE_EX, _CAPSTYLE_NAME, _EXPORT_TARGET, _KEY_HINT, _PROVIDER_NAME, _RUNIN,
        _model_options, _short_provider,
    )
    from narani.workdir import recent_workdirs

    can_pick = _can_pick()

    def save_global(section: str, field: str, value, label: str) -> None:  # noqa: ANN001
        config.set_section_value(section, field, value)
        ui.notify(t("기본값 저장됨 · {label}", label=label), type="positive", timeout=1200)

    with ui.dialog() as dlg, ui.card().classes("md4-setdlg"):
        with ui.row().classes("md4-setdlg-head items-center w-full no-wrap"):
            ui.label(t("설정")).classes("text-base font-bold")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat dense round size=sm color=grey")
        with ui.row().classes("md4-setdlg-body w-full no-wrap gap-0"):
            with ui.tabs().props("vertical no-caps dense").classes("md4-settabs") as tabs:
                ui.tab("ai", "AI", icon="smart_toy")
                ui.tab("library", t("저장 위치"), icon="folder_open")
                ui.tab("convert", t("변환·번역"), icon="translate")
                ui.tab("advanced", t("고급"), icon="tune")
            with ui.tab_panels(tabs, value="ai").props("vertical animated=false") \
                    .classes("md4-setpanels"):
                with ui.tab_panel("ai"):
                    _ai_panel(ui, _PROVIDER_NAME, _KEY_HINT, _model_options, _short_provider)
                with ui.tab_panel("library"):
                    _library_panel(ui, run, state, on_change, can_pick, recent_workdirs)
                with ui.tab_panel("convert"):
                    export_sel = _convert_panel(ui, save_global, _EXPORT_TARGET)
                with ui.tab_panel("advanced"):
                    refresh_scope = _advanced_panel(ui, run, state, on_change, save_global,
                                                    _CAPSTYLE_EX, _CAPSTYLE_NAME, _RUNIN)

    def opener(tab: str | None = None) -> None:
        if tab:
            tabs.set_value(tab)
        # 목록의 ⋯ 메뉴에서도 내보내기 형식을 바꿀 수 있다 — 열 때 지금 값으로 맞춘다
        export_sel.set_value(config.resolve_export_target())
        refresh_scope()  # 그새 만든 프로젝트도 서지 보강 범위에 보이게
        dlg.open()

    return opener


def _ai_panel(ui, provider_name, key_hint, model_options, short_provider) -> None:  # noqa: ANN001
    """AI — 서비스·모델·키. (예전 홈 'AI 설정' 패널 그대로)"""
    ui.label(t("PDF→마크다운 '변환'은 AI 없이도 됩니다. 참고문헌 정리·용어집·번역에만 AI를 씁니다.")) \
        .classes("text-xs text-gray-500")

    @ui.refreshable
    def key_status_row() -> None:
        st = config.key_status()
        ui.label(t("키 등록됨: ") + " · ".join(
            f"{'✅' if st[p] else '⬜'} {short_provider(p)}" for p in config.PROVIDERS)) \
            .classes("text-xs text-gray-500")

    prov = ui.select({k: t(v) for k, v in provider_name.items()}, value=config.resolve_provider(),
                     label=t("AI 서비스")).props("dense outlined").classes("w-full")
    model_sel = ui.select(model_options(prov.value), value=config.resolve_model(prov.value),
                          label=t("모델 (토큰당 가격순 · 저렴할수록 위)")).props("dense outlined").classes("w-full")
    key_in = ui.input(t("API 키 붙여넣기"), password=True, password_toggle_button=True) \
        .props(f'dense outlined placeholder="{t(key_hint[config.resolve_provider()])}"').classes("w-full")

    reveal = {"on": False}

    @ui.refreshable
    def key_detail() -> None:
        info = config.stored_key_preview(prov.value)
        if not info:
            ui.label(t("이 서비스에 저장된 키가 없습니다.")).classes("text-xs text-gray-400")
            return
        shown = config.resolve_key(prov.value) if reveal["on"] else info["masked"]
        src = t("환경변수 (파일 아님 — 여기서 못 지움)") if info["source"] == "env" else t("이 컴퓨터에 저장됨")
        with ui.row().classes("items-center gap-1 no-wrap w-full"):
            ui.label(t("현재 키: {shown}", shown=shown)).classes("text-xs text-gray-600 break-all")
            ui.label(f"· {src}").classes("text-xs text-gray-400 shrink-0")
            ui.button(icon="visibility_off" if reveal["on"] else "visibility",
                      on_click=lambda: (reveal.__setitem__("on", not reveal["on"]), key_detail.refresh())) \
                .props("flat dense round size=sm").tooltip(t("전체 보기 / 가리기"))
            if info["source"] == "file":
                ui.button(icon="delete", on_click=delete_key) \
                    .props("flat dense round size=sm color=negative").tooltip(t("저장된 키 삭제"))

    def delete_key() -> None:
        config.delete_key(prov.value)
        reveal["on"] = False
        ui.notify(t("{v} 키 삭제됨", v=short_provider(prov.value)), type="warning")
        key_detail.refresh()
        key_status_row.refresh()

    def on_prov() -> None:
        model_sel.set_options(model_options(prov.value), value=config.resolve_model(prov.value))
        key_in.props(f'placeholder="{t(key_hint[prov.value])}"')
        reveal["on"] = False
        key_detail.refresh()
    prov.on_value_change(on_prov)

    def save_ai() -> None:
        config.set_default("default_provider", prov.value)
        config.set_section_value("models", prov.value, model_sel.value)
        k = (key_in.value or "").strip()
        if k:
            config.set_key(prov.value, k)
            key_in.value = ""
        ui.notify(t("{v} · {value} 저장됨", v=short_provider(prov.value), value=model_sel.value), type="positive")
        key_detail.refresh()
        key_status_row.refresh()

    async def test_ai() -> None:
        from nicegui import run as ng_run

        k = (key_in.value or "").strip() or config.resolve_key(prov.value)
        if not k:
            ui.notify(t("테스트할 키가 없습니다 — 키를 입력하거나 먼저 저장하세요."), type="warning")
            return
        test_btn.props("loading")
        try:
            ok, msg = await ng_run.io_bound(config.validate_provider_key, prov.value, model_sel.value, k)
        finally:
            test_btn.props(remove="loading")
        ui.notify(("✅ " if ok else "❌ ") + msg, type="positive" if ok else "negative",
                  timeout=3000 if ok else 6000)

    key_detail()
    with ui.row().classes("gap-2 items-center"):
        ui.button(t("저장"), icon="save", on_click=save_ai).props("dense color=primary")
        test_btn = ui.button(t("연결 테스트"), icon="wifi_tethering", on_click=test_ai) \
            .props("dense outline").tooltip(t("입력/저장된 키로 아주 작은 호출을 보내 즉시 확인"))
        key_status_row()
    ui.label(t("키는 {CONFIG_PATH}에 소유자만 읽는 권한(0600)으로 저장되고, 선택한 AI 서비스로만 전송됩니다. 환경변수(OPENAI_API_KEY 등)가 있으면 그게 우선합니다.", CONFIG_PATH=config.CONFIG_PATH)).classes("text-xs text-gray-400")


def _library_panel(ui, run, state, on_change, can_pick, recent_workdirs) -> None:  # noqa: ANN001
    """저장 위치 — 공통 폴더 하나(종류별 세 칸은 접어서) · 자동 저장 · 작업 폴더."""

    def set_dir(which: str, path: str | None) -> None:
        """폴더 지정/해제 — 값이 실제로 바뀔 때만 저장하고 다시 그린다(blur마다 포커스가 튀지 않게)."""
        raw = str(path).strip() if path else ""
        new = Path(raw).expanduser() if raw else None
        if library.dir_for(which) == new:
            return
        config.set_library_dir(which, str(new) if new else None)
        lib_rows.refresh()
        common_row.refresh()
        ui.notify(f"{t(_LIB_LABEL[which])} → {new}" if new else t("{v} 저장 위치 해제됨", v=t(_LIB_LABEL[which])),
                  type="positive" if new else "info")

    async def pick(which: str) -> None:
        cur = library.dir_for(which) or state["upload_dir"]
        picked = await desktop.choose_folder(t("{v}을 쌓을 폴더 선택", v=t(_LIB_LABEL[which])), str(cur))
        if picked:  # 취소면 조용히 원래 값 유지
            set_dir(which, picked)

    async def pick_one_folder() -> None:
        """폴더 하나 → en/ko/pdf 세 칸을 한꺼번에 (프로젝트 폴더와 똑같은 배치)."""
        cur = library.dir_for("en") or state["upload_dir"]
        picked = await desktop.choose_folder(t("공통 저장 폴더 선택 (영어는 루트 · 번역 {sub}/ · PDF pdf/)", sub=projects.translation_subdir()), str(cur))
        if not picked:
            return
        for which, path in projects.layout(picked).items():
            config.set_library_dir(which, str(path))
        projects.ensure_layout(picked)
        lib_rows.refresh()
        common_row.refresh()
        ui.notify(t("저장 위치 → {picked} (영어는 루트 · 번역 {v}/ · PDF pdf/)", picked=picked, v=projects.translation_subdir()), type="positive")

    @ui.refreshable
    def common_row() -> None:
        """지금 공통 자리가 어떻게 잡혀 있는지 한 줄로 — 세 칸을 펼치지 않아도 보이게."""
        dirs = library.dirs_for()
        en = dirs.get("en")
        if en is None and not any(dirs.values()):
            ui.label(t("지정 안 됨 — 폴더를 정하지 않은 논문은 작업 폴더에만 남습니다.")) \
                .classes("text-xs text-gray-400")
            return
        layout = projects.layout(en) if en else {}
        if en and all(dirs.get(k) == layout.get(k) for k in library.KINDS):
            ui.label(f"{en}  ·  {projects.translation_subdir()}/  ·  pdf/").classes("text-xs text-gray-500 break-all")
        else:  # 세 칸을 따로 정해 둔 경우 — 각각 보여 준다
            for which in library.KINDS:
                if dirs.get(which):
                    ui.label(f"{t(_LIB_LABEL[which])}: {dirs[which]}").classes("text-xs text-gray-500 break-all")

    @ui.refreshable
    def lib_rows() -> None:
        for which in library.KINDS:
            cur = library.dir_for(which)
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(t(_LIB_LABEL[which])).classes("text-sm shrink-0").style("width:104px")
                inp = ui.input(value=str(cur or ""), placeholder=t("폴더를 고르거나 경로를 붙여넣으세요")) \
                    .props("dense outlined").classes("flex-grow")
                # 직접 입력은 포커스를 벗어나거나 Enter를 칠 때만 저장(타이핑 중 매번 쓰지 않게)
                inp.on("blur", lambda _, w=which, i=inp: set_dir(w, (i.value or "").strip() or None))
                inp.on("keydown.enter", lambda _, w=which, i=inp: set_dir(w, (i.value or "").strip() or None))
                btn = ui.button(icon="folder_open", on_click=lambda _, w=which: pick(w)) \
                    .props("dense outline").tooltip(t("폴더 선택 대화상자 열기"))
                if not can_pick:
                    _no_dialog(btn)
                clear = ui.button(icon="close", on_click=lambda _, w=which: set_dir(w, None)) \
                    .props("flat dense round size=sm color=grey").tooltip(t("이 폴더 설정 해제"))
                clear.set_visibility(cur is not None)
        if library.same_folder():
            ui.label(t("두 폴더가 같아서 파일 이름 뒤에 .en / .ko를 붙여 구분합니다.")) \
                .classes("text-xs text-orange-600")

    async def export_all() -> None:
        if not any_library_dirs():
            ui.notify(t("먼저 폴더를 지정하세요."), type="warning")
            return
        roots = [r["root"] for r in recent_workdirs(state["upload_dir"], limit=1000)]
        if not roots:
            ui.notify(t("내보낼 논문이 없습니다."), type="warning")
            return
        export_btn.props("loading")
        try:
            ok, failed = await run.io_bound(library.export_many, roots)
        finally:
            export_btn.props(remove="loading")
        msg = t("{ok}편을 저장 위치에 내보냈습니다", ok=ok) + (t(" · {failed}편 실패", failed=failed) if failed else "")
        ui.notify(msg, type="positive" if ok else "warning")

    async def pick_workspace() -> None:
        picked = await desktop.choose_folder(t("작업 폴더 선택 (원본 PDF·중간 파일)"), str(state["upload_dir"]))
        if not picked:
            return
        set_workspace(picked)

    def set_workspace(path: str) -> None:
        raw = (path or "").strip()
        if not raw or Path(raw).expanduser() == Path(state["upload_dir"]):
            return
        if not Path(raw).expanduser().is_dir():  # 오타 한 글자로 목록이 통째로 비지 않게
            ui.notify(t("폴더가 없습니다: {raw}", raw=raw), type="warning")
            ws_row.refresh()
            return
        config.set_section_value("output", "workspace", raw)
        state["upload_dir"] = Path(raw).expanduser()
        ws_row.refresh()
        on_change()
        ui.notify(t("작업 폴더 → {raw}", raw=raw), type="positive")

    @ui.refreshable
    def ws_row() -> None:
        cur = str(state["upload_dir"])
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            # 대화상자를 못 쓰는 환경(원격 브라우저 등)에서도 바꿀 수 있게 직접 입력 칸을 둔다
            inp = ui.input(value=cur).props("dense outlined").classes("flex-grow min-w-0")
            inp.on("keydown.enter", lambda _, i=inp: set_workspace(i.value))
            inp.on("blur", lambda _, i=inp: set_workspace(i.value))
            btn = ui.button(t("변경"), icon="folder_open", on_click=pick_workspace).props("dense outline no-caps")
            if not can_pick:
                _no_dialog(btn)

    ui.label(t("변환·번역이 끝난 마크다운(원하면 원본 PDF도)을 쌓아 둘 공통 폴더입니다. 컬렉션에 폴더를 "
             "정해 두면 그 논문은 그 폴더로 가고, 컬렉션이 없거나 폴더를 안 정한 논문만 이곳으로 옵니다.")) \
        .classes("text-xs text-gray-500")
    ui.label(t("공통 저장 폴더")).classes("md4-setsub")
    common_row()
    with ui.row().classes("items-center gap-2"):
        one_btn = ui.button(t("폴더 하나 고르기"), icon="create_new_folder", on_click=pick_one_folder) \
            .props("dense outline no-caps") \
            .tooltip(t("폴더 하나를 고르면 영어는 루트, 번역은 {v}/, PDF는 pdf/로 자동 배치합니다", v=projects.translation_subdir()))
        if not can_pick:
            _no_dialog(one_btn)
    with ui.expansion(t("개별 지정 — 영어·한국어·PDF를 각각 다른 폴더로"), value=False) \
            .classes("w-full").props("dense"):
        ui.label(t("예: Obsidian 볼트의 Papers/EN, Papers/KO, Papers/PDF")).classes("text-xs text-gray-400")
        lib_rows()
    ui.switch(t("변환·번역이 끝나면 자동으로 저장"), value=config.resolve_library_auto(),
              on_change=lambda e: config.set_section_value("library", "auto", e.value)) \
        .props("dense").tooltip(t("끄면 목록의 '폴더로 저장'이나 논문 화면에서 직접 내보낼 때만 저장합니다"))
    with ui.row().classes("items-center gap-2"):
        export_btn = ui.button(t("이미 변환한 논문도 지금 내보내기"), icon="drive_file_move",
                               on_click=export_all).props("dense outline no-caps")
    ui.label(t("그림은 images/<논문 이름>/ 아래에 들어갑니다. 같은 논문을 다시 내보내면 덮어씁니다.")) \
        .classes("text-xs text-gray-400")
    ui.label(t("작업 폴더")).classes("md4-setsub")
    ws_row()
    ui.label(t("작업 폴더에는 올린 원본 PDF와 작업 파일(.md4)이 들어갑니다 — 위 저장 위치와 별개입니다. "
             "다른 폴더에서 변환해 둔 논문이 안 보이면 여기서 그 폴더를 고르세요.")).classes("text-xs text-gray-400")


def _convert_panel(ui, save_global, export_target):  # noqa: ANN001, ANN202 — ui.select
    """변환·번역 — 자주 바꾸는 것만. 돌려주는 것은 내보내기 형식 select(열 때 값을 맞춘다)."""
    ui.label(t("앞으로 변환·번역하는 논문의 기본값입니다. 논문별 세부 조정은 논문 화면에서 언제든 바꿀 수 있어요.")) \
        .classes("text-xs text-gray-500")
    ui.label(t("번역")).classes("md4-setsub")
    from narani import langs

    @ui.refreshable
    def style_select() -> None:
        """번역 문체 — 고른 번역 언어의 프리셋 (언어를 바꾸면 다시 그린다)."""
        lang = config.resolve_target_lang()
        opts = langs.style_options(lang)
        cur = config.resolve_style(lang)
        if cur not in opts:  # custom:"…" — 목록에 넣어 둬야 값이 지워지지 않는다
            opts = {**opts, cur: cur}
        ui.select(opts, value=cur, label=t("번역 문체 · {native}", native=langs.get(lang).native),
                  on_change=lambda e: save_global("translate", config.style_key(lang), e.value, t("번역 문체"))) \
            .props("dense outlined").classes("w-full")

    def pick_lang(e) -> None:  # noqa: ANN001
        if e.value == config.resolve_target_lang():
            return
        save_global("translate", "target_lang", e.value, t("번역 언어 · {native}", native=langs.get(e.value).native))
        style_select.refresh()

    def pick_ui_lang(e) -> None:  # noqa: ANN001
        if e.value == i18n.ui_lang():
            return
        save_global("ui", "language", e.value, t("화면 언어 — 창을 새로 열면 바뀝니다"))

    _ui = i18n.ui_lang()
    ui.select({c: langs.get(c).native if c != "en" else "English" for c in i18n.available()}, value=_ui,
              label=t("화면 언어"), on_change=pick_ui_lang).props("dense outlined").classes("w-full")
    ui.label(t("메뉴·단추·안내문의 언어입니다. 창을 새로 열면 바뀝니다.")) \
        .classes("text-xs text-gray-400")
    _lang = config.resolve_target_lang()
    ui.select(langs.language_options(_lang), value=_lang, label=t("번역 언어 (모국어)"), on_change=pick_lang) \
        .props("dense outlined").classes("w-full")
    ui.label(t("새로 번역하는 논문이 이 언어로 번역됩니다. 이미 번역한 논문은 그 언어를 그대로 둡니다. "
             "그림 속 글자 번역은 아랍·데바나가리·태국 문자처럼 이어 쓰는 문자에선 아직 안 됩니다.")).classes("text-xs text-gray-400")
    style_select()
    ui.switch(t("섹션 제목도 번역"), value=config.resolve_translate_headers(),
              on_change=lambda e: save_global("translate", "translate_headers", e.value, t("헤더 번역"))) \
        .props("dense")

    ui.label(t("내보내기")).classes("md4-setsub")

    def pick_target(e) -> None:  # noqa: ANN001
        if e.value == config.resolve_export_target():
            return  # 열 때 값을 맞추느라 부른 경우 — 저장·알림 없이
        config.set_section_value("output", "export_target", e.value)
        ui.notify(t("내보내기 형식 · {v}", v=t(export_target[e.value])), type="positive", timeout=1200)

    sel = ui.select({k: t(v) for k, v in export_target.items()}, value=config.resolve_export_target(), label=t("내보내기 형식"),
                    on_change=pick_target).props("dense outlined").classes("w-full")
    ui.label(t("원문·번역 다운로드 zip과 저장 위치 사본에 적용합니다 (이미지 임베드·인용 표기).")) \
        .classes("text-xs text-gray-400")

    ui.label(t("변환")).classes("md4-setsub")
    ui.switch(t("OCR (스캔 PDF 전용)"), value=config.resolve_ocr(),
              on_change=lambda e: save_global("extract", "ocr", e.value, "OCR")) \
        .props("dense").tooltip(t("스캔 PDF일 때만 — born-digital 논문엔 불필요, 느림"))
    return sel


def _advanced_panel(ui, run, state, on_change, save_global,  # noqa: ANN001, ANN202 — () -> None
                    capstyle_ex, capstyle_name, runin):
    """고급 — 표기 세부·BibTeX·서지 보강·이름 규칙. 돌려주는 것은 보강 범위 목록을 새로 맞추는 함수."""
    from narani import enrich as _enrich_mod
    from narani import paper_meta

    ui.label(t("표기")).classes("md4-setsub")
    # 캡션 표기 — 리뷰 화면의 설정 패널과 동일한 '렌더 미리보기 클릭' 피커로 통일 (드롭다운 아님)
    ui.label(t("그림·표 캡션 표기")).classes("text-sm")

    @ui.refreshable
    def gcapstyle_picker() -> None:
        cur = config.resolve_caption_style()
        for key, example in ((k, i18n.html(v)) for k, v in capstyle_ex.items()):
            sel = cur == key
            row = ui.row().classes(
                "items-center gap-2 w-full no-wrap q-px-sm q-py-xs rounded-borders cursor-pointer"
            ).style("background: rgba(35,131,226,.12)" if sel else "background: transparent")
            with row:
                ui.icon("radio_button_checked" if sel else "radio_button_unchecked", size="18px") \
                    .classes("text-primary" if sel else "text-grey-5")
                ui.html(example).classes("text-sm").tooltip(t(capstyle_name[key]))
            row.on("click", lambda e, k=key: (
                save_global("output", "caption_style", k, t("캡션 표기")), gcapstyle_picker.refresh()))

    gcapstyle_picker()
    ui.select({k: t(v) for k, v in runin.items()}, value=config.resolve_runin_mode(), label=t("본문 속 소제목 처리"),
              on_change=lambda e: save_global("structure", "runin_headings", e.value, t("소제목 처리"))) \
        .props("dense outlined").classes("w-full")

    ui.label(t("저자 표기 (이름은 항상)")).classes("text-xs text-gray-500 q-mt-xs")
    gapar = {p: (p in config.resolve_author_parts()) for p in ("email", "affiliation")}

    def save_gapar() -> None:
        chosen = [p for p in ("email", "affiliation") if gapar[p]]
        save_global("output", "author_parts", chosen, t("저자 표기"))

    with ui.row().classes("items-center gap-3"):
        for akey, alabel in (("email", t("이메일")), ("affiliation", t("소속"))):
            ui.checkbox(alabel, value=gapar[akey],
                        on_change=lambda e, k=akey: (gapar.__setitem__(k, e.value), save_gapar())) \
                .props("dense")

    ui.label(t("본문 인용 표기 (조합 가능)")).classes("text-xs text-gray-500 q-mt-xs")
    gparts = {p: (p in config.resolve_citation_parts()) for p in ("number", "authoryear", "short")}

    def save_gparts() -> None:
        chosen = [p for p in ("number", "authoryear", "short") if gparts[p]]
        save_global("cite", "parts", chosen or ["number"], t("인용 표기"))

    with ui.row().classes("items-center gap-3"):
        for pkey, plabel in (("number", t("번호  [1]")),
                             ("authoryear", t("저자·연도  (Vaswani et al. 2017)")),
                             ("short", t("약칭  (Transformer)"))):
            ui.checkbox(plabel, value=gparts[pkey],
                        on_change=lambda e, k=pkey: (gparts.__setitem__(k, e.value), save_gparts())) \
                .props("dense")
    ui.switch(t("참고문헌에 DOI/arXiv 링크 달기"), value=config.resolve_reference_links(),
              on_change=lambda e: save_global("cite", "reference_links", e.value, t("참고문헌 링크"))).props("dense")
    ui.switch(t("참고문헌 목록도 번역"), value=config.resolve_translate_references(),
              on_change=lambda e: save_global("translate", "translate_references", e.value, t("참고문헌 번역"))) \
        .props("dense")

    ui.label(t("서지 · BibTeX")).classes("md4-setsub")
    ui.switch(t("BibTeX(references.bib)도 함께 쌓기"), value=config.resolve_library_bibtex(),
              on_change=lambda e: config.set_section_value("library", "bibtex", e.value)) \
        .props("dense").tooltip(t("논문마다 항목 하나가 저장 위치의 references.bib에 덧붙습니다 — "
                                "인용할 때 그대로 복사해 붙이도록. 서지 정보가 없는 논문은 건너뜁니다."))
    ui.switch(t("변환이 끝나면 서지를 온라인에서 보강"), value=config.resolve_bib_lookup(),
              on_change=lambda e: config.set_section_value("library", "bib_lookup", e.value)) \
        .props("dense").tooltip(t("논문 API(Semantic Scholar→Crossref)에서 출판 기록을 받아 "
                                ".bib 항목을 정확하게 만들고, 참고문헌의 빈 DOI를 채워 인용을 "
                                "링크로 만듭니다. 편당 십몇 초가 더 걸리고, 논문 제목만 전송합니다. "
                                "끄면 나중에 아래 '서지 정보 보강'으로 언제든 할 수 있습니다."))

    # 보강 범위 — 사용자가 여기서 직접 고른다. 홈 목록의 필터를 따라가게 했더니 '지금 무엇을
    # 보강하려는지'가 화면에 안 보여서, 버튼 옆에 프로젝트를 명시적으로 고르게 했다.
    enrich_scope = {"pid": projects.active() if projects.active() not in
                    (projects.ALL, projects.NONE) else _enrich_mod.ALL}

    # 진행 상황은 **평범한 dict로만** 주고받는다. NiceGUI 위젯을 워커 스레드에서 직접 고치면
    # 브라우저로 밀어 주는 시점이 한참 뒤라 화면이 멈춘 것처럼 보인다(실측: 0/74에서 7분 동안
    # 멈춰 있다가 1/74로 튐). 그래서 워커는 dict만 쓰고, 화면은 아래 ui.timer가 갱신한다.
    enrich_state = {"stop": False, "running": False, "n": 0, "total": 0, "label": "", "current": ""}

    def enrich_paint() -> None:
        """진행 상황 dict → 위젯 (메인 스레드에서만 불린다)."""
        if not enrich_state["running"]:
            return
        total = enrich_state["total"] or 1
        enrich_bar.set_value(enrich_state["n"] / total)
        tail = f" · {enrich_state['current'][:34]}" if enrich_state["current"] else ""
        enrich_label.set_text(t("{label} · {n}/{total}편{tail}", label=enrich_state['label'], n=enrich_state['n'], total=enrich_state['total'], tail=tail))

    ui.timer(0.3, enrich_paint)

    async def enrich_now() -> None:
        """서지 정보를 온라인에서 보강 — 빈 연도·학회, 출판 기록(.bib), 참고문헌 DOI를 한 번에.

        범위는 **옆에서 고른 프로젝트**다. 논문마다 API를 여러 번 두드려 편당 몇 초에서 몇십 초가
        걸리므로 진행률을 보여 주고 언제든 멈출 수 있게 한다.
        """
        from narani import enrich

        if enrich_state["running"]:
            return
        pid = enrich_scope["pid"]
        roots = enrich.project_roots(pid, state["upload_dir"])
        label = t("모든 컬렉션") if pid == enrich.ALL else projects.name_of(pid)
        if not roots:
            ui.notify(t("{label}에 속한 논문이 없습니다.", label=label), type="info")
            return
        enrich_state.update(stop=False, running=True, n=0, total=len(roots), label=label, current="")
        enrich_btn.set_visibility(False)
        enrich_progress.set_visibility(True)
        enrich_paint()

        def tick(n: int, root, got: dict) -> None:  # noqa: ANN001
            # 워커 스레드 — dict만 만진다 (위젯은 ui.timer가 메인 스레드에서 갱신)
            enrich_state["n"] = n
            enrich_state["current"] = Path(root).stem

        counts = {}
        try:
            counts = await run.io_bound(
                enrich.enrich_many, roots, mailto=config.resolve_enrich_mailto() or None,
                on_progress=tick, should_stop=lambda: enrich_state["stop"])
            renamed = 0
            if counts.get("papers"):
                renamed = (await run.io_bound(paper_meta.apply_naming, state["upload_dir"]))["renamed"]
            if library.configured(pid if pid != enrich.ALL else None):
                await run.io_bound(library.export_many, roots)
        finally:
            enrich_state.update(running=False, current="")
            enrich_progress.set_visibility(False)
            enrich_btn.set_visibility(True)
        if not counts:
            ui.notify(t("보강을 시작하지 못했습니다 — 로그를 확인해 주세요."), type="negative")
            return
        msg = t("{checked}/{total}편 확인 · {papers}편 보강 (연도 {year} · 학회 {venue} · 출판 기록 {records} · "
                "참고문헌 DOI {refs}건)", checked=counts["checked"], total=counts["total"], papers=counts["papers"],
                year=counts.get("year", 0), venue=counts.get("venue", 0), records=counts["records"],
                refs=counts["refs_filled"])
        if renamed:
            msg += t(" · 이름 {n}편 정리", n=renamed)
        if counts["stopped"]:
            msg = t("중지했습니다 — {msg} (여기까지는 저장됐습니다)", msg=msg)
        ui.notify(msg, type="warning" if counts["stopped"] else
                  ("positive" if counts["papers"] else "info"), timeout=8000)
        if counts["retracted"]:  # 철회된 논문을 인용한 채 제출하는 일은 없어야 한다
            names = ", ".join(Path(r).stem for r in counts["retracted"][:5])
            ui.notify(t("⚠ 출판사가 철회(retracted)한 논문 {n}편: {names}", n=len(counts['retracted']), names=names),
                      type="negative", timeout=0, close_button=t("닫기"))
        on_change()

    with ui.row().classes("items-center gap-2"):
        enrich_btn = ui.button(t("서지 정보 보강"), icon="travel_explore",
                               on_click=lambda: enrich_now()).props("dense outline no-caps") \
            .tooltip(t("고른 컬렉션의 논문에 대해: 빈 연도·학회를 채우고, 출판 기록을 받아 그 "
                     "컬렉션 폴더의 references.bib를 정확하게 만들고, 참고문헌의 빈 DOI를 "
                     "채워 인용을 링크로 만듭니다. 제목만 전송합니다."))
        scope_sel = ui.select({_enrich_mod.ALL: t("모든 컬렉션"), **projects.options(with_none=True)},
                              value=enrich_scope["pid"],
                              on_change=lambda e: enrich_scope.update(pid=e.value)) \
            .props("dense outlined").classes("w-44") \
            .tooltip(t("보강할 범위 — 이 컬렉션의 논문만 확인하고 그 폴더의 references.bib를 갱신합니다"))
    # 진행률 + 중지 — 편당 몇 초에서 몇십 초라 스피너만으로는 얼마나 남았는지 알 수 없다
    with ui.column().classes("w-full gap-1") as enrich_progress:
        with ui.row().classes("items-center gap-2 w-full"):
            enrich_label = ui.label("").classes("text-xs text-gray-500 grow")
            ui.button(t("중지"), icon="stop_circle",
                      on_click=lambda: (enrich_state.update(stop=True),
                                        enrich_label.set_text(t("이 논문까지만 마치고 멈춥니다…")))) \
                .props("dense flat no-caps color=negative") \
                .tooltip(t("논문 하나를 끝낸 자리에서 멈춥니다 — 여기까지 보강한 내용은 저장됩니다"))
        enrich_bar = ui.linear_progress(value=0, show_value=False).props("rounded")
    enrich_progress.set_visibility(False)

    # --- 파일 이름 규칙 (논문 폴더·PDF·저장 위치 사본이 모두 이 규칙의 기준명을 공유) ---
    ui.label(t("파일 이름 규칙")).classes("md4-setsub")

    def save_template(value: str) -> None:
        tpl = (value or "").strip()
        if tpl == config.resolve_naming_template():
            return
        err = config.naming_template_error(tpl)
        if err:
            ui.notify(t("이름 규칙 오류: {err}", err=err), type="negative")
            naming_rows.refresh()  # 입력을 저장된 값으로 되돌림
            return
        config.set_section_value("output", "naming", tpl)
        naming_rows.refresh()
        ui.notify(t("이름 규칙 저장됨 · 예: {v}", v=paper_meta.naming_preview()), type="positive")

    async def apply_naming_now() -> None:
        naming_btn.props("loading")
        try:
            counts = await run.io_bound(paper_meta.apply_naming, state["upload_dir"])
        finally:
            naming_btn.props(remove="loading")
        msg = t("{n}편 이름 변경", n=counts["renamed"])
        if counts["no_meta"]:
            msg += t(" · {n}편은 서지 정보가 없어 건너뜀", n=counts["no_meta"])
        ui.notify(msg, type="positive" if counts["renamed"] else "info")
        on_change()  # 목록·경로 표시 새로고침

    @ui.refreshable
    def naming_rows() -> None:
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label(t("이름 규칙")).classes("text-sm shrink-0").style("width:72px")
            inp = ui.input(value=config.resolve_naming_template()) \
                .props("dense outlined").classes("flex-grow min-w-0")
            inp.on("blur", lambda _, i=inp: save_template(i.value))
            inp.on("keydown.enter", lambda _, i=inp: save_template(i.value))
        ui.label(t("예: {v} — ", v=paper_meta.naming_preview()) +
                 t("조각: {year} 연도 · {title} 제목 약칭 · {author} 1저자 성 · {venue} 학회")) \
            .classes("text-xs text-gray-400")

    naming_rows()
    with ui.row().classes("items-center gap-2"):
        naming_btn = ui.button(t("기존 논문·PDF 이름 정리"), icon="drive_file_rename_outline",
                               on_click=apply_naming_now).props("dense outline no-caps") \
            .tooltip(t("이미 변환한 논문의 폴더·PDF·저장 위치 사본 이름을 지금 규칙으로 맞춥니다"))
    ui.label(t("논문 폴더·원본 PDF·저장 위치의 md/PDF가 모두 이 규칙의 이름을 씁니다 — "
             "이름이 같아야 md에서 PDF를 바로 찾을 수 있어요.")).classes("text-xs text-gray-400")

    def refresh_scope() -> None:
        opts = {_enrich_mod.ALL: t("모든 컬렉션"), **projects.options(with_none=True)}
        if enrich_scope["pid"] not in opts:  # 보던 프로젝트가 지워졌으면 전체로
            enrich_scope["pid"] = _enrich_mod.ALL
        scope_sel.set_options(opts, value=enrich_scope["pid"])

    return refresh_scope


# ====================================================================================
# 프로젝트 하나 — 새로 만들기와 편집이 같은 대화상자
# ====================================================================================
def open_project_dialog(state: dict, pid: str | None, on_saved: Callable[[dict, bool], object],
                        on_delete: Callable[[str, Callable[[], None]], None] | None = None,
                        parent: str | None = None) -> None:
    """프로젝트 대화상자 — pid가 None이면 새로 만들기, 있으면 그 프로젝트 편집.

    만드는 길과 고치는 길이 한 화면이다: 이름·폴더·'이 프로젝트만 다르게'를 여기서 다 정하고,
    **저장/만들기를 눌러야 적용**된다(취소하면 아무것도 바뀌지 않는다). 예전처럼 칸마다 바로 쓰면
    새로 만드는 중에는 쓸 프로젝트가 아직 없어 두 대화상자가 따로 놀았다.

    on_saved(proj, created)는 적용 뒤에 불린다(코루틴이어도 된다). on_delete(pid, close)는 편집 모드
    바닥의 '프로젝트 삭제'가 부른다 — 확인은 호출측이 받고(편수를 세는 것은 목록 쪽이라), 지우기로 하면
    close()로 이 대화상자를 닫는다. `parent`는 새로 만들 때 미리 고를 상위 컬렉션(하위 컬렉션 만들기).

    상위 컬렉션을 고르면 폴더·설정을 비워 둔 칸은 그 상위 것을 물려받는다(§projects).
    """
    import inspect

    from nicegui import ui

    from narani.ui.app import _EXPORT_TARGET

    can_pick = _can_pick()
    editing = pid is not None and projects.exists(pid)
    projs = projects.all_projects()
    cur_parent = projects.parent_of(pid, projs) if editing else (parent if projects.exists(parent or "") else "")
    # 고를 수 있는 상위 — 자기 자신·자기 하위는 뺀다(순환). 트리 순서, 경로 이름으로.
    banned = {pid, *projects.descendants(pid, projs)} if editing else set()
    parent_opts = {"": t("없음 (맨 위)")}
    parent_opts.update({p["id"]: projects.path_name(p["id"], projs)
                        for p, _d in projects.tree(projs) if p["id"] not in banned})
    own = dict(projects.settings_of(pid)) if editing else {}
    pending = dict(own)  # 대화상자 안에서 고른 값 — 저장할 때 한꺼번에 쓴다

    async def pick() -> None:
        cur = (root_in.value or "").strip() or (projects.root_of(pid) if editing else None) or state["upload_dir"]
        picked = await desktop.choose_folder(
            t("'{v}' 컬렉션 폴더 선택", v=name_in.value or t("새")), str(cur))
        if picked:
            root_in.value = str(picked)

    async def save() -> None:
        name = projects.clean_name(name_in.value)
        if not name:
            ui.notify(t("이름을 입력하세요."), type="warning")
            return
        raw = (root_in.value or "").strip()
        if editing:
            proj_id = pid
            projects.rename(proj_id, name)
            new_root = Path(raw).expanduser() if raw else None
            if projects.root_of(proj_id) != new_root:
                projects.set_root(proj_id, raw or None)  # ko/·pdf/ 자리까지 만들어 준다
            projects.set_parent(proj_id, parent_sel.value or "")
        else:
            proj_id = projects.create(name, raw or None, parent=parent_sel.value or "")["id"]
        for key in projects.SETTINGS:
            if pending.get(key) != own.get(key):
                projects.set_setting(proj_id, key, pending.get(key))
        dlg.close()
        proj = projects.get(proj_id) or {"id": proj_id, "name": name}
        if editing:
            ui.notify(t("'{name}' 설정을 저장했습니다", name=proj['name']), type="positive", timeout=1500)
        else:
            ui.notify(t("'{name}' 컬렉션을 만들었습니다", name=proj['name'])
                      + ("" if raw else t(" — 폴더는 나중에 정해도 됩니다.")), type="positive")
        res = on_saved(proj, not editing)
        if inspect.isawaitable(res):
            await res

    def set_pending(key: str, value) -> None:  # noqa: ANN001
        if value is None:
            pending.pop(key, None)
        else:
            pending[key] = value

    with ui.dialog() as dlg, ui.card().classes("gap-2").style("width:480px; max-width:94vw"):
        ui.label(t("'{v}' 컬렉션", v=projects.name_of(pid)) if editing else t("새 컬렉션")).classes("font-bold")
        name_in = ui.input(t("이름"), value=projects.name_of(pid) if editing else "") \
            .props('dense outlined autofocus').props(f'placeholder="{t("예: 튜터 챗봇 서베이")}"').classes("w-full")
        name_in.on("keydown.enter", save)
        parent_sel = ui.select(parent_opts, value=cur_parent if cur_parent in parent_opts else "",
                               label=t("상위 컬렉션")) \
            .props("dense outlined").classes("w-full")
        ui.label(t("폴더 (선택) — 폴더 하나만 정하면 영어 md는 루트, {v}/·pdf/·references.bib가 자동으로 자리를 잡습니다. 비워 두면 상위 컬렉션의 폴더(없으면 공통 저장 위치)에 쌓입니다.", v=projects.translation_subdir())) \
            .classes("text-xs text-gray-500 q-mt-xs")
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            root_in = ui.input(value=str(projects.root_of(pid) or "") if editing else "",
                               placeholder=t("폴더를 고르거나 경로를 붙여넣으세요")) \
                .props("dense outlined clearable").classes("flex-grow min-w-0")
            root_in.on("keydown.enter", save)
            btn = ui.button(icon="folder_open", on_click=pick).props("dense outline") \
                .tooltip(t("폴더 선택 대화상자 열기"))
            if not can_pick:
                _no_dialog(btn)

        # 이 프로젝트만 다르게 쓸 설정 — 저장·출력에 관한 것만. 기본은 **전역 따름**이고, 바꾼 항목만
        # 프로젝트에 저장된다. 그래서 전역 설정을 고치면 따로 정하지 않은 프로젝트들은 저절로 따라온다.
        follow = t("상위 따름") if cur_parent else t("전역 따름")
        title = t("고급 — 이 컬렉션만 다르게 ({n}개 지정됨)", n=len(own)) if own else t("고급 — 이 컬렉션만 다르게")
        with ui.expansion(title, icon="tune", value=False).classes("w-full").props("dense"):
            ui.label(t("정하지 않은 항목은 상위 컬렉션(없으면 전역 설정)을 그대로 따릅니다.")) \
                .classes("text-xs text-gray-400")
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(t("이름 규칙")).classes("text-xs w-20 shrink-0")
                nm = ui.input(value=own.get("naming", ""),
                              placeholder=f"{follow} ({config.resolve_naming_template(cur_parent or None)})") \
                    .props("dense outlined").classes("flex-grow min-w-0")
                nm.on_value_change(lambda e: set_pending("naming", (e.value or "").strip() or None))
            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                ui.label(t("내보내기")).classes("text-xs w-20 shrink-0")
                ui.select({"": f"{follow} ({t(_EXPORT_TARGET[config.resolve_export_target(cur_parent or None)])})",
                           **{k: t(v) for k, v in _EXPORT_TARGET.items()}},
                          value=own.get("export_target", ""),
                          on_change=lambda e: set_pending("export_target", e.value or None)) \
                    .props("dense outlined").classes("flex-grow")
            # (설정 키, 라벨, 전역값을 읽는 함수) — 켜짐/꺼짐 세 갈래 선택으로 같은 모양이다
            toggles = (("bibtex", t("references.bib도 함께 쌓기"), config.resolve_library_bibtex),
                       ("auto", t("변환·번역이 끝나면 자동 저장"), config.resolve_library_auto),
                       ("bib_lookup", t("변환 직후 서지를 온라인 보강"), config.resolve_bib_lookup))
            for key, text, global_value in toggles:
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    glob = global_value(cur_parent or None)  # 상위 컬렉션이 있으면 물려받는 값
                    ui.select({"": f"{follow} ({t('켜짐') if glob else t('꺼짐')})", "on": t("켜짐"), "off": t("꺼짐")},
                              value="" if key not in own else ("on" if own[key] else "off"),
                              on_change=lambda e, k=key: set_pending(k, None if not e.value else e.value == "on")) \
                        .props("dense outlined").classes("w-44 shrink-0")
                    ui.label(text).classes("text-xs text-gray-500")

        with ui.row().classes("items-center w-full no-wrap gap-2 q-mt-sm"):
            if editing and on_delete is not None:
                # 확인 대화상자를 이 위에 띄우고, 지우기로 하면 이 대화상자도 함께 닫는다. 먼저 닫고 바로 새 것을
                # 열면 닫히는 애니메이션과 겹쳐 확인 창이 보이지 않았다.
                ui.button(t("컬렉션 삭제"), icon="delete", on_click=lambda: on_delete(pid, dlg.close)) \
                    .props("flat dense no-caps color=grey").tooltip(t("컬렉션만 지우기 (논문·폴더는 그대로)"))
            ui.space()
            ui.button(t("취소"), on_click=dlg.close).props("flat")
            ui.button(t("저장") if editing else t("만들기"), icon="check" if editing else "add", on_click=save) \
                .props("unelevated no-caps color=primary")
    dlg.open()
