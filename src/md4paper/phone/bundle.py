"""논문 한 편 → 폰 리더 번들, 고른 논문들 → 목록(index.json).

번들은 모바일 HTML(§mobile)과 **같은 렌더**를 쓴다 — 행(`align_rows`)·셀·문장 짝·인용 시트가 한 곳에서
나온다. 다른 점은 논문을 하나씩 따로 굽는다는 것뿐이다(폰이 필요한 논문만 받아 오프라인에 둔다).

`hash`는 번들을 만든 재료(원문·번역·그림·서지·렌더 코드)의 지문이다. 굽기 전에 싸게 셀 수 있어서
바뀌지 않은 논문은 다시 굽지도 올리지도 않고, 폰은 같은 지문이면 다시 받지 않는다. 메모 병합(2단계)도
이 지문으로 '어느 판의 본문에 붙인 메모인지'를 가린다 — 형식(FORMAT)을 바꾸면 올려 둔 번들이 모두
다시 구워진다.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from md4paper.workdir import WorkDir

FORMAT = 1
PID = "p0"  # 리더는 논문을 한 편씩 띄운다 — id 접두어는 하나로 충분하다

# 렌더 결과를 바꾸는 코드 — 이것들이 바뀌면(앱 업데이트) 지문도 바뀌어 번들을 다시 굽는다.
# mobile.py는 통째로가 아니라 렌더 함수만 본다: CSS·JS는 번들이 아니라 웹앱(§phone.web)에 실린다.
# 번들 모양(build_paper)을 바꾸면 FORMAT을 올린다.
_RENDER_FUNCS = ("_image_bytes", "_sub_image", "_localize_images", "_render", "_split_shared_images",
                 "_clean_heading", "_first_h1", "_paper", "_paper_html")
_RENDER_MODULES = ("ui/mdrender.py",)


def _code_stamp() -> str:
    import inspect

    from md4paper.ui import mobile

    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for name in _RENDER_FUNCS:
        h.update(inspect.getsource(getattr(mobile, name)).encode())
    for rel in _RENDER_MODULES:
        try:
            h.update((root / rel).read_bytes())
        except OSError:
            h.update(rel.encode())
    return h.hexdigest()


def file_name(key: str) -> str:
    """논문 키 → 저장소 안의 번들 파일 이름."""
    return "papers/" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20] + ".json"


def content_hash(root: Path, code: str | None = None) -> str:
    """번들 재료의 지문 — 굽지 않고 파일 내용·그림 목록만 읽는다."""
    wd = WorkDir(root)
    h = hashlib.sha256(f"format={FORMAT};code={code or _code_stamp()};lang={wd.lang}".encode())
    files = [wd.en_md, wd.ko_md, wd.paper_meta_json, wd.footnotes_json, wd.sections_yaml]
    if wd.cite.is_dir():
        files += sorted(wd.cite.glob("*.json"))
    for f in files:
        h.update(b"\0" + f.name.encode())
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"-")
    if wd.out_images.is_dir():
        for img in sorted(wd.out_images.iterdir()):
            try:
                st = img.stat()
            except OSError:
                continue
            h.update(f"\0{img.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:32]


def build_paper(root: Path, digest: str | None = None) -> dict:
    """작업 디렉토리 → 번들(dict). `html`은 리더가 그대로 꽂는 `<article>` 하나다."""
    from md4paper.ui import mobile

    p = mobile._paper(0, root)
    wd = WorkDir(root)
    return {
        "format": FORMAT,
        "key": p["key"],
        "hash": digest or content_hash(root),
        "lang": wd.lang,
        "title": p["title"],
        "title_ko": p["title_ko"],
        "authors": p["authors"],
        "year": p["year"],
        "venue": p["venue"],
        "has_ko": p["has_ko"],
        "aligned": p["aligned"],
        "html": mobile._paper_html(p),
        "cites": p["cites"],
        "fns": p["fns"],
        "built": time.time(),
    }


def index_entry(bundle: dict, size: int, extra: dict | None = None) -> dict:
    """번들 → 목록 한 줄 (본문은 빼고 목록 카드에 필요한 것만)."""
    out = {k: bundle[k] for k in ("key", "hash", "lang", "title", "title_ko", "authors", "year", "venue",
                                  "has_ko", "aligned")}
    out["file"] = file_name(bundle["key"])
    out["size"] = size
    out.update(extra or {})
    return out


def dumps(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
