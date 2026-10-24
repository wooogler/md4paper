"""모바일로 읽는 논문 묶음 — 고른 논문들을 **HTML 한 장**으로 (서버 없이, 밖에서도 폰으로 연다).

로컬 앱은 Mac의 127.0.0.1에만 떠 있어 밖에서는 닿지 않는다. 그래서 읽기에 필요한 것을 전부 파일
하나에 굳혀 둔다 — 원문·번역 렌더 결과, 그림(폰에 맞게 줄여 data URI로), 인용·각주 서지 정보.
iCloud Drive·구글 드라이브 같은 동기화 폴더에 두면 오프라인에서도 열린다.

**JS 없이도 읽힌다.** iOS 파일 앱의 미리보기(Quick Look)는 HTML을 열 때 JavaScript를 끈다.
그래서 뼈대는 CSS만으로 동작하게 만든다:
  - 보기 모드(번역 / 원문 / 문단 대조)는 라디오 버튼 + `:checked` 선택자로 바꾼다.
  - 목차·인용·각주는 평범한 앵커 링크다(논문·언어별로 id에 접두어를 붙여 겹치지 않게).
브라우저로 열면 JS가 그 위에 얹힌다 — 논문 목록 화면, 문장을 누르면 짝 문장(원문↔번역)이
아래 시트에 뜨는 기능, 인용을 누르면 어떤 논문인지 보여 주는 시트, 문단 하나만 대조로 펼치기,
글자 크기, 읽던 위치·모드 기억.

**행 = 뷰어의 정렬 행.** 원문·번역의 짝은 뷰어와 같은 `align_rows`(섹션→문단 정렬)로 만들고,
문장 짝은 뷰어와 같은 JS(§sentpair)로 누를 때 그 행만 맞춘다 — 두 화면의 짝이 갈라지지 않는다.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
import time
from pathlib import Path

from narani import i18n
from narani.i18n import t
from narani.ui import mdrender, sentpair
from narani.workdir import WorkDir, paper_title

# 그림 — 폰 화면(최대 3x 레티나 ~1300px)에 충분한 폭으로 줄이고, 이미 작은 파일은 그대로 둔다.
MAX_IMG_W = 1200
KEEP_IMG_BYTES = 120_000
_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".svg": "image/svg+xml"}

# 렌더 결과에서 줄 번호 표식(분할 뷰 전용)은 떼어 낸다 — 이 파일에선 쓸 데가 없고 크기만 는다.
_LINE_ATTR_RE = re.compile(r' data-line="\d+"| class="code-line"')
_ID_RE = re.compile(r'(\sid=")([^"]+)(")')
_TAG_ID_RE = re.compile(r'(<[a-zA-Z][^>]*?)\sid="([^"]+)"')
_HREF_HASH_RE = re.compile(r'(\shref="#)([^"]*)(")')
_HREF_EXT_RE = re.compile(r'<a href="(https?://[^"]+)"')
_IMG_SRC_RE = re.compile(r'(<img\s[^>]*src=")md4img/([^"]+)(")')
_IMG_LINE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\s[^>]*>")
_HEAD_MARKUP_RE = re.compile(r"<[^>]+>|\\|[*_`]|\[([^\]]*)\]\([^)]*\)")


def _image_bytes(path: Path) -> tuple[bytes, str, tuple[int, int] | None] | None:
    """그림 파일 → (바이트, MIME, (폭, 높이)). 크면 폭을 줄이고 PNG·WebP 중 작은 쪽으로 다시 굽는다."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    mime = _IMG_MIME.get(path.suffix.lower(), "image/png")
    try:
        from PIL import Image, features

        with Image.open(io.BytesIO(raw)) as im:
            size = im.size
            if len(raw) <= KEEP_IMG_BYTES and im.size[0] <= MAX_IMG_W:
                return raw, mime, size
            im.load()
            if im.size[0] > MAX_IMG_W:
                im = im.resize((MAX_IMG_W, max(1, round(im.size[1] * MAX_IMG_W / im.size[0]))),
                               Image.LANCZOS)
            if im.mode in ("RGBA", "LA", "P"):  # 투명 배경은 흰 바탕에 얹는다(JPEG엔 알파가 없다)
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            png, lossy = io.BytesIO(), io.BytesIO()
            # 도표·선 그림은 PNG가 작고 선명하다. 색이 256개 이하면 팔레트로 — 손실 없이 훨씬 작다.
            (im.quantize(256) if im.getcolors(256) else im).save(png, "PNG", optimize=True)
            # 사진·스크린샷은 WebP(iOS 14+·안드로이드 크롬) — 실측 GEPA 36장: JPEG 5.6MB → WebP 3.6MB
            if features.check("webp"):
                im.save(lossy, "WEBP", quality=80, method=4)
                lossy_mime = "image/webp"
            else:
                im.save(lossy, "JPEG", quality=82, optimize=True, progressive=True)
                lossy_mime = "image/jpeg"
    except Exception:  # noqa: BLE001 — PIL 없음·깨진 그림: 원본 그대로 (크기는 모른다)
        return raw, mime, None
    best = min((png.getvalue(), "image/png"), (lossy.getvalue(), lossy_mime), key=lambda x: len(x[0]))
    return (best[0], best[1], im.size) if len(best[0]) < len(raw) else (raw, mime, size)


class _Images:
    """논문 한 편의 그림을 data URI로 — 같은 그림은 한 번만 굽는다."""

    def __init__(self, images_dir: Path) -> None:
        self.dir = images_dir
        self.cache: dict[str, tuple[str, tuple[int, int] | None]] = {}

    def get(self, name: str) -> tuple[str, tuple[int, int] | None]:
        """(data URI, (폭, 높이)) — 파일이 없으면 ("", None)."""
        if name not in self.cache:
            got = _image_bytes(self.dir / name)
            self.cache[name] = ((f"data:{got[1]};base64,{base64.b64encode(got[0]).decode('ascii')}", got[2])
                                if got else ("", None))
        return self.cache[name]


def _sub_image(m: re.Match, images: _Images) -> str:
    """`md4img/<이름>` → data URI + width/height.

    크기를 적어 두면 그림이 디코딩되기 전에도 자리가 잡힌다 — 없으면 그림 높이가 0이었다가
    늘어나며 본문이 밀려, 읽던 위치 복원·참고문헌 이동이 수백 px씩 빗나간다(실측 537px).
    """
    uri, size = images.get(html.unescape(m.group(2)))
    dims = f' width="{size[0]}" height="{size[1]}"' if size and " width=" not in m.group(1) else ""
    return m.group(1) + uri + m.group(3) + dims


def _localize_images(md: str) -> str:
    """마크다운·HTML의 그림 경로를 `md4img/<파일명>` 자리표시로 — 렌더 뒤 data URI로 갈아 끼운다.

    data URI를 마크다운 단계에서 넣으면 수 MB짜리 문자열이 파서를 통과한다. 파일명만 남겨 두고
    HTML에서 바꾸는 편이 싸다.
    """
    from narani.ui.app import _HTML_IMG_RE, _MD_IMG_RE

    def sub(m: re.Match) -> str:
        path = m.group(2).strip()
        if path.startswith(("http://", "https://", "data:")):
            return m.group(0)
        name = path.split("#")[0].rstrip("/").split("/")[-1]
        return m.group(1) + "md4img/" + name + m.group(3)

    return _HTML_IMG_RE.sub(sub, _MD_IMG_RE.sub(sub, md))


def _render(md: str, prefix: str, images: _Images, shared: bool = False) -> str:
    """마크다운 블록 → HTML. id·내부 링크에 접두어를 붙여 여러 논문·두 언어가 한 문서에 공존하게 한다.

    prefix는 `p<n>-ko-`처럼 논문·언어를 가른다 — 원문 셀의 인용은 원문 쪽 참고문헌으로, 번역 셀의
    인용은 번역 쪽으로 간다(숨은 쪽으로 가면 화면이 안 움직인다).

    shared는 원문·번역이 같아 한 번만 싣는 셀이다. 번역하지 않은 참고문헌 목록이 대개 그렇다.
    양쪽 셀의 링크가 모두 이리로 와야 하므로 id마다 원문용 별칭 앵커를 하나 더 심는다 —
    JS 없이(iOS 미리보기) 인용을 눌러도 참고문헌으로 이동하게.
    """
    out = _LINE_ATTR_RE.sub("", mdrender.render(_localize_images(md)))
    if shared:
        en_prefix = prefix.replace("-ko-", "-en-")
        out = _TAG_ID_RE.sub(
            lambda m: f'<a id="{en_prefix}{m.group(2)}"></a>{m.group(1)} id="{prefix}{m.group(2)}"', out)
    else:
        out = _ID_RE.sub(lambda m: m.group(1) + prefix + m.group(2) + m.group(3), out)
    link_prefix = prefix
    out = _HREF_HASH_RE.sub(lambda m: m.group(1) + link_prefix + m.group(2) + m.group(3), out)
    out = _HREF_EXT_RE.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener"', out)
    return _IMG_SRC_RE.sub(lambda m: _sub_image(m, images), out)


def _split_shared_images(enb: str, kob: str) -> tuple[str, str, str]:
    """원문·번역 블록 앞머리의 같은 그림 줄을 떼어 (공통, 원문 나머지, 번역 나머지)로.

    그림 문단은 보통 `![Figure 1](…)` 바로 아래 캡션이 붙어 한 문단이라, 캡션이 번역되면 블록
    전체로는 같지 않다. 그림 줄만 공통 셀로 올려야 그림이 한 번만 실린다(실측: 고유 6장이 11번 실렸다).
    """
    el, kl = enb.split("\n"), kob.split("\n")
    n = 0
    while (n < min(len(el), len(kl)) and el[n].strip() == kl[n].strip()
           and _IMG_LINE_RE.fullmatch(el[n].strip())):
        n += 1
    return "\n".join(el[:n]), "\n".join(el[n:]), "\n".join(kl[n:])


def _clean_heading(text: str) -> str:
    return re.sub(r"\s+", " ", _HEAD_MARKUP_RE.sub(lambda m: m.group(1) or "", text)).strip()


def _first_h1(md: str) -> str:
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return _clean_heading(m.group(1)) if m else ""


def _paper(idx: int, root: Path) -> dict:
    """논문 한 편 → 목록 카드·본문 HTML·툴팁 데이터."""
    from narani import paper_meta
    from narani.ui.app import _heading_of, _split_sections, align_rows
    from narani.ui.controller import citation_tooltips, footnote_tooltips

    wd = WorkDir(root)
    en = wd.en_md.read_text(encoding="utf-8") if wd.en_md.exists() else ""
    ko = wd.ko_md.read_text(encoding="utf-8") if wd.ko_md.exists() else ""
    pid = f"p{idx}"
    images = _Images(wd.out_images)
    rows = align_rows(en, ko) if (en and ko) else None

    toc: list[tuple[int, str, str]] = []
    parts: list[str] = []

    def row_html(k: int, enb: str | None, kob: str | None, head) -> str:  # noqa: ANN001 — (lvl, text) | None
        rid = f"{pid}-r{k}"
        if head:
            text = _heading_of(kob)[1] if (kob and _heading_of(kob)) else head[1]
            toc.append((head[0], _clean_heading(text), rid))
        sep = " sec" if (head and k > 0) else ""
        if enb is not None and kob is not None and enb.strip() == kob.strip():
            # 원문·번역이 같은 블록(그림 문단·수식·표) — 한 번만 싣는다. 그림이 두 번 들어가면 파일이 두 배다.
            cell = f'<div class="cell same">{_render(enb, pid + "-ko-", images, shared=True)}</div>'
            return f'<section class="row{sep}" id="{rid}">{cell}</section>'
        cells = []
        if enb is not None and kob is not None:
            shared, enb, kob = _split_shared_images(enb, kob)
            if shared:
                cells.append(f'<div class="cell same">{_render(shared, pid + "-ko-", images, shared=True)}</div>')
        if kob is not None:
            cells.append(f'<div class="cell ko" lang="ko">{_render(kob, pid + "-ko-", images)}</div>')
        if enb is not None:
            cells.append(f'<div class="cell en" lang="en">{_render(enb, pid + "-en-", images)}</div>')
        return f'<section class="row{sep}" id="{rid}">{"".join(cells)}</section>'

    if rows is not None:
        for k, (enb, kob, head) in enumerate(rows):
            parts.append(row_html(k, enb, kob, head))
    else:
        # 번역이 없거나 정렬 실패 — 원문 섹션(·번역 섹션)을 따로 싣는다. 문단 대조는 쓸 수 없다.
        k = 0
        for s in _split_sections(en):
            parts.append(row_html(k, s, None, _heading_of(s) if not ko else None))
            k += 1
        for s in _split_sections(ko):
            parts.append(row_html(k, None, s, _heading_of(s)))
            k += 1

    meta = paper_meta.load(wd) or {}
    title = paper_title(root)
    return {
        "id": pid,
        "key": root.stem,
        "title": title,
        "title_ko": _first_h1(ko) if ko else "",
        "authors": paper_meta.authors_short(meta.get("authors") or []),
        "year": meta.get("year") or "",
        "venue": meta.get("venue") or "",
        "has_ko": bool(ko),
        "aligned": rows is not None,
        "toc": toc,
        "body": "".join(parts),
        "cites": {k: {"text": v.get("text", ""), "url": v.get("url", "")}
                  for k, v in citation_tooltips(wd).items()},
        "fns": footnote_tooltips(wd),
    }


def _esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def _paper_html(p: dict) -> str:
    cls = "paper" + (" has-ko" if p["has_ko"] else "") + ("" if p["aligned"] else " noalign")
    meta = " · ".join(_esc(x) for x in (p["authors"], p["year"], p["venue"]) if x)
    toc = "".join(
        f'<li class="l{min(max(lvl, 1), 4)}"><a href="#{rid}">{_esc(text)}</a></li>' for lvl, text, rid in p["toc"])
    notes = []
    if not p["has_ko"]:
        notes.append(t("번역이 없는 논문이라 원문만 보입니다."))
    elif not p["aligned"]:
        notes.append(t("원문과 번역의 섹션 수가 달라 문단 대조·문장 짝을 쓸 수 없습니다."))
    note = "".join(f'<p class="note">{n}</p>' for n in notes)
    return (
        f'<article class="{cls}" id="{p["id"]}" data-key="{_esc(p["key"])}">'
        f'<header class="ph"><p class="ph-meta">{meta}</p>{note}'
        f'<details class="toc"><summary>{t("목차")}</summary><ol>{toc}</ol></details></header>'
        f'{p["body"]}'
        f'<p class="end"><a href="#lib">↑ {t("논문 목록")}</a></p>'
        f'</article>'
    )


def _lib_html(papers: list[dict]) -> str:
    items = []
    for p in papers:
        meta = " · ".join(_esc(x) for x in (p["authors"], p["year"], p["venue"]) if x)
        sub = f'<span class="li-en">{_esc(p["title"])}</span>' if p["title_ko"] else ""
        badge = t("번역") if p["has_ko"] else t("원문만")
        items.append(
            f'<li><a href="#{p["id"]}" data-open="{p["id"]}">'
            f'<span class="li-title">{_esc(p["title_ko"] or p["title"])}</span>{sub}'
            f'<span class="li-meta">{meta}<span class="badge">{badge}</span></span></a></li>')
    return f'<nav id="lib"><h1>{t("논문 {n}편", n=len(papers))}</h1><ol>{"".join(items)}</ol></nav>'


def build(roots: list[Path]) -> str:
    """고른 논문들(작업 디렉토리 경로) → 모바일 HTML 문서 한 장."""
    papers = [_paper(i + 1, Path(r)) for i, r in enumerate(roots)]
    data = {p["id"]: {"key": p["key"], "title": p["title_ko"] or p["title"],
                      "cites": p["cites"], "fns": p["fns"]} for p in papers}
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return (
        f"<!doctype html><html lang=\"{i18n.ui_lang()}\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
        "<meta name=\"color-scheme\" content=\"light dark\">"
        f"<title>Narani · {t('논문 {n}편', n=len(papers))}</title><style>{_CSS}</style>"
        f"{i18n.head_html()}</head><body>"
        # 라디오는 본문(main)·상단 바보다 **앞의 형제**여야 `:checked ~` 선택자가 닿는다
        "<input type=\"radio\" name=\"mode\" id=\"m-ko\" class=\"mode\" checked>"
        "<input type=\"radio\" name=\"mode\" id=\"m-en\" class=\"mode\">"
        "<input type=\"radio\" name=\"mode\" id=\"m-both\" class=\"mode\">"
        "<header class=\"top\">"
        f"<button class=\"only-js back\" id=\"back\" aria-label=\"{t('논문 목록')}\">☰</button>"
        "<span class=\"top-title\" id=\"top-title\">Narani</span>"
        f"<span class=\"seg\"><label for=\"m-ko\">{t('번역')}</label><label for=\"m-en\">{t('원문')}</label>"
        f"<label for=\"m-both\">{t('대조')}</label></span>"
        f"<button class=\"only-js fs\" id=\"fs-dn\" aria-label=\"{t('글자 작게')}\">{t('가')}−</button>"
        f"<button class=\"only-js fs\" id=\"fs-up\" aria-label=\"{t('글자 크게')}\">{t('가')}+</button>"
        "</header>"
        f"<main>{_lib_html(papers)}{''.join(_paper_html(p) for p in papers)}"
        f"<p class=\"stamp\">{t('Narani에서 {stamp}에 만듦', stamp=stamp)}</p></main>"
        "<div id=\"sheet\" role=\"dialog\" aria-live=\"polite\"><div class=\"sh-grip\"></div>"
        "<div class=\"sh-label\"></div><div class=\"sh-text\"></div><div class=\"sh-acts\"></div></div>"
        f"<script>var DATA = {payload};\n{_JS}</script>"
        "</body></html>"
    )


def file_name(n: int) -> str:
    return t("narani-mobile-{n}편-{date}.html", n=n, date=time.strftime("%Y%m%d"))


_CSS = """
:root { --bg:#fbfaf7; --fg:#1f1e1c; --muted:#6b6862; --line:#e4e1da; --card:#fff; --accent:#2f6fd0;
  --hl:#ffe9a8; --hl2:#fff4cf; --sheet:#fff; --fs:17px; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --fg:#e8e6e1; --muted:#9a968f; --line:#33322f; --card:#1f1f1d; --accent:#7fb0ff;
    --hl:#5a4a16; --hl2:#3a3218; --sheet:#232321; }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--fg); font-size: var(--fs); line-height: 1.75;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", "Segoe UI", sans-serif;
  word-break: keep-all; overflow-wrap: anywhere; }
a { color: var(--accent); }
[id] { scroll-margin-top: 64px; }
input.mode { position: absolute; opacity: 0; pointer-events: none; }

.top { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; padding-top: max(8px, env(safe-area-inset-top)); background: var(--bg);
  border-bottom: 1px solid var(--line); font-size: 15px; line-height: 1.2; }
.top-title { flex: 1; min-width: 0; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 8px;
  font: inherit; padding: 6px 9px; min-height: 34px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; flex: none; }
.seg label { padding: 7px 10px; min-height: 34px; color: var(--muted); cursor: pointer; user-select: none; }
.seg label + label { border-left: 1px solid var(--line); }
#m-ko:checked ~ .top label[for=m-ko], #m-en:checked ~ .top label[for=m-en],
#m-both:checked ~ .top label[for=m-both] { background: var(--fg); color: var(--bg); font-weight: 600; }
.only-js { display: none; }
.js .only-js { display: inline-block; }

main { max-width: 720px; margin: 0 auto; padding: 0 16px 45vh; }
#lib h1 { font-size: 1.15em; margin: 20px 0 8px; }
#lib ol { list-style: none; padding: 0; margin: 0 0 32px; }
#lib li a { display: block; padding: 12px 14px; margin: 8px 0; background: var(--card); color: var(--fg);
  border: 1px solid var(--line); border-radius: 12px; text-decoration: none; line-height: 1.45; }
.li-title { display: block; font-weight: 600; }
.li-en { display: block; color: var(--muted); font-size: .85em; margin-top: 2px; }
.li-meta { display: flex; gap: 8px; align-items: center; color: var(--muted); font-size: .8em; margin-top: 6px; }
.badge { margin-left: auto; flex: none; white-space: nowrap; border: 1px solid var(--line); border-radius: 999px;
  padding: 0 8px; }

.paper { border-top: 1px solid var(--line); padding-top: 8px; }
.ph-meta { color: var(--muted); font-size: .85em; margin: 10px 0 4px; }
.note { font-size: .85em; color: var(--muted); background: var(--card); border: 1px dashed var(--line);
  border-radius: 8px; padding: 6px 10px; }
.toc { margin: 8px 0 16px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; }
.toc summary { padding: 9px 12px; cursor: pointer; font-weight: 600; font-size: .9em; }
.toc ol { list-style: none; margin: 0; padding: 0 12px 10px; font-size: .9em; line-height: 1.5; }
.toc li { padding: 4px 0; } .toc li.l3 { padding-left: 14px; } .toc li.l4 { padding-left: 28px; }
.toc a { color: var(--fg); text-decoration: none; }
.end, .stamp { text-align: center; color: var(--muted); font-size: .8em; margin: 32px 0; }

/* 본문 */
.row.sec { margin-top: 1.2em; }
.cell h1 { font-size: 1.45em; line-height: 1.35; margin: .6em 0 .4em; }
.cell h2 { font-size: 1.22em; line-height: 1.4; margin: 1.2em 0 .4em; }
.cell h3, .cell h4, .cell h5, .cell h6 { font-size: 1.06em; margin: 1em 0 .3em; }
.cell p { margin: 0 0 .9em; }
.cell img { display: block; max-width: 100%; height: auto; margin: 10px auto; border-radius: 4px; background: #fff; }
.cell blockquote { margin: 0 0 1em; padding: 2px 0 2px 12px; border-left: 3px solid var(--line);
  color: var(--muted); font-size: .9em; }
.cell table { display: block; overflow-x: auto; border-collapse: collapse; font-size: .82em; margin: 0 0 1em;
  max-width: 100%; }
.cell th, .cell td { border: 1px solid var(--line); padding: 4px 8px; vertical-align: top; }
.cell pre { overflow-x: auto; font-size: .82em; background: var(--card); padding: 10px; border-radius: 8px; }
.cell code { font-size: .9em; }
.cell math[display=block] { display: block; overflow-x: auto; overflow-y: hidden; margin: .6em 0; }
/* 인라인 수식은 줄을 바꾸지 못한다 — 화면보다 길면 페이지 전체가 가로로 늘어나 아래 시트까지 잘린다 */
.cell math:not([display=block]) { display: inline-block; max-width: 100%; overflow-x: auto; overflow-y: hidden;
  vertical-align: middle; }
.cell ul, .cell ol { padding-left: 1.3em; margin: 0 0 .9em; }
.cell a[href*="-ref-"], .cell sup a { text-decoration: none; border-bottom: 1px dotted currentColor; }
.cell sup { line-height: 0; }
.cell .md-math-raw { white-space: pre-wrap; }

/* 보기 모드 — 번역 / 원문 / 문단 대조 (JS 없이 CSS로만).
   펼친 문단(.peek)은 숨기는 규칙보다 우선해야 한다 — 숨기는 규칙이 #id를 품고 있어서 펼치는 쪽도
   같은 #id로 시작하고 클래스를 하나 더 얹어 이긴다. */
#m-ko:checked ~ main .paper.has-ko .cell.en { display: none; }
#m-en:checked ~ main .cell.ko { display: none; }
#m-both:checked ~ main .paper.noalign .cell.en { display: none; }
#m-ko:checked ~ main .paper .row.peek .cell.en, #m-en:checked ~ main .paper .row.peek .cell.ko { display: block; }
/* 아래에 붙는 쪽(대조 모드의 원문, 펼친 문단의 상대 언어)은 옅게 · 왼쪽 선 */
#m-both:checked ~ main .paper.has-ko:not(.noalign) .cell.en,
#m-ko:checked ~ main .paper .row.peek .cell.en, #m-en:checked ~ main .paper .row.peek .cell.ko {
  font-size: .9em; color: var(--muted); border-left: 3px solid var(--line); padding-left: 11px; margin: -.3em 0 1.2em; }
/* 원문 모드에서 펼친 번역은 원문 '아래'로 (DOM은 번역이 먼저다) */
#m-en:checked ~ main .paper .row.peek .cell.ko { order: 2; margin: .2em 0 1.2em; }
#m-both:checked ~ main .cell.en img, #m-ko:checked ~ main .paper .row.peek .cell.en img { display: none; }
#m-both:checked ~ main .cell.en :is(h1, h2, h3, h4, h5, h6),
#m-ko:checked ~ main .paper .row.peek .cell.en :is(h1, h2, h3, h4, h5, h6) { font-size: 1.05em; margin-top: .2em; }
.row.peek { display: flex; flex-direction: column; background: var(--hl2); border-radius: 8px; margin: 0 -8px;
  padding: 4px 8px 1px; }

/* JS가 켜지면 — 목록 화면과 논문 한 편씩 */
.js .paper { display: none; border: 0; }
.js .paper.on { display: block; }
.js #lib.off, .js .end { display: none; }
.sent { border-radius: 3px; -webkit-tap-highlight-color: transparent; }
.sent.on { background: var(--hl); }

#sheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 10; max-height: 55vh; overflow-y: auto;
  background: var(--sheet); border-top: 1px solid var(--line); border-radius: 16px 16px 0 0;
  box-shadow: 0 -6px 24px rgba(0,0,0,.14); padding: 6px 18px calc(14px + env(safe-area-inset-bottom));
  transform: translateY(105%); transition: transform .18s ease; visibility: hidden; }
#sheet.open { transform: none; visibility: visible; }
.sh-grip { width: 36px; height: 4px; border-radius: 2px; background: var(--line); margin: 4px auto 10px; }
.sh-label { font-size: .75em; font-weight: 600; color: var(--muted); letter-spacing: .02em; margin-bottom: 4px; }
.sh-text { font-size: .95em; line-height: 1.65; }
.sh-text .more { color: var(--muted); font-size: .9em; margin-top: 6px; }
.sh-acts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.sh-acts a, .sh-acts button { border: 1px solid var(--line); background: var(--card); color: var(--fg);
  border-radius: 9px; padding: 7px 12px; font: inherit; font-size: .85em; text-decoration: none; }
"""

# 읽기 화면의 공통 동작 — 보기 모드·글자 크기·아래 시트·문장 짝·인용/각주. 목록↔논문 전환(_ROUTE_JS)은
# 쓰는 쪽이 따로 붙인다: 이 파일(논문 전부가 한 문서에)과 폰 리더(§phone.web, 논문을 하나씩 불러옴).
# 붙이는 쪽이 `cur`(연 논문 article)를 채우고 `showLib(push)`·`openPaper(id, push, target)`·`DATA`를 둔다.
CORE_JS = r"""
  var doc = document, body = doc.body;
  body.classList.add('js');
  var sheet = doc.getElementById('sheet');
  var shLabel = sheet.querySelector('.sh-label'), shText = sheet.querySelector('.sh-text'),
      shActs = sheet.querySelector('.sh-acts');
  var lib = doc.getElementById('lib'), topTitle = doc.getElementById('top-title');
  var cur = null;          // 지금 연 논문 article
  var tapped = null;       // 시트를 연 문장 짝 (같은 문장을 다시 누르면 닫는다)

  // ---- 저장 (이 브라우저에만 — 없거나 막혀 있어도 동작은 한다) ----
  function load(k, d){ try { var v = localStorage.getItem('md4m:' + k); return v === null ? d : v; } catch (e) { return d; } }
  function store(k, v){ try { localStorage.setItem('md4m:' + k, v); } catch (e) {} }

  // ---- 보기 모드 · 글자 크기 ----
  var mode = load('mode', 'ko');
  if (doc.getElementById('m-' + mode)) doc.getElementById('m-' + mode).checked = true;
  // 모드를 바꿔도 읽던 문단을 지킨다. change 때는 CSS가 이미 바뀌어 문단 위치가 달라져 있으므로
  // 라벨을 누르는 순간(바뀌기 전) 화면 맨 위 문단을 잡아 둔다.
  var modeAnchor = null;
  [].forEach.call(doc.querySelectorAll('.seg label'), function(l){
    l.addEventListener('pointerdown', function(){ modeAnchor = topRow(); });
  });
  [].forEach.call(doc.querySelectorAll('input.mode'), function(r){
    r.addEventListener('change', function(){
      var anchor = modeAnchor || topRow(); modeAnchor = null;
      clearSent(); closeSheet();
      store('mode', r.id.slice(2));
      if (anchor) anchor.scrollIntoView({block: 'start'});
    });
  });
  var fs = +load('fs', 17) || 17;
  function setFs(v){ fs = Math.max(13, Math.min(26, v)); doc.documentElement.style.setProperty('--fs', fs + 'px'); store('fs', fs); }
  setFs(fs);
  doc.getElementById('fs-dn').onclick = function(){ var a = topRow(); setFs(fs - 1); if (a) a.scrollIntoView({block: 'start'}); };
  doc.getElementById('fs-up').onclick = function(){ var a = topRow(); setFs(fs + 1); if (a) a.scrollIntoView({block: 'start'}); };

  // ---- 화면 맨 위 문단 ----
  function topRow(){
    if (!cur) return null;
    var rows = cur.querySelectorAll('.row'), y = 70, i;
    for (i = 0; i < rows.length; i++){
      var r = rows[i].getBoundingClientRect();
      if (r.height && r.bottom > y) return rows[i];
    }
    return null;
  }
  // ---- 아래 시트 ----
  function closeSheet(){ sheet.classList.remove('open'); tapped = null; }
  function openSheet(label, nodes, acts){
    shLabel.textContent = label; shText.innerHTML = ''; shActs.innerHTML = '';
    nodes.forEach(function(n){ shText.appendChild(n); });
    acts.forEach(function(a){ shActs.appendChild(a); });
    var close = doc.createElement('button'); close.textContent = md4T('닫기'); close.onclick = function(){ clearSent(); closeSheet(); };
    shActs.appendChild(close);
    sheet.scrollTop = 0; sheet.classList.add('open');
  }
  function para(text, cls){ var p = doc.createElement('div'); if (cls) p.className = cls; p.textContent = text; return p; }
  function button(text, fn){ var b = doc.createElement('button'); b.textContent = text; b.onclick = fn; return b; }

  // ---- 문장 짝 (누른 행만 그때 맞춘다 — 한 편에 행이 수백 개라 미리 다 하면 무겁다) ----
  function prepare(row){
    if (row.__pair !== undefined) return row.__pair;
    var en = row.querySelector('.cell.en'), ko = row.querySelector('.cell.ko');
    row.__pair = null;
    if (!en || !ko) return null;
    var E = sentsOf(en), K = sentsOf(ko), pairs = pairUp(E, K);
    if (!pairs) return null;
    var segE = [], segK = [], i, z;
    for (i = 0; i < pairs.length; i++){
      for (z = 0; z < pairs[i][0].length; z++) segE.push({s: E[pairs[i][0][z]].s, e: E[pairs[i][0][z]].e, pi: i});
      for (z = 0; z < pairs[i][1].length; z++) segK.push({s: K[pairs[i][1][z]].s, e: K[pairs[i][1][z]].e, pi: i});
    }
    var txtE = en.textContent, txtK = ko.textContent;   // 감싸도 textContent는 그대로다
    function make(seg){ var sp = doc.createElement('span'); sp.className = 'sent'; sp.setAttribute('data-pi', seg.pi); return sp; }
    wrapRanges(en, segE, make); wrapRanges(ko, segK, make);
    row.__pair = {pairs: pairs, en: E, ko: K, txt: {en: txtE, ko: txtK}};
    return row.__pair;
  }
  function clearSent(){
    [].forEach.call(doc.querySelectorAll('.sent.on'), function(s){ s.classList.remove('on'); });
  }
  function sentText(pr, side, pi){
    var idx = pr.pairs[pi][side === 'en' ? 0 : 1], src = pr[side], out = [];
    for (var i = 0; i < idx.length; i++) out.push(pr.txt[side].slice(src[idx[i]].s, src[idx[i]].e));
    return out.join(' ');
  }
  function bothVisible(row){
    return doc.getElementById('m-both').checked || row.classList.contains('peek');
  }
  function peekButton(row){
    return button(row.classList.contains('peek') ? md4T('문단 대조 닫기') : md4T('이 문단 대조해서 보기'), function(){
      row.classList.toggle('peek'); clearSent(); closeSheet();
    });
  }
  function onSentence(e, cell){
    var row = cell.parentElement, side = cell.classList.contains('ko') ? 'ko' : 'en';
    if (!row.querySelector('.cell.en') || !row.querySelector('.cell.ko')){ clearSent(); closeSheet(); return; }  // 짝이 될 쪽이 없는 행
    var other = side === 'ko' ? 'en' : 'ko', label = other === 'en' ? md4T('원문') : md4T('번역');
    var pr = prepare(row);
    if (!pr){
      if (bothVisible(row)) return;
      openSheet(label, [para(md4T('이 문단은 문장 짝을 맞추지 못했어요. 문단째로 대조해 보세요.'), 'more')], [peekButton(row)]);
      return;
    }
    var pi = sentAt(e, cell, pr, side);
    if (pi === null){ clearSent(); closeSheet(); return; }
    var key = row.id + ':' + pi;
    clearSent();
    if (tapped === key){ closeSheet(); return; }
    [].forEach.call(row.querySelectorAll('.sent[data-pi="' + pi + '"]'), function(s){ s.classList.add('on'); });
    // 쓰는 쪽이 문장에 붙일 동작(폰 리더의 하이라이트·메모)을 줄 수 있다 — {nodes, buttons}
    var acts = typeof sentenceActs === 'function' ? sentenceActs(row, +pi, pr, side) : null;
    if (bothVisible(row) && !acts){ tapped = null; closeSheet(); return; }   // 양쪽이 다 보이면 짝 표시로 충분
    var nodes = [];
    if (!bothVisible(row)){
      var txt = sentText(pr, other, +pi);
      nodes.push(para(txt || md4T('(짝이 되는 문장이 없어요 — 번역에서 합쳐지거나 빠진 문장)'), txt ? '' : 'more'));
    }
    openSheet(bothVisible(row) ? '' : label, nodes.concat(acts ? acts.nodes : []), acts ? acts.buttons : []);
    tapped = key;
  }
  function sentAt(e, cell, pr, side){
    // 방금 감쌌으면 e.target은 감싸기 전 노드다 — 손가락 아래를 다시 본다
    var under = doc.elementFromPoint(e.clientX, e.clientY);
    var sp = under && under.closest ? under.closest('.sent') : null;
    if (sp && cell.contains(sp)) return sp.getAttribute('data-pi');
    // 못 찾으면 글자 위치로 — 누른 자리의 오프셋을 품은 문장
    var r = doc.caretRangeFromPoint ? doc.caretRangeFromPoint(e.clientX, e.clientY) : null;
    if (!r || !cell.contains(r.startContainer)) return null;
    var pre = doc.createRange(); pre.selectNodeContents(cell);
    try { pre.setEnd(r.startContainer, r.startOffset); } catch (err) { return null; }
    var off = pre.toString().length, list = pr[side], i, k;
    for (i = 0; i < pr.pairs.length; i++){
      var idx = pr.pairs[i][side === 'en' ? 0 : 1];
      for (k = 0; k < idx.length; k++) if (list[idx[k]].s <= off && off < list[idx[k]].e) return String(i);
    }
    return null;
  }

  // ---- 인용 · 각주 ----
  function visibleTarget(id){
    // 같은 앵커가 원문(-en-)·번역(-ko-) 셀에 하나씩 있다 — 지금 보이는 쪽을 고른다
    var m = /^(p\d+)-(?:en|ko)-(.*)$/.exec(id), cands = [id];
    if (m) cands.push(m[1] + '-ko-' + m[2], m[1] + '-en-' + m[2]);
    for (var i = 0; i < cands.length; i++){
      var el = doc.getElementById(cands[i]);
      if (el && el.getClientRects().length) return el;
    }
    return doc.getElementById(id);
  }
  function blockText(el){
    var b = el && el.closest ? el.closest('p, li, td, div.cell') : null;
    return b ? b.textContent.replace(/\s+/g, ' ').trim() : '';
  }
  function onLink(a, e){
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '#') return;               // 바깥 링크는 그대로 (새 창)
    var id = href.slice(1), m = /^(p\d+)-(?:en|ko)-(ref|fn)-(.+)$/.exec(id);
    e.preventDefault();
    if (m){
      var info = (DATA[m[1]] || {})[m[2] === 'ref' ? 'cites' : 'fns'] || {}, tip = info[m[3]];
      var text = (m[2] === 'ref' ? (tip && tip.text) : tip) || blockText(visibleTarget(id));
      var acts = [];
      if (m[2] === 'ref' && tip && tip.url){
        var go = doc.createElement('a'); go.href = tip.url; go.target = '_blank'; go.rel = 'noopener';
        go.textContent = md4T('논문 링크 열기'); acts.push(go);
      }
      acts.push(button(m[2] === 'ref' ? md4T('참고문헌 목록에서 보기') : md4T('각주로 이동'), function(){
        closeSheet(); var t = visibleTarget(id); if (t) t.scrollIntoView({block: 'start'});
      }));
      openSheet((m[2] === 'ref' ? md4T('참고문헌 {n}', {n: m[3]}) : md4T('각주 {n}', {n: m[3]})), [para(text || md4T('(서지 정보를 찾지 못했어요)'))], acts);
      return;
    }
    if (id === 'lib'){ showLib(true); return; }
    var pm = /^(p\d+)$/.exec(id);
    if (pm){ openPaper(pm[1], true); return; }
    var t = visibleTarget(id);
    if (t) t.scrollIntoView({block: 'start'});
  }

  doc.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (a){ if (!sheet.contains(a)) onLink(a, e); return; }
    if (sheet.contains(e.target) || e.target.closest('.top')) return;
    var sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed) return;               // 길게 눌러 고르는 중이면 건드리지 않는다
    var cell = e.target.closest('.row .cell.ko, .row .cell.en');
    if (!cell || e.target.closest('img, table, math, pre')){ clearSent(); closeSheet(); return; }
    onSentence(e, cell);
  });
  doc.addEventListener('keydown', function(e){ if (e.key === 'Escape'){ clearSent(); closeSheet(); } });

"""

_ROUTE_JS = r"""
  // ---- 목록 ↔ 논문 ----
  function showLib(push){
    if (cur) cur.classList.remove('on');
    cur = null; lib.classList.remove('off'); topTitle.textContent = 'Narani';
    closeSheet(); window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!lib');
  }
  function openPaper(id, push, target){
    var art = doc.getElementById(id);
    if (!art || !art.classList.contains('paper')) return false;
    if (cur && cur !== art) cur.classList.remove('on');
    cur = art; art.classList.add('on'); lib.classList.add('off');
    topTitle.textContent = (DATA[id] || {}).title || '';
    closeSheet();
    if (push) history.pushState(null, '', '#!' + id);
    var el = target ? doc.getElementById(target) : null;
    var off = 0;
    if (!el){   // 저장해 둔 '행:행 안에서 내려간 px'
      var saved = load('pos:' + art.getAttribute('data-key'), '').split(':');
      el = saved[0] ? doc.getElementById(id + '-' + saved[0]) : null; off = +saved[1] || 0;
    }
    if (el){ el.scrollIntoView({block: 'start'}); if (off) window.scrollBy(0, off); } else window.scrollTo(0, 0);
    return true;
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}   // 다시 열 때의 자리는 우리가 정한다
  // 주소는 `#!p1` — `#p1`이면 그 id(논문 article)가 있어 Safari가 로드 직후 그리로 스크롤해
  // 읽던 위치 복원을 덮어쓴다. JS 없는 미리보기에서 온 `#p1`·`#p1-ko-ref-3`도 받아 준다.
  function route(){
    var h = location.hash.replace(/^#!?/, ''), m = /^(p\d+)(?:-|$)/.exec(h);
    if (m && openPaper(m[1], false, h === m[1] ? null : h)) return;
    showLib(false);
  }
  doc.getElementById('back').onclick = function(){ showLib(true); };
  window.addEventListener('popstate', route);
  var saveTimer = null;
  window.addEventListener('scroll', function(){
    if (!cur || saveTimer) return;
    saveTimer = setTimeout(function(){
      saveTimer = null;
      var r = topRow(); if (!r || !cur) return;
      var into = Math.max(0, Math.round(64 - r.getBoundingClientRect().top));   // scroll-margin-top과 같은 기준
      store('pos:' + cur.getAttribute('data-key'), r.id.replace(/^p\d+-/, '') + ':' + into);
    }, 400);
  }, {passive: true});

  route();
"""

# 문장 짝 함수들(§sentpair)을 이 IIFE 안에 그대로 끼워 지역 함수로 쓴다.
_JS = "(function(){\n" + sentpair.JS + CORE_JS + _ROUTE_JS + "})();\n"
