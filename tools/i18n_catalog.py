"""화면 언어 카탈로그 관리 (§md4paper/i18n.py).

    uv run python tools/i18n_catalog.py sync en          # 코드의 t()/md4T() 문구를 en.json에 빈 항목으로 더한다
    uv run python tools/i18n_catalog.py sync en --prune  # 코드에서 사라진 항목도 지운다
    uv run python tools/i18n_catalog.py check            # 모든 카탈로그 검사 (자리표시 불일치 → 실패)
    uv run python tools/i18n_catalog.py fill en         # 빈 항목을 기본 LLM으로 초벌 번역 (비용 발생)

문구를 옮기는 순서: 파일 하나를 고른다 → 문구를 t("…")로 감싼다(f-string은 t("…{n}…", n=…)) → sync →
fill 또는 손으로 채운다 → check → 그 파일만 커밋해 바로 머지한다(다른 세션과 충돌을 줄이려고).
"""

from __future__ import annotations

import argparse
import json
import sys

from md4paper import i18n


def _fill(lang: str, batch: int = 60) -> int:
    """빈 항목을 LLM으로 채운다 — 한 번에 batch개씩 {원문: 번역} JSON으로 받는다."""
    from pydantic import BaseModel

    from md4paper import config, langs

    class _Out(BaseModel):
        items: list[dict[str, str]]

    path = i18n.LOCALES / f"{lang}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    todo = [k for k, v in data.items() if not v]
    prov = config.build_provider(None, None)
    name = langs.get(lang).english
    system = (f"You translate UI strings of a desktop app for reading academic papers from Korean into {name}. "
              "Keep them short like UI text. Keep placeholders such as {n} or {name} exactly. Keep product names "
              "(md4paper, GitHub, Obsidian, Notion, OpenAI) as is. Return items: [{\"ko\": source, \"tr\": translation}] "
              "for every source, in order.")
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        res = prov.parse(system, json.dumps(part, ensure_ascii=False), _Out, max_tokens=8000)
        for it in res.items:
            src, tr = it.get("ko"), it.get("tr")
            if src in data and tr and i18n.fields(src) == i18n.fields(tr):
                data[src] = tr
                done += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{lang}: {done}/{len(todo)}개 채움 · 비용 ≈ ${prov.cost():.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("lang")
    s.add_argument("--prune", action="store_true")
    sub.add_parser("check")
    f = sub.add_parser("fill")
    f.add_argument("lang")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        r = i18n.sync(a.lang, prune=a.prune)
        print(f"{a.lang}: 새 문구 {len(r['added'])}개 · 안 쓰는 항목 {len(r['unused'])}개"
              f"{' (지움)' if a.prune else ''} · 번역 {r['translated']}/{r['total']}")
        return 0
    if a.cmd == "fill":
        return _fill(a.lang)
    bad = 0
    for lang in i18n.available()[1:]:
        for p in i18n.problems(lang):
            print(f"{lang}: {p}")
            bad += 1
    print("카탈로그 문제 없음" if not bad else f"문제 {bad}개")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
