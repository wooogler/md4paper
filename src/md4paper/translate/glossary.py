"""용어집 — 번역 전에 자동 생성하고, 사용자가 번역어를 검토·수정한 뒤 번역에 적용한다.

glossary.yaml은 편집 가능한 아티팩트다: 지금은 $EDITOR, 이후 웹 UI가 "자동 생성 → 표시 →
번역어 수정 → 번역"의 검토 지점으로 삼는다. 번역 단계는 이 파일을 소비한다.

**컬렉션 용어집**(`CONFIG_DIR/glossary/<컬렉션 id>.yaml`, 같은 YAML 형식): 논문마다 따로 만든
용어집은 같은 컬렉션 안에서도 번역어·policy가 어긋난다(실데이터: 80편 컬렉션에서 2편 이상에 나온
137개 용어 중 81개가 어긋남). 컬렉션 용어집은 그 어긋남을 보고 일부러 고른 목록이라, 번역 때
논문 용어집 위에 **주 컬렉션**(소속 첫 항목)의 것을 덮어쓴다 — 같은 용어(`term_key`)면 컬렉션이
이긴다(`effective`). 논문 용어집 파일 자체는 건드리지 않는다(뷰어 표·자동 생성은 논문 것 그대로).
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.llm.base import Provider
from md4paper.workdir import WorkDir

_SYSTEM = """You build a glossary for translating an academic paper into Korean.
From the title and the excerpt below (abstract + introduction in full, plus section titles), pick this
paper's key technical terms and propose a Korean rendering for each. Each entry:
- term: the English term (verbatim)
- korean: its Korean translation (or transliteration)
- policy: translate | transliterate | keep | 병기-first-use  (병기 = show the English alongside on first use)
Only terms that must be translated consistently in this paper's context, at most 25. Exclude common everyday words.
Terms are case-insensitive — never list one twice in different capitalizations (e.g. both "Living Test Set" and
"living test set"); pick one form. The `korean` field must be written in Korean."""

_POLICY_LINE = "# policy: translate(의미번역) | transliterate(음역) | keep(원어유지) | 병기-first-use(첫등장시 병기)\n"
_HEADER = "# 용어집 — 번역 전에 검토/수정하세요 (웹 UI에서도 편집 가능).\n" + _POLICY_LINE
_COLLECTION_HEADER = (
    "# 컬렉션 용어집 — 이 컬렉션의 모든 논문 번역에 논문 용어집보다 우선 적용됩니다.\n" + _POLICY_LINE
)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


def term_key(term: str) -> str:
    """중복 판정 키 — 대소문자와 앞뒤/중복 공백 차이를 흡수한다.

    "Data-Prompt Co-Evolution"과 "data-prompt co-evolution"은 논문 안에서(제목·문장 첫머리 등)
    표기만 다른 같은 용어다. 둘을 따로 두면 번역어·policy가 서로 어긋난 채 프롬프트에 들어간다.
    """
    return re.sub(r"\s+", " ", term or "").strip().casefold()


def dedupe(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """대소문자만 다른 항목을 하나로 합친다 — 먼저 나온 표기·설정을 남기고,

    앞선 항목의 번역어가 비어 있으면 뒤 항목의 번역어·policy로 채운다."""
    at: dict[str, int] = {}
    kept: list[GlossaryEntry] = []
    for e in entries:
        key = term_key(e.term)
        if not key:
            continue
        if key not in at:
            at[key] = len(kept)
            kept.append(e.model_copy())
        elif not kept[at[key]].korean.strip() and e.korean.strip():
            kept[at[key]].korean, kept[at[key]].policy = e.korean, e.policy
    return kept


def to_yaml(gloss: GlossaryList, header: str = _HEADER) -> str:
    data = [{"term": e.term, "korean": e.korean, "policy": e.policy} for e in dedupe(gloss.entries)]
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return header + stream.getvalue()


def dump(gloss: GlossaryList, path: Path, header: str = _HEADER) -> None:
    """용어집을 경로에 쓴다 — 논문(`save`)·컬렉션(`save_collection`) 용어집이 같은 형식을 쓰도록."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(gloss, header), encoding="utf-8")


def load_path(path: Path) -> GlossaryList:
    data = _yaml().load(path.read_text(encoding="utf-8")) or []
    entries = [
        GlossaryEntry(term=str(d["term"]), korean=str(d.get("korean", "")), policy=d.get("policy", "translate"))
        for d in data
        if d.get("term")
    ]
    return GlossaryList(entries=dedupe(entries))  # 예전에 저장된 대소문자 중복도 열 때 합쳐진다


def save(gloss: GlossaryList, wd: WorkDir) -> None:
    """논문 용어집 저장 — 쓰기 전에 그 논문의 번역 캐시를 v3로 옮겨 둔다(`apply.migrate_cache`).

    옛(v2·v1) 캐시 키는 논문 용어집을 품고 있어, 고친 뒤에는 안 맞아 그 논문이 통째로 다시 번역된다.
    여기서 옮기면 뷰어 자동 저장·용어 추출·CLI 편집·용어집 재생성이 모두 저장 전에 이행된다. 이행이
    실패해도 용어집 저장은 해야 하므로 예외는 삼킨다(그때는 예전처럼 다음 번역이 전부 다시 돈다).
    """
    try:
        from md4paper.translate import apply  # 순환 import 피하기 (apply → glossary)

        apply.migrate_cache(wd)
    except Exception:  # noqa: BLE001 — 캐시 이행은 덤이다; 용어집 저장을 막으면 안 된다
        pass
    dump(gloss, wd.glossary_yaml)


def load(wd: WorkDir) -> GlossaryList:
    return load_path(wd.glossary_yaml)


_SYSTEM_ADD = """You pick technical terms to ADD to a glossary for translating an academic paper into Korean.
From the body excerpt below, choose key technical terms, but EXCLUDE any already in the 'existing terms' list —
only new ones. Each entry:
- term: the English term
- korean: its Korean translation (or transliteration)
- policy: translate | transliterate | keep | 병기-first-use
Only newly added terms, at most 20. Exclude common everyday words. Terms are case-insensitive — a term whose
capitalization differs from an existing one is NOT new. The `korean` field must be in Korean."""


def generate(provider: Provider, title: str, source: str, *, max_tokens: int = 4096) -> GlossaryList:
    """LLM으로 용어집 초안 생성. source=초록·서론·섹션 제목·첫 문단 발췌."""
    user = f"Title: {title}\n\nExcerpt:\n{source}"
    result = provider.parse(_SYSTEM, user, GlossaryList, max_tokens=max_tokens)
    return GlossaryList(entries=dedupe(result.entries))


def extend(
    provider: Provider, title: str, text: str, existing_terms: list[str], *, max_tokens: int = 4096
) -> GlossaryList:
    """본문 발췌에서 '기존 용어에 없는' 새 용어만 뽑는다 (중복은 결정론적으로 한 번 더 거른다)."""
    have = ", ".join(existing_terms) or "(none)"
    user = f"Title: {title}\n\nExisting terms: {have}\n\nBody excerpt:\n{text}"
    result = provider.parse(_SYSTEM_ADD, user, GlossaryList, max_tokens=max_tokens)
    have = {term_key(t) for t in existing_terms}
    fresh = [e for e in result.entries if term_key(e.term) not in have]
    return GlossaryList(entries=dedupe(fresh))  # 응답 안에서 대소문자만 다른 중복도 제거


def ensure(
    provider: Provider, wd: WorkDir, title: str, source: str, *, regenerate: bool = False
) -> GlossaryList:
    """glossary.yaml이 있으면 그걸(사용자 수정 반영), 없으면 생성해 저장."""
    if wd.glossary_yaml.exists() and not regenerate:
        return load(wd)
    gloss = generate(provider, title, source)
    save(gloss, wd)
    return gloss


# --- 컬렉션 용어집 -------------------------------------------------------------

def collection_dir() -> Path:
    """컬렉션 용어집 폴더 — 부를 때마다 `config.CONFIG_DIR`을 읽는다.

    CONFIG_DIR은 환경변수(MD4PAPER_CONFIG_DIR)로 정해지고 테스트가 monkeypatch로 바꾼다. 모듈
    상수로 굳히면(projects.PROJECTS_PATH처럼) 바꾼 값이 여기 닿지 않는다.
    """
    from md4paper import config

    return Path(config.CONFIG_DIR) / "glossary"


def collection_path(pid: str) -> Path:
    return collection_dir() / f"{pid}.yaml"


def load_collection(pid: str) -> GlossaryList:
    """컬렉션 용어집 — 파일이 없거나(아직 안 만든 컬렉션) id가 비었으면 빈 목록."""
    if not pid:
        return GlossaryList(entries=[])
    path = collection_path(pid)
    if not path.exists():
        return GlossaryList(entries=[])
    return load_path(path)


def save_collection(pid: str, gloss: GlossaryList) -> None:
    dump(gloss, collection_path(pid), header=_COLLECTION_HEADER)


def delete_collection(pid: str) -> bool:
    """컬렉션 용어집 파일을 지운다(컬렉션을 지울 때). 반환: 지운 파일이 있었는지 — 없으면 조용히."""
    if not pid:
        return False
    try:
        collection_path(pid).unlink()
        return True
    except FileNotFoundError:
        return False


def effective(wd: WorkDir, pid: str | None = None) -> tuple[GlossaryList, dict[str, str]]:
    """번역에 실제로 쓰는 용어집 = 논문 용어집 위에 주 컬렉션 용어집을 덮어쓴 것.

    같은 `term_key`면 컬렉션 항목이 논문 항목의 **자리**를 차지한다 — 순서는 논문 용어집 순, 컬렉션에만
    있는 항목은 뒤에 컬렉션 순. 컬렉션이 이기는 이유: 컬렉션 용어집은 논문들 사이의 어긋남을 보고
    일부러 고른 '통일안'이다(논문이 이기면 이미 어긋난 용어에 아무 효과가 없다). 대가로 논문별 예외는
    둘 수 없다. 다중 소속이면 주 컬렉션(소속 첫 항목)만 — 논문에 걸리는 다른 설정과 같은 규칙.

    pid가 None이면 `library.project_of(wd)`(미분류면 컬렉션 없음). 논문 용어집 파일이 없으면 빈 것으로.
    둘째 반환값: term_key → "paper" | "collection"(어느 쪽 항목이 쓰였는지; 뷰어 표시용).
    """
    if pid is None:
        from md4paper import library  # 순환 import 피하기 (library → … → translate)

        pid = library.project_of(wd)
    paper = load(wd).entries if wd.glossary_yaml.exists() else []
    coll = load_collection(pid).entries
    over = {term_key(e.term): e for e in coll}
    out: list[GlossaryEntry] = []
    origin: dict[str, str] = {}
    for e in paper:
        key = term_key(e.term)
        if key in origin:  # load가 dedupe하지만, 넘겨받은 목록이 아닐 때를 대비해 한 번 더
            continue
        if key in over:
            out.append(over[key].model_copy())
            origin[key] = "collection"
        else:
            out.append(e.model_copy())
            origin[key] = "paper"
    for key, e in over.items():
        if key not in origin:
            out.append(e.model_copy())
            origin[key] = "collection"
    return GlossaryList(entries=out), origin


@dataclass
class Variant:
    """한 용어의 번역 변이 — (번역어, policy) 조합과 그렇게 쓴 논문들."""

    korean: str
    policy: str
    papers: list = field(default_factory=list)


@dataclass
class Conflict:
    """여러 논문에서 번역어·policy가 어긋난 용어. term은 가장 많이 쓰인 표기, variants는 논문 수 많은 순."""

    key: str
    term: str
    variants: list[Variant] = field(default_factory=list)

    @property
    def paper_count(self) -> int:
        return sum(len(v.papers) for v in self.variants)


def _group(by_paper: Mapping[Hashable, list[GlossaryEntry]]) -> dict[str, tuple[Counter, dict]]:
    """term_key → (표기 빈도, {(번역어, policy): [논문…]}). 한 논문 안의 같은 용어는 첫 항목만 센다."""
    groups: dict[str, tuple[Counter, dict]] = {}
    for paper, entries in by_paper.items():
        seen: set[str] = set()
        for e in entries:
            key = term_key(e.term)
            if not key or key in seen:
                continue
            seen.add(key)
            forms, variants = groups.setdefault(key, (Counter(), {}))
            forms[e.term.strip()] += 1
            variants.setdefault((e.korean.strip(), e.policy), []).append(paper)
    return groups


def conflicts(by_paper: Mapping[Hashable, list[GlossaryEntry]]) -> list[Conflict]:
    """논문 용어집들에서 번역어·policy가 어긋난 용어 — 컬렉션 용어집으로 통일할 후보.

    입력은 {논문 식별자: 항목들}(식별자는 token·root Path 무엇이든; 결과의 papers에 그대로 담긴다).
    용어는 `term_key`로 묶는다(대소문자·공백만 다른 표기는 같은 용어). (korean.strip(), policy)
    조합이 둘 이상인 용어만 — policy만 달라도(번역 vs 병기) 결과 글자가 달라지므로 어긋남이다.
    띄어쓰기만 다른 번역어(조직기억/조직 기억)도 다른 변이로 센다(독자에게는 다르게 보인다).
    변이는 논문 수 많은 순, 용어는 관련 논문 수(합) 많은 순(같으면 처음 나온 순).
    """
    out: list[Conflict] = []
    for key, (forms, variants) in _group(by_paper).items():
        if len(variants) < 2:
            continue
        vs = [Variant(korean=k, policy=p, papers=list(ps)) for (k, p), ps in variants.items()]
        vs.sort(key=lambda v: -len(v.papers))  # 안정 정렬 — 같은 편수면 처음 나온 순
        out.append(Conflict(key=key, term=forms.most_common(1)[0][0], variants=vs))
    out.sort(key=lambda c: -c.paper_count)
    return out


def shared(by_paper: Mapping[Hashable, list[GlossaryEntry]], min_papers: int = 2) -> list[tuple]:
    """어긋나지 않았지만 min_papers편 이상이 같은 번역어·policy로 쓴 용어 → [(term, korean, policy, papers)].

    컬렉션 용어집에 올려 둘 후보다 — 올려 두면 새로 들어오는 논문도 그 번역어를 따른다. 논문 수 많은 순.
    """
    out: list[tuple] = []
    for _key, (forms, variants) in _group(by_paper).items():
        if len(variants) != 1:
            continue
        (korean, policy), papers = next(iter(variants.items()))
        if len(papers) >= min_papers:
            out.append((forms.most_common(1)[0][0], korean, policy, list(papers)))
    out.sort(key=lambda t: -len(t[3]))
    return out


def members(pid: str, workspace: Path) -> list[dict]:
    """작업 폴더에서 **주 컬렉션이 이 컬렉션인** 논문 행(recent_workdirs 행; 숨긴 논문은 빼고).

    둘째 이후 자리로만 속한 논문은 뺀다: 번역에는 주 컬렉션의 용어집만 적용되므로(`effective`), 그런
    논문을 충돌·영향·통일 대상에 넣으면 이 컬렉션 용어집으로 통일해도 실제로는 적용되지 않는다. 충돌
    집계(`collection_entries_by_paper`)·영향 미리보기(`impact`)와 그 위의 CLI·에이전트 tool이 모두
    이것을 쓴다.
    """
    from md4paper import projects
    from md4paper.workdir import recent_workdirs

    def primary(row: dict) -> str:
        ids = projects.normalize_many(row["projects"])
        return ids[0] if ids else ""

    return [row for row in recent_workdirs(Path(workspace), limit=100_000) if primary(row) == pid]


def collection_entries_by_paper(pid: str, workspace: Path) -> dict[Path, list[GlossaryEntry]]:
    """주 컬렉션이 이 컬렉션인 논문들의 논문 용어집 — {논문 root(Path): 항목들}. 용어집 없는 논문은 뺀다.

    키는 token이 아니라 root Path다: token 규칙(경로 sha1 앞 12자리)은 ui 쪽(`app.wd_token`,
    `agent.tools.token_of`)에 있고 이 모듈이 ui를 import하면 안 된다 — 호출자가 token으로 바꾼다.
    다중 소속이면 주 컬렉션(소속 첫 항목)이 이 컬렉션일 때만 센다(`members`). 숨긴 논문은 목록에서처럼 뺀다.
    """
    out: dict[Path, list[GlossaryEntry]] = {}
    for row in members(pid, workspace):
        wd = WorkDir(row["root"])
        if wd.glossary_yaml.exists():
            out[wd.root] = load(wd).entries
    return out


_CHARS_PER_TOKEN = 3.5  # 영어 원문 글자 → 입력 토큰 (대략)
_OUT_RATIO = 1.3        # 한국어 출력 토큰 ≈ 입력 × 1.3 (대략)


def impact(pid: str, workspace: Path, *, model_for: Callable[[WorkDir], str] | None = None) -> list[dict]:
    """컬렉션 용어집이 바뀌면 어느 논문의 어느 섹션이 다시 번역되고 대략 얼마가 드는지 (LLM 호출 없음).

    주 컬렉션이 이 컬렉션인 논문(`members`) 중 번역(ko.md)이 있는 것마다 `apply.retranslate_units`
    (effective 용어집 반영)로 다시 번역될 유닛을 찾아 그 원문 글자 수 → 토큰(입력 ≈ 글자/3.5, 출력 ≈ 입력×1.3) → `PRICING`으로 비용을 어림한다.
    시스템 프롬프트·재시도·프로바이더 캐시는 셈하지 않는다 — 승인 카드·대화상자가 '대략'을 보여 주는 용도.
    원문이 바뀐 섹션도 같은 판정에 걸리므로 함께 잡힌다(다음 번역에서 어차피 같이 다시 돈다). 보관된
    번역(캐시)을 통째로 못 쓰는 논문(캐시 파일 없음, 지난 번역 뒤 논문 용어집·모델·문체가 바뀐 옛 캐시)은
    `full=True` — stale은 번역 대상 섹션 전부, chars는 그 유닛 전체(다음 실행이 처음부터 다시 번역한다).

    모델: `model_for(wd)`를 주면 그것, 아니면 그 논문을 마지막으로 번역한 모델(`last_translate_model`,
    기록이 없으면 input_hash로 되찾은 `recover_translate_model`), 그것도 없으면 전역 기본. 반환 항목: {root, title, stale(섹션 id, 문서 순), units, chars, model,
    est_cost_usd, full}. 다시 번역될 게 없는 논문은 뺀다.
    """
    from md4paper import config, pipeline
    from md4paper.llm.base import Usage, cost_usd
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply
    from md4paper.workdir import paper_title

    out: list[dict] = []
    for row in members(pid, workspace):
        wd = WorkDir(row["root"])
        if not wd.ko_md.exists():
            continue
        try:
            manifest = manifest_io.load(wd)
        except (OSError, ValueError):
            continue
        units, full = apply.retranslate_units(wd, manifest)
        if not units:
            continue
        sids: list[str] = []
        for u in units:
            sids.extend(s for s in u["sids"] if s not in sids)
        chars = sum(len(u["source"]) for u in units)
        if model_for is not None:
            model = model_for(wd)
        else:
            last = pipeline.last_translate_model(wd) or pipeline.recover_translate_model(wd, manifest)
            model = last[1] if last else config.resolve_model(config.resolve_provider())
        inp = int(chars / _CHARS_PER_TOKEN)
        cost = cost_usd(model, Usage(input_tokens=inp, output_tokens=int(inp * _OUT_RATIO)))
        out.append({"root": wd.root, "title": paper_title(wd.root), "stale": sids, "units": len(units),
                    "chars": chars, "model": model, "est_cost_usd": cost, "full": full})
    return out
