"""뷰어 하이라이트 · 메모 — paper.md4/annotations.json 읽기/쓰기 (NiceGUI 무관, 테스트 가능).

한 표시(item)는 **문장 단위**이고 원문·번역 양쪽에 걸린다 — 앵커(anchor)를 쪽마다 하나씩,
최대 둘 가진다. 색과 메모는 표시 하나에 하나뿐이라 양쪽이 어긋날 일이 없다.

앵커링 전략: 렌더된 HTML이 아니라 **셀의 평문 오프셋**에 붙는다.
  side  — 원문(en) / 번역(ko) 중 어느 컬럼인지
  row   — 정렬 그리드의 행 번호 (align_rows의 인덱스)
  start/end — 그 셀 평문에서의 문자 오프셋
  quote/prefix/suffix — 문서가 바뀌어 오프셋이 밀렸을 때 다시 찾기 위한 단서

재조립·재번역으로 행이 밀리면 클라이언트가 quote로 다시 찾아 붙이고, 고쳐진 좌표를 되저장한다.
그래도 못 찾으면 버리지 않고 '위치를 찾지 못한 메모'로 목록에만 남긴다 — 사용자가 쓴 글은
파이프라인이 조용히 지우지 않는다.
"""

from __future__ import annotations

import json
import time

from md4paper.workdir import WorkDir

VERSION = 2
COLORS = ("yellow", "green", "blue", "pink", "purple")
SIDES = ("en", "ko")

MAX_ITEMS = 2000        # 한 논문에 담아 둘 표시 수 상한 (파일이 무한정 커지지 않게)
MAX_ANCHORS = 2         # 쪽마다 하나 — 원문·번역
MAX_QUOTE = 4000        # 문장 단위라 문단 하이라이트보다 여유를 둔다
MAX_NOTE = 4000         # 메모 길이 상한
MAX_CONTEXT = 60        # 재탐색용 앞뒤 문맥 길이


def _clip(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _int(value: object, default: int = 0) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _anchor(raw: object) -> dict | None:
    """앵커 하나를 정리. quote가 없으면 다시 찾을 방법이 없으므로 버린다."""
    if not isinstance(raw, dict):
        return None
    quote = _clip(raw.get("quote"), MAX_QUOTE)
    if not quote.strip():
        return None
    side = raw.get("side")
    start = _int(raw.get("start"))
    end = _int(raw.get("end"), start + len(quote))
    return {
        "side": side if side in SIDES else "en",
        "row": _int(raw.get("row")),
        "start": start,
        "end": max(end, start + 1),
        "quote": quote,
        "prefix": _clip(raw.get("prefix"), MAX_CONTEXT),
        "suffix": _clip(raw.get("suffix"), MAX_CONTEXT),
    }


def _anchors(raw: object) -> list[dict]:
    """쪽당 하나씩만 남긴다 (같은 쪽 앵커가 둘이면 먼저 온 것)."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else [])[:MAX_ANCHORS * 2]:
        a = _anchor(item)
        if a is None or a["side"] in seen:
            continue
        seen.add(a["side"])
        out.append(a)
        if len(out) >= MAX_ANCHORS:
            break
    return out


def normalize(items: object) -> list[dict]:
    """클라이언트가 보낸 목록을 저장 가능한 형태로 정리 (모르는 필드·망가진 항목은 버린다).

    앵커가 하나도 안 남은 표시는 버린다. v1(쪽 하나짜리 평면 구조)도 앵커 하나로 받아들인다.
    """
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    now = time.time()
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        anchors = _anchors(raw.get("anchors")) if "anchors" in raw else _anchors([raw])
        if not anchors:
            continue
        aid = _clip(raw.get("id"), 64) or f"a{len(out)}-{int(now * 1000)}"
        if aid in seen:
            continue
        seen.add(aid)
        out.append({
            "id": aid,
            "color": raw.get("color") if raw.get("color") in COLORS else COLORS[0],
            "note": _clip(raw.get("note"), MAX_NOTE),
            "anchors": anchors,
            "created": float(raw.get("created") or now),
            "updated": now,
        })
    return out


def load(wd: WorkDir) -> list[dict]:
    """저장된 표시 목록. 파일이 없거나 깨졌으면 빈 목록 (뷰어가 열리지 못하게 하지 않는다)."""
    path = wd.annotations_json
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return normalize(data.get("items") if isinstance(data, dict) else data)


def save(wd: WorkDir, items: object) -> list[dict]:
    """정리 후 저장하고, 저장된 목록을 돌려준다. 비면 파일을 지운다(빈 껍데기를 남기지 않게)."""
    clean = normalize(items)
    path = wd.annotations_json
    if not clean:
        path.unlink(missing_ok=True)
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": VERSION, "items": clean}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)  # 원자적 교체 — 저장 중 종료돼도 반쪽 파일이 남지 않는다
    return clean


def _side(item: dict, side: str) -> str:
    for a in item["anchors"]:
        if a["side"] == side:
            return " ".join(a["quote"].split())
    return ""


def _order(item: dict) -> tuple[int, int]:
    """문서 순서 — 원문 앵커 기준, 없으면 번역 앵커."""
    a = next((x for x in item["anchors"] if x["side"] == "en"), item["anchors"][0])
    return (a["row"], a["start"])


def to_markdown(items: list[dict], title: str = "") -> str:
    """하이라이트·메모를 읽을 수 있는 마크다운으로 (내보내기용).

    문서 순서로, 인용구 안에 원문과 번역을 나란히 두고 메모를 그 아래에 붙인다.
    """
    head = f"# {title} — 하이라이트 · 메모" if title else "# 하이라이트 · 메모"
    if not items:
        return head + "\n\n_아직 표시한 내용이 없습니다._\n"
    lines = [head, ""]
    for item in sorted(items, key=_order):
        parts = [q for q in (_side(item, "en"), "*" + _side(item, "ko") + "*") if q not in ("", "**")]
        lines.append("\n>\n".join("> " + q for q in parts))   # 원문 · 번역을 한 인용구 안에
        note = item.get("note", "").strip()
        if note:
            lines.append("")
            lines.extend(note.splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_bytes(wd: WorkDir, title: str = "") -> tuple[str, bytes]:
    """(파일명, 내용) — 다운로드용 마크다운."""
    stem = (title or wd.root.stem).strip() or "paper"
    name = "".join(c for c in stem if c not in '\\/:*?"<>|').strip()[:80] or "paper"
    return f"{name} - 메모.md", to_markdown(load(wd), title).encode("utf-8")
