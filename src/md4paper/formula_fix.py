"""수식 한 개 손보기 — 캔버스에서 고른 수식을 원본 크롭과 대조하고, 다시 읽혀 문서에 반영한다.

수식 단계(`formulas.py`)는 논문 전체를 한 번에 읽는다. 그런데 사용자가 보기에 틀린 것은 대개
한두 개다 — 그 하나만 같은 그림으로 다시 물어보고, 마음에 들면 그 자리만 바꾸는 길이 여기 있다.

**수식 ↔ 원본을 잇는 열쇠는 레코드 id다** (`formula-NN`, formulas.json). 레코드마다 크롭 PNG
(`extract/images/formula-NN.png`)와 쪽 번호가 이미 있으므로, 화면의 수식이 어느 레코드인지만
알면 원본 그림은 그대로 꺼내 쓴다. 문제는 en.md에 id가 남지 않는다는 점이다 — 수식 단계가
`![formula-NN](…)` 줄을 `$$…$$`로 갈아 끼우면서 id가 사라진다. 그래서 **레코드가 만들어 냈을
블록 문자열**(`formulas._block`)을 본문에서 그대로 찾아 잇는다. 재변환 없이 기존 논문에도
그대로 통한다. 직접 편집 등으로 글자가 달라진 블록은 정확히 맞은 블록들 사이의 순서로 근사한다
(앞뒤 닻 사이에 남은 블록 수와 레코드 수가 같을 때만 — 어긋날 바엔 안 잇는다).

**반영은 원천(raw.md + formulas.json)에** 한다. en.md는 raw.md에서 다시 조립되는 산출물이라
거기만 고치면 다음 재조립 때 사라진다. 번역(ko.md)도 같은 블록을 그대로 품고 있으므로(번역은
수식을 보호한다) 같은 자리를 바꾸고, 번역 캐시의 키도 새 원문으로 옮겨 그 섹션이 괜히
'원문 바뀜'으로 잡히지 않게 한다. ko.md에서 못 찾으면 캐시를 옮기지 않는다 — 그러면 en.md가
바뀐 그 섹션이 저절로 '원문 바뀜'(stale_section_ids)으로 잡혀 다음 번역에서 다시 번역된다.
"""

from __future__ import annotations

import json
import re

from md4paper import formulas
from md4paper.workdir import WorkDir, hash_text

# 렌더 직전 수식 앞에 붙이는 빈 표식 — 글자가 없어 하이라이트 오프셋·찾기·문장 짝에 영향이 없다.
# 파일에는 절대 쓰지 않는다(화면용 문자열에만 붙인다).
MARK = '<span class="md4-fx" data-fx="{id}"></span>'
_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_LINE_RE = re.compile(r"^\$(?!\$)(.+?)(?<!\\)\$$", re.MULTILINE)
_IMAGE_LINE_RE = re.compile(r"^!\[(formula-\d+)\]\((?:[^)]*/)?formula-\d+\.png[^)]*\)$", re.MULTILINE)


def block(record: dict) -> str:
    """레코드가 마크다운에 남기는 블록 (`formulas._block`과 같은 규칙)."""
    return formulas._block(record)


def _blocks_in(md: str) -> list[tuple[int, int, str]]:
    """본문의 수식 후보 블록 (시작, 끝, 원문) — 문서 순서. 줄 맨 앞에서 시작하는 것만."""
    out: list[tuple[int, int, str]] = []
    for m in _DISPLAY_RE.finditer(md):
        if m.start() == 0 or md[m.start() - 1] == "\n":
            out.append((m.start(), m.end(), m.group(0)))
    for rx in (_INLINE_LINE_RE, _IMAGE_LINE_RE):
        for m in rx.finditer(md):
            if not any(s <= m.start() < e for s, e, _ in out):
                out.append((m.start(), m.end(), m.group(0)))
    return sorted(out)


def _image_id(text: str) -> str | None:
    m = _IMAGE_LINE_RE.match(text)
    return m.group(1) if m else None


def assign(md: str, records: list[dict]) -> dict[str, str]:
    """본문 블록 원문 → 레코드 id. 정확히 맞는 블록이 닻, 그 사이는 순서로 근사.

    코드·평문으로 읽힌 레코드는 수식이 아니라서 화면에 표식을 달 데가 없다 — 제외한다.
    """
    recs = [r for r in records if r.get("kind") not in ("code", "text")]
    want = {}
    for i, r in enumerate(recs):
        want.setdefault(block(r), i)
    found = _blocks_in(md)
    # 각 블록의 레코드 번호 (정확히 맞은 것만) — 같은 레코드를 두 번 쓰지 않는다
    hit: list[int | None] = []
    used: set[int] = set()
    for _s, _e, text in found:
        i = want.get(text)
        if i is None:
            img = _image_id(text)
            i = next((k for k, r in enumerate(recs) if r["id"] == img), None) if img else None
        if i is not None and i in used:
            i = None
        if i is not None:
            used.add(i)
        hit.append(i)
    # 닻 사이 근사: 블록 순서가 레코드 순서와 같다고 보고, 남은 개수가 같을 때만 짝짓는다
    anchors = [(-1, -1)] + [(b, i) for b, i in enumerate(hit) if i is not None] + [(len(hit), len(recs))]
    for (b0, i0), (b1, i1) in zip(anchors, anchors[1:]):
        gap_b = [b for b in range(b0 + 1, b1) if found[b][2].startswith("$$")]
        gap_r = [i for i in range(i0 + 1, i1) if i not in used and recs[i].get("kind") != "inline"]
        if gap_b and len(gap_b) == len(gap_r):
            for b, i in zip(gap_b, gap_r):
                hit[b] = i
    return {found[b][2]: recs[i]["id"] for b, i in enumerate(hit) if i is not None}


def tag(md: str, mapping: dict[str, str]) -> str:
    """화면용 — 잇게 된 블록 앞에 빈 표식을 붙인다 (블록 자체는 그대로)."""
    if not mapping:
        return md
    out: list[str] = []
    pos = 0
    for s, e, text in _blocks_in(md):
        fid = mapping.get(text)
        if fid is None:
            continue
        out.append(md[pos:s])
        out.append(MARK.format(id=fid) + text)
        pos = e
    out.append(md[pos:])
    return "".join(out)


def find(wd: WorkDir, fid: str) -> dict | None:
    return next((r for r in formulas.load(wd) if r.get("id") == fid), None)


# 사용자가 틀렸다고 본 판본을 같이 보여 준다 — 같은 그림을 같은 말로 다시 물으면 같은 답이 오기 쉽다
_REDO_NUDGE = ("\n\nA reader compared an earlier transcription of this image with the original and "
               "judged it WRONG:\n{old}\nRead the image again carefully and transcribe it correctly.")


def regenerate(wd: WorkDir, fid: str, provider) -> dict:  # noqa: ANN001 — Provider
    """그 수식 하나를 다시 읽힌다 — 문서는 건드리지 않고 후보만 돌려준다.

    반환: {latex, kind, block, error}. 렌더 불가한 LaTeX는 error로 돌려준다(적용하지 못하게).
    """
    rec = find(wd, fid)
    if rec is None:
        return {"error": f"수식 {fid}를 찾지 못했습니다."}
    png_path = wd.extract_images / rec["file"]
    if not png_path.is_file():
        return {"error": "원본 수식 그림이 없습니다 — 다시 변환해야 합니다."}
    old = rec.get("latex") or ""
    extra = _REDO_NUDGE.format(old=old) if old else ""
    got = formulas._attempt(provider, rec, png_path.read_bytes(), extra)
    if got.get("error"):
        got = formulas._attempt(provider, rec, png_path.read_bytes(),
                                extra + formulas._RETRY_NUDGE.format(why=got["error"][:120]))
    if got.get("error"):
        return {"error": got["error"], "latex": got.get("rejected", ""), "kind": got.get("kind", "")}
    return {"latex": got["latex"], "kind": got["kind"], "block": block(got), "error": ""}


def replace_once(md: str, old: str, new: str) -> str | None:
    """`old` 블록이 **줄 단위로 정확히 한 번** 있을 때만 바꾼다. 없거나 여럿이면 None (추측하지 않는다)."""
    hits = [m.start() for m in re.finditer(re.escape(old), md)
            if (m.start() == 0 or md[m.start() - 1] == "\n")
            and (m.end() == len(md) or md[m.end()] in "\n")]
    if len(hits) != 1:
        return None
    i = hits[0]
    return md[:i] + new + md[i + len(old):]


def locate(md: str, rec: dict) -> str | None:
    """본문에서 이 레코드 자리의 **지금 글자**를 하나로 특정한다 — 못 하면 None.

    지금 레코드의 블록을 먼저 찾고, 없으면 그림 폴백 줄(`![formula-NN](…)`)을 id로 찾는다
    (조립은 경로에 `images/`를 붙이므로 경로는 가리지 않는다).
    """
    cur = block(rec)
    if replace_once(md, cur, cur) is not None:
        return cur
    lines = [m.group(0) for m in _IMAGE_LINE_RE.finditer(md) if m.group(1) == rec["id"]]
    return lines[0] if len(lines) == 1 else None


def _patch(md: str, rec: dict, new_block: str) -> str | None:
    old = locate(md, rec)
    return None if old is None else replace_once(md, old, new_block)


def rekey_cache(cache: dict[str, str], sources: list[str], old: str, new: str) -> int:
    """번역 캐시를 새 원문 키로 옮긴다 — 원문·번역 양쪽에서 같은 블록만 바뀐 경우.

    `sources`는 **바꾸기 전** en.md로 만든 유닛·섹션 원문들. 옮긴 개수를 돌려준다.
    """
    moved = 0
    for src in sources:
        if old not in src:
            continue
        tr = cache.get(hash_text(src))
        if tr is None or old not in tr:
            continue
        cache[hash_text(src.replace(old, new))] = tr.replace(old, new)
        moved += 1
    return moved


def apply(wd: WorkDir, fid: str, latex: str, kind: str) -> dict:
    """후보를 원천에 반영 — formulas.json·raw.md. en.md·ko.md는 호출자가(재조립 여부를 아는 쪽이) 맡는다.

    반환: {old, new} 블록 문자열. 실패하면 ValueError.
    """
    records = formulas.load(wd)
    idx = next((i for i, r in enumerate(records) if r.get("id") == fid), None)
    if idx is None:
        raise ValueError(f"수식 {fid}를 찾지 못했습니다.")
    if kind not in ("display", "inline", "code", "text"):
        raise ValueError(f"알 수 없는 종류: {kind}")
    latex = formulas._clean(latex) if kind in ("display", "inline") else latex.strip()
    if not latex or (kind in ("display", "inline") and not formulas.renderable(latex)):
        raise ValueError("렌더할 수 없는 LaTeX입니다.")
    rec = records[idx]
    new_rec = {**rec, "latex": latex, "kind": kind, "error": "", "rejected": "", "edited": True}
    old_block, new_block = block(rec), block(new_rec)
    raw = wd.raw_md.read_text(encoding="utf-8")
    patched = _patch(raw, rec, new_block)
    if patched is None:
        raise ValueError("원문(raw.md)에서 이 수식 자리를 하나로 특정하지 못했습니다.")
    records[idx] = new_rec
    wd.formulas_json.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    wd.raw_md.write_text(patched, encoding="utf-8")
    return {"old": old_block, "new": new_block, "old_rec": rec}


def patch_text(md: str, old_rec: dict, new_block: str) -> str | None:
    """en.md·ko.md 같은 산출물에서 그 자리를 바꾼다 (못 찾으면 None)."""
    return _patch(md, old_rec, new_block)
