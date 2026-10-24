"""컬렉션 용어집 대화상자 — 홈 사이드바 ⋯ '용어집…'과 뷰어 용어집 시트의 '컬렉션 용어집 열기…'.

컬렉션 용어집(`CONFIG_DIR/glossary/<id>.yaml`)은 주 컬렉션이 이 컬렉션인 논문 모두의 번역에 논문
용어집보다 우선한다(`glossary.effective`; 여러 컬렉션에 속한 논문은 첫 소속의 것만). 논문마다 따로 만든 용어집이 같은 컬렉션 안에서도 어긋나므로(80편 컬렉션에서
2편 이상에 나온 용어의 절반 이상), 여기서 그 어긋남을 보고 표기를 하나로 고른다.

- **용어집** 탭: 뷰어 용어집 표와 같은 행(원어·번역어·처리 방식·삭제). 고치면 바로 저장된다.
- **충돌** 탭: 주 컬렉션이 이 컬렉션인 논문들의 용어집에서 번역어·처리 방식이 어긋난 용어와 그 변이(편수). '이 표기로 통일'을
  누르면 그 항목이 컬렉션 용어집에 들어간다. 어긋나지 않고 여러 편이 같이 쓰는 용어는 접힌 구역에 —
  올려 두면 새로 들어오는 논문도 그 번역어를 따른다.
- **바닥**: 다음 번역 때 다시 번역될 곳(편수·섹션·대략 비용, `glossary.impact`)과 '낡은 섹션 재번역'
  (일괄 번역 확인 대화상자). 고칠 때마다 1초쯤 뒤 다시 센다.

충돌·영향은 그 논문들의 파일을 다 읽어야 해서 `run.io_bound`로 읽는다(80편에 1초 안팎).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Mapping
from pathlib import Path

from narani.i18n import t
from narani import config, projects
from narani.ir import GlossaryEntry, GlossaryList
from narani.translate import glossary


def merge_dupes(rows: list[dict]) -> tuple[list[dict], int]:
    """대소문자만 다른 용어 행을 합친다(먼저 나온 행 유지) — (남은 행, 합쳐 없앤 수). 빈 행은 그대로.

    뷰어 용어집 표(app.py merge_dupe_rows)와 같은 규칙 — 앞 행의 번역어가 비면 뒤 행 것으로 채운다.
    """
    at: dict[str, int] = {}
    kept: list[dict] = []
    for d in rows:
        key = glossary.term_key(d["term"])
        if not key:
            kept.append(d)
            continue
        if key not in at:
            at[key] = len(kept)
            kept.append(d)
        elif not kept[at[key]]["korean"].strip() and d["korean"].strip():
            kept[at[key]]["korean"], kept[at[key]]["policy"] = d["korean"], d["policy"]
    return kept, len(rows) - len(kept)


def merge_entry(rows: list[dict], term: str, korean: str, policy: str) -> list[dict]:
    """한 항목을 넣는다(merge) — 같은 term_key 행이 있으면 그 자리에서 번역어·처리 방식만 바꾸고, 없으면 뒤에."""
    key = glossary.term_key(term)
    for d in rows:
        if glossary.term_key(d["term"]) == key:
            d["korean"], d["policy"] = korean, policy
            return rows
    rows.append({"term": term, "korean": korean, "policy": policy})
    return rows


def conflict_rows(conflicts: list[glossary.Conflict], current: list[dict],
                  titles: Mapping[Hashable, str] | None = None) -> list[dict]:
    """충돌 표에 그릴 데이터 — 용어마다 {key, term, current, papers, variants}.

    current: 컬렉션 용어집에 이미 있으면 그 행의 {korean, policy}(없으면 None). variants: 편수 많은 순
    {korean, policy, n, titles(논문 제목들), roots(논문 키들), chosen(지금 컬렉션 값과 같은지)}. titles는 {논문 키: 제목}.
    """
    titles = titles or {}
    have = {glossary.term_key(d["term"]): d for d in current if d["term"].strip()}
    out: list[dict] = []
    for c in conflicts:
        cur = have.get(c.key)
        cur_v = {"korean": cur["korean"], "policy": cur["policy"]} if cur else None
        out.append({
            "key": c.key, "term": c.term, "current": cur_v, "papers": c.paper_count,
            "variants": [{
                "korean": v.korean, "policy": v.policy, "n": len(v.papers),
                "titles": [titles.get(p, str(p)) for p in v.papers],
                "roots": list(v.papers),   # 예문 찾기 — 이 표기를 쓴 논문들
                "chosen": cur_v is not None and cur_v["korean"].strip() == v.korean and cur_v["policy"] == v.policy,
            } for v in c.variants],
        })
    return out


def load_overview(pid: str, workspace: Path) -> dict:
    """충돌 탭 데이터를 한 번에 — {conflicts, shared, titles, papers(용어집이 있는 주 소속 논문 수),
    secondary(둘째 이후 자리로만 속해 빠진 논문 수)}. 파일 읽기만."""
    from narani.workdir import paper_title

    by_paper = glossary.collection_entries_by_paper(pid, workspace)
    return {"conflicts": glossary.conflicts(by_paper), "shared": glossary.shared(by_paper),
            "titles": {root: paper_title(root) for root in by_paper}, "papers": len(by_paper),
            "secondary": glossary.secondary_count(pid, workspace)}


def impact_summary(rows: list[dict]) -> str:
    """영향 표(`glossary.impact`) → 바닥 한 줄."""
    if not rows:
        return t("낡은 번역 없음 — 지금 용어집으로 다시 번역될 곳이 없습니다")
    secs = sum(len(r["stale"]) for r in rows)
    cost = sum(r["est_cost_usd"] for r in rows)
    money = "< $0.01" if 0 < cost < 0.01 else f"≈ ${cost:.2f}"
    out = t("다음 번역 때 다시 번역될 곳: 논문 {n}편 · 섹션 {secs}개 · {money}", n=len(rows), secs=secs, money=money)
    full = sum(1 for r in rows if r.get("full"))
    if full:  # 보관된 번역을 통째로 못 써 처음부터 다시 도는 논문 — 비용의 대부분이 여기서 나온다
        out += t(" · 그중 {full}편은 전체 재번역(캐시 못 씀)", full=full)
    return out


def open_collection_glossary(state: dict, pid: str, *, on_change: Callable[[], None] | None = None) -> None:
    """컬렉션 용어집 대화상자를 연다 — 지금 `with` 문맥(홈의 host, 뷰어의 숨은 자리) 안에 짓는다.

    on_change는 닫힐 때 불린다(편집이 있었든 없든) — 호출측이 목록·용어집 표·'원문 바뀜'을 다시 맞춘다.
    """
    from nicegui import run, ui

    from narani.ui import batch_translate_dialog
    from narani.ui.app import _POLICY

    host = ui.context.slot.parent  # 일괄 번역 확인 대화상자도 같은 자리에 (이 대화상자는 닫히면 치운다)
    workspace = Path(state.get("upload_dir") or config.resolve_workspace())
    name = projects.name_of(pid)
    rows: list[dict] = [{"term": e.term, "korean": e.korean, "policy": e.policy}
                        for e in glossary.load_collection(pid).entries]
    st: dict = {"overview": None, "impact": None, "dirty_at": 0.0, "counting": False, "unified": set(),
                "error": "", "ex_open": set(), "ex": {}}
    from narani.ui import spa, term_examples

    spa.add_css(term_examples.CSS)  # 홈에서 열면 뷰어 CSS가 없다 — 같은 창이면 한 번만 들어간다

    async def toggle_examples(key: str, term: str, papers: list[tuple], refresh) -> None:  # noqa: ANN001
        """예문 열기/닫기 — 처음 열 때 논문들의 본문을 io_bound로 읽는다(논문마다 정렬 행을 만든다)."""
        if key in st["ex_open"]:
            st["ex_open"].discard(key)
            refresh.refresh()
            return
        st["ex_open"].add(key)
        if key not in st["ex"]:
            st["ex"][key] = None   # 읽는 중
            refresh.refresh()
            try:
                groups = await run.io_bound(term_examples.across, papers, term, per_paper=2, max_papers=4)
            except Exception as e:  # noqa: BLE001 — 예문 하나 못 읽어도 대화상자는 산다
                groups = [{"title": t("읽지 못했습니다: {e}", e=e), "examples": [], "rows": 0}]
            if dlg.is_deleted:
                return
            st["ex"][key] = groups
        refresh.refresh()

    def examples_view(key: str) -> None:
        if key not in st["ex_open"]:
            return
        with ui.column().classes("w-full gap-0").style("padding:0 0 6px 14px"):
            groups = st["ex"].get(key)
            if groups is None:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="xs")
                    ui.label(t("본문에서 찾는 중…")).classes("md4-tex-meta")
                return
            term_examples.render_across(groups)

    def member_papers(korean: str, policy: str) -> list[tuple]:
        """용어집 탭의 예문 — 주 컬렉션이 이 컬렉션인 논문들, 표시는 이 행의 번역어로."""
        return [(Path(r["root"]), korean, policy) for r in glossary.members(pid, workspace)]

    def n_terms() -> int:
        return sum(1 for d in rows if d["term"].strip())

    def save() -> None:
        kept, removed = merge_dupes(rows)
        if removed:
            rows[:] = kept
            gloss_rows.refresh()
            ui.notify(t("대소문자만 다른 중복 용어를 합쳤습니다."), type="info")
        entries = [GlossaryEntry(term=d["term"].strip(), korean=d["korean"], policy=d["policy"])
                   for d in rows if d["term"].strip()]
        glossary.save_collection(pid, GlossaryList(entries=glossary.dedupe(entries)))
        head.refresh()
        st["dirty_at"] = time.monotonic()  # 영향 요약은 잠깐 뒤 다시 센다 (타자마다 80편을 훑지 않게)

    def add_row() -> None:
        rows.append({"term": "", "korean": "", "policy": "translate"})
        gloss_rows.refresh()

    def remove_row(idx: int) -> None:
        rows.pop(idx)
        gloss_rows.refresh()
        save()

    def adopt(term: str, korean: str, policy: str, key: str | None = None) -> None:
        """충돌·공통 용어 한 줄을 컬렉션 용어집에 넣는다 (같은 용어가 있으면 그 자리에서 바꾼다)."""
        merge_entry(rows, term, korean, policy)
        if key:
            st["unified"].add(key)
        gloss_rows.refresh()
        save()
        conflict_list.refresh()

    @ui.refreshable
    def head() -> None:
        ui.label(t("컬렉션 '{name}' 용어집 ({v})", name=name, v=n_terms())).classes("text-lg font-bold")

    @ui.refreshable
    def gloss_rows() -> None:
        if not rows:
            ui.label(t("아직 비어 있습니다 — '행 추가'로 넣거나, '충돌' 탭에서 논문들이 어긋나게 쓴 용어의 표기를 "
                     "골라 통일하세요.")).classes("text-sm text-gray-400")
        for i, d in enumerate(rows):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.input(placeholder=t("원어"), value=d["term"]).bind_value(d, "term") \
                    .on("blur", lambda: save()).props("dense outlined").classes("w-48")
                ui.icon("arrow_forward").classes("text-gray-400")
                ui.input(placeholder=t("번역어"), value=d["korean"]).bind_value(d, "korean") \
                    .on("blur", lambda: save()).props("dense outlined").classes("w-48")
                ui.select({k: t(v) for k, v in _POLICY.items()}, value=d["policy"]).bind_value(d, "policy") \
                    .on("update:model-value", lambda: save()).props("dense outlined").classes("w-44")
                gkey = "g:" + glossary.term_key(d["term"]) + "|" + d["korean"]
                ui.button(icon="format_quote", on_click=lambda _, d=d, k=gkey: (
                    toggle_examples(k, d["term"], member_papers(d["korean"], d["policy"]), gloss_rows)
                    if d["term"].strip() else ui.notify(t("원어를 먼저 입력하세요."), type="warning"))) \
                    .props("flat dense round size=sm color=" + ("primary" if gkey in st["ex_open"] else "grey-6")) \
                    .tooltip(t("이 용어가 쓰인 문장 — 이 컬렉션 논문들의 원문·번역"))
                ui.button(icon="delete", on_click=lambda _, idx=i: remove_row(idx)) \
                    .props("flat dense round size=sm")
            examples_view("g:" + glossary.term_key(d["term"]) + "|" + d["korean"])

    def policy_label(p: str) -> str:
        return t(_POLICY.get(p, p))

    @ui.refreshable
    def conflict_list() -> None:
        ov = st["overview"]
        if st["error"]:
            ui.label(t("논문 용어집을 읽지 못했습니다: {error}", error=st['error'])).classes("text-sm text-negative")
            return
        if ov is None:
            with ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm")
                ui.label(t("논문 용어집을 읽는 중…")).classes("text-sm text-gray-500")
            return
        data = conflict_rows(ov["conflicts"], rows, ov["titles"])
        ui.label(t("용어집이 있는 논문 {papers}편{v} · 번역어나 처리 방식이 어긋난 용어 {n}개 (편수 많은 순)", papers=ov['papers'], v=glossary.secondary_note(ov.get('secondary', 0)), n=len(data))).classes("text-xs text-gray-500")
        if not data:
            ui.label(t("어긋난 용어가 없습니다.")).classes("text-sm text-gray-400")
        for c in data:
            with ui.row().classes("items-start gap-3 w-full no-wrap q-py-xs") \
                    .style("border-bottom:1px solid #f0eeeb"):
                with ui.column().classes("gap-0 shrink-0").style("width:190px"):
                    ui.label(c["term"]).classes("text-sm font-medium break-words")
                    if c["current"]:
                        cur = c["current"]
                        ui.label(f"✓ {cur['korean'] or t('(빈 칸)')} · {policy_label(cur['policy'])}") \
                            .classes("text-xs text-green-8").tooltip(t("지금 컬렉션 용어집의 값"))
                    else:
                        ui.label(t("{papers}편에 등장", papers=c['papers'])).classes("text-xs text-gray-400")
                if c["key"] in st["unified"] and c["current"]:  # 방금 통일 — 한 줄로 접는다
                    with ui.row().classes("items-center gap-2 flex-grow min-w-0"):
                        ui.label(t("✓ 통일됨: {v} · {v2}", v=c['current']['korean'] or '(빈 칸)', v2=policy_label(c['current']['policy']))).classes("text-sm text-green-8")
                        ui.button(t("다시 고르기"), on_click=lambda _, k=c["key"]: (
                            st["unified"].discard(k), conflict_list.refresh())) \
                            .props("flat dense no-caps size=sm color=grey-7")
                    continue
                with ui.column().classes("gap-1 flex-grow min-w-0"):
                    for v in c["variants"]:
                        vkey = f"v:{c['key']}|{v['korean']}|{v['policy']}"
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.button(icon="format_quote", on_click=lambda _, c=c, v=v, k=vkey: toggle_examples(
                                k, c["term"], [(Path(r), v["korean"], v["policy"]) for r in v["roots"]],
                                conflict_list)) \
                                .props("flat dense round size=xs color="
                                       + ("primary" if vkey in st["ex_open"] else "grey-6")).classes("shrink-0") \
                                .tooltip(t("이 표기를 쓴 논문들이 실제로 옮긴 문장"))
                            ui.label(t("{v} · {v2} · {n}편", v=v['korean'] or '(빈 칸)', v2=policy_label(v['policy']), n=v['n'])) \
                                .classes("text-sm truncate min-w-0" + (" text-green-8" if v["chosen"] else "")) \
                                .tooltip(" · ".join(t[:40] for t in v["titles"][:10])
                                         + (" …" if len(v["titles"]) > 10 else ""))
                            ui.space()
                            if v["chosen"]:
                                ui.label(t("지금 값")).classes("text-xs text-green-8 shrink-0")
                            else:
                                ui.button(t("이 표기로 통일"), on_click=lambda _, c=c, v=v: adopt(
                                    c["term"], v["korean"], v["policy"], c["key"])) \
                                    .props("outline dense no-caps size=sm color=primary").classes("shrink-0")
                        examples_view(vkey)
        shared = ov["shared"]
        if shared:
            have = {glossary.term_key(d["term"]) for d in rows if d["term"].strip()}
            with ui.expansion(t("일관되게 쓰는 용어 {n}개 — 컬렉션에 올리면 새 논문도 따릅니다", n=len(shared)),
                              icon="done_all", value=False).classes("w-full q-mt-sm").props("dense"):
                for term, korean, policy, papers in shared:
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        ui.label(t("{term} → {v} · {v2} · {n}편", term=term, v=korean or '(빈 칸)', v2=policy_label(policy), n=len(papers))) \
                            .classes("text-sm truncate min-w-0")
                        ui.space()
                        if glossary.term_key(term) in have:
                            ui.label(t("✓ 컬렉션에 있음")).classes("text-xs text-green-8 shrink-0")
                        else:
                            ui.button(t("컬렉션에 올리기"), on_click=lambda _, tv=term, k=korean, p=policy: adopt(tv, k, p)) \
                                .props("outline dense no-caps size=sm").classes("shrink-0")

    @ui.refreshable
    def impact_box() -> None:
        rows_ = st["impact"]
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            if rows_ is None or st["counting"]:
                ui.spinner(size="xs")
                ui.label(t("다시 번역될 곳을 세는 중…")).classes("text-xs text-gray-500")
            else:
                ui.icon("update" if rows_ else "check_circle", size="18px") \
                    .classes("text-orange-7" if rows_ else "text-green-7")
                ui.label(impact_summary(rows_)).classes("text-sm") \
                    .tooltip(t("원문·용어집이 바뀌어 번역이 낡은 섹션 — 원문 글자 수로 어림한 대략 비용"))
            ui.space()
            if rows_:
                ui.button(t("낡은 섹션 재번역 ({n}편)", n=len(rows_)), icon="refresh",
                          on_click=lambda: retranslate([r["root"] for r in rows_])) \
                    .props("unelevated dense no-caps color=orange")

    def retranslate(roots: list[Path]) -> None:
        dlg.close()
        with host:
            batch_translate_dialog.open_batch_translate(roots)

    async def count_impact() -> None:
        st["counting"] = True
        impact_box.refresh()
        try:
            res = await run.io_bound(glossary.impact, pid, workspace)
        except Exception:  # noqa: BLE001 — 세지 못해도 대화상자는 쓸 수 있어야 한다
            res = []
        st["counting"] = False
        if dlg.is_deleted:
            return
        st["impact"] = res
        impact_box.refresh()

    async def load_overview_async() -> None:
        try:
            ov = await run.io_bound(load_overview, pid, workspace)
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e) or type(e).__name__
            ov = None
        if dlg.is_deleted:
            return
        st["overview"] = ov
        conflict_list.refresh()
        tab = tabs_ref["conflicts"]
        tab._props["label"] = t("충돌 {n}", n=len(ov["conflicts"])) if ov else t("충돌")  # noqa: SLF001 — 탭 글자만 바꾼다
        tab.update()

    async def tick() -> None:
        at = st["dirty_at"]
        if at and not st["counting"] and time.monotonic() - at >= 1.0:
            st["dirty_at"] = 0.0
            await count_impact()

    tabs_ref: dict = {}
    with ui.dialog() as dlg, ui.card().classes("gap-2 no-wrap").style(
            "width:800px; max-width:94vw; max-height:84vh; display:flex; flex-direction:column"):
        with ui.row().classes("items-center w-full no-wrap gap-2 shrink-0"):
            head()
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat dense round")
        ui.label(t("이 컬렉션이 주 컬렉션인 논문 모두에 논문 용어집보다 우선 적용됩니다(여러 컬렉션에 속한 논문은 첫 소속만). 고치면 그 용어가 나오는 섹션이 '원문 바뀜'이 "
                 "되어 다음 번역 때 다시 번역됩니다.")).classes("text-xs text-gray-600 shrink-0")
        with ui.tabs().props("dense no-caps align=left").classes("w-full shrink-0") as tabs:
            ui.tab("gloss", t("용어집"), icon="menu_book")
            tabs_ref["conflicts"] = ui.tab("conflicts", t("충돌"), icon="call_split")
        with ui.tab_panels(tabs, value="gloss").props("animated=false").classes("w-full md4-scroll") \
                .style("flex:1 1 auto; min-height:0; overflow-y:auto"):
            with ui.tab_panel("gloss").classes("q-pa-sm"):
                with ui.row().classes("items-center w-full gap-2"):
                    ui.button(t("행 추가"), icon="add", on_click=add_row).props("outline dense no-caps")
                    ui.label(t("고친 내용은 바로 저장됩니다.")).classes("text-xs text-gray-500")
                with ui.column().classes("w-full gap-1"):
                    gloss_rows()
            with ui.tab_panel("conflicts").classes("q-pa-sm"):
                with ui.column().classes("w-full gap-1"):
                    conflict_list()
        tabs.on_value_change(lambda e: conflict_list.refresh() if e.value == "conflicts" else None)
        ui.separator().classes("shrink-0")
        with ui.column().classes("w-full gap-1 shrink-0"):
            impact_box()
        ui.timer(0.05, load_overview_async, once=True)
        ui.timer(0.05, count_impact, once=True)
        ui.timer(0.5, tick)

    def closed() -> None:
        if on_change is not None:
            on_change()
        dlg.delete()  # 열 때마다 새로 짓는다 — 타이머가 남지 않게

    dlg.on("hide", closed)
    dlg.open()
