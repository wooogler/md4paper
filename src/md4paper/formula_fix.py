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


# 문장 속 인라인 수식 — markdown2 latex 확장과 같은 규칙으로 센다(캔버스가 그 렌더러다).
# 코드(펜스·백틱)와 $$…$$ 안은 수식이 아니므로 먼저 가린다 — 가린 자리도 길이는 그대로 둬 오프셋이 맞게.
_SINGLE_RE = re.compile(r"(?<!\$)\$(?!\$)(.*?)\$")
_MASK_RES = (re.compile(r"```.*?```", re.DOTALL), re.compile(r"(?<!`)(`)(.*?)(?<!`)\1(?!`)"),
             _DISPLAY_RE)
INLINE_PREFIX = "inline-"


def inline_spans(md: str) -> list[tuple[int, int, str]]:
    """본문의 `$…$` 자리 (시작, 끝, 원문) — 문서 순서. 번호가 곧 인라인 수식의 id다."""
    masked = md
    for rx in _MASK_RES:
        masked = rx.sub(lambda m: "\0" * len(m.group(0)), masked)
    return [(m.start(), m.end(), md[m.start():m.end()]) for m in _SINGLE_RE.finditer(masked)
            if m.group(1).strip()]


def _ranked(spans: list[tuple[int, int, str]]) -> list[tuple[str, int]]:
    """각 자리 → (원문, 같은 원문 중 몇 번째). 원문·번역에서 같은 수식을 짝짓는 열쇠."""
    seen: dict[str, int] = {}
    out = []
    for _s, _e, text in spans:
        out.append((text, seen.get(text, 0)))
        seen[text] = seen.get(text, 0) + 1
    return out


def inline_index(en_md: str) -> dict[tuple[str, int], int]:
    """(원문, 순위) → en.md의 인라인 번호. 번역(ko.md)의 인라인 수식도 이걸로 같은 id를 받는다."""
    return {key: n for n, key in enumerate(_ranked(inline_spans(en_md)))}


def tag(md: str, mapping: dict[str, str], inline: dict[tuple[str, int], int] | None = None) -> str:
    """화면용 — 잇게 된 블록(과 인라인 수식) 앞에 빈 표식을 붙인다 (글자 자체는 그대로)."""
    marks: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for s, e, text in _blocks_in(md):
        fid = mapping.get(text)
        if fid is not None:
            marks.append((s, fid))
            taken.append((s, e))
    if inline:
        spans = inline_spans(md)
        for (s, e, _t), key in zip(spans, _ranked(spans)):
            n = inline.get(key)
            if n is not None and not any(a <= s < b for a, b in taken):
                marks.append((s, f"{INLINE_PREFIX}{n}"))
    if not marks:
        return md
    out: list[str] = []
    pos = 0
    for s, fid in sorted(marks):
        out.append(md[pos:s])
        out.append(MARK.format(id=fid))
        pos = s
    out.append(md[pos:])
    return "".join(out)


def find(wd: WorkDir, fid: str) -> dict | None:
    return next((r for r in formulas.load(wd) if r.get("id") == fid), None)


# 사용자가 틀렸다고 본 판본을 같이 보여 준다 — 같은 그림을 같은 말로 다시 물으면 같은 답이 오기 쉽다
_REDO_NUDGE = ("\n\nA reader compared an earlier transcription of this image with the original and "
               "judged it WRONG:\n{old}\nRead the image again carefully and transcribe it correctly.")


def source_pdf(wd: WorkDir) -> str | None:
    try:
        src = json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source")
    except Exception:  # noqa: BLE001
        return None
    return src if src and str(src).lower().endswith(".pdf") else None


def _inline_n(fid: str) -> int | None:
    if not fid.startswith(INLINE_PREFIX):
        return None
    try:
        return int(fid[len(INLINE_PREFIX):])
    except ValueError:
        return None


def subject(wd: WorkDir, fid: str) -> dict | None:
    """팝오버가 다루는 대상 — 수식 레코드, 또는 en.md의 n번째 인라인 수식 (레코드도 크롭도 없다).

    인라인: {id, inline: True, latex, text(`$…$` 원문), page(문맥으로 짐작, 없으면 None)}.
    """
    n = _inline_n(fid)
    if n is None:
        rec = find(wd, fid)
        return None if rec is None else {**rec, "inline": False}
    en = wd.en_md.read_text(encoding="utf-8") if wd.en_md.exists() else ""
    spans = inline_spans(en)
    if not 0 <= n < len(spans):
        return None
    s, _e, text = spans[n]
    return {"id": fid, "inline": True, "text": text, "latex": text[1:-1], "kind": "inline",
            "number": "", "orig": "", "page": guess_page(wd, en[:s], en[_e:])}


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# 낱말만 남긴다 — 수식·링크 주소·HTML, 그리고 인용 단계가 붙인 `[[10, 저자 2018, 제목](#ref-10)]`
# (PDF에는 "[10]"뿐이다)를 지워야 텍스트 레이어와 맞는다.
_STRIP_MD_RE = re.compile(r"\[\[[^\]]*\]\([^)]*\)\]|\$[^$]*\$|\]\([^)]*\)|<[^>]+>")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_STRIP_MD_RE.sub(" ", text).lower())


def guess_page(wd: WorkDir, before: str, after: str) -> int | None:
    """인라인 수식의 쪽 — 앞뒤 낱말 몇 개를 PDF 텍스트 레이어에서 찾는다 (1-based, 못 찾으면 None).

    좌표는 추출 때 남기지 않으므로 사람이 그 쪽에서 영역을 고른다. 쪽만 맞춰 줘도 찾는 품이 준다.
    """
    from md4paper import pdfio

    src = source_pdf(wd)
    if not src:
        return None
    pages = ["".join(_words(t)) for t in pdfio.page_texts(src)]
    if not pages:
        return None
    pre, post = _words(before.rsplit("\n\n", 1)[-1]), _words(after.split("\n\n", 1)[0])
    for k in (8, 5, 3):
        for probe in ("".join(pre[-k:]) if len(pre) >= k else "", "".join(post[:k]) if len(post) >= k else ""):
            if len(probe) < 12:
                continue
            hits = [i for i, t in enumerate(pages) if probe in t]
            if len(hits) == 1:
                return hits[0] + 1
    return None


def guess_bbox(wd: WorkDir, rec: dict) -> list[float] | None:
    """bbox를 남기기 전에 변환한 논문 — 저장된 크롭을 쪽 그림에서 찾아 영역을 되짚는다(템플릿 매칭).

    크롭은 같은 PDF를 같은 렌더러로 잘라 낸 것이라 거의 그대로 겹친다. 확신이 없으면 None.
    """
    if rec.get("bbox"):
        return rec["bbox"]
    src = source_pdf(wd)
    png = wd.extract_images / rec.get("file", "")
    if not src or not rec.get("page") or not png.is_file():
        return None
    try:
        import cv2
        import numpy as np

        from md4paper import pdfio

        zoom = 1.5  # 크롭(배율 3)을 절반으로 줄여 맞춘다 — 쪽 전체를 3배로 그리는 것보다 훨씬 싸다
        page = pdfio.render_page_png(src, int(rec["page"]) - 1, zoom=zoom)
        if page is None:
            return None
        img = cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_GRAYSCALE)
        tpl = cv2.imdecode(np.frombuffer(png.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        tpl = cv2.resize(tpl, (max(1, tpl.shape[1] // 2), max(1, tpl.shape[0] // 2)),
                         interpolation=cv2.INTER_AREA)
        if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
            return None
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _lo, score, _lp, (x, y) = cv2.minMaxLoc(res)
    except Exception:  # noqa: BLE001 — 짐작일 뿐이다; 실패하면 사람이 처음부터 고른다
        return None
    if score < 0.8:
        return None
    return [round(x / zoom, 1), round(y / zoom, 1),
            round((x + tpl.shape[1]) / zoom, 1), round((y + tpl.shape[0]) / zoom, 1)]


_MIN_CROP_PT = 3.0


def render_crop(wd: WorkDir, page: int, rect) -> bytes:  # noqa: ANN001
    """사람이 고른 영역(1-based 쪽, 좌상단 원점 pt [l, t, r, b])을 수식 단계와 같은 배율로 자른다."""
    from md4paper import pdfio

    src = source_pdf(wd)
    if not src:
        raise ValueError("원본 PDF를 찾을 수 없습니다.")
    try:
        left, top, right, bottom = (float(v) for v in rect)
    except (TypeError, ValueError):
        raise ValueError("영역이 올바르지 않습니다.") from None
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    if right - left < _MIN_CROP_PT or bottom - top < _MIN_CROP_PT:
        raise ValueError("영역이 너무 작습니다.")
    png = pdfio.render_region_png(src, int(page) - 1, (left, top, right, bottom))
    if png is None:
        raise ValueError("그 영역을 잘라 내지 못했습니다.")
    return png


def regenerate(wd: WorkDir, fid: str, provider, page: int | None = None,  # noqa: ANN001 — Provider
               rect=None) -> dict:  # noqa: ANN001
    """그 수식 하나를 다시 읽힌다 — 문서는 건드리지 않고 후보만 돌려준다.

    `page`·`rect`를 주면 사람이 고른 그 영역을 새로 잘라 읽힌다(크롭이 잘렸거나 인라인 수식일 때).
    반환: {latex, kind, block, error, crop(새로 자른 PNG 바이트 — 그 영역을 쓴 경우)}.
    렌더 불가한 LaTeX는 error로 돌려준다(적용하지 못하게).
    """
    sub = subject(wd, fid)
    if sub is None:
        return {"error": f"수식 {fid}를 찾지 못했습니다."}
    crop = None
    if rect is not None:
        try:
            crop = render_crop(wd, int(page or sub.get("page") or 1), rect)
        except ValueError as e:
            return {"error": str(e)}
        png = crop
    elif sub["inline"]:
        return {"error": "PDF에서 수식 영역을 먼저 지정해 주세요."}
    else:
        png_path = wd.extract_images / sub["file"]
        if not png_path.is_file():
            return {"error": "원본 수식 그림이 없습니다 — 영역을 지정해 주세요."}
        png = png_path.read_bytes()
    old = sub.get("latex") or ""
    extra = _REDO_NUDGE.format(old=old) if old else ""
    if sub["inline"]:
        extra += "\n\nThis is an INLINE formula inside a sentence: answer kind=inline (or text if it is not math)."
    got = formulas._attempt(provider, sub, png, extra)
    if got.get("error"):
        got = formulas._attempt(provider, sub, png, extra + formulas._RETRY_NUDGE.format(why=got["error"][:120]))
    if got.get("error"):
        return {"error": got["error"], "latex": got.get("rejected", ""), "kind": got.get("kind", "")}
    kind = got["kind"]
    if sub["inline"] and kind not in ("inline", "text"):
        kind = "inline"  # 문장 속 자리는 독립 수식이 될 수 없다
    out = {"latex": got["latex"], "kind": kind, "error": "", "crop": crop,
           "block": inline_text(got["latex"], kind) if sub["inline"] else block({**got, "kind": kind})}
    return out


def inline_text(latex: str, kind: str) -> str:
    return latex if kind == "text" else f"${latex}$"


def line_position(md: str, old: str) -> int | None:
    """`old` 블록이 **줄 단위로 정확히 한 번** 있는 자리. 없거나 여럿이면 None (추측하지 않는다)."""
    hits = [m.start() for m in re.finditer(re.escape(old), md)
            if (m.start() == 0 or md[m.start() - 1] == "\n")
            and (m.end() == len(md) or md[m.end()] in "\n")]
    return hits[0] if len(hits) == 1 else None


def replace_once(md: str, old: str, new: str) -> str | None:
    """`old` 블록이 줄 단위로 정확히 한 번 있을 때만 바꾼다 (§line_position)."""
    i = line_position(md, old)
    return None if i is None else md[:i] + new + md[i + len(old):]


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


def _nth(text: str, sub: str, k: int) -> int:
    i = -1
    for _ in range(k + 1):
        i = text.find(sub, i + 1)
        if i < 0:
            return -1
    return i


def rekey_cache(cache: dict[str, str], sources: list[str], doc: str, at: int, old: str, new: str) -> int:
    """번역 캐시를 새 원문 키로 옮긴다 — 원문·번역 양쪽에서 **그 한 자리만** 바뀐 경우.

    `sources`는 바꾸기 전 en.md(`doc`)로 만든 유닛·섹션 원문들, `at`은 doc에서 바뀐 자리(`old`의 시작).
    그 자리를 품은 원문만 옮긴다. 번역에서는 원문 속 같은 글자 중 몇 번째인지로 짝을 찾는다 —
    개수가 다르면(번역이 수식을 빼먹거나 덧붙였으면) 옮기지 않아 그 섹션이 '원문 바뀜'으로 남는다.
    """
    moved = 0
    for src in sources:
        off = doc.find(src) if src else -1
        if off < 0 or not off <= at < off + len(src) or src[at - off:at - off + len(old)] != old:
            continue
        tr = cache.get(hash_text(src))
        if tr is None:
            continue
        rel = at - off
        rank = src[:rel].count(old)
        if tr.count(old) != src.count(old):
            continue
        j = _nth(tr, old, rank)
        if j < 0:
            continue
        cache[hash_text(src[:rel] + new + src[rel + len(old):])] = tr[:j] + new + tr[j + len(old):]
        moved += 1
    return moved


def save_crop(wd: WorkDir, rec: dict, page: int, rect, png: bytes) -> dict:  # noqa: ANN001
    """사람이 고른 영역을 이 레코드의 원본 크롭으로 — extract/images(근거)와 out/images(본문 그림) 둘 다."""
    for d in (wd.extract_images, wd.out_images):
        d.mkdir(parents=True, exist_ok=True)
        (d / rec["file"]).write_bytes(png)
    left, top, right, bottom = (float(v) for v in rect)
    return {**rec, "page": int(page),
            "bbox": [round(min(left, right), 1), round(min(top, bottom), 1),
                     round(max(left, right), 1), round(max(top, bottom), 1)], "manual_crop": True}


def apply(wd: WorkDir, fid: str, latex: str, kind: str, page: int | None = None, rect=None) -> dict:  # noqa: ANN001
    """후보를 원천에 반영 — formulas.json·raw.md. en.md·ko.md는 호출자가(재조립 여부를 아는 쪽이) 맡는다.

    `rect`가 있으면 그 영역을 새 원본 크롭으로 저장한다(다음에 열 때도, 수식 단계를 다시 돌릴 때도 그 그림).
    반환: {old, new, old_rec} 블록 문자열. 실패하면 ValueError.
    """
    records = formulas.load(wd)
    idx = next((i for i, r in enumerate(records) if r.get("id") == fid), None)
    if idx is None:
        raise ValueError(f"수식 {fid}를 찾지 못했습니다.")
    latex = _checked(latex, kind)
    rec = records[idx]
    new_rec = {**rec, "latex": latex, "kind": kind, "error": "", "rejected": "", "edited": True}
    old_block, new_block = block(rec), block(new_rec)
    raw = wd.raw_md.read_text(encoding="utf-8")
    patched = _patch(raw, rec, new_block)
    if patched is None:
        raise ValueError("원문(raw.md)에서 이 수식 자리를 하나로 특정하지 못했습니다.")
    if rect is not None:
        new_rec = save_crop(wd, new_rec, int(page or rec.get("page") or 1), rect,
                            render_crop(wd, int(page or rec.get("page") or 1), rect))
    records[idx] = new_rec
    wd.formulas_json.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    wd.raw_md.write_text(patched, encoding="utf-8")
    return {"old": old_block, "new": new_block, "old_rec": rec}


def _checked(latex: str, kind: str) -> str:
    if kind not in ("display", "inline", "code", "text"):
        raise ValueError(f"알 수 없는 종류: {kind}")
    latex = formulas._clean(latex) if kind in ("display", "inline") else latex.strip()
    if not latex or (kind in ("display", "inline") and not formulas.renderable(latex)):
        raise ValueError("렌더할 수 없는 LaTeX입니다.")
    return latex


def nth_same(md: str, text: str, rank: int, total: int) -> tuple[int, int] | None:
    """`md`에서 `text`(인라인 `$…$`)의 rank번째 자리 — 같은 글자의 개수가 기준(total)과 같을 때만.

    원문(en.md)과 raw.md·ko.md는 같은 수식을 같은 순서로 품고 있다. 개수가 다르면(어느 쪽에서
    빠지거나 덧붙었으면) 몇 번째인지 믿을 수 없다 — 그때는 유일할 때만 잇는다.
    """
    same = [(s, e) for s, e, t in inline_spans(md) if t == text]
    if len(same) == total and 0 <= rank < total:
        return same[rank]
    if len(same) == 1 and total == 1:
        return same[0]
    return None


def inline_target(en_md: str, n: int, expect: str | None = None) -> tuple[str, int, int]:
    """en.md의 n번째 인라인 수식 → (원문, 같은 글자 중 순위, 같은 글자 총수)."""
    spans = inline_spans(en_md)
    if not 0 <= n < len(spans):
        raise ValueError("문서가 바뀌어 그 수식을 찾지 못했습니다 — 다시 눌러 주세요.")
    text = spans[n][2]
    if expect is not None and text[1:-1] != expect:
        raise ValueError("문서가 바뀌어 그 수식을 찾지 못했습니다 — 다시 눌러 주세요.")
    text, rank = _ranked(spans)[n]
    return text, rank, sum(1 for _s, _e, t in spans if t == text)


def replace_at(md: str, where: tuple[int, int], new: str) -> str:
    return md[:where[0]] + new + md[where[1]:]


def patch_text(md: str, old_rec: dict, new_block: str) -> str | None:
    """en.md·ko.md 같은 산출물에서 그 자리를 바꾼다 (못 찾으면 None)."""
    return _patch(md, old_rec, new_block)
