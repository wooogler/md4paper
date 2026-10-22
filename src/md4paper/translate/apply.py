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


def build_units(wd: WorkDir, manifest: Manifest, lines: list[str]) -> list[dict]:
    """en.md를 섹션 단위 유닛으로. 각 유닛: {sids, translatable, source}.

    공개 접합부 — 에이전트의 섹션 재번역(ui/agent/jobtools)이 어느 유닛의 캐시를 지울지 고를 때 쓴다.

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

    # 작은 번역 섹션 병합 + 비번역 구간은 그대로 통과.
    # 유닛에는 섹션 조각(sections: [(sid, 원문)])도 남긴다 — 캐시를 섹션 단위로도 두기 위해서다.
    # 병합 경계는 체크 상태에 따라 달라지므로 유닛 해시만으로는 "체크를 풀었다 다시 켠" 섹션을 못 찾는다.
    units: list[dict] = []
    buf_sids: list[str] = []
    buf_secs: list[tuple[str, str]] = []
    buf_text = ""

    def flush() -> None:
        nonlocal buf_sids, buf_secs, buf_text
        if buf_sids or buf_text.strip():
            units.append({"sids": buf_sids, "translatable": True, "source": buf_text, "sections": buf_secs})
        buf_sids, buf_secs, buf_text = [], [], ""

    for sids, do, text in spans:
        if not do:
            flush()
            units.append({"sids": sids, "translatable": False, "source": text,
                          "sections": [(sids[0], text)] if sids else []})
            continue
        buf_sids = buf_sids + sids
        if sids:
            buf_secs = buf_secs + [(sids[0], text)]
        buf_text = text if not buf_text else buf_text + "\n" + text
        if len(buf_text) >= _MERGE_MIN:
            flush()
    flush()

    # 큰 섹션은 문단 경계로 파트 분할 (같은 sid를 공유; 파트는 섹션 조각이 아니므로 sections 없음)
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


def stale_section_ids(wd: WorkDir, manifest: Manifest) -> set[str]:
    """번역이 낡은 섹션 — 지금 en.md로 만든 번역 유닛의 원문이 캐시에 없는 섹션들의 id.

    재변환·레이아웃 수정·직접 편집으로 원문이 바뀌면 그 유닛의 해시가 달라져 캐시에 없다 → 다음
    번역 실행 때 그 유닛만 다시 번역되고 나머지는 캐시를 쓴다. UI가 "어느 섹션이 다시 번역될지"를
    미리 보여 주는 데 쓴다. 번역 자체가 없거나 캐시가 없으면 빈 집합(전부 새 번역이라 '낡음'이 아니다).
    캐시 키(모델·프롬프트)가 바뀌었는지는 보지 않는다 — 그 경우 어차피 전부 다시 번역된다.
    실패(passthrough)한 유닛도 캐시에 없으므로 여기 포함된다(재번역 대상인 것은 같다).
    """
    if not (wd.ko_md.exists() and wd.cache_json.exists() and wd.en_md.exists()):
        return set()
    try:
        chunks = json.loads(wd.cache_json.read_text(encoding="utf-8")).get("chunks", {})
    except (OSError, json.JSONDecodeError):
        return set()
    out: set[str] = set()
    for u in _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines()):
        if u["translatable"] and u["sids"] and _cached_translation(chunks, u) is None:
            out.update(u["sids"])
    return out


def run_glossary(wd: WorkDir, provider: Provider, *, regenerate: bool = True) -> int:
    """용어집만 생성/갱신 (웹 UI의 '번역 전 용어집 검토' 지점). 항목 수 반환."""
    if not wd.en_md.exists():
        raise TranslateError(f"paper.en.md 없음: {wd.en_md}. 먼저 convert를 실행하세요.")
    manifest = manifest_io.load(wd)
    source = context.extract_glossary_source(wd, manifest)  # 초록·서론·섹션 제목·첫 문단
    gloss = glossary.ensure(provider, wd, manifest.title, source, regenerate=regenerate)
    return len(gloss.entries)


def _split_by_headings(text: str, k: int) -> list[str] | None:
    """번역된 유닛을 헤더 줄 기준으로 k조각으로 — 섹션 조각 수와 맞을 때만(코드 펜스 안 #는 무시)."""
    lines = text.split("\n")
    idx: list[int] = []
    fence = False
    for i, ln in enumerate(lines):
        st = ln.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
        elif not fence and re.match(r"^#{1,6}\s", ln):
            idx.append(i)
    if len(idx) != k or not idx or idx[0] != 0:
        return None
    return ["\n".join(lines[idx[j]:(idx[j + 1] if j + 1 < k else len(lines))]) for j in range(k)]


def _cached_translation(cache: dict[str, str], u: dict) -> str | None:
    """유닛의 보관된 번역 — 유닛 원문 해시로, 없으면 섹션 조각 해시들을 이어 붙여서."""
    hit = cache.get(hash_text(u["source"]))
    if hit is not None:
        return hit
    secs = u.get("sections") or []
    if not secs or len(secs) != len(u["sids"]):
        return None
    parts: list[str] = []
    for _sid, text in secs:
        t = cache.get(hash_text(text))
        if t is None:
            return None
        parts.append(t)
    return "\n".join(parts)


def _remember(cache: dict[str, str], u: dict, translated: str) -> None:
    """유닛 번역을 캐시에 — 유닛 해시로, 그리고 섹션 조각이 여럿이면 조각별로도."""
    cache[hash_text(u["source"])] = translated
    secs = u.get("sections") or []
    if len(secs) >= 2 and all(sid for sid, _t in secs):
        pieces = _split_by_headings(translated, len(secs))
        if pieces is not None:
            for (_sid, text), piece in zip(secs, pieces):
                cache[hash_text(text)] = piece


_build_units = build_units   # 옛 이름 (controller가 쓴다)


def unit_hashes(u: dict, gloss=None) -> set[str]:  # noqa: ANN001 — GlossaryList | None
    """유닛의 캐시 키들 — 유닛 원문 해시 + 섹션 조각 해시(병합 유닛이면 조각별로도 저장되므로).

    공개 접합부: 에이전트의 섹션 재번역이 이 키들을 cache.json에서 지워 '그 유닛만 다시'를 만든다.
    gloss는 지금 쓰지 않는다 — 다른 브랜치가 용어집 반영 해시로 바꿀 접합부다(이름·인자를 유지할 것).
    조각 해시를 빼면 병합 유닛은 유닛 해시를 지워도 조각으로 캐시에 걸려 다시 번역되지 않는다.
    """
    return {hash_text(u["source"]), *(hash_text(t) for _s, t in (u.get("sections") or []))}


def _cache_keys(korean_style: str, gloss, provider: Provider, manifest: Manifest, system_prompt: str) -> tuple[str, str]:
    """(현재 키, 옛 키). 캐시는 문체·용어집·모델·헤더/참고문헌 번역 여부가 같을 때만 재사용한다.

    제목·초록·개요(섹션 목록)는 키에 넣지 않는다 — 재변환으로 섹션 제목 하나만 바뀌어도 전부 다시
    번역하게 되고, 그러면 '체크 안 한 섹션의 기존 번역 유지'도 함께 무너진다. 옛 키(시스템 프롬프트
    전체)는 기존 캐시를 한 번 더 읽어 주기 위해 남긴다 — 다음 저장부터는 새 키로 쓴다.
    """
    tail = f"{provider.model}|headers={manifest.translate_headers}|refs={manifest.translate_references}"
    return (hash_text(f"v2|{korean_style}|{engine._glossary_block(gloss)}|{tail}"),
            hash_text(f"{system_prompt}|{tail}"))


def _load_cache(wd: WorkDir, keys: tuple[str, ...]) -> dict[str, str]:
    if not wd.cache_json.exists():
        return {}
    try:
        stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return stored.get("chunks", {}) if stored.get("key") in keys else {}


def unticked_without_cache(wd: WorkDir, manifest: Manifest) -> set[str]:
    """번역 대상에서 뺀 섹션 중, 보관된 번역이 없어 다음 실행 뒤 영어 원문으로 바뀔 섹션 id.

    체크를 풀어도 캐시에 그 원문의 번역이 있으면 run_translate가 그대로 둔다(keep). 없을 때만 영어로
    돌아가므로, UI가 실행 전에 "이 섹션들은 영어가 됩니다"라고 물을 수 있게 골라 준다. 번역이 아직
    없으면(첫 번역) 빈 집합 — 잃을 것이 없다. 참고문헌처럼 설정으로 빠진 구간은 체크와 무관하므로 제외.
    """
    if not (wd.ko_md.exists() and wd.en_md.exists()):
        return set()
    chunks: dict = {}
    if wd.cache_json.exists():
        try:
            chunks = json.loads(wd.cache_json.read_text(encoding="utf-8")).get("chunks", {})
        except (OSError, json.JSONDecodeError):
            chunks = {}
    # 참고문헌은 설정(translate_references)으로 빠지는 것이라 체크와 무관 — 원래도 영어였으니 잃을 게 없다
    ref_ids = set() if manifest.translate_references else section_ids_by_text(manifest, _REF_KEYWORDS)
    unticked = {s.id for s in manifest.sections if not s.translate} - ref_ids
    out: set[str] = set()
    for u in _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines()):
        if u["translatable"] or not u["sids"]:
            continue
        if any(sid in unticked for sid in u["sids"]) and _cached_translation(chunks, u) is None:
            out.update(sid for sid in u["sids"] if sid in unticked)
    return out


def run_translate(
    wd: WorkDir, provider: Provider, korean_style: str, on_progress=None, workers: int | None = None,
    keep_cached: bool = True,
) -> dict:
    """번역 실행 → paper.ko.md. 섹션 단위 병렬. on_progress(done, total, status)로 진행 보고.

    status: {section_id: 'translating'|'done'|'passthrough'} — 섹션별 진행을 UI에 표시.
    keep_cached: 번역 대상에서 뺀(체크 해제) 섹션이라도 캐시에 같은 원문의 번역이 있으면 그대로 둔다.
    "일부만 다시 번역"하려고 나머지를 풀었을 때 그 나머지가 영어로 돌아가 버리는 사고를 막는다.
    캐시는 덮어쓰지 않고 합치며, 지금 문서에 없는 원문의 항목만 버린다.
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

    cache_key, legacy_key = _cache_keys(korean_style, gloss, provider, manifest, system_prompt)
    cache = _load_cache(wd, (cache_key, legacy_key))

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
    counts = {"ok": 0, "retried": 0, "passthrough": 0, "cached": 0, "kept": 0}
    new_cache: dict[str, str] = {}
    done = [0]
    lock = threading.Lock()

    def report() -> None:
        if on_progress is not None:
            on_progress(done[0], total, dict(status))

    def work(u: dict) -> str:
        if not u["translatable"]:
            kept = _cached_translation(cache, u) if (keep_cached and u["sids"]) else None
            if kept is not None:  # 체크를 풀었어도 보관된 번역이 있으면 그대로 둔다
                with lock:
                    counts["kept"] += 1
                    _remember(new_cache, u, kept)
                return kept
            return u["source"]
        with lock:
            for sid in u["sids"]:
                status.setdefault(sid, "translating")
            report()
        hit = _cached_translation(cache, u)
        if hit is not None:
            translated, st, problems = hit, "cached", []
        else:
            translated, st, problems = engine.translate_with_retry(
                provider, system_prompt, u["source"],
                protect_headings=protect_headings, context_tail=u.get("context_tail", ""),
            )
        with lock:
            counts[st] += 1
            if st != "passthrough":
                _remember(new_cache, u, translated)  # 실패(passthrough)는 캐시하지 않음 → 재실행 시 그 섹션만 재시도
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
    # 캐시는 합친다 — 이번에 안 쓴 항목도 지금 문서에 그 원문이 남아 있으면 보관(체크를 풀었다가
    # 다시 켜도 비용 없이 돌아온다). 원문이 사라진 항목만 버린다.
    current: set[str] = set().union(*(unit_hashes(u, gloss) for u in units)) if units else set()
    merged = {h: t for h, t in {**cache, **new_cache}.items() if h in current}
    wd.cache_json.write_text(
        json.dumps({"key": cache_key, "chunks": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "chunks": total,
        "glossary": len(gloss.entries),
        **counts,
        "failures": {sid: ", ".join(reasons) for sid, reasons in sid_reasons.items()},
        "cost_usd": provider.cost(),
    }
