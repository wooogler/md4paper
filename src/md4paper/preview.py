"""홈 '논문 미리보기' 패널용 — 초록·대표 이미지·메모 앞부분.

목록에서 논문을 고르면 옆 패널에 초록과 그림 한 장, 메모 첫머리를 보여 준다(Zotero 식).
초록은 LLM 없이 출력 md에서 뽑는다: **번호 매긴 첫 절(또는 Introduction) 앞의 가장 긴 평문
문단**, 'Abstract' 표시가 있으면 그 바로 뒤 문단을 우선. 규칙을 쌓기보다 이 한 가지에 기대고,
저자 줄·캡션·저작권 같은 머리말 잡음만 걸러 낸다.

캐시는 `<wd.root>/preview.json`에 둔다. paper_meta.json은 서지 단계가 모양을 정해 통째로 다시
쓰는 파일이라(save가 model_dump로 덮는다) 거기 끼우면 서지를 다시 뽑을 때 사라진다. 캐시를
쓴 뒤에는 폴더 수정시각을 되돌린다 — 홈 목록이 폴더 mtime으로 최근순 정렬하므로, 미리보기를
본 것만으로 논문이 맨 위로 튀면 안 된다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from md4paper.workdir import WorkDir

# 규칙이 바뀌면 올린다 → 예전 캐시를 버리고 다시 뽑는다
_RULES_VERSION = 1
_ABSTRACT_CAP = 2500  # 여러 문단짜리 초록을 이어 붙일 때 상한(자)
_MIN_ABSTRACT = 150  # '가장 긴 문단'이 이보다 짧으면 초록이 아니라고 본다
_THUMB_MAX = 900  # PDF 1쪽 썸네일의 긴 변 상한(px)
USER_SRC = "user"  # 초록 출처 표지 — 사용자가 손으로 고쳤다(md가 바뀌어도 다시 뽑지 않는다)

# 번호 매긴 절 헤딩: '# 1 Introduction', '# 1. 서론', '## 1', '# I. INTRODUCTION'
_NUM_HEAD = re.compile(r"^#{1,6}\s+(?:\d+|[IVX]+)\.?(?:\s|$)")
# 번호 없는 서론 헤딩: '## INTRODUCTION', '# 서론'
_INTRO_HEAD = re.compile(
    r"^#{1,6}\s+(?:\*\*)?\s*(?:introduction|서론|들어가며|들어가는\s*말|머리말)\b", re.I)
# 'Abstract' 단독 표시(헤딩·굵은 글씨) / 문단 머리에 붙은 표시('**Abstract.** 본문', 'ABSTRACT: 본문')
_ABSTRACT = r"a ?b ?s ?t ?r ?a ?c ?t"  # Elsevier의 'A B S T R A C T'도
_ABS_WORDS = rf"(?:{_ABSTRACT}|summary|초록|요약|개요)"
_ABS_ALONE = re.compile(rf"^(?:#{{1,6}}\s*)?(?:\*\*|__)?\s*{_ABS_WORDS}\s*[.:—–-]?\s*(?:\*\*|__)?\s*$", re.I)
# 번호 절 뒤로 밀려난 표시(추출 순서가 꼬인 경우)는 'Abstract/초록'만 믿는다 — 'Summary'는 맺음 절일 수 있다
_ABS_ALONE_STRICT = re.compile(rf"^(?:#{{1,6}}\s*)?(?:\*\*|__)?\s*(?:{_ABSTRACT}|초록)\s*[.:]?\s*(?:\*\*|__)?\s*$", re.I)
# 구조화 초록의 소제목('## Background', '## Methods' …) — 초록을 이어 붙이다 만나도 멈추지 않는다
_STRUCT_HEAD = re.compile(
    r"^#{1,6}\s*(?:background|objectives?|purpose|aims?|methods?|design|results?|findings|"
    r"conclusions?|implications|context|배경|목적|방법|결과|결론)\s*:?\s*$", re.I)
_ABS_LEAD = re.compile(
    rf"^(?:\*\*|__)?\s*{_ABS_WORDS}\s*(?:[.:—–-]\s*(?:\*\*|__)?|(?:\*\*|__)\s*[.:—–-]?)\s*"
    r"|^(?-i:(?:Abstract|ABSTRACT)\s+(?=[A-Z]))", re.I)  # 'Abstract The …'(구두점 없이 붙은 표시)
# 머리말 '목록류' 문단(헤딩·굵은 글씨·맨 글자 모두) — 헤딩이면 그 아래 문단까지 건너뛴다
_META = re.compile(
    r"^(?:#{1,6}\s*|\*{1,2}|_{1,2})?\s*(?:acm reference format|ccs concepts|categories and subject descriptors|"
    r"keywords?|index terms|general terms|additional key words|author keywords|"
    r"키워드|주제어|핵심어|ccs\s*개념|acm\s*참고\s*형식)", re.I)
# 저작권·식별자·게재 정보 문단 (문단 머리에서만 본다 — 초록 끝에 붙은 저작권 한 줄 때문에 초록을 버리지 않게)
_BOILER = re.compile(
    r"permission to make|copyright|©|\bdoi\b|doi\.org|arxiv|\bisbn\b|\bissn\b|licen[cs]e|"
    r"all rights reserved|\$\d+\.00|acm classification|proceedings of|article in press|"
    r"저작권|저작물|디지털.{0,12}복사", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\}@")
# 초록 끝에 붙어 나온 저작권 문장
_TAIL_COPYRIGHT = re.compile(r"\s*(?:Copyright\b|©).*$", re.S)
_NO_STOP_CAP = 8000  # 번호 절·서론 헤딩이 없을 때 앞에서 이만큼(자)만 본다 — 본문 문단이 뽑히지 않게


# --- 마크다운 → 평문 ---------------------------------------------------------

_MATH = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+\$)", re.S)


def _plain(md: str) -> str:
    """마크다운 서식을 걷어 평문으로. 인라인 수식 `$…$`는 손대지 않는다."""
    parts = _MATH.split(md)
    for i in range(0, len(parts), 2):  # 짝수 칸이 수식 밖
        parts[i] = _plain_text(parts[i])
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _plain_text(s: str) -> str:
    s = re.sub(r"<sup\b[^>]*>.*?</sup>", "", s, flags=re.S)  # 각주 표지 위첨자
    s = re.sub(r"<[^>]+>", "", s)  # 남은 HTML 태그
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)  # 그림
    s = re.sub(r"\[\^[^\]]*\]", "", s)  # 각주 [^1]
    # 번호 인용 링크 '[8, 저자 2008, 제목](#ref-8)' → '8' (바깥 대괄호가 남아 '[8]')
    s = re.sub(r"\[(\d+)(?:,[^\]]*)?\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # 그 밖의 링크 → 글자만
    s = re.sub(r"\*\*|__", "", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])", "", s)  # 기울임 '*'(곱셈 'a * b'는 둔다)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)  # 헤딩 기호
    s = re.sub(r"^\s*(?:[-+]|\d+\.)\s+", "", s, flags=re.M)  # 목록 기호
    return s.replace("\\", "")


# --- 초록 추출 ---------------------------------------------------------------

def _blocks(md: str) -> list[str]:
    """빈 줄로 나눈 문단 목록 (앞뒤 공백 정리, 빈 것 제외). 문단에 빈 줄 없이 붙은 헤딩도 떼어 낸다."""
    return [b.strip() for b in re.split(r"\n\s*\n|\n(?=#{1,6}\s)", md) if b.strip()]


def _is_heading(b: str) -> bool:
    return b.startswith("#")


def _is_stop(b: str) -> bool:
    """번호 매긴 절 또는 서론 헤딩 — 초록을 찾는 범위의 끝."""
    first = b.splitlines()[0]
    return bool(_NUM_HEAD.match(first) or _INTRO_HEAD.match(first))


def _is_noise(b: str) -> bool:
    """초록 후보가 될 수 없는 문단 — 그림·캡션·표·앵커·저작권·목록류·저자/소속 줄."""
    if b.startswith(("![", ">", "|", "<a ", "```", "$$")) or _META.match(b):
        return True
    return bool(_BOILER.search(b[:200]) or _EMAIL.search(b[:300]))


def _ends_open(text: str) -> bool:
    """문장이 끝나지 않은 채 끊겼나 — 쪽·단 나눔으로 문단이 쪼개진 흔적."""
    return not re.search(r"[.!?:;。)\]\"'”’]$", text)


def _clean(text: str) -> str:
    if len(text) > 200:
        m = _TAIL_COPYRIGHT.search(text, 200)
        if m:
            text = text[:m.start()]
    return text[:_ABSTRACT_CAP].strip()


def extract_abstract(md: str) -> str:
    """paper.*.md 본문에서 초록 평문을 뽑는다. 못 찾으면 ""."""
    blocks = _blocks(md)
    if blocks and _is_heading(blocks[0]):
        blocks = blocks[1:]  # 제목
    # 범위: 번호 매긴 첫 절(또는 서론) 헤딩 앞까지. 그런 헤딩이 없으면 앞부분만.
    stop = next((i for i, b in enumerate(blocks) if _is_stop(b)), None)
    if stop is not None:
        region = blocks[:stop]
    else:
        region, size = [], 0
        for b in blocks:
            region.append(b)
            size += len(b)
            if size >= _NO_STOP_CAP:
                break

    # 1) 'Abstract' 표시가 있으면 그 뒤부터 이어 붙인다 (범위 밖이면 'Abstract/초록' 헤딩만 믿는다)
    marked = _after_marker(region, _ABS_ALONE, lead=True)
    if len(marked) < _MIN_ABSTRACT and stop is not None:
        marked = _after_marker(blocks[stop:], _ABS_ALONE_STRICT, lead=False, multi=False) or marked
    if len(marked) >= _MIN_ABSTRACT:
        return _clean(marked)

    # 2) 표시가 없으면 가장 긴 평문 문단 (쪽 나눔으로 쪼개진 문단은 다시 잇는다)
    best, run, skip = "", "", False
    for b in region:
        if _is_heading(b) or (_META.match(b) and len(b) < 60):  # 기울임 라벨 문단('*CCS Concepts*')도 헤딩처럼
            skip = bool(_META.match(b))  # 키워드·CCS 헤딩 아래 문단은 건너뛴다
            run = ""
            continue
        if skip or _is_noise(b):
            skip = False
            continue  # 잡음은 건너뛰되 끊긴 문단(run)은 유지 — 저작권 상자 너머로 이어진다
        text = _plain(b)
        if len(run) >= 200 and _ends_open(run) and text[:1].islower():  # 짧은 저자·소속 줄과는 잇지 않는다
            run = f"{run} {text}"
        else:
            run = text
        if len(run) > len(best):
            best = run
    if len(best) >= _MIN_ABSTRACT:
        return _clean(best)
    return _clean(marked) if len(marked) >= 40 else ""  # 짧게 잘린 초록이라도 표시가 있었다면


def _after_marker(blocks: list[str], alone: re.Pattern, *, lead: bool, multi: bool = True) -> str:
    """'Abstract' 표시 뒤 본문. 표시가 없으면 ""."""
    for i, b in enumerate(blocks):
        if alone.match(b):
            # Elsevier 판('A B S T R A C T')은 초록 뒤에 헤딩 없이 서론이 바로 붙는다 → 한 문단만
            spaced = bool(re.search(r"a b s t r", b, re.I))
            got = _collect(blocks[i + 1:], multi=multi and not spaced)
        elif lead and not _is_heading(b) and _ABS_LEAD.match(b):
            got = _collect([_ABS_LEAD.sub("", b, count=1)] + blocks[i + 1:], multi=multi)
        else:
            continue
        if got:
            return got
    return ""


def _collect(blocks: list[str], *, multi: bool) -> str:
    """'Abstract' 뒤 문단들을 헤딩·잡음을 만날 때까지 이어 붙인다(ACM 판은 초록이 여러 문단).

    multi=False면 첫 문단에 쪽 나눔으로 끊긴 뒷부분과 구조화 초록의 다음 소절만 잇는다 — 초록
    바로 뒤에 헤딩 없이 서론이 이어지는 판에서 서론이 딸려 오지 않게.
    """
    out: list[str] = []
    total, more = 0, False  # more: 구조화 소제목을 지났다 → 다음 문단도 초록
    for b in blocks:
        if _STRUCT_HEAD.match(b):
            more = True
            continue
        if _is_heading(b):
            break
        if _is_noise(b):
            if out and not _ends_open(out[-1]):
                break
            continue  # 표시 바로 뒤의 그림·저자 줄, 끊긴 문단 사이의 저작권 상자는 넘어간다
        text = _plain(b)
        if out and len(text) < 40:  # 쪽 번호·머리글 같은 자투리에서 멈춘다
            break
        if out and not multi and not (more or (_ends_open(out[-1]) and text[:1].islower())):
            break
        out.append(text)
        more = False
        total += len(text)
        if total >= _ABSTRACT_CAP:
            break
    return " ".join(out)


# --- 캐시 --------------------------------------------------------------------

def _load_cache(wd: WorkDir) -> dict:
    try:
        data = json.loads(wd.preview_json.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep_mtime(wd: WorkDir, write) -> bool:  # noqa: ANN001 — 인자 없는 콜러블
    """write()를 실행하고 폴더 수정시각을 되돌린다(홈 최근순 정렬을 흔들지 않게). 실패면 False."""
    try:
        before = wd.root.stat()
        write()
    except OSError:
        return False
    try:
        os.utime(wd.root, ns=(before.st_atime_ns, before.st_mtime_ns))
    except OSError:
        pass
    return True


def _save_cache(wd: WorkDir, data: dict) -> None:
    path = wd.preview_json
    tmp = path.with_suffix(".json.tmp")

    def write() -> None:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    _keep_mtime(wd, write)


def _signature(path: Path) -> str:
    """원본 md가 바뀌었는지 볼 표지 — 수정시각(ns)·크기·규칙 버전."""
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}:v{_RULES_VERSION}"


def abstract_of(wd: WorkDir, lang: str = "en") -> str:
    """초록 본문(마크다운 서식 제거한 평문). 없으면 "".

    결과는 `<wd.root>/preview.json`에 `abstract`(en) / `abstract_ko`(ko)로 캐시하고, 원본 md의
    수정시각·크기가 그대로면 캐시를 돌려준다. 못 찾은 결과("")도 캐시한다(매번 다시 뒤지지 않게).
    사용자가 고친 초록(`set_abstract`, 출처 표지 "user")은 md가 바뀌어도, 없어도 그대로 돌려준다.
    """
    md = wd.ko_md if lang == "ko" else wd.en_md
    key = "abstract_ko" if lang == "ko" else "abstract"
    cache = _load_cache(wd)
    if cache.get(f"{key}_src") == USER_SRC and isinstance(cache.get(key), str):
        return cache[key]  # 사용자 수정이 이긴다 — md 표지보다 먼저 본다
    try:
        sig = _signature(md)
    except OSError:
        return ""  # md 없음
    if cache.get(f"{key}_src") == sig and isinstance(cache.get(key), str):
        return cache[key]
    try:
        text = extract_abstract(md.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""
    cache[key] = text
    cache[f"{key}_src"] = sig
    if wd.root.is_dir():
        _save_cache(wd, cache)
    return text


def set_abstract(wd: WorkDir, lang: str, text: str) -> None:
    """초록을 사용자 값으로 고정 — preview.json의 `abstract`(en)/`abstract_ko`(ko)에 쓰고 출처를 "user"로.

    빈 문자열도 그대로 사용자 값이다(엉뚱한 문단이 뽑혀 일부러 지운 경우 다시 살아나지 않게).
    폴더 수정시각은 되돌린다(`_save_cache`) — 초록을 고쳤다고 목록 맨 위로 튀지 않게.
    """
    if lang not in ("en", "ko"):
        raise ValueError(f"초록 언어는 en·ko 중 하나여야 합니다: {lang!r}")
    key = "abstract_ko" if lang == "ko" else "abstract"
    cache = _load_cache(wd)
    cache[key] = str(text or "").strip()
    cache[f"{key}_src"] = USER_SRC
    _save_cache(wd, cache)


# --- 대표 이미지 ---------------------------------------------------------------

def _figure_order(p: Path) -> tuple[int, int, str]:
    """번호순 — figure-1 < figure-1-2(같은 그림의 둘째 조각) < figure-2 < figure-12 < figure-C(번호 아님)."""
    m = re.match(r"figure-(\d+)(?:-(\d+))?$", p.stem)
    if m:
        return (int(m.group(1)), int(m.group(2) or 0), p.name)
    return (1 << 30, 0, p.name)


def _first_figure(wd: WorkDir) -> Path | None:
    try:
        figs = [p for p in wd.out_images.glob("figure-*.png") if p.is_file()]
    except OSError:
        return None
    return min(figs, key=_figure_order) if figs else None


def _source_pdf(wd: WorkDir) -> Path | None:
    from md4paper.library import _source_pdf as find  # library는 무거워 필요할 때만

    return find(wd)


def _render_thumb(pdf: Path) -> bytes | None:
    """PDF 1쪽을 긴 변 _THUMB_MAX px 이내 PNG로. pypdfium2 없음·손상이면 None."""
    import io

    try:
        from md4paper import pdfio

        png = pdfio.render_page_png(pdf, 0, zoom=1.0)
        if not png:
            return None
        from PIL import Image

        img = Image.open(io.BytesIO(png))
        if max(img.size) <= _THUMB_MAX:
            return png
        img.thumbnail((_THUMB_MAX, _THUMB_MAX))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — 미리보기는 실패해도 목록이 떠야 한다
        return None


def thumbnail_png(wd: WorkDir) -> Path | None:
    """대표 이미지 PNG 경로.

    우선순위: out/images의 첫 그림(figure-1.png, 없으면 번호 가장 작은 figure-*.png) → 원본 PDF
    1쪽 렌더를 `<wd.root>/thumb.png`에 캐시(PDF가 더 새로우면 다시 렌더) → 둘 다 없으면 None.
    """
    fig = _first_figure(wd)
    if fig is not None:
        return fig
    pdf = _source_pdf(wd)
    thumb = wd.thumb_png
    try:
        if thumb.is_file() and (pdf is None or thumb.stat().st_mtime >= pdf.stat().st_mtime):
            return thumb
    except OSError:
        pass
    if pdf is None or not wd.root.is_dir():
        return None
    png = _render_thumb(pdf)
    if png is None:
        return None
    tmp = thumb.with_suffix(".png.tmp")

    def write() -> None:
        tmp.write_bytes(png)
        os.replace(tmp, thumb)

    return thumb if _keep_mtime(wd, write) else None


# --- 메모 ----------------------------------------------------------------------

def notes_excerpt(wd: WorkDir, limit: int = 300) -> str:
    """notes.md 앞부분 평문(헤딩 기호·링크 서식 제거), 없으면 ""."""
    try:
        md = wd.notes_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    md = re.sub(r"^```.*?^```", "", md, flags=re.S | re.M)  # 코드 블록은 미리보기에서 뺀다
    text = _plain(md)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
