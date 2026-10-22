"""비용이 드는 tool — 섹션 재번역·용어집 다시 만들기. 서버 수명 작업(`ui/jobs.py`)으로 돈다.

에이전트 턴 안에서 번역을 끝까지 기다리면 SSE가 몇 분씩 붙잡히고 창을 닫으면 끊긴다. 그래서
tool은 뷰어의 '번역 실행'과 **같은 작업 종류·라벨**(`translate`/"번역")로 `jobs.start`만 하고 바로
돌아온다. 같은 논문에 번역이 이미 돌고 있으면 jobs가 거절하므로 뷰어 단추와 에이전트가 겹치지 않는다.

- 뷰어와 같은 길: 번역은 `Controller.translate`(status.json에 모델·실패 기록, '직접 고친 번역' 표시
  지우기) + `library.auto_export`. 모델은 뷰어 드롭다운의 기본값(`Controller.translate_model()` —
  이 논문을 지난번에 번역한 모델, 없으면 전역 기본)이고 문체는 논문 설정(`manifest.korean_style`).
  전역 기본 모델을 쓰면 지난 번역과 모델이 달라 캐시 키가 바뀌고 **논문 전체**가 다시 번역된다.
- "이 섹션만 다시": 캐시(`translate/cache.json`)에서 그 섹션이 든 번역 유닛의 해시(유닛 원문·섹션
  조각)를 지우고 보통 번역을 돌린다. 나머지는 캐시를 그대로 쓴다. 작은 섹션은 이웃과 한 유닛으로
  합쳐 번역되므로(apply._MERGE_MIN) 이웃도 함께 다시 번역될 수 있다 — 승인 카드에 그 수를 보인다.
- 용어집: 변환 직후 자동 생성과 같은 전역 기본 모델로 `pipeline.run_glossary`. 뷰어의 '용어 추출'과
  같은 작업 종류(`terms`)라 둘이 동시에 glossary.yaml을 쓰지 않는다.

작업을 띄우면 `deps.jobs`에 기록을 남기고(run_turn이 세션 `jobs`에 합친다) 화면 동작
`watch_job`을 낸다 — 뷰어가 그 논문을 보고 있으면 자기가 시작한 작업처럼 진행 바를 붙이고, 끝나면
캔버스를 다시 그리고 완료 알림을 띄운다(app.py `attach_translate`/`attach_terms`). 결과는 세션의
`jobs`가 레지스트리와 대조해 채우고(`sessions.sync_jobs`), 다음 턴 지시문에 한 번 실린다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import RunContext

from md4paper.ui.agent.tools import Deps, _write_target
from md4paper.workdir import WorkDir

TRANSLATE_KIND = "translate"   # jobs 종류 — 뷰어 번역 패널과 같다 (같은 논문에 하나만 돈다)
TRANSLATE_LABEL = "번역"
GLOSSARY_KIND = "terms"        # 뷰어의 '용어 추출'과 같은 종류 — 둘 다 glossary.yaml을 쓴다
GLOSSARY_LABEL = "용어집 생성"
SECTIONS_MAX = 30              # 한 번에 다시 번역할 섹션 수 상한 (비용 사고 방지)


# --- 모델 ---------------------------------------------------------------


def translate_choice(wd: WorkDir) -> tuple[str, str]:
    """번역에 쓸 (제공사, 모델) — 뷰어 번역 패널 드롭다운의 기본값과 같다."""
    from md4paper.ui.controller import UIController as Controller

    prov, _, model = Controller(wd).translate_model().partition("/")
    return prov, model


def glossary_choice() -> tuple[str, str]:
    """용어집에 쓸 (제공사, 모델) — 변환 직후 자동 용어집(app._auto_glossary)과 같은 전역 기본."""
    from md4paper import config

    prov = config.resolve_provider()
    return prov, config.resolve_model(prov)


def default_job_provider(purpose: str, wd: WorkDir):  # noqa: ANN201 — llm.base.Provider
    """작업용 LLM 프로바이더 (purpose: "translate" | "glossary"). 키가 없으면 RuntimeError(안내 문구).

    에이전트 대화 모델([chat])이 아니라 앱이 번역에 쓰는 설정이다. 테스트는 `Deps.build_job_provider`
    로 FakeProvider를 끼운다.
    """
    from md4paper import config

    prov, model = translate_choice(wd) if purpose == "translate" else glossary_choice()
    return config.build_provider(prov, model)


def _provider_for(deps: Deps) -> Callable:
    return deps.build_job_provider or default_job_provider


# --- 섹션 → 번역 유닛 ----------------------------------------------------


def _section_names(wd: WorkDir) -> dict[str, str]:
    """sections.map.json의 섹션 id → 제목 (문서 순서)."""
    try:
        smap = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    except (OSError, ValueError):
        return {}
    return {str(e["id"]): str(e.get("text") or "")
            for e in sorted(smap, key=lambda e: e.get("out_line", 0)) if e.get("id")}


def plan_retranslate(wd: WorkDir, ids: list[str]) -> dict | str:
    """다시 번역할 계획 — `{"ids", "names", "affected", "hashes"}` 또는 안 되는 까닭(문자열).

    affected는 실제로 다시 번역될 섹션 id(같은 유닛에 합쳐진 이웃 포함, 문서 순서), hashes는
    캐시에서 지울 해시들. 파일을 고치지 않는다(승인 카드 설명에도 쓴다).
    """
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    if not wd.en_md.exists():
        return "이 논문은 아직 변환이 끝나지 않았습니다 (paper.en.md 없음)."
    if not wd.ko_md.exists():
        return ("이 논문은 아직 번역이 없습니다. 첫 번역은 논문 전체라 비용이 크니 사용자에게 뷰어의 "
                "번역 패널에서 '번역 실행'을 누르라고 안내하세요.")
    names = _section_names(wd)
    unknown = [i for i in ids if i not in names]
    if unknown:
        sample = ", ".join(f"{k} {v[:30]}" for k, v in list(names.items())[:8])
        return (f"없는 섹션 id입니다: {', '.join(unknown)}. outline 결과의 섹션 id(h_0004 꼴)를 쓰세요. "
                f"(예: {sample} …)")
    manifest = manifest_io.load(wd)
    units = apply.build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
    want = set(ids)
    hit = [u for u in units if u["translatable"] and want & set(u["sids"])]
    covered = {s for u in hit for s in u["sids"]}
    skipped = [i for i in ids if i not in covered]
    if skipped:
        return ("번역 대상에서 빠진 섹션입니다(번역 패널에서 체크를 풀었거나 참고문헌): "
                + ", ".join(f"'{names[i][:40]}'({i})" for i in skipped)
                + ". 다시 번역하려면 사용자가 번역 패널에서 그 섹션을 체크해야 합니다.")
    order = list(names)
    affected = sorted(covered, key=lambda s: order.index(s) if s in order else len(order))
    gloss = glossary.load(wd) if wd.glossary_yaml.exists() else None
    hashes = sorted({h for u in hit for h in apply.unit_hashes(u, gloss)})
    return {"ids": ids, "names": [names[i] for i in ids], "affected": affected, "hashes": hashes}


def invalidate_cache(wd: WorkDir, hashes: list[str]) -> int:
    """번역 캐시에서 그 해시들을 지운다 (키는 그대로) — 다음 번역이 그 유닛만 새로 번역한다. 지운 수."""
    if not wd.cache_json.exists():
        return 0
    try:
        stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    chunks = stored.get("chunks") or {}
    gone = [h for h in hashes if h in chunks]
    if not gone:
        return 0
    for h in gone:
        del chunks[h]
    tmp = wd.cache_json.with_name(wd.cache_json.name + ".tmp")
    tmp.write_text(json.dumps({**stored, "chunks": chunks}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(wd.cache_json)
    return len(gone)


def _names_text(names: list[str], n: int) -> str:
    first = " ".join((names[0] if names else "").split())
    first = first if len(first) <= 40 else first[:40].rstrip() + "…"
    return f"'{first}' 등 {n}개 섹션" if n > 1 else f"'{first}' 섹션"


# --- 기록 ---------------------------------------------------------------


def _record(deps: Deps, job, kind: str, tok: str, wd: WorkDir, detail: str,  # noqa: ANN001 — jobs.Job
            **extra: object) -> dict:
    """세션에 남길 작업 기록 — 레지스트리의 작업은 (논문, 종류, 시작 시각)으로 다시 찾는다."""
    rec = {"id": f"j{int(job.started * 1000):x}", "kind": kind, "job_kind": job.kind,
           "token": tok, "root": str(Path(wd.root).resolve()), "label": job.label,
           "title": job.title, "detail": detail, "started": job.started, "done": False,
           "status": "running", "progress": [0, 0], "result": None, "error": "",
           "message": "", "notified": False, "turn_id": None, **extra}
    deps.jobs.append(rec)
    return rec


def _busy_reason(root: Path, *, glossary: bool = False) -> str | None:
    from md4paper.ui import jobs

    if jobs.is_running(root, "reconvert"):
        return "이 논문은 PDF에서 다시 변환하는 중이라 지금은 할 수 없습니다. 끝난 뒤 다시 요청하세요."
    if not glossary and jobs.is_running(root, TRANSLATE_KIND):
        return "이 논문은 이미 번역 중입니다. 지금 작업이 끝난 뒤 다시 요청하세요."
    if glossary and jobs.is_running(root, GLOSSARY_KIND):
        return "이 논문은 이미 용어집(용어 추출)을 만드는 중입니다. 끝난 뒤 다시 요청하세요."
    return None


async def _title(wd: WorkDir) -> str:
    from md4paper.workdir import paper_title

    return " ".join((await asyncio.to_thread(paper_title, Path(wd.root))).split())


# --- tool ---------------------------------------------------------------


async def retranslate_sections(ctx: RunContext[Deps], section_ids: list[str],
                               token: str | None = None) -> str:
    """논문의 특정 섹션만 다시 번역한다 (LLM 비용 발생). **사용자가 승인해야 실행된다.**

    사용자가 어느 섹션을 다시 번역해 달라고 할 때만 쓴다. 섹션 id는 outline 결과의 `h_0004` 꼴
    (각 헤더 줄의 `[rN]` 뒤)이다 — 먼저 outline으로 확인한다. 번역은 백그라운드 작업으로 돌고 이 tool은
    바로 돌아온다. 끝나면 다음 질문의 지시문 '백그라운드 작업'에 결과가 실린다. 나머지 섹션은 지난
    번역을 그대로 쓴다(짧은 섹션은 이웃과 함께 다시 번역될 수 있다).

    Args:
        section_ids: 다시 번역할 섹션 id 목록 (outline의 `h_0004` 꼴, 최대 30개).
        token: 논문 token. 생략하면 지금 화면의 논문.
    """
    from md4paper import library
    from md4paper.ui import jobs
    from md4paper.ui.controller import UIController as Controller

    deps = ctx.deps
    ids = list(dict.fromkeys(str(s).strip() for s in (section_ids or []) if str(s).strip()))
    if not ids:
        return "section_ids가 비어 있습니다. outline으로 섹션 id(h_0004 꼴)를 확인해 넣으세요."
    if len(ids) > SECTIONS_MAX:
        return f"한 번에 {SECTIONS_MAX}개 섹션까지 다시 번역할 수 있습니다 (받은 수: {len(ids)})."
    got = _write_target(ctx, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    plan = await asyncio.to_thread(plan_retranslate, wd, ids)
    if isinstance(plan, str):
        return plan
    busy = _busy_reason(Path(wd.root))
    if busy:
        return busy
    try:
        prov = await asyncio.to_thread(_provider_for(deps), "translate", wd)
    except RuntimeError as e:
        return f"번역을 시작하지 못했습니다: {e}"
    n = len(plan["affected"])
    detail = _names_text(plan["names"], len(ids))

    # 워커 스레드에서 화면 없이 완결된다 (뷰어 _start_translate의 work와 같은 일)
    def work(job: jobs.Job) -> dict:
        again = plan_retranslate(wd, ids)    # 승인~시작 사이 원문이 바뀌었을 수 있다 — 지금 것으로
        if isinstance(again, str):
            raise RuntimeError(again)
        invalidate_cache(wd, again["hashes"])

        def on_progress(d: int, t: int, status: dict) -> None:
            job.progress(d, t)
            job.detail["status"] = status

        ctrl = Controller(wd)
        summary = ctrl.translate(prov, ctrl.manifest.korean_style, on_progress)
        saved = library.auto_export(wd)
        msg = f"{_names_text(again['names'], len(ids))} 재번역 완료 (≈${summary['cost_usd']:.4f})"
        if saved:
            msg += f" · 저장 위치에 {len(saved)}개 파일 저장"
        if summary.get("passthrough"):
            msg += f" · {summary['passthrough']}개 구간은 구조 검증 실패로 영어 유지"
        job.message = msg
        return {**summary, "sections": len(again["affected"])}

    job = jobs.start(wd.root, TRANSLATE_KIND, TRANSLATE_LABEL, work, title=await _title(wd))
    if job is None:
        return "이 논문은 이미 번역 중입니다. 지금 작업이 끝난 뒤 다시 요청하세요."
    rec = _record(deps, job, "translate", tok, wd, detail, sections=ids,
                  model=f"{getattr(prov, 'name', '')}/{prov.model}")
    deps.ui_actions.put_nowait({"kind": "watch_job", "job": TRANSLATE_KIND, "token": tok})
    extra = f" (같은 번역 단위로 묶인 이웃까지 {n}개 섹션)" if n > len(ids) else ""
    return (f"번역 작업을 시작했습니다(job {rec['id']}): {detail}{extra}, 모델 {prov.model}. "
            "백그라운드로 돌고, 끝나면 알려 드립니다.")


async def run_glossary(ctx: RunContext[Deps], token: str | None = None,
                       regenerate: bool = True) -> str:
    """논문의 번역 용어집을 LLM으로 다시 만든다 (LLM 비용 발생). **사용자가 승인해야 실행된다.**

    사용자가 용어집을 (다시) 만들어 달라고 할 때만 쓴다. 백그라운드 작업으로 돌고 바로 돌아온다.
    용어집이 바뀌면 다음 번역은 지난 번역 캐시를 못 써 논문 전체를 다시 번역하게 된다 — 사용자에게
    알려 준다.

    Args:
        token: 논문 token. 생략하면 지금 화면의 논문.
        regenerate: True(기본)면 지금 용어집을 버리고 새로 만든다. False면 용어집이 없을 때만 만든다.
    """
    from md4paper import pipeline
    from md4paper.ui import jobs

    deps = ctx.deps
    got = _write_target(ctx, token)
    if isinstance(got, str):
        return got
    tok, wd = got
    if not wd.en_md.exists():
        return "이 논문은 아직 변환이 끝나지 않았습니다 (paper.en.md 없음)."
    busy = _busy_reason(Path(wd.root), glossary=True)
    if busy:
        return busy
    try:
        prov = await asyncio.to_thread(_provider_for(deps), "glossary", wd)
    except RuntimeError as e:
        return f"용어집을 만들지 못했습니다: {e}"
    regen = bool(regenerate)

    def work(job: jobs.Job) -> int:
        n = pipeline.run_glossary(wd, prov, regenerate=regen)
        job.message = f"용어집 {n}개 항목을 {'새로 만들었습니다' if regen else '준비했습니다'} (≈${prov.cost():.4f})"
        job.detail["cost_usd"] = prov.cost()
        return n    # 뷰어 attach_terms는 결과가 참이면 표를 파일에서 다시 읽는다

    job = jobs.start(wd.root, GLOSSARY_KIND, GLOSSARY_LABEL, work, title=await _title(wd))
    if job is None:
        return "이 논문은 이미 용어집(용어 추출)을 만드는 중입니다. 끝난 뒤 다시 요청하세요."
    rec = _record(deps, job, "glossary", tok, wd, "다시 생성" if regen else "없으면 생성",
                  model=f"{getattr(prov, 'name', '')}/{prov.model}")
    deps.ui_actions.put_nowait({"kind": "watch_job", "job": GLOSSARY_KIND, "token": tok})
    return (f"용어집 작업을 시작했습니다(job {rec['id']}), 모델 {prov.model}. "
            "백그라운드로 돌고, 끝나면 알려 드립니다.")


# --- 승인 카드 설명 -----------------------------------------------------


def _model_text(choice: tuple[str, str]) -> str:
    return f"모델 {choice[0]}/{choice[1]}" if choice[1] else "모델 미정"


def describe_retranslate(args: dict, root: Path | None) -> str:
    ids = [str(s) for s in (args.get("section_ids") or []) if str(s).strip()]
    if root is None:
        return f"{len(ids)}개 섹션 재번역 (LLM 비용 발생)"
    wd = WorkDir(Path(root))
    plan = plan_retranslate(wd, ids)
    if isinstance(plan, str):     # 실행해도 안내 문구만 돌아온다 — 카드에 그 까닭을 미리 보인다
        return f"{len(ids)}개 섹션 재번역 — 실행되지 않음: {plan[:80]}"
    out = f"{_names_text(plan['names'], len(ids))} 재번역 (LLM 비용 발생, {_model_text(translate_choice(wd))})"
    if len(plan["affected"]) > len(ids):
        out += f" · 묶인 이웃 포함 {len(plan['affected'])}개 섹션"
    if wd.load_status().get("manual_edit_ko"):
        out += " · 직접 고친 번역문은 덮어써짐"
    return out


def describe_glossary(args: dict, root: Path | None) -> str:
    regen = args.get("regenerate", True) is not False
    out = (f"용어집 {'다시 생성' if regen else '생성(없을 때만)'} (LLM 비용 발생, "
           f"{_model_text(glossary_choice())})")
    if regen and root is not None and WorkDir(Path(root)).ko_md.exists():
        out += " · 다음 번역은 전체를 다시 번역함"
    return out


# tool 이름 → (args, 논문 경로|None) → 승인 카드 한 줄
DESCRIBE: dict[str, Callable[[dict, Path | None], str]] = {
    "retranslate_sections": describe_retranslate,
    "run_glossary": describe_glossary,
}

TOOLS = [retranslate_sections, run_glossary]
