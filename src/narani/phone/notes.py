"""폰 ↔ 데스크톱 하이라이트·메모 — 두 쪽이 서로 다른 파일에만 쓴다(같은 파일을 둘이 고치지 않는다).

저장소 안의 모양 (§phone):
  notes/<id>.json   — 데스크톱이 쓴다. 그 논문의 annotations.json 항목 전부 + applied
                      (폰이 남긴 기록을 어디까지 합쳤는지 — 폰은 그보다 옛 기록을 지운다)
  mobile/<id>.json  — 폰이 쓴다. 폰에서 한 일의 기록 {ops: [{op: put|del, ts, item|id}]}
                      put은 표시 하나를 통째로(만들기·색·메모 고치기), del은 지우기.

데스크톱은 annotations.json을 저장할 때마다 항목의 updated를 지금 시각으로 다시 적는다 — 그래서
'나중에 고친 쪽'을 updated로 가릴 수 없다. 대신 폰 기록의 ts(폰 시계)와, 데스크톱이 이미 합친
마지막 ts(applied, 논문 폴더의 phone.json)를 견준다. 합친 뒤 데스크톱에서 다시 고친 것은
annotations.json에 그대로 남고, 폰은 다음에 notes/를 받아 그걸 본다.

앵커 형식은 데스크톱 뷰어와 같다(§ui.annotations) — 폰 리더의 행·셀은 뷰어와 같은 align_rows로 만들고,
문구가 조금 어긋나면 quote(+앞뒤 문맥)로 다시 찾는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from narani.phone import bundle as bmod
from narani.phone.store import Store
from narani.ui import annotations
from narani.workdir import WorkDir

FORMAT = 1
MAX_OPS = 5000  # 폰 기록이 이보다 많으면 뒤쪽(최근)만 본다


def _hashpart(key: str) -> str:
    return bmod.file_name(key).split("/", 1)[1]


def notes_name(key: str) -> str:
    return "notes/" + _hashpart(key)


def mobile_name(key: str) -> str:
    return "mobile/" + _hashpart(key)


def _state_path(wd: WorkDir) -> Path:
    return wd.root / "phone.json"


def load_state(wd: WorkDir) -> dict:
    try:
        st = json.loads(_state_path(wd).read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(wd: WorkDir, st: dict) -> None:
    p = _state_path(wd)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(p)


def apply_ops(items: list[dict], ops: object, applied: float) -> tuple[list[dict], float, int]:
    """폰 기록 중 applied 뒤의 것만 순서대로 얹는다. 반환: (항목들, 새 applied, 얹은 기록 수)."""
    if not isinstance(ops, list):
        return items, applied, 0
    fresh = []
    for op in ops[-MAX_OPS:]:
        try:
            ts = float(op.get("ts"))
        except (AttributeError, TypeError, ValueError):
            continue
        if ts > applied and op.get("op") in ("put", "del"):
            fresh.append((ts, op))
    if not fresh:
        return items, applied, 0
    fresh.sort(key=lambda x: x[0])
    out = [dict(it) for it in items]
    pos = {it["id"]: i for i, it in enumerate(out)}
    for ts, op in fresh:
        if op["op"] == "del":
            aid = str(op.get("id") or "")
            if aid in pos:
                out[pos[aid]] = None  # type: ignore[call-overload]
                del pos[aid]
            continue
        item = op.get("item")
        if not isinstance(item, dict) or not item.get("id"):
            continue
        aid = str(item["id"])
        if aid in pos:
            prev = out[pos[aid]]
            out[pos[aid]] = {**item, "created": prev.get("created", item.get("created"))}
        else:
            pos[aid] = len(out)
            out.append(dict(item))
    kept = [it for it in out if it is not None]
    return annotations.normalize(kept), max(applied, fresh[-1][0]), len(fresh)


def _read_json(store: Store, name: str) -> object:
    raw = store.read(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def sync_paper(store: Store, root: Path, prev_hash: str = "") -> dict:
    """논문 한 편: 폰 기록을 annotations.json에 합치고, 바뀌었으면 notes/를 다시 올린다.

    반환: {merged: 얹은 폰 기록 수, notes_hash: 올린 스냅숏의 지문('' = 표시 없음), wrote: 올렸는지}.
    """
    wd = WorkDir(root)
    key = root.stem
    st = load_state(wd)
    applied = float(st.get("applied") or 0)
    items = annotations.load(wd)
    merged = 0
    phone = _read_json(store, mobile_name(key))
    if isinstance(phone, dict):
        items2, applied2, merged = apply_ops(items, phone.get("ops"), applied)
        if merged:
            items = annotations.save(wd, items2)
            st["applied"] = applied = applied2
            save_state(wd, st)
    if not items and not applied:
        if prev_hash:
            store.delete(notes_name(key))
        return {"merged": merged, "notes_hash": "", "wrote": bool(prev_hash)}
    # updated는 저장 때마다 바뀌므로 지문에서 뺀다 — 내용이 같으면 다시 올리지 않는다
    stable = [{k: v for k, v in it.items() if k != "updated"} for it in items]
    digest = hashlib.sha256(bmod.dumps({"items": stable, "applied": applied})).hexdigest()[:24]
    wrote = digest != prev_hash
    if wrote:
        store.write(notes_name(key), bmod.dumps({"format": FORMAT, "key": key, "items": items,
                                                 "applied": applied}))
    return {"merged": merged, "notes_hash": digest, "wrote": wrote}
