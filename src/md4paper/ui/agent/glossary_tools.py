"""컬렉션 용어집 tool — 컬렉션 안에서 어긋난 번역어를 읽고, 통일안을 컬렉션 용어집에 쓴다.

같은 컬렉션의 논문들도 용어집을 따로 만들어 번역어·policy가 어긋난다. 에이전트가
`glossary_conflicts`로 어긋난 용어를 읽고 답에서 통일안을 제안한 뒤 `write_collection_glossary`로
한 번에 쓴다(사용자 승인 뒤). 컬렉션 용어집은 번역 때 소속 논문의 용어집보다 우선한다
(`translate.glossary.effective`) — 그래서 쓰고 나면 그 용어가 든 섹션이 낡고, 다음 번역 실행 때
다시 번역된다. `translation_impact`가 그 범위와 대략 비용을 미리 센다(LLM 호출 없음).

tools.py의 규칙을 그대로 따른다(안내 문자열을 돌려주고 예외를 던지지 않는다, 파일 I/O는
`asyncio.to_thread`, 목록 결과는 `LIST_MAX` 상한, 논문은 `deps.register`로 token을 받아 적는다).
쓰기의 대상은 **인자로 받은 컬렉션**이지 화면의 논문이 아니므로 `_write_target`·승인 metadata는
쓰지 않는다. tools.py가 맨 아래에서 이 모듈을 import해 `TOOLS`에 잇는다 — 그때는 아래에서
가져오는 이름들이 이미 정의돼 있어 순환 import가 풀린다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import RunContext, Tool

from md4paper.ui.agent import tools
from md4paper.ui.agent.tools import LIST_MAX, Deps, _find_project, _refresh
from md4paper.workdir import WorkDir

POLICIES = ("translate", "transliterate", "keep", "병기-first-use")
CONFLICT_LIMIT_DEFAULT = 40
CONFLICT_LIMIT_MAX = 200
PAPERS_PER_VARIANT = 4   # 변이마다 적는 논문 token 수
WRITE_MAX = 500          # write_collection_glossary 한 번에 받는 항목 수 상한
# 컬렉션 단위 재번역은 섹션 id로 한 편씩 도는 retranslate_sections와 모양이 안 맞는다 — 여러 편이면 홈의
# 일괄 번역(전체 선택 → 번역) 또는 컬렉션 용어집 대화상자의 '낡은 섹션 재번역'으로 안내한다.
RETRANSLATE_HINT = ("재번역은 사용자가 원할 때 합니다 — 홈에서 컬렉션을 '전체 선택' 뒤 '번역', 또는 컬렉션 ⋯ 메뉴 "
                    "'용어집…'의 '낡은 섹션 재번역'(낡은 논문만 골라 띄운다). 한 편의 몇 섹션만이면 retranslate_sections.")


# --- 공통 ---------------------------------------------------------------


async def _collection(deps: Deps, key: str) -> tuple[str, str, Path] | str:
    """컬렉션 id·이름 → (id, 이름, 작업 폴더). 없으면 모델에게 보일 안내 문자열."""
    from md4paper import config

    proj = await asyncio.to_thread(_find_project, key)
    if proj is None:
        return f"'{key}' 컬렉션이 없습니다. list_projects로 id를 확인하세요."
    ws = Path(deps.workspace or config.resolve_workspace())
    return proj["id"], proj["name"], ws


def _korean(korean: str, policy: str) -> str:
    k = (korean or "").strip()
    if k:
        return k
    return "(원어 유지)" if policy == "keep" else "(번역어 없음)"


def _papers(deps: Deps, roots: list) -> str:
    """논문 root들 → `[tok1, tok2 …]` (앞 4편만 token을 등록해 적고 나머지는 '외 n편')."""
    toks = [deps.register(WorkDir(Path(r))) for r in roots[:PAPERS_PER_VARIANT]]
    rest = len(roots) - len(toks)
    return "[" + ", ".join(toks) + (f" 외 {rest}편" if rest > 0 else "") + "]"


def _variant(deps: Deps, korean: str, policy: str, roots: list) -> str:
    return f"{_korean(korean, policy)}({policy}) ×{len(roots)}편 {_papers(deps, roots)}"


def _usd(x: float) -> str:
    return f"${x:.2f}" if x >= 0.01 else "$0.01 미만"


def _impact_sums(rows: list[dict]) -> tuple[int, int, float, int]:
    """impact 표 → (편수, 다시 번역될 섹션 수, 대략 비용 합, 그중 전체 재번역(캐시 못 씀) 편수)."""
    return (len(rows), sum(len(r["stale"]) for r in rows), sum(r["est_cost_usd"] for r in rows),
            sum(1 for r in rows if r.get("full")))


def _full_note(full: int) -> str:
    return f" (그중 {full}편은 전체 재번역)" if full else ""


# --- 읽기 ---------------------------------------------------------------


async def glossary_conflicts(ctx: RunContext[Deps], collection: str, offset: int = 0,
                             limit: int = CONFLICT_LIMIT_DEFAULT, shared: bool = False) -> str:
    """컬렉션 소속 논문들의 용어집을 모아, 논문마다 번역어·policy가 **어긋난 용어**를 돌려준다.

    컬렉션 용어 통일의 첫 단계다. 용어는 대소문자·공백 차이를 무시하고 묶는다. 각 줄은
    `- 용어: 번역어A(policy) ×3편 [token, …] | 번역어B(policy) ×1편 [token]` — 변이는 쓴 논문 수
    많은 순, 용어는 관련 논문 수 많은 순이다. token은 list_papers와 같은 논문 token이다(search_paper
    등으로 그 논문의 문맥을 읽을 수 있다). 컬렉션 용어집에 이미 정해 둔 용어면 줄 끝에
    `→ 컬렉션: 번역어(policy)`가 붙는다. 결과가 길면 잘리고 꼬리에 다음 offset이 적힌다.

    Args:
        collection: 컬렉션 id 또는 이름 (list_projects에서).
        offset: 몇 번째 용어부터 (0부터, 이어 읽을 때 꼬리의 값을 넣는다).
        limit: 최대 용어 수 (기본 40, 최대 200). 글자 상한에 걸리면 그보다 적게 보인다.
        shared: True면 어긋난 용어 대신, 둘 이상의 논문이 **같은 번역어로** 쓰는 용어를 돌려준다 —
            컬렉션 용어집에 올려 두면 새로 들어오는 논문도 그 번역어를 따르게 할 후보.
    """
    from md4paper.translate import glossary

    deps = ctx.deps
    got = await _collection(deps, collection)
    if isinstance(got, str):
        return got
    pid, name, ws = got
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or CONFLICT_LIMIT_DEFAULT), CONFLICT_LIMIT_MAX))

    def work() -> tuple[int, dict, list, list, dict]:
        members = len(glossary.members(pid, ws))
        by_paper = glossary.collection_entries_by_paper(pid, ws)
        decided = {glossary.term_key(e.term): e for e in glossary.load_collection(pid).entries}
        return (members, by_paper, glossary.conflicts(by_paper),
                glossary.shared(by_paper) if shared else [], decided)

    try:
        members, by_paper, found, common, decided = await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001 — 깨진 용어집 하나 때문에 run이 끊기면 안 된다
        return f"컬렉션 '{name}'의 용어집을 읽지 못했습니다: {e}"
    if not members:
        return f"컬렉션 '{name}'에 논문이 없습니다."
    head = (f"컬렉션 '{name}' — 논문 {members}편(용어집 있는 {len(by_paper)}편), "
            f"어긋난 용어 {len(found)}개, 컬렉션 용어집 항목 {len(decided)}개")
    if not by_paper:
        return head + "\n\n용어집이 있는 논문이 없습니다 (번역을 한 번 돌리면 논문마다 만들어진다)."

    def mark(term: str) -> str:
        e = decided.get(glossary.term_key(term))
        return f" → 컬렉션: {_korean(e.korean, e.policy)}({e.policy})" if e else ""

    if shared:
        head += f", 여러 논문이 같은 번역어로 쓰는 용어 {len(common)}개"
        items = [(t, f"- {t}: {_variant(deps, k, p, ps)}{mark(t)}") for t, k, p, ps in common]
        what, empty = "같은 번역어로 쓰는 용어", "둘 이상의 논문이 같은 번역어로 쓰는 용어가 없습니다."
    else:
        items = [(c.term, c) for c in found]
        what, empty = "어긋난 용어", "어긋난 용어가 없습니다 — 소속 논문들의 번역어·policy가 모두 같습니다."
    if not items:
        return head + "\n\n" + empty
    if offset >= len(items):
        return head + f"\n\noffset={offset}이 범위 밖입니다 ({what}는 {len(items)}개)."

    lines: list[str] = []
    size = 0
    for _term, it in items[offset:offset + limit]:
        if isinstance(it, str):
            line = it
        else:
            vs = " | ".join(_variant(deps, v.korean, v.policy, v.papers) for v in it.variants)
            line = f"- {it.term}: {vs}{mark(it.term)}"
        if size + len(line) + 1 > LIST_MAX - 600 and lines:   # 600 = 머리·꼬리 자리
            break
        lines.append(line)
        size += len(line) + 1
    end = offset + len(lines)
    head += f"\n{what} {offset + 1}–{end}번째 (관련 논문 수 많은 순):"
    tail = ""
    if end < len(items):
        tail = f"\n\n(남은 {len(items) - end}개 — 다음: offset={end})"
    return tools._cap(head + "\n" + "\n".join(lines) + tail, LIST_MAX)


async def read_collection_glossary(ctx: RunContext[Deps], collection: str) -> str:
    """컬렉션 용어집(이 컬렉션의 모든 논문 번역에 논문 용어집보다 우선 적용되는 목록)을 돌려준다.

    각 줄은 `- 용어 → 번역어 (policy)`.

    Args:
        collection: 컬렉션 id 또는 이름 (list_projects에서).
    """
    from md4paper.translate import glossary

    got = await _collection(ctx.deps, collection)
    if isinstance(got, str):
        return got
    pid, name, _ws = got
    try:
        entries = (await asyncio.to_thread(glossary.load_collection, pid)).entries
    except Exception as e:  # noqa: BLE001
        return f"컬렉션 '{name}'의 용어집을 읽지 못했습니다: {e}"
    if not entries:
        return f"컬렉션 '{name}'의 용어집은 비어 있습니다."
    lines = [f"- {e.term} → {_korean(e.korean, e.policy)} ({e.policy})" for e in entries]
    head = (f"컬렉션 '{name}' 용어집 {len(entries)}개 (소속 논문의 번역에 논문 용어집보다 "
            "우선 적용):\n")
    return tools._cap(head + "\n".join(lines), LIST_MAX)


async def translation_impact(ctx: RunContext[Deps], collection: str) -> str:
    """지금 컬렉션 용어집을 기준으로 **다음 번역 실행 때 다시 번역될 곳**과 대략 비용을 돌려준다.

    번역(ko.md)이 있는 소속 논문마다 낡은 섹션 수·원문 글자 수·모델(그 논문을 마지막으로 번역한
    모델)·대략 비용을 적고, 끝에 합계를 적는다. LLM을 부르지 않는 어림이다(시스템 프롬프트·재시도는
    셈하지 않음). 원문이 바뀐 섹션도 함께 잡힌다(다음 번역에서 어차피 같이 다시 돈다). 보관된
    번역(캐시)을 통째로 못 쓰는 논문(지난 번역 뒤 논문 용어집·모델·문체가 바뀜, 캐시 파일 없음)은
    '전체 재번역(캐시 못 씀)'으로 적는다 — 다음 실행이 처음부터 다시 번역해 비용이 크다.
    실제로 다시 번역하지는 않는다.

    Args:
        collection: 컬렉션 id 또는 이름 (list_projects에서).
    """
    from md4paper.translate import glossary

    deps = ctx.deps
    got = await _collection(deps, collection)
    if isinstance(got, str):
        return got
    pid, name, ws = got
    try:
        rows = await asyncio.to_thread(glossary.impact, pid, ws)
    except Exception as e:  # noqa: BLE001
        return f"컬렉션 '{name}'의 번역 영향을 세지 못했습니다: {e}"
    if not rows:
        return (f"컬렉션 '{name}': 지금 컬렉션 용어집을 기준으로 다시 번역될 섹션이 없습니다 "
                "(번역된 소속 논문이 모두 최신).")
    lines: list[str] = []
    size = 0
    for r in rows:
        tok = deps.register(WorkDir(Path(r["root"])))
        title = " ".join(str(r["title"] or "").split())
        title = title if len(title) <= tools.TITLE_CLIP else title[:tools.TITLE_CLIP].rstrip() + "…"
        if r.get("full"):
            what = f"전체 재번역(캐시 못 씀, 섹션 {len(r['stale'])}개, 약 {r['chars']:,}자)"
        else:
            what = f"낡은 섹션 {len(r['stale'])}개(유닛 {r['units']}, 약 {r['chars']:,}자)"
        line = f"- token {tok}: {title} — {what}, 모델 {r['model']}, ≈ {_usd(r['est_cost_usd'])}"
        if size + len(line) + 1 > LIST_MAX - 600 and lines:
            lines.append(f"…(상한에 걸려 {len(rows) - len(lines)}편 생략 — 합계에는 들어 있음)")
            break
        lines.append(line)
        size += len(line) + 1
    n, secs, cost, full = _impact_sums(rows)
    head = (f"컬렉션 '{name}' — 지금 컬렉션 용어집을 기준으로 다음 번역 실행 때 다시 번역될 곳 "
            "(LLM 없이 어림한 대략 비용):\n")
    total = f"\n합계: 논문 {n}편{_full_note(full)}, 낡은 섹션 {secs}개, 대략 {_usd(cost)}"
    return tools._cap(head + "\n".join(lines) + total, LIST_MAX)


# --- 쓰기 (사용자 승인 뒤 실행) ----------------------------------------


def _clean_entries(entries: object) -> tuple[list, list[str]]:
    """모델이 준 항목들 → (GlossaryEntry 목록, 건너뛴 까닭들). 같은 용어가 거듭 오면 뒤의 것이 이긴다."""
    from md4paper.ir import GlossaryEntry
    from md4paper.translate import glossary

    if not isinstance(entries, list):
        return [], ["entries가 목록이 아닙니다"]
    kept: dict[str, GlossaryEntry] = {}
    skipped: list[str] = []
    for i, d in enumerate(entries[:WRITE_MAX]):
        if not isinstance(d, dict):
            skipped.append(f"{i + 1}번째 항목: 객체가 아님")
            continue
        term = " ".join(str(d.get("term") or "").split())
        korean = " ".join(str(d.get("korean") or "").split())
        policy = str(d.get("policy") or "translate").strip()
        if not term:
            skipped.append(f"{i + 1}번째 항목: term이 비어 있음")
        elif policy not in POLICIES:
            skipped.append(f"{term}: policy '{policy}'는 {', '.join(POLICIES)} 중 하나여야 함")
        elif not korean and policy != "keep":
            skipped.append(f"{term}: korean이 비어 있음 (keep이 아니면 번역어가 필요)")
        else:
            key = glossary.term_key(term)
            kept.pop(key, None)    # 뒤의 것이 이기되 자리도 뒤로
            kept[key] = GlossaryEntry(term=term, korean=korean, policy=policy)
    if len(entries) > WRITE_MAX:
        skipped.append(f"{WRITE_MAX}개를 넘는 {len(entries) - WRITE_MAX}개는 받지 않음")
    return list(kept.values()), skipped


async def write_collection_glossary(ctx: RunContext[Deps], collection: str, entries: list[dict],
                                    mode: str = "merge") -> str:
    """컬렉션 용어집에 용어를 쓴다. **사용자가 승인해야 실행된다.**

    통일안 전체를 **한 번에** 넘긴다(항목마다 따로 부르지 않는다). 컬렉션 용어집은 소속 논문의
    번역에 논문 용어집보다 우선하므로, 쓰고 나면 그 용어가 든 섹션이 다음 번역 실행 때 다시
    번역된다(결과에 영향 범위·대략 비용이 적힌다). 이 tool은 다시 번역하지 않는다.

    Args:
        collection: 컬렉션 id 또는 이름 (list_projects에서).
        entries: 쓸 항목들 — 각각 {"term": 영어 용어, "korean": 번역어, "policy": 정책}.
            policy는 "translate"(의미 번역, 기본) | "transliterate"(음역) | "keep"(원어 유지 —
            korean은 비워도 됨) | "병기-first-use"(첫 등장 때 원어 병기). 잘못된 항목은 건너뛴다.
        mode: "merge"(기본 — 기존 컬렉션 용어집에서 같은 용어는 덮어쓰고 나머지는 덧붙임) 또는
            "replace"(컬렉션 용어집을 통째로 바꿈 — 사용자가 그렇게 해 달라고 분명히 말했을 때만).
    """
    from md4paper.ir import GlossaryList
    from md4paper.translate import glossary

    if mode not in ("merge", "replace"):
        return f"mode는 'merge' 또는 'replace'여야 합니다 (받은 값: {mode!r})."
    deps = ctx.deps
    got = await _collection(deps, collection)
    if isinstance(got, str):
        return got
    pid, name, ws = got
    new, skipped = _clean_entries(entries)
    skip_note = ("\n건너뛴 항목 " + str(len(skipped)) + "개: " + "; ".join(skipped[:20])
                 + (" …" if len(skipped) > 20 else "")) if skipped else ""
    if not new:
        return f"쓸 수 있는 항목이 없어 컬렉션 '{name}' 용어집을 바꾸지 않았습니다.{skip_note}"

    def work() -> tuple[int, int, int, int]:
        old = glossary.load_collection(pid).entries
        before = {glossary.term_key(e.term): e for e in old}
        added = changed = same = 0
        for e in new:
            prev = before.get(glossary.term_key(e.term))
            if prev is None:
                added += 1
            elif (prev.korean.strip(), prev.policy) == (e.korean, e.policy):
                same += 1
            else:
                changed += 1
        if mode == "replace":
            out = list(new)
            keys = {glossary.term_key(e.term) for e in new}
            dropped = sum(1 for k in before if k not in keys)
        else:
            at = {glossary.term_key(e.term): i for i, e in enumerate(old)}
            out = [e.model_copy() for e in old]
            for e in new:
                key = glossary.term_key(e.term)
                if key in at:
                    out[at[key]] = e
                else:
                    at[key] = len(out)
                    out.append(e)
            dropped = 0
        glossary.save_collection(pid, GlossaryList(entries=out))
        return added, changed, same, dropped

    try:
        added, changed, same, dropped = await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001
        return f"컬렉션 '{name}' 용어집을 쓰지 못했습니다: {e}"
    _refresh(deps, "glossary", None, collection=pid)

    how = "통째로 바꿔 " if mode == "replace" else ""
    msg = (f"컬렉션 '{name}' 용어집에 {how}{len(new)}개 반영(새로 {added}, 바뀜 {changed}, "
           f"그대로 {same})")
    if dropped:
        msg += f", 빠짐 {dropped}"
    try:
        rows = await asyncio.to_thread(glossary.impact, pid, ws)
    except Exception:  # noqa: BLE001 — 영향 어림이 실패해도 쓰기는 끝났다
        rows = None
    if rows is None:
        msg += " → 영향: 다시 번역될 범위를 세지 못했습니다 (translation_impact로 다시 확인)."
    elif rows:
        n, secs, cost, full = _impact_sums(rows)
        some = f"그중 {full}편은 전체 재번역, " if full else ""
        msg += (f" → 영향: 다음 번역 실행 때 논문 {n}편의 섹션 {secs}개가 다시 번역됨 "
                f"({some}대략 {_usd(cost)}; 논문별은 translation_impact).")
    else:
        msg += " → 영향: 다시 번역될 섹션이 없습니다."
    return msg + skip_note + "\n" + RETRANSLATE_HINT


# --- 승인 카드 설명 -----------------------------------------------------


def _describe_write_glossary(args: dict) -> str:
    key = str(args.get("collection") or "")
    try:
        proj = _find_project(key)
    except Exception:  # noqa: BLE001 — 설명 한 줄 때문에 승인 카드가 깨지면 안 된다
        proj = None
    name = proj["name"] if proj else key
    items, _why = _clean_entries(args.get("entries"))   # 실제로 반영될 항목만 센다 (결과 문구와 같은 기준)
    how = "통째로 바꾸기" if args.get("mode") == "replace" else "merge"
    ex = ", ".join(f"{tools._short(e.term, 30)}→{tools._short(e.korean, 20) or '(원어 유지)'}"
                   for e in items[:3])
    more = f" 외 {len(items) - 3}개" if len(items) > 3 else ""
    out = f"'{name}' 컬렉션 용어집에 {len(items)}개 쓰기({how})"
    return out + (f" — 예: {ex}{more}" if ex else "")


tools.DESCRIBE.update({"write_collection_glossary": _describe_write_glossary})

TOOLS = [glossary_conflicts, read_collection_glossary, translation_impact,
         Tool(write_collection_glossary, requires_approval=True)]
