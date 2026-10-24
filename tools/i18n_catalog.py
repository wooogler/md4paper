"""화면 언어 카탈로그 관리 (§narani/i18n.py).

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

from narani import i18n


# 같은 낱말이 화면마다 다르게 옮겨지지 않게 — 자주 나오는 말의 번역어를 못 박는다 (영어 카탈로그와 같은 뜻)
_TERMS = {
    "ja": "原文=原文 (source), 翻訳=翻訳 (translation), 用語集=用語集 (glossary), コレクション=コレクション (collection), "
          "メモ=メモ (note), ハイライト=ハイライト, 論文=論文 (paper), セクション=セクション, 保存場所=保存先 (save location), "
          "作業フォルダ=作業フォルダ, エージェント=エージェント, 設定=設定",
    "zh-Hans": "原文 (source), 翻译 (translation), 术语表 (glossary), 合集 (collection), 笔记 (note), 高亮 (highlight), "
               "论文 (paper), 章节 (section), 保存位置 (save location), 工作文件夹, 智能体 (agent), 设置 (settings)",
    "es": "original (source), traducción (translation), glosario (glossary), colección (collection), nota (note), "
          "resaltado (highlight), artículo (paper), sección (section), ubicación de guardado (save location), "
          "carpeta de trabajo, agente (agent), ajustes (settings)",
}


def _fill(lang: str, batch: int = 40, model: str | None = None) -> int:
    """빈 항목을 LLM으로 채운다 — 한국어 원문과 영어 번역(있으면)을 함께 주고 한 번에 batch개씩.

    자리표시({n} 등)가 원문과 다른 번역은 버린다(빈 채로 남아 한국어로 보인다). 한 번 더 돌려 빠진 것을 채운다.
    """
    from pydantic import BaseModel

    from narani import config, langs

    class _Item(BaseModel):
        ko: str
        tr: str

    class _Out(BaseModel):
        items: list[_Item]

    path = i18n.LOCALES / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for k in i18n.collect():
        data.setdefault(k, "")
    en = i18n.catalog("en")
    prov = config.build_provider(None, model)
    name = langs.get(lang).english
    system = (
        f"You translate the user-interface strings of Narani, a desktop app for reading academic papers "
        f"(PDF → Markdown, side-by-side translation, glossaries, notes, an AI agent), from Korean into {name}.\n"
        "Each item has the Korean source `ko` and, when available, an English translation `en` for reference. "
        f"Translate the Korean meaning into natural, concise {name} UI text (buttons short, tooltips plain).\n"
        "Rules: keep placeholders such as {n}, {name}, {cost:.4f} exactly (same names, same format spec); "
        "keep product and file names (Narani, GitHub, OpenAI, Obsidian, Notion, BibTeX, DOI, arXiv, LaTeX, PDF, "
        "references.bib, sections.yaml, notes/.trash/) and keyboard keys (Esc, Enter, Shift+Enter, ⌘/Ctrl+C) as is; "
        "keep leading/trailing spaces and punctuation like ' · ' or ' — ' at the same ends; keep Markdown ** and ` marks.\n"
        f"Use these terms consistently: {_TERMS.get(lang, '')}.\n"
        "Return items [{ko, tr}] for every input item, with `ko` copied exactly.")
    done = 0
    for _round in range(2):
        todo = [k for k, v in data.items() if not v]
        for i in range(0, len(todo), batch):
            part = [{"ko": k, **({"en": en[k]} if k in en else {})} for k in todo[i:i + batch]]
            try:
                res = prov.parse(system, json.dumps(part, ensure_ascii=False), _Out, max_tokens=16000)
            except Exception as e:  # noqa: BLE001 — 한 묶음이 실패해도 나머지는 계속
                print(f"  묶음 {i // batch + 1} 실패: {e}")
                continue
            for it in res.items:
                if it.ko in data and not data[it.ko] and it.tr.strip() and i18n.fields(it.ko) == i18n.fields(it.tr):
                    data[it.ko] = it.tr
                    done += 1
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"  {lang}: {done}개 · 비용 ≈ ${prov.cost():.4f}", flush=True)
    left = sum(1 for v in data.values() if not v)
    print(f"{lang}: {done}개 채움 · 빈 항목 {left}개 · 비용 ≈ ${prov.cost():.4f} ({prov.model})")
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
    f.add_argument("--model", default=None, help="번역에 쓸 모델 (기본: 설정의 기본 모델)")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        r = i18n.sync(a.lang, prune=a.prune)
        print(f"{a.lang}: 새 문구 {len(r['added'])}개 · 안 쓰는 항목 {len(r['unused'])}개"
              f"{' (지움)' if a.prune else ''} · 번역 {r['translated']}/{r['total']}")
        return 0
    if a.cmd == "fill":
        return _fill(a.lang, model=a.model)
    bad = 0
    for lang in i18n.available()[1:]:
        for p in i18n.problems(lang):
            print(f"{lang}: {p}")
            bad += 1
    print("카탈로그 문제 없음" if not bad else f"문제 {bad}개")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
