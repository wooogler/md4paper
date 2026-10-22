"""translate 오케스트레이션 — 문서 컨텍스트 + 용어집 + 섹션 단위 병렬 번역 + 캐시.

paper.en.md(cite 후면 최종 인용 형태 반영) → paper.ko.md. 섹션을 유닛으로 나눠 원문측 컨텍스트
(개요 + 직전 섹션 원문 꼬리)만 참조하며 스레드로 병렬 번역한다(청크 간 결과 의존 없음). 내용 해시
캐시로 바뀐 유닛만 재번역. 병기(첫 등장 원어)는 조립 후 postprocess가 전역 1회로 정리한다.

**캐시 키(v3)**: 문서 키에는 문체·모델·헤더/참고문헌 번역 여부만 넣고 용어집은 뺀다. 용어집은 유닛
해시(`unit_hash`)에 **그 유닛 원문에 나오는 항목의 줄만** 섞는다 — 용어 하나를 고치면(논문 용어집이든
컬렉션 용어집이든) 그 용어가 나오는 유닛만 다시 번역된다. 예전(v2)엔 용어집 블록 전체가 문서 키에
들어가 한 항목만 바꿔도 논문 전체가 재번역됐다. 프롬프트에는 여전히 용어집 **전체**가 들어간다(캐시
키만 좁힌다). 대가: 용어가 단어 첫머리부터 글자 그대로 나오지 않는 간접 영향(불규칙 복수·파생형 등)은
해시가 못 본다 — 그 유닛은 옛 번역을 그대로 쓴다. 관련 항목이 없는 유닛의 해시는 평문
`hash_text(source)`라 옛 캐시 항목과 같다. 옛 캐시 — v2(문서 키에 용어집 블록), v1(문서 키가 시스템
프롬프트 전체의 해시; 실데이터 번역 대부분이 이것) — 는 `_load_cache`가 한 번 읽어 옮긴다. 논문
용어집을 고치면 옛 키가 더는 안 맞으므로, 저장 전에 `migrate_cache`가 미리 v3로 옮겨 둔다.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from md4paper import config
from md4paper.ir import GlossaryList, Manifest
from md4paper.llm.base import Provider
from md4paper.regions import section_ids_by_text
from md4paper.review import manifest as manifest_io
from md4paper.translate import chunker, context, engine, glossary, postprocess
from md4paper.workdir import WorkDir, hash_text

_REF_KEYWORDS = {"references", "reference", "bibliography"}
_MERGE_MIN = 1500   # 이보다 작은 인접 번역 섹션은 한 유닛으로 병합 (LLM 호출 수 절감)
_BIG_SPLIT = 6000   # 이보다 큰 섹션은 문단 경계로 파트 분할 (출력 토큰 한도·재시도 비용)
_TAIL_CHARS = 400   # 직전 섹션 원문 꼬리(참고 문맥) 길이
_SCHEME = 3         # cache.json의 해시 체계 표식 — 모델을 모르는 쪽(stale 판정)이 v3인지 알아보는 데 쓴다


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


_build_units = build_units  # 옛 이름 (controller가 쓴다)


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


def relevant_lines(gloss: GlossaryList, source: str) -> str:
    """이 원문에 나오는 용어집 항목의 프롬프트 줄들(정렬해 "\n"으로 이음) — 유닛 해시에 섞는 부분.

    '나온다' = 공백을 정규화하고 casefold한 원문에 `term_key(term)`이 **단어 첫머리에서** 시작해 들어
    있음(줄바꿈을 넘는 여러 단어 용어도 잡힌다). 앞 경계만 본다: 그냥 부분 문자열이면 "edge"가
    "knowledge"에, "ai"가 "said"에 걸려 짧은 용어 하나로 컬렉션 논문 대부분이 낡음이 된다. 뒤 경계는 안
    봐서 "model"이 "models"에 걸린다(규칙 복수형은 잡는다). 불규칙 복수·파생형처럼 첫머리부터 글자 그대로
    안 나오면 못 잡는다(감수). 줄은 정렬한다 — 용어집 항목 순서만 바뀌어도(컬렉션 덮어쓰기는 논문 자리를
    차지하고, 컬렉션에만 있는 항목은 뒤에 붙는다) 해시가 바뀌면 번역 결과와 무관한 재번역이 생긴다.
    줄은 프롬프트와 같은 `engine.glossary_line`이되 원어 자리는 `term_key`로 — 프롬프트가 "대소문자
    무시"를 지시하므로 "Encoder"와 "encoder"는 같은 지시다. 그대로 두면 컬렉션이 논문과 표기만 다른
    같은 항목을 올릴 때마다 번역 결과와 무관하게 그 유닛들을 다시 번역(비용)하게 된다. keep 항목의 줄에는 번역어가 없으니 keep
    용어의 번역어만 고치는 것도 캐시를 낡게 하지 않는다(프롬프트에도 안 들어가는 글자라 결과가 같다).
    """
    if not gloss.entries:
        return ""
    text = re.sub(r"\s+", " ", source).casefold()
    lines = []
    for e in gloss.entries:
        key = glossary.term_key(e.term)
        if not key:
            continue
        # 앞 경계는 용어가 글자로 시작할 때만 — "(x)"·"-based"처럼 기호로 시작하는 용어는 그대로 찾는다
        lead = r"(?<!\w)" if re.match(r"\w", key) else ""
        if re.search(lead + re.escape(key), text):
            lines.append(engine.glossary_line(e.model_copy(update={"term": key})))
    return "\n".join(sorted(lines))


def unit_hash(source: str, gloss: GlossaryList) -> str:
    """번역 캐시 해시 — 원문 + 그 원문에 나오는 용어집 줄. 관련 항목이 없으면 평문 `hash_text(source)`.

    평문으로 떨어지게 둔 이유: 용어가 안 나오는 유닛은 용어집과 무관하고, 옛 캐시 항목과 같은 해시라
    그대로 읽힌다. 섹션 조각(sections)도 같은 함수로 — 재번역 tool이 "이 섹션의 캐시 항목 지우기"를 할
    때도 이 함수를 써야 같은 항목을 찾는다.
    """
    rel = relevant_lines(gloss, source)
    return hash_text(source + "\n\x00" + rel) if rel else hash_text(source)


def unit_hashes(u: dict, gloss: GlossaryList) -> set[str]:
    """유닛이 캐시에 남길 수 있는 해시들 — 유닛 원문 + 섹션 조각들."""
    return {unit_hash(u["source"], gloss), *(unit_hash(t, gloss) for _s, t in (u.get("sections") or []))}


class _Cache:
    """보관된 번역 조회기 — 저장된 캐시가 어느 해시 체계인지에 따라 원문을 어느 해시로 찾을지 정한다.

    v3: `unit_hash(원문, 지금 용어집)`으로. 옛 v2·v1(`legacy`=그 실행 때의 논문 용어집): 항목이 평문
    `hash_text(원문)`으로 저장돼 있다. 그 번역은 논문 용어집 전체로 만든 것이므로, 지금 용어집(컬렉션
    덮어쓰기 포함)에서 **그 원문에 나오는 항목이 논문 용어집 때와 같을 때만** 쓴다 — 덮어쓰기가 그
    유닛의 용어를 바꿨으면 거부해 다시 번역하게 한다.
    """

    def __init__(self, chunks: dict[str, str], gloss: GlossaryList,
                 legacy: GlossaryList | None = None) -> None:
        self.chunks = chunks
        self.gloss = gloss
        self.legacy = legacy

    def get(self, text: str) -> str | None:
        hit = self.chunks.get(unit_hash(text, self.gloss))
        if hit is not None or self.legacy is None:
            return hit
        if relevant_lines(self.gloss, text) != relevant_lines(self.legacy, text):
            return None
        return self.chunks.get(hash_text(text))

    def upgraded(self, units: list[dict]) -> _Cache:
        """옛 모드면 지금 문서의 유닛·섹션 조각 중 쓸 수 있는 항목을 새 해시로 옮긴 v3 캐시로.

        이번 실행에서 안 쓰는 항목(체크를 푼 유닛, 병합 경계가 달라 조각으로만 남은 섹션)도 새 해시로
        옮겨 둬야 다음 저장의 병합(지금 문서의 새 해시 집합으로 거름)에서 버려지지 않는다.
        """
        if self.legacy is None:
            return self
        chunks = dict(self.chunks)
        for u in units:
            for text in [u["source"], *(t for _s, t in (u.get("sections") or []))]:
                hit = self.get(text)
                if hit is not None:
                    chunks[unit_hash(text, self.gloss)] = hit
        return _Cache(chunks, self.gloss)


def _cached_translation(cache: _Cache, u: dict) -> str | None:
    """유닛의 보관된 번역 — 유닛 원문 해시로, 없으면 섹션 조각 해시들을 이어 붙여서."""
    hit = cache.get(u["source"])
    if hit is not None:
        return hit
    secs = u.get("sections") or []
    if not secs or len(secs) != len(u["sids"]):
        return None
    parts: list[str] = []
    for _sid, text in secs:
        t = cache.get(text)
        if t is None:
            return None
        parts.append(t)
    return "\n".join(parts)


def _remember(cache: dict[str, str], u: dict, translated: str, gloss: GlossaryList) -> None:
    """유닛 번역을 캐시에 — 유닛 해시로, 그리고 섹션 조각이 여럿이면 조각별로도."""
    cache[unit_hash(u["source"], gloss)] = translated
    secs = u.get("sections") or []
    if len(secs) >= 2 and all(sid for sid, _t in secs):
        pieces = _split_by_headings(translated, len(secs))
        if pieces is not None:
            for (_sid, text), piece in zip(secs, pieces):
                cache[unit_hash(text, gloss)] = piece


def _key_tail(model: str, manifest: Manifest) -> str:
    return f"{model}|headers={manifest.translate_headers}|refs={manifest.translate_references}"


def _cache_keys(korean_style: str, paper_gloss: GlossaryList, model: str,
                manifest: Manifest) -> tuple[str, str]:
    """(v3 키, 옛 v2 키). 캐시는 문체·모델·헤더/참고문헌 번역 여부가 같을 때만 재사용한다.

    용어집은 v3 키에서 뺐다 — 유닛 해시가 맡는다(모듈 docstring). 옛 v2 키(용어집 블록 전체 포함)는
    **논문 용어집만으로** 만든다: 지난 실행이 논문 용어집만 썼으므로, 사용자가 그 뒤 논문 용어집을 안
    고쳤다면 저장된 키와 같다 → 옛 캐시를 옮겨 읽는다. 제목·초록·개요(섹션 목록)는 키에 넣지 않는다 —
    재변환으로 섹션 제목 하나만 바뀌어도 전부 다시 번역하게 되고, 그러면 '체크 안 한 섹션의 기존 번역
    유지'도 함께 무너진다. 더 옛 v1 키까지 함께 맞춰 보려면 `_legacy_keys`.
    """
    tail = _key_tail(model, manifest)
    return (hash_text(f"v3|{korean_style}|{tail}"),
            hash_text(f"v2|{korean_style}|{engine._glossary_block(paper_gloss)}|{tail}"))


def _legacy_keys(wd: WorkDir, manifest: Manifest, korean_style: str, paper_gloss: GlossaryList,
                 model: str) -> set[str]:
    """옛 캐시 키들 — v2(`_cache_keys`의 둘째)와 v1(시스템 프롬프트 전체 + 꼬리의 해시).

    v1은 v2 이전 코드가 쓴 키다: `engine.build_system_prompt(문체, 용어집, 제목, 초록, 개요)`를 통째로
    해시했다. 그때도 논문 용어집만 썼으므로 **논문 용어집**으로 다시 만든다. 제목·초록·개요가 들어가
    있어 재변환으로 원문이 바뀌었으면 안 맞는다(그때는 예전에도 전부 다시 번역됐다). 제목·초록은 파일을
    쓰지 않는 `context.title_abstract`로 — 뷰어 표시·영향 미리보기도 이 함수를 부른다. 섹션 지도가
    없거나 못 읽으면 v1은 건너뛴다 — 옛 `context.extract_context`가 sections.map.json을 조건 없이 읽어
    (FileNotFoundError) 그런 논문은 v1 코드로 번역될 수 없었으니 그런 v1 키도 없다. (개요만은 지도가 없으면
    빈 문자열로 진행했지만, 그보다 먼저 제목·초록 단계에서 멈췄다.)

    **v1 키는 지금의 `engine.build_system_prompt` 문구로 만든다** — 그 문구를 바꾸면 v1 캐시를 가진 논문이
    전부 재번역된다. golden 테스트(`test_v1_legacy_key_is_golden`)가 이를 지킨다.
    """
    keys = {_cache_keys(korean_style, paper_gloss, model, manifest)[1]}
    try:
        title, abstract = context.title_abstract(wd, manifest)
        section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", [])
        outline = _build_outline(section_map)
    except (OSError, ValueError, KeyError, AttributeError):
        return keys
    prompt = engine.build_system_prompt(korean_style, paper_gloss, title, abstract, outline)
    keys.add(hash_text(f"{prompt}|{_key_tail(model, manifest)}"))
    return keys


def _read_stored(wd: WorkDir) -> dict | None:
    if not wd.cache_json.exists():
        return None
    try:
        stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return stored if isinstance(stored, dict) else None


def _load_cache(wd: WorkDir, *, v3_key: str, legacy_keys: set[str], paper_gloss: GlossaryList,
                eff_gloss: GlossaryList) -> _Cache:
    """저장된 캐시 — 키가 v3면 새 해시로, 논문 용어집으로 만든 옛 키(v2·v1, `_legacy_keys`) 중 하나와
    같으면 옛 모드로, 그 밖이면 빈 것."""
    stored = _read_stored(wd)
    if stored is None:
        return _Cache({}, eff_gloss)
    chunks = stored.get("chunks") or {}
    if stored.get("key") == v3_key:
        return _Cache(chunks, eff_gloss)
    if stored.get("key") in legacy_keys:
        return _Cache(chunks, eff_gloss, legacy=paper_gloss)
    return _Cache({}, eff_gloss)


def _paper_glossary(wd: WorkDir) -> GlossaryList:
    return glossary.load(wd) if wd.glossary_yaml.exists() else GlossaryList(entries=[])


def _last_model_style(wd: WorkDir, manifest: Manifest) -> tuple[str, str] | None:
    """지난 번역의 (모델, 문체) — status.json의 명시 기록, 없으면 input_hash로 되찾은 것, 그것도 없으면 None.

    모델 기록 전의 번역(실데이터 대부분)도 input_hash(`문체:모델` 해시)는 남아 있어 대개 되찾힌다
    (`pipeline.recover_translate`). 되찾으면 옛 키를 그 모델로 맞춰 볼 수 있어, '쓸 수 있음' 판정이 다음
    실행(뷰어 기본 모델도 같은 순서로 정한다)과 어긋나지 않는다.
    """
    from md4paper import pipeline  # 순환 import 피하기 (pipeline → apply)

    last = wd.load_status().get("translate")
    if isinstance(last, dict) and last.get("model"):
        return last["model"], last.get("style") or manifest.korean_style
    got = pipeline.recover_translate(wd, manifest)
    return (got[1], got[2]) if got else None


def _model_style_or_default(wd: WorkDir, manifest: Manifest) -> tuple[str, str]:
    """`_last_model_style`, 모르면 (전역 기본 모델, manifest 문체) — 뷰어 번역 모델 드롭다운 기본값과
    같은 순서. `_stored_cache`(옛 키 대조)와 `migrate_cache`(v3 키)가 같은 값을 쓰도록 한곳에 둔다."""
    return _last_model_style(wd, manifest) or (
        config.resolve_model(config.resolve_provider()), manifest.korean_style)


def _stored_cache(wd: WorkDir, manifest: Manifest, gloss: GlossaryList | None) -> _Cache | None:
    """프로바이더 없이(stale·unticked 판정) 저장된 캐시를 읽는다. 쓸 수 없는 캐시면 None.

    모델을 모르니 v3 키는 다시 만들 수 없다 — cache.json의 `scheme` 표식으로 알아보고, v3면 키(모델·
    문체)는 따지지 않는다(예전부터 이 판정은 키를 안 봤다: 모델을 바꾸면 어차피 전부 다시 번역된다).
    표식이 없는 옛 파일은 지난 번역의 문체·모델(`_last_model_style` — 명시 기록, 없으면 input_hash로
    되찾은 것)로 옛 키(v2·v1, `_legacy_keys`)를 다시 만들어 맞춰 보고, 맞으면 옛 모드, 안 맞으면
    None(다음 실행이 캐시를 못 쓴다). 그것도 모르면 `migrate_cache`와 같은 대체값(전역 기본 모델 +
    manifest 문체, `_model_style_or_default`)으로 맞춰 본다 — 키를 안 보고 '쓸 수 있음'이라 하면,
    용어집이 `glossary.save`를 거치지 않고 바뀌었을 때(YAML 직접 편집·설치 앱) 표시는 몇 유닛인데
    실행은 전부 다시 번역한다. 대체값은 다음 실행(뷰어 기본 모델)이 실제로 쓸 값이기도 하다.
    """
    stored = _read_stored(wd)
    if stored is None:
        return None
    eff = gloss if gloss is not None else glossary.effective(wd)[0]
    chunks = stored.get("chunks") or {}
    if stored.get("scheme") == _SCHEME:
        return _Cache(chunks, eff)
    paper = _paper_glossary(wd)
    model, style = _model_style_or_default(wd, manifest)
    if stored.get("key") not in _legacy_keys(wd, manifest, style, paper, model):
        return None
    return _Cache(chunks, eff, legacy=paper)


def migrate_cache(wd: WorkDir, manifest: Manifest | None = None) -> bool:
    """옛(v2·v1) 캐시를 지금 effective 용어집 기준의 v3로 옮겨 저장한다. 옮겼으면 True.

    논문 용어집을 고치기 **전에** 부른다(`glossary.save` 첫머리): 옛 키는 논문 용어집을 품고 있어 고친
    뒤에는 안 맞고, 그러면 용어 하나 고친 논문이 통째로 다시 번역된다. 미리 v3(용어집은 유닛 해시가
    맡는다)로 옮겨 두면 고친 용어가 나오는 유닛만 낡는다. 쓸 수 있는지는 `_stored_cache`와 같은 판정
    (cache.json이 없거나 이미 v3거나 못 쓰는 옛 캐시면 아무것도 안 하고 False).

    v3 키의 모델·문체는 `_model_style_or_default`(명시 기록 → input_hash로 되찾은 것 → 전역 기본 모델과
    manifest 문체) — 뷰어 번역 모델 드롭다운의 기본값(`Controller.translate_model`)과 같은 순서라 다음
    실행이 그대로 키를 맞춘다. 항목은 `run_translate`의 저장처럼 지금 문서의 해시 집합으로 거른다.
    쓰기는 임시 파일 + `os.replace`(중간에 실패해도 원본 cache.json이 그대로 남게; 임시 파일은 지운다).
    """
    if not (wd.cache_json.exists() and wd.en_md.exists()):
        return False
    stored = _read_stored(wd)
    if stored is None or stored.get("scheme") == _SCHEME:
        return False
    if manifest is None:
        manifest = manifest_io.load(wd)
    cache = _stored_cache(wd, manifest, None)
    if cache is None or cache.legacy is None:
        return False
    model, style = _model_style_or_default(wd, manifest)
    units = _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
    up = cache.upgraded(units)
    current: set[str] = set().union(*(unit_hashes(u, up.gloss) for u in units)) if units else set()
    v3_key, _v2 = _cache_keys(style, cache.legacy, model, manifest)
    chunks = {h: t for h, t in up.chunks.items() if h in current}
    tmp = wd.cache_json.with_name(wd.cache_json.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"key": v3_key, "scheme": _SCHEME, "chunks": chunks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, wd.cache_json)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def stale_units(wd: WorkDir, manifest: Manifest, gloss: GlossaryList | None = None) -> list[dict]:
    """번역이 낡은 번역 유닛들(`build_units`의 유닛, 문서 순) — 지금 원문·용어집으로 캐시에 없는 것.

    `stale_section_ids`의 바탕이고, 영향 미리보기(`glossary.impact`)가 낡은 유닛의 원문 글자 수로 비용을
    어림하는 데 쓴다. gloss는 effective 용어집(None이면 이 논문의 것을 읽는다). 번역·캐시가 없거나
    다음 실행이 캐시를 못 쓰는 경우(옛 키 불일치)는 빈 목록 — 원문·용어집이 바뀐 게 아니라 '낡음'이 아니다.
    그 경우에도 다음 실행은 전부 다시 번역하므로, 비용을 셀 때는 `retranslate_units`를 쓴다.
    """
    if not (wd.ko_md.exists() and wd.cache_json.exists() and wd.en_md.exists()):
        return []
    cache = _stored_cache(wd, manifest, gloss)
    if cache is None:
        return []
    return [u for u in _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
            if u["translatable"] and u["sids"] and _cached_translation(cache, u) is None]


def retranslate_units(wd: WorkDir, manifest: Manifest,
                      gloss: GlossaryList | None = None) -> tuple[list[dict], bool]:
    """다음 번역 실행 때 LLM을 다시 부를 유닛들(문서 순)과 full(캐시를 통째로 못 써 전부 다시 도는지).

    `stale_units`와 다른 점: 그쪽은 '원문·용어집이 바뀐 유닛'(뷰어의 '원문 바뀜')이고, 이쪽은 '다음 실행이
    실제로 다시 번역할 유닛'이다 — 번역(ko.md)은 있는데 저장된 캐시를 못 쓰면(cache.json 없음, 또는 표식
    없는 옛 파일의 키가 지난 번역의 모델·문체·논문 용어집과 안 맞음 — 그 뒤 논문 용어집을 고쳤거나 모델을
    바꿈) 원문이 바뀐 게 아니어도 번역 대상 유닛 전부가 다시 돈다 → (전부, True). 영향 미리보기·일괄 번역
    대화상자·에이전트가 이걸로 비용을 센다 — 그렇지 않으면 그 논문이 '최신'으로 보이고 비용이 0으로 나온다.
    ko.md(또는 en.md)가 없으면 ([], False) — 그건 '번역 없음'이지 재번역이 아니다(호출측이 따로 센다).
    그 밖에는 (stale_units, False). gloss는 effective 용어집(None이면 이 논문의 것을 읽는다).
    """
    if not (wd.ko_md.exists() and wd.en_md.exists()):
        return [], False
    if _stored_cache(wd, manifest, gloss) is None:
        units = _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
        return [u for u in units if u["translatable"] and u["sids"]], True
    return stale_units(wd, manifest, gloss), False


def stale_section_ids(wd: WorkDir, manifest: Manifest, gloss: GlossaryList | None = None) -> set[str]:
    """번역이 낡은 섹션 — 지금 en.md로 만든 번역 유닛이 캐시에 없는 섹션들의 id.

    재변환·레이아웃 수정·직접 편집으로 원문이 바뀌거나, 유닛에 나오는 용어의 용어집 항목(논문 또는 컬렉션)
    이 바뀌면 그 유닛의 해시가 달라져 캐시에 없다 → 다음 번역 실행 때 그 유닛만 다시 번역되고 나머지는
    캐시를 쓴다. UI가 "어느 섹션이 다시 번역될지"를 미리 보여 주는 데 쓴다. 작은 섹션은 병합된 유닛
    단위로 낡으므로 그 유닛의 섹션이 함께 잡힌다(실제로 함께 다시 번역된다). gloss는 effective 용어집
    (None이면 `glossary.effective(wd)`). 실패(passthrough)한 유닛도 캐시에 없으므로 여기 포함된다.
    """
    out: set[str] = set()
    for u in stale_units(wd, manifest, gloss):
        out.update(u["sids"])
    return out


def unticked_without_cache(wd: WorkDir, manifest: Manifest) -> set[str]:
    """번역 대상에서 뺀 섹션 중, 보관된 번역이 없어 다음 실행 뒤 영어 원문으로 바뀔 섹션 id.

    체크를 풀어도 캐시에 그 원문의 번역이 있으면 run_translate가 그대로 둔다(keep). 없을 때만 영어로
    돌아가므로, UI가 실행 전에 "이 섹션들은 영어가 됩니다"라고 물을 수 있게 골라 준다. 번역이 아직
    없으면(첫 번역) 빈 집합 — 잃을 것이 없다. 참고문헌처럼 설정으로 빠진 구간은 체크와 무관하므로 제외.
    보관 여부는 stale 판정과 같은 해시 규칙(effective 용어집)으로 본다 — 다음 실행이 캐시를 못 쓰는
    경우(옛 키 불일치)엔 보관된 것이 없는 것으로 본다(실제로 그 섹션들은 영어가 된다).
    """
    if not (wd.ko_md.exists() and wd.en_md.exists()):
        return set()
    eff = glossary.effective(wd)[0]
    cache = _stored_cache(wd, manifest, eff) or _Cache({}, eff)
    # 참고문헌은 설정(translate_references)으로 빠지는 것이라 체크와 무관 — 원래도 영어였으니 잃을 게 없다
    ref_ids = set() if manifest.translate_references else section_ids_by_text(manifest, _REF_KEYWORDS)
    unticked = {s.id for s in manifest.sections if not s.translate} - ref_ids
    out: set[str] = set()
    for u in _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines()):
        if u["translatable"] or not u["sids"]:
            continue
        if any(sid in unticked for sid in u["sids"]) and _cached_translation(cache, u) is None:
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
    paper_gloss = glossary.ensure(provider, wd, title, context.extract_glossary_source(wd, manifest))
    gloss, _origin = glossary.effective(wd)  # 논문 용어집 + 주 컬렉션 용어집 덮어쓰기
    section_map = json.loads(wd.sections_map.read_text(encoding="utf-8")).get("sections", []) \
        if wd.sections_map.exists() else []
    outline = _build_outline(section_map)
    system_prompt = engine.build_system_prompt(korean_style, gloss, title, abstract, outline)

    cache_key, _v2 = _cache_keys(korean_style, paper_gloss, provider.model, manifest)
    cache = _load_cache(wd, v3_key=cache_key, paper_gloss=paper_gloss, eff_gloss=gloss,
                        legacy_keys=_legacy_keys(wd, manifest, korean_style, paper_gloss, provider.model))

    units = _build_units(wd, manifest, wd.en_md.read_text(encoding="utf-8").splitlines())
    cache = cache.upgraded(units)  # 옛 v2·v1 캐시면 쓸 수 있는 항목을 새 해시로 옮겨 둔다
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
                    _remember(new_cache, u, kept, gloss)
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
                _remember(new_cache, u, translated, gloss)  # 실패(passthrough)는 캐시하지 않음 → 재실행 시 그 섹션만 재시도
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
    merged = {h: t for h, t in {**cache.chunks, **new_cache}.items() if h in current}
    wd.cache_json.write_text(
        json.dumps({"key": cache_key, "scheme": _SCHEME, "chunks": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "chunks": total,
        "glossary": len(gloss.entries),
        **counts,
        "failures": {sid: ", ".join(reasons) for sid, reasons in sid_reasons.items()},
        "cost_usd": provider.cost(),
    }
