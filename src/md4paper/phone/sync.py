"""고른 논문들을 저장소에 올린다 — 지문(§bundle.content_hash)이 같은 논문은 굽지도 올리지도 않는다.

목록(index.json)은 마지막에 한 번 쓴다. 중간에 끊겨도 폰은 예전 목록과 그 목록이 가리키는 번들을 그대로
본다(새로 올린 번들은 다음 목록에서야 보인다).

prune=True면 목록을 고른 논문들로 **맞춘다**(빠진 논문의 번들은 지운다). False면 고른 논문을 더하거나
고치기만 한다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from md4paper import i18n
from md4paper.phone import bundle as bmod
from md4paper.phone.store import Store

INDEX = "index.json"


def read_index(store: Store) -> dict:
    raw = store.read(INDEX)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("papers"), list):
                return data
        except ValueError:
            pass
    return {"format": bmod.FORMAT, "papers": []}


def push(roots: Iterable[Path], store: Store, *, prune: bool = False, extra: dict[str, dict] | None = None,
         on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """논문들 → 저장소. extra는 논문 키 → 목록 줄에 덧붙일 값(고정·컬렉션 …).

    반환: {uploaded, skipped, removed, bytes, papers}.
    """
    roots = list(dict.fromkeys(Path(r) for r in roots))
    old = {e.get("key"): e for e in read_index(store)["papers"] if isinstance(e, dict)}
    code = bmod._code_stamp()
    entries: list[dict] = []
    uploaded = skipped = sent = 0
    for n, root in enumerate(roots):
        key = root.stem
        if on_progress:
            on_progress(n, len(roots), key)
        digest = bmod.content_hash(root, code)
        prev = old.get(key)
        more = (extra or {}).get(key, {})
        if prev and prev.get("hash") == digest and prev.get("format", bmod.FORMAT) == bmod.FORMAT:
            entries.append({**prev, **more})
            skipped += 1
            continue
        b = bmod.build_paper(root, digest)
        data = bmod.dumps(b)
        store.write(bmod.file_name(key), data)
        entries.append(bmod.index_entry(b, len(data), more))
        uploaded += 1
        sent += len(data)
    keys = {e["key"] for e in entries}
    removed = 0
    if prune:
        for k, e in old.items():
            if k not in keys and e.get("file"):
                store.delete(e["file"])
                removed += 1
    else:  # 고르지 않은 예전 논문은 목록에 그대로 둔다
        entries += [e for k, e in old.items() if k not in keys]
    index = {"format": bmod.FORMAT, "updated": time.time(), "ui_lang": i18n.ui_lang(), "papers": entries}
    store.write(INDEX, bmod.dumps(index))
    if on_progress:
        on_progress(len(roots), len(roots), "")
    return {"uploaded": uploaded, "skipped": skipped, "removed": removed, "bytes": sent, "papers": len(entries)}
