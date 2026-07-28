"""translate 오케스트레이션 — 문서 컨텍스트 + 용어집 + 섹션 단위 병렬 번역 + 캐시.

paper.en.md(cite 후면 최종 인용 형태 반영) → paper.ko.md. 섹션을 유닛으로 나눠 원문측 컨텍스트
(개요 + 직전 섹션 원문 꼬리)만 참조하며 스레드로 병렬 번역한다(청크 간 결과 의존 없음). 내용 해시
캐시로 바뀐 유닛만 재번역. 병기(첫 등장 원어)는 조립 후 postprocess가 전역 1회로 정리한다.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from md4paper import config
from md4paper.ir import Manifest
from md4paper.llm.base import Provider
from md4paper.regions import section_ids_by_text
from md4paper.review import manifest as manifest_io
from md4paper.translate import chunker, context, engine, glossary, postprocess
from md4paper.workdir import WorkDir, hash_text

_REF_KEYWORDS = {"references", "reference", "bibliography"}
_MERGE_MIN = 1500   # 이보다 작은 인접 번역 섹션은 한 유닛으로 병합 (LLM 호출 수 절감)
_BIG_SPLIT = 6000   # 이보다 큰 섹션은 문단 경계로 파트 분할 (출력 토큰 한도·재시도 비용)
_TAIL_CHARS = 400   # 직전 섹션 원문 꼬리(참고 문맥) 길이


class TranslateError(RuntimeError):
    pass


def _build_outline(section_map: list[dict]) -> str:
    """섹션 제목 트리 (시스템 프롬프트의 문서 개요)."""
    lines = []
    for e in sorted(section_map, key=lambda x: x["out_line"]):
        lvl = e.get("level", 1)
        indent = "  " * (max(1, int(lvl)) - 1) if str(lvl).isdigit() else ""
        lines.append(f"{indent}- {e.get('text', '')}")
    return "\n".join(lines)


def _context_tail(text: str, max_chars: int = _TAIL_CHARS) -> str:
    """섹션 원문 끝부분 (다음 섹션 번역의 참고 문맥). 문장 경계에서 시작하도록 다듬음."""
    t = text.strip()
    if len(t) <= max_chars:
        return t
    tail = t[-max_chars:]
    m = re.search(r"[.!?]\s+", tail)
    return tail[m.end():] if m else tail


def _build_units(wd: WorkDir, manifest: Manifest, lines: list[str]) -> list[dict]:
    """en.md를 섹션 단위 유닛으로. 각 유닛: {sids, translatable, source}.

    섹션별 translate 플래그를 따르고, 참고문헌은 translate_references가 꺼져 있으면 제외.
    작은 번역 섹션은 인접끼리 병합(멤버 sid 보존), 큰 섹션은 문단 경계로 파트 분할(각 파트가 같은 sid).
    """
    full = "\n".join(lines)
    if not wd.sections_map.exists():
        return [{"sids": [], "translatable": True, "source": full}]
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
    if not section_map:
        return [{"sids": [], "translatable": True, "source": full}]

    flags = {s.id: s.translate for s in manifest.sections}
    ref_ids = set() if manifest.translate_references else section_ids_by_text(manifest, _REF_KEYWORDS)
    ordered = sorted(section_map, key=lambda e: e["out_line"])

    # (sids, translatable, text) 구간 — 첫 헤더 앞 서문은 번역 대상
    spans: list[tuple[list[str], bool, str]] = []
    if ordered[0]["out_line"] > 0:
        spans.append(([], True, "\n".join(lines[: ordered[0]["out_line"]])))
    for i, e in enumerate(ordered):
        start = e["out_line"]
        end = ordered[i + 1]["out_line"] if i + 1 < len(ordered) else len(lines)
        do = flags.get(e["id"], True) and e["id"] not in ref_ids
        spans.append(([e["id"]], do, "\n".join(lines[start:end])))

    # 작은 번역 섹션 병합 + 비번역 구간은 그대로 통과
    units: list[dict] = []
    buf_sids: list[str] = []
    buf_text = ""

    def flush() -> None:
        nonlocal buf_sids, buf_text
        if buf_sids or buf_text.strip():
            units.append({"sids": buf_sids, "translatable": True, "source": buf_text})
        buf_sids, buf_text = [], ""

    for sids, do, text in spans:
        if not do:
            flush()
            units.append({"sids": sids, "translatable": False, "source": text})
            continue
        buf_sids = buf_sids + sids
        buf_text = text if not buf_text else buf_text + "\n" + text
        if len(buf_text) >= _MERGE_MIN:
            flush()
    flush()

    # 큰 섹션은 문단 경계로 파트 분할 (같은 sid를 공유)
    final: list[dict] = []
    for u in units:
        if u["translatable"] and len(u["source"]) > _BIG_SPLIT:
            for part in chunker.split_by_paragraphs(u["source"], target_chars=_BIG_SPLIT):
                final.append({"sids": u["sids"], "translatable": True, "source": part})
        else:
            final.append(u)

    # 앞 문맥: 각 유닛에 직전 유닛 원문 꼬리 (원문 유도 → 병렬 안전)
    for i, u in enumerate(final):
        u["context_tail"] = _context_tail(final[i - 1]["source"]) if i > 0 else ""
    return final


def run_glossary(wd: WorkDir, provider: Provider, *, regenerate: bool = True) -> int:
    """용어집만 생성/갱신 (웹 UI의 '번역 전 용어집 검토' 지점). 항목 수 반환."""
    if not wd.en_md.exists():
        raise TranslateError(f"paper.en.md 없음: {wd.en_md}. 먼저 convert를 실행하세요.")
    manifest = manifest_io.load(wd)
    source = context.extract_glossary_source(wd, manifest)  # 초록·서론·섹션 제목·첫 문단
    gloss = glossary.ensure(provider, wd, manifest.title, source, regenerate=regenerate)
    return len(gloss.entries)


def run_translate(
    wd: WorkDir, provider: Provider, korean_style: str, on_progress=None, workers: int | None = None
) -> dict:
    """번역 실행 → paper.ko.md. 섹션 단위 병렬. on_progress(done, total, status)로 진행 보고.

    status: {section_id: 'translating'|'done'|'passthrough'} — 섹션별 진행을 UI에 표시.
    """
    if not wd.en_md.exists():
        raise TranslateError(f"paper.en.md 없음: {wd.en_md}. 먼저 convert를 실행하세요.")

    manifest = manifest_io.load(wd)
    protect_headings = not manifest.translate_headers
    title, abstract = context.extract_context(wd, manifest)  # 초록은 청크마다 주입되는 문서 컨텍스트
    gloss = glossary.ensure(provider, wd, title, context.extract_glossary_source(wd, manifest))
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", []) \
        if wd.sections_map.exists() else []
    outline = _build_outline(section_map)
    system_prompt = engine.build_system_prompt(korean_style, gloss, title, abstract, outline)

    # 캐시 유효성: 시스템 프롬프트 + 모델 + 헤더/참고문헌 번역 여부가 같아야 재사용
    cache_key = hash_text(
        f"{system_prompt}|{provider.model}|headers={manifest.translate_headers}|refs={manifest.translate_references}"
    )
    cache: dict[str, str] = {}
    if wd.cache_json.exists():
        stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
        if stored.get("key") == cache_key:
            cache = stored.get("chunks", {})

    units = _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
    total = sum(1 for u in units if u["translatable"])

    # 섹션(sid)별 완료 추적 — 여러 파트가 같은 sid를 공유하므로 카운트로 관리
    sid_total: Counter = Counter()
    for u in units:
        if u["translatable"]:
            for sid in u["sids"]:
                sid_total[sid] += 1
    sid_done: Counter = Counter()
    sid_failed: set[str] = set()
    sid_reasons: dict[str, list[str]] = {}  # 실패 sid → 구조 위반 항목 (UI에 이유 표시)
    status: dict[str, str] = {}
    counts = {"ok": 0, "retried": 0, "passthrough": 0, "cached": 0}
    new_cache: dict[str, str] = {}
    done = [0]
    lock = threading.Lock()

    def report() -> None:
        if on_progress is not None:
            on_progress(done[0], total, dict(status))

    def work(u: dict) -> str:
        if not u["translatable"]:
            return u["source"]
        with lock:
            for sid in u["sids"]:
                status.setdefault(sid, "translating")
            report()
        h = hash_text(u["source"])
        if h in cache:
            translated, st, problems = cache[h], "cached", []
        else:
            translated, st, problems = engine.translate_with_retry(
                provider, system_prompt, u["source"],
                protect_headings=protect_headings, context_tail=u.get("context_tail", ""),
            )
        with lock:
            counts[st] += 1
            if st != "passthrough":
                new_cache[h] = translated  # 실패(passthrough)는 캐시하지 않음 → 재실행 시 그 섹션만 재시도
            done[0] += 1
            for sid in u["sids"]:
                sid_done[sid] += 1
                if st == "passthrough":
                    sid_failed.add(sid)
                    if problems:
                        sid_reasons.setdefault(sid, [])
                        for p in problems:
                            if p not in sid_reasons[sid]:
                                sid_reasons[sid].append(p)
                if sid_done[sid] >= sid_total[sid]:
                    status[sid] = "passthrough" if sid in sid_failed else "done"
            report()
        return translated

    results: list[str] = [""] * len(units)
    max_workers = workers or config.resolve_translate_workers()
    if max_workers <= 1 or len(units) <= 1:  # 순차 폴백 (테스트·소량)
        for i, u in enumerate(units):
            results[i] = work(u)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(work, u): i for i, u in enumerate(units)}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()

    ko = postprocess.dedupe_first_use("\n".join(results) + "\n", gloss)
    ko = postprocess.enforce_keep(ko, gloss)  # keep(원어 유지) 용어가 번역됐으면 영어로 되돌림
    wd.out.mkdir(parents=True, exist_ok=True)
    wd.ko_md.write_text(ko, encoding="utf-8")
    wd.cache_json.write_text(
        json.dumps({"key": cache_key, "chunks": new_cache}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "chunks": total,
        "glossary": len(gloss.entries),
        **counts,
        "failures": {sid: ", ".join(reasons) for sid, reasons in sid_reasons.items()},
        "cost_usd": provider.cost(),
    }
