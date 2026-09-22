"""수식 — Docling이 버리는 자리를 그림 + 원문으로 건져낸다.

Docling은 수식 클러스터를 **찾아내고도** `text=""`로 둔다(readingorder_model이 원문을
`orig`에만 넣는다). 그러면 마크다운 직렬화기가 그 자리에 `<!-- formula-not-decoded -->`라는
**HTML 주석**을 적는다 — 렌더링하면 아무것도 안 보이니, 읽는 사람에게는 수식이 통째로
사라진 것과 같다. 실측: 논문 105편 중 24편에서 81개가 이렇게 없어졌다.

여기서 두 가지를 건진다.

1. **크롭 PNG** — 수식 영역만 잘라낸 그림. LLM에 그대로 먹일 근거이자, LLM을 안 쓸 때의
   폴백이다(적어도 눈으로는 원문 그대로 보인다).
2. **`orig` 텍스트** — "⟨ Π ∗ , Θ ∗ ⟩ Φ = arg max ⟨ Π , Θ ⟩ Φ E …" 정도로 나온다.
   그 자체로는 LaTeX가 아니지만, 그림만 보고 읽을 때 헷갈리는 기호(ℓ/l, ∗/*)를 가려주는
   힌트라 LLM 프롬프트에 같이 넣는다.

식 번호는 Docling이 **따로 떨어진 수식 아이템**으로 잡는 일이 잦다(GEPA 실측: `(1)`이
독립 아이템). 그대로 두면 `$$( 1 )$$`가 되므로, 번호만 있는 아이템은 직전 수식에 붙이고
본문에서는 없앤다.
"""

from __future__ import annotations

import json
import re

from md4paper.extract.reading_order import _top_left

# 직렬화기가 수식 자리에 남기는 표식 (docling_core/transforms/serializer/markdown.py)
PLACEHOLDER = "<!-- formula-not-decoded -->"
# 수식 그림의 파일명 규약 — img-NN(본문 그림)과 겹치지 않아야 캡션 짝짓기가 이걸 그림으로 안 본다
FILE_RE = re.compile(r"^formula-(\d+)\.png$")
IMAGE_RE = re.compile(r"^!\[(formula-\d+)\]\((formula-\d+\.png)\)$")

_NUMBER_ONLY_RE = re.compile(r"^[(\[]\s*(\d+[a-z]?)\s*[)\]]$")
# 구두점만 있는 영역 — 수식 끝의 마침표 하나가 독립 수식으로 잡히는 일이 있다(TextGrad 실측).
# ASCII 구두점만으로 한정한다: '∑'이나 'x'처럼 짧아도 진짜인 수식을 지우면 안 된다.
_PUNCT_ONLY_RE = re.compile(r"^[.,;:·\-—]+$")
# 식 번호는 수식 오른쪽 끝에 붙어 나온다 — 원문 힌트에서 떼어내 따로 기록한다
_TRAILING_NUMBER_RE = re.compile(r"\s*[(\[]\s*(\d+[a-z]?)\s*[)\]]\s*$")
# bbox를 넉넉히 잡는다: Docling 클러스터는 첨자·적분 기호 끝을 종종 1~2pt 잘라먹는다
_PAD_PT = 4.0
_MIN_SIDE_PT = 6.0  # 이보다 얇으면 수식이 아니라 잡티 (렌더도 실패한다)


def _label(item) -> str:  # noqa: ANN001 — DocItem
    return str(getattr(item, "label", "")).replace("DocItemLabel.", "").lower()


def _slots(document) -> list:  # noqa: ANN001 — DoclingDocument
    """마크다운에 PLACEHOLDER를 남길 수식 아이템을, **문서 순서대로**.

    직렬화기 조건과 정확히 같아야 k번째 아이템 ↔ k번째 표식이 어긋나지 않는다:
    `text`가 비어 있고 `orig`가 있을 때만 표식이 나온다(둘 다 비면 아무것도 안 적는다).
    """
    out = []
    for item, _level in document.iterate_items():
        if _label(item) != "formula":
            continue
        if (getattr(item, "text", "") or "").strip():
            continue  # 이미 LaTeX가 채워짐 (docling 수식 인식을 켠 경우)
        if not (getattr(item, "orig", "") or "").strip():
            continue  # 표식조차 안 나오는 아이템 — 세면 안 된다
        out.append(item)
    return out


def _rect(item, document) -> tuple[int, tuple[float, float, float, float]] | None:  # noqa: ANN001
    """수식 아이템 → (0-based 페이지, 좌상단 원점 rect). 좌표가 없으면 None."""
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    page_no = int(prov[0].page_no)
    size = getattr(getattr(document, "pages", {}).get(page_no, None), "size", None)
    height = float(getattr(size, "height", 0) or 0)
    width = float(getattr(size, "width", 0) or 0)
    if not height:
        return None
    box = prov[0].bbox
    top, bottom = _top_left(box, height)
    left, right = min(box.l, box.r), max(box.l, box.r)
    if right - left < _MIN_SIDE_PT or bottom - top < _MIN_SIDE_PT:
        return None
    rect = (max(0.0, left - _PAD_PT), max(0.0, top - _PAD_PT),
            min(width, right + _PAD_PT) if width else right + _PAD_PT,
            min(height, bottom + _PAD_PT))
    return page_no - 1, rect


def collect(document, source, images_dir) -> list[dict]:  # noqa: ANN001
    """수식 영역을 잘라 PNG로 저장하고 레코드를 돌려준다 (문서 순서).

    레코드에는 `slot`이 있다 — 마크다운의 몇 번째 PLACEHOLDER인지. 번호만 있는 아이템은
    `drop=True` 레코드로 남겨(그 자리 표식은 지워야 한다) 직전 수식의 `number`가 된다.
    """
    from md4paper import pdfio

    records: list[dict] = []
    seq = 0
    last_real: dict | None = None
    for slot, item in enumerate(_slots(document)):
        orig = " ".join((item.orig or "").split())
        num_only = _NUMBER_ONLY_RE.match(orig)
        # 식 번호를 먼저 떼어낸다 — 떼기 전에는 ". (7)"이 구두점만인 영역으로 안 보인다.
        number, hint = "", orig
        trailing = _TRAILING_NUMBER_RE.search(hint)
        if trailing and not num_only:
            number = trailing.group(1)
            hint = hint[: trailing.start()].strip()
        if num_only or _PUNCT_ONLY_RE.match(hint):
            # 번호만 있거나(떨어져 나온 식 번호) 마침표 하나뿐인 영역 — 수식이 아니다.
            # 번호는 직전 수식의 것이므로 버리지 않고 넘겨준다.
            if last_real is not None:
                found = num_only.group(1) if num_only else number
                last_real["number"] = last_real.get("number") or found
            records.append({"slot": slot, "drop": True})
            continue
        placed = _rect(item, document)
        if placed is None:
            records.append({"slot": slot, "drop": True})  # 좌표 없음 — 그림도 못 만든다
            continue
        page, rect = placed
        try:
            png = pdfio.render_region_png(source, page, rect)
        except Exception:  # noqa: BLE001 — 크롭 실패가 추출 전체를 막지 않게
            png = None
        if png is None:
            records.append({"slot": slot, "drop": True})
            continue
        seq += 1
        name = f"formula-{seq:02d}.png"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / name).write_bytes(png)
        last_real = {
            "id": f"formula-{seq:02d}", "slot": slot, "file": name,
            "page": page + 1, "orig": hint, "number": number, "latex": "",
        }
        records.append(last_real)
    return records


def place(md: str, records: list[dict]) -> str:
    """마크다운의 PLACEHOLDER를 수식 그림 참조로 바꾼다 (`drop` 슬롯은 삭제).

    `records`는 `collect`가 준 문서 순서 그대로여야 한다 — k번째 표식이 k번째 레코드다.
    레코드보다 표식이 많으면(문서가 바뀐 경우) 남는 표식은 그대로 둔다: 조용히 어긋난
    수식을 붙이느니 원래의 빈 주석이 낫다.
    """
    by_slot = {r["slot"]: r for r in records}
    out: list[str] = []
    seen = 0
    for line in md.split("\n"):
        if line.strip() == PLACEHOLDER:
            rec = by_slot.get(seen)
            seen += 1
            if rec is None:
                out.append(line)
            elif not rec.get("drop"):
                out.append(f"![{rec['id']}]({rec['file']})")
            continue  # drop 슬롯은 줄 자체를 없앤다
        out.append(line)
    return _squeeze_blanks(out)


def _squeeze_blanks(lines: list[str]) -> str:
    """`drop` 슬롯을 지우면서 생긴 빈 줄 3연속 이상을 2줄로 (문단 경계는 유지)."""
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
        else:
            blanks += 1
            if blanks > 2:
                continue
        out.append(line)
    return "\n".join(out)


def save(records: list[dict], path) -> int:  # noqa: ANN001 — Path
    """본문에 남은 수식 레코드를 formulas.json으로. 저장한 개수를 돌려준다."""
    kept = [r for r in records if not r.get("drop")]
    if not kept:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(kept)
