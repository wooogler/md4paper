"""그림 속 글자 번역 — 축 라벨·범례·다이어그램 상자 글자를 번역한 그림(`figure-3.ko.png`)을 만든다.

**번역 언어.** 논문의 번역 언어(`wd.lang`)로 — 그림 이름은 `figure-3.<lang>.png`(한국어 `.ko.png`), 기록은
`figures-<lang>.json`, 글꼴은 언어별 후보(`find_font`). 글자 모양을 이어 그리는 문자(아랍·데바나가리·태국 문자)는
Pillow 기본 배치로는 깨지므로 막는다(`supported`). 아래 설명의 '한국어'는 '번역 언어'로 읽으면 된다.

**LLM은 라벨만, 그림은 코드가.** 이미지 생성 모델(gpt-image 계열)로 그림을 다시 그리면 글자는 자연스럽지만
데이터가 바뀐다 — 실측(2026-10, 5장)에서 유지하라고 한 축 라벨 `[1, 5)`를 `[1, 5]`로, UI 식별자
`favorite_count`를 엉뚱한 글로 바꿨고, 마스크를 줘도 상자 밖 픽셀이 1.5~7% 바뀌었다. 그래서 여기서는
그림을 다시 그리지 않는다.
1. 글자 줄 상자: 벡터 그림은 PDF 글자 레이어(그림 자리는 §figure_fix.where), 래스터 그림은 rapidocr.
   글자 레이어가 깨진 글꼴(`% &"#$`)이면 버리고 OCR로.
2. LLM(`parse_image`, 번역 모델)은 줄마다 번역/유지/무시와 한국어 한 줄만 준다 — 논문 용어집을 함께 준다.
3. 코드가 그 줄의 글자 픽셀만 지우고(inpaint) 같은 자리에 한국어를 그린다. 상자 밖은 한 픽셀도 안 바뀐다.

**어디에 두나.** 번역 그림은 `out/images/<이름>.ko.png`, 기록은 `extract/figures-ko.json`(extract 그림
파일명 → 원본 digest·줄 상자·영어·한국어). 기록이 남아 있으니 한국어를 고쳐 LLM 없이 다시 그릴 수 있다.
ko.md의 그림 참조만 `.ko.png`로 바꾼다(`link_text`) — en.md·뷰어 렌더링은 그대로이고, 저장 위치 내보내기와
그림 참조 호버는 ko.md를 따라온다. 원본 그림이 바뀌면(영역 다시 지정·되돌리기·고해상도) digest가 어긋나
그 번역은 ko.md에서 빠진다(기록은 남아 '원본이 바뀜'으로 보인다).

**스크린샷.** LLM이 그림 종류를 함께 고른다. 스크린샷이면 OCR로 읽은 화면 글자는 그대로 두고 저자가 얹은
벡터 주석만 번역한다 — 화면 문단을 한글로 바꾸면 길이가 달라 겹치고, 화면은 원래 모습이 정보다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from narani.workdir import WorkDir

RENDERER = 1  # 그리는 방식을 바꾸면 올린다 — 기록의 값과 다르면 '다시 그리기'가 필요하다는 표시
# 번역 그림 이름 꼴 — `<원본 이름>.<언어 코드>.png`. 원본 그림 이름(figure-3.png·image-1.png)엔 가운데 점이 없다
_TR_RE = re.compile(r"\.[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\.png$")
_OCR_MIN_SCORE = 0.5
_PAD = 2  # 줄 상자 둘레 여백(px) — 안티에일리어싱 가장자리까지 지운다
_FG_DIST = 50  # 배경색과 이만큼 떨어진 픽셀을 글자로 본다 (RGB 거리)
# 한 장당 대략 토큰 (실측: 입력 1.2~4.9k — 그림 + 줄 목록, 출력 0.2~2.5k) — 일괄 비용 어림용
EST_INPUT_TOKENS = 2500
EST_OUTPUT_TOKENS = 600

_LOCK = threading.RLock()  # 기록·ko.md를 함께 고친다 (UI 라우트·일괄 작업·에이전트가 동시에 부를 수 있다)
_OCR_LOCK = threading.Lock()
_OCR = None

_NOTO_CJK = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)
_FONT_CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",        # macOS
    "/Library/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",                       # Windows (맑은 고딕)
    *_NOTO_CJK,
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)
# 번역 언어별 글꼴 후보 — 없는 언어는 라틴·키릴 문자를 두루 갖춘 글꼴(_LATIN_FONTS)
_LANG_FONTS = {
    "ko": _FONT_CANDIDATES,
    "ja": ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", "C:/Windows/Fonts/YuGothM.ttc",
           "C:/Windows/Fonts/meiryo.ttc", *_NOTO_CJK),
    "zh-Hans": ("/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
                "C:/Windows/Fonts/msyh.ttc", *_NOTO_CJK),
    "zh-Hant": ("/System/Library/Fonts/STHeiti Light.ttc", "C:/Windows/Fonts/msjh.ttc", *_NOTO_CJK),
}
_LATIN_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", *_NOTO_CJK,
)
# 글자를 이어 그리거나 모양을 바꾸는 문자 — Pillow 기본 배치(raqm 없이)로는 깨진다
_UNSUPPORTED = ("ar", "fa", "ur", "he", "hi", "bn", "th", "my", "km")


class FigureTranslateError(ValueError):
    pass


# --- 이름·기록 ---------------------------------------------------------------


def supported(lang: str) -> bool:
    """이 번역 언어로 그림 글자를 그릴 수 있나 — 이어 쓰는 문자(아랍·데바나가리·태국 …)는 아직 못 한다."""
    return lang.split("-")[0] not in _UNSUPPORTED


def _require_supported(wd: WorkDir) -> None:
    if not supported(wd.lang):
        raise FigureTranslateError(f"이 번역 언어({wd.lang})는 그림 속 글자 번역을 아직 지원하지 않습니다 "
                                   "— 글자를 이어 그리는 문자라 그림에 바르게 못 씁니다.")


def ko_name(out_name: str, lang: str = "ko") -> str:
    """`figure-3.png` → `figure-3.ko.png`(번역 언어별 `figure-3.ja.png`; 원본 형식과 상관없이 PNG)."""
    return os.path.splitext(out_name)[0] + f".{lang}.png"


def is_ko(name: str) -> bool:
    """번역 그림 이름인가 (언어와 상관없이 `.<lang>.png`)."""
    return bool(_TR_RE.search(name or ""))


def is_candidate(name: str) -> bool:
    """번역 대상이 될 수 있는 본문 그림 이름 — 수식 크롭·번역 그림은 아니다."""
    return bool(name) and "/" not in name and not name.startswith("formula-") and not is_ko(name)


def load(wd: WorkDir) -> dict[str, dict]:
    try:
        data = json.loads(wd.figures_ko_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 기록 없음
        return {}
    return data if isinstance(data, dict) else {}


def _save(wd: WorkDir, data: dict) -> None:
    wd.extract.mkdir(parents=True, exist_ok=True)
    tmp = wd.figures_ko_json.with_name(wd.figures_ko_json.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, wd.figures_ko_json)


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def original_name(wd: WorkDir, name: str) -> str:
    """번역 그림 이름이면 원본 그림 이름(`figure-3.ko.png` → `figure-3.png`), 아니면 그대로."""
    if not is_ko(name):
        return name
    for rec in load(wd).values():
        if rec.get("ko") == name and rec.get("out"):
            return rec["out"]
    stem = _TR_RE.sub("", name)
    if wd.out_images.is_dir():
        for f in sorted(wd.out_images.iterdir()):
            if os.path.splitext(f.name)[0] == stem and not is_ko(f.name):
                return f.name
    return stem + ".png"


def _source(wd: WorkDir, out_name: str) -> str:
    """out 그림 이름 → extract 원천 파일명 (figure_fix와 같은 바이트 대조). 못 찾으면 오류."""
    from narani import figure_fix

    out_name = original_name(wd, out_name)
    if not is_candidate(out_name):
        raise FigureTranslateError("번역할 수 있는 그림이 아닙니다.")
    src = figure_fix.extract_name(wd, out_name)
    if src is None:
        raise FigureTranslateError("이 그림의 원본(extract/images)을 찾지 못했습니다.")
    return src


def _rec_for(wd: WorkDir, out_name: str) -> tuple[str | None, dict | None]:
    """out 이름으로 기록 찾기 — (extract 이름, 기록). 원본을 바이트로 못 찾아도(원본이 바뀜) 기록의 out으로."""
    out_name = original_name(wd, out_name)
    data = load(wd)
    for src, rec in data.items():
        if rec.get("out") == out_name:
            return src, rec
    return None, None


def _current_digest(wd: WorkDir, src: str) -> str | None:
    p = wd.extract_images / src
    return _digest(p.read_bytes()) if p.is_file() else None


def stale(wd: WorkDir, src: str, rec: dict) -> bool:
    """원본 그림이 번역 뒤에 바뀌었나 (영역 다시 지정·되돌리기·고해상도)."""
    return rec.get("src_digest") != _current_digest(wd, src)


def valid(wd: WorkDir) -> dict[str, str]:
    """ko.md에 걸 번역 그림 {out 이름: ko 이름} — 파일이 있고 원본이 그대로인 것만."""
    out: dict[str, str] = {}
    for src, rec in load(wd).items():
        ko, name = rec.get("ko"), rec.get("out")
        if ko and name and (wd.out_images / ko).is_file() and not stale(wd, src, rec):
            out[name] = ko
    return out


# --- ko.md 연결 -------------------------------------------------------------

# 이미지 참조의 `images/<파일>` — md `](images/x`, Obsidian `[[images/x`, HTML `src="images/x`
_REF_RE = re.compile(r"(\]\(|\[\[|src=[\"'])images/([^\s)\"'\]|#?]+)")


def link_text(wd: WorkDir, md: str) -> str:
    """ko.md 글에서 번역 그림이 있는 그림 참조는 `.ko.png`로, 번역이 없어졌거나 낡은 `.ko.png`는 원본으로."""
    ok = valid(wd)

    def sub(m: re.Match) -> str:
        name = m.group(2)
        if is_ko(name):
            orig = original_name(wd, name)
            name = ok.get(orig, orig)
        else:
            name = ok.get(name, name)
        return f"{m.group(1)}images/{name}"

    return _REF_RE.sub(sub, md)


def link_ko(wd: WorkDir) -> bool:
    """ko.md 파일에 `link_text`를 반영한다 — 바뀌었으면 True. '번역 직접 고침' 표시는 건드리지 않는다."""
    with _LOCK:
        if not wd.ko_md.exists():
            return False
        text = wd.ko_md.read_text(encoding="utf-8")
        new = link_text(wd, text)
        if new == text:
            return False
        tmp = wd.ko_md.with_suffix(f".md.tmp{os.getpid()}")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, wd.ko_md)
        return True


# --- 글자 줄 찾기 -------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join(text.replace("\x02", "-").replace("\u00ad", "-").split())


def _readable(text: str) -> bool:
    """깨진 글꼴(ToUnicode 없음)로 나온 글자 레이어가 아닌가 — 글자·숫자·흔한 기호가 대부분이어야 한다."""
    s = text.strip()
    if not s:
        return False
    good = sum(c.isalnum() or c in " -–—.,:;()[]{}/%+*'’‘“”\"?!&=<>#@_|~" for c in s) / len(s)
    return good >= 0.8 and re.search(r"[A-Za-z0-9]", s) is not None


def _vertical(box: list[float], text: str) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    return len(text) >= 3 and h > 1.6 * w


def pdf_lines(wd: WorkDir, src: str, size: tuple[int, int]) -> list[dict]:
    """PDF 글자 레이어의 줄 (그림 픽셀 좌표). 글자 레이어가 없거나 깨졌으면 빈 목록."""
    from narani import figure_fix, pdfio

    pdf = figure_fix.source_pdf(wd)
    at = figure_fix.where(wd, src) if pdf else None
    if not at or at.get("guess"):  # 짐작한 자리는 남의 글자를 가져올 수 있다 — OCR에 맡긴다
        return []
    left, top, right, bottom = (float(v) for v in at["bbox"])
    try:
        raw = pdfio.text_lines(pdf, int(at["page"]) - 1, (left, top, right, bottom))
    except Exception:  # noqa: BLE001 — PDF를 못 읽으면 OCR로
        return []
    W, H = size
    sx, sy = W / max(1e-6, right - left), H / max(1e-6, bottom - top)
    lines = []
    for text, (x0, y0, x1, y1) in raw:
        text = _clean(text)
        if not text:
            continue
        box = [max(0.0, (x0 - left) * sx), max(0.0, (y0 - top) * sy), min(W, (x1 - left) * sx), min(H, (y1 - top) * sy)]
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            continue
        lines.append({"en": text, "box": [round(v, 1) for v in box], "src": "pdf", "ok": _readable(text)})
    if lines and sum(x["ok"] for x in lines) < len(lines) / 2:
        return []  # 대부분 깨진 글꼴 — 이 그림의 글자 레이어는 믿지 않는다
    return _join_runs([{k: v for k, v in x.items() if k != "ok"} for x in lines if x["ok"]])


def _join_runs(lines: list[dict]) -> list[dict]:
    """같은 기준선에서 끊긴 조각을 한 줄로 — pdfium은 합자(fi)·글꼴 바뀜에서 사각형을 끊는다
    (실측: 'verify scienti' · 'fi' · 'c QA'). 세로로 70% 넘게 겹치고 가로 틈이 글자 높이의 절반 안이면 잇는다."""
    out: list[dict] = []
    for x in sorted(lines, key=lambda v: (round(v["box"][1] / 4), v["box"][0])):
        prev = out[-1] if out else None
        if prev is not None:
            a, b = prev["box"], x["box"]
            h = min(a[3] - a[1], b[3] - b[1])
            vov = min(a[3], b[3]) - max(a[1], b[1])
            gap = b[0] - a[2]
            if h > 0 and vov >= 0.7 * h and -0.2 * h <= gap <= 0.5 * h:
                prev["en"] = prev["en"] + ("" if gap < 0.2 * h else " ") + x["en"]  # 낱말 사이 공백은 약 0.25em
                prev["box"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                continue
        out.append(dict(x))
    return out


def _ocr_engine():  # noqa: ANN202
    global _OCR  # noqa: PLW0603 — 모델을 한 번만 싣는다 (첫 사용 때 모델 파일을 내려받는다)
    if _OCR is None:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import EngineType

        # onnxruntime은 깔려 있지 않다 — docling이 함께 들여온 torch 엔진을 쓴다
        _OCR = RapidOCR(params={"Det.engine_type": EngineType.TORCH, "Cls.engine_type": EngineType.TORCH,
                                "Rec.engine_type": EngineType.TORCH})
        import logging

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # 그림마다 엔진·모델 경로를 찍는다
    return _OCR


def ocr_lines(png: bytes) -> list[dict]:
    """래스터 그림의 글자 줄 (rapidocr). OCR을 쓸 수 없으면 빈 목록."""
    import numpy as np
    from PIL import Image

    try:
        with _OCR_LOCK:
            eng = _ocr_engine()
            arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))[:, :, ::-1].copy()  # BGR
            res = eng(arr)
    except Exception:  # noqa: BLE001 — OCR 패키지·모델이 없으면 벡터 글자만
        return []
    if res is None or getattr(res, "boxes", None) is None:
        return []
    out = []
    for quad, text, score in zip(res.boxes, res.txts, res.scores):
        text = _clean(str(text))
        if not text or float(score) < _OCR_MIN_SCORE:
            continue
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        out.append({"en": text, "box": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
                    "src": "ocr"})
    return out


def _overlap(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def find_lines(wd: WorkDir, src: str, png: bytes, *, ocr=None) -> list[dict]:  # noqa: ANN001
    """그림의 글자 줄 — 벡터 글자 + 그것과 안 겹치는 OCR 글자. 각 줄에 id·vertical을 붙인다."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        size = im.size
    lines = pdf_lines(wd, src, size)
    extra = (ocr or ocr_lines)(png)
    lines += [x for x in extra if all(_overlap(x["box"], v["box"]) < 0.3 for v in lines)]
    lines.sort(key=lambda x: (x["box"][1], x["box"][0]))
    for i, x in enumerate(lines):
        x["id"] = i
        x["vertical"] = _vertical(x["box"], x["en"])
    return lines


def group(lines: list[dict]) -> list[list[int]]:
    """한 덩어리 글(여러 줄 상자 이름표 등)로 이어지는 줄끼리 묶는다 — LLM이 줄을 나눠 번역하도록.

    같은 글자 높이, 세로로 붙어 있고, 왼쪽이 맞거나 가로로 겹치면 한 블록. 세로 글자는 따로.
    """
    blocks: list[list[dict]] = []
    for x in sorted(lines, key=lambda v: (v["box"][1], v["box"][0])):
        x0, y0, x1, y1 = x["box"]
        h = y1 - y0
        for g in blocks:
            last = g[-1]
            if x["vertical"] or last["vertical"]:
                continue
            lx0, _ly0, lx1, ly1 = last["box"]
            lh = ly1 - last["box"][1]
            gap = y0 - ly1
            same_h = max(h, lh) / max(1.0, min(h, lh)) < 1.35
            xov = min(x1, lx1) - max(x0, lx0) > 0 or abs(x0 - lx0) < lh
            if same_h and -0.3 * lh < gap < 0.7 * lh and xov:
                g.append(x)
                break
        else:
            blocks.append([x])
    return [[x["id"] for x in g] for g in blocks]


# --- LLM 라벨 -------------------------------------------------------------------


class _Item(BaseModel):
    id: int
    action: Literal["translate", "keep", "drop"]
    ko_lines: list[str]


class _Labels(BaseModel):
    kind: Literal["chart", "diagram", "screenshot", "photo", "table", "other"]
    items: list[_Item]


_SYSTEM = """You label text found inside a figure from an academic paper so that code can replace it with Korean.
First classify the figure `kind`: chart (plots, bar/line charts), diagram (flowcharts, architecture, concept
figures), screenshot (a software UI captured as an image), photo, table, other.

For each text block (id, lines) decide `action`:
- "translate": natural-language English (axis titles, legends, labels, titles, captions, sentences). Give concise
  Korean that fits the same space. Keep technical names, model/product names, variable or field names, code
  identifiers and abbreviations in English inside the Korean.
- "keep": numbers, tick values, units, single letters or badges (A, B1, (a)), names, identifiers like media_count,
  math, citations. ko_lines = the original lines.
- "drop": OCR noise or icons misread as text (e.g. 'B', 'I <>', '>', '□'). ko_lines = empty strings.
In screenshots, labels and callouts the authors placed over the screenshot are natural language too — translate them.

A block may span several visual lines. Return `ko_lines` with EXACTLY as many strings as `lines`, splitting your
Korean so each string replaces the line at the same position (balance lengths; separate legend entries stay
separate). Return every id exactly once. Write Korean in noun-phrase style suited to figure labels."""


def _glossary_block(wd: WorkDir, texts: list[str]) -> str:
    """그림 글자에 나오는 용어집 항목만 — 본문 번역과 같은 번역어를 쓰도록."""
    try:
        from narani.translate import glossary

        entries = glossary.effective(wd)[0].entries
    except Exception:  # noqa: BLE001 — 용어집이 없거나 못 읽으면 없이
        return ""
    blob = " ".join(texts).lower()
    rows = []
    for e in entries:
        if e.term and e.term.lower() in blob:
            rows.append(f"- {e.term} → {e.term if e.policy == 'keep' else e.korean}")
    return (f"Glossary (use these {_lang_name(wd.lang)} terms):\n" + "\n".join(rows[:60]) + "\n\n") if rows else ""


def _lang_name(lang: str) -> str:
    from narani import langs

    return langs.get(lang).english


def _system(lang: str) -> str:
    """라벨 프롬프트 — 한국어는 그대로, 다른 언어는 'Korean'을 그 언어 이름으로 (필드 이름 ko_lines는 그대로)."""
    return _SYSTEM if lang == "ko" else _SYSTEM.replace("Korean", _lang_name(lang))


def label(provider, wd: WorkDir, png: bytes, lines: list[dict]) -> tuple[str, list[dict]]:  # noqa: ANN001
    """LLM 한 번 — (그림 종류, 줄마다 {action, ko}가 채워진 lines)."""
    blocks = group(lines)
    by_id = {x["id"]: x for x in lines}
    user = _glossary_block(wd, [x["en"] for x in lines]) + "Text blocks:\n" + "\n".join(
        json.dumps({"id": bi, "lines": [by_id[i]["en"] for i in ids]}, ensure_ascii=False)
        for bi, ids in enumerate(blocks))
    res = provider.parse_image(_system(wd.lang), user, [png], _Labels, max_tokens=8000)
    items = {it.id: it for it in res.items}
    out = []
    for bi, ids in enumerate(blocks):
        it = items.get(bi)
        kl = list(it.ko_lines) if it else []
        if len(kl) != len(ids):  # 줄 수가 어긋나면 그 블록은 손대지 않는다 — 한 줄에 몰면 글자가 깨알이 된다
            it = None
        for j, i in enumerate(ids):
            x = dict(by_id[i])
            x["block"] = bi
            x["action"] = it.action if it else "keep"
            x["ko"] = (kl[j].strip() if it else x["en"]) if x["action"] == "translate" else (
                "" if x["action"] == "drop" else x["en"])
            if x["action"] == "translate" and (not x["ko"] or x["ko"] == x["en"]):
                x["action"], x["ko"] = "keep", x["en"]
            if res.kind == "screenshot" and x["src"] == "ocr" and x["action"] == "translate":
                x["action"], x["ko"], x["ui"] = "keep", x["en"], True  # 화면 글자는 그대로 (모듈 설명)
            out.append(x)
    out.sort(key=lambda v: v["id"])
    return res.kind, out


# --- 그리기 -------------------------------------------------------------------------


def find_font(lang: str = "ko") -> str:
    """번역 언어의 글꼴 파일 — `[figures] font_<lang>` > `[figures] font` > 운영체제 기본 글꼴 후보."""
    from narani import config

    cfg = config.load_config().get("figures") or {}
    want = [cfg.get(f"font_{lang}"), cfg.get("font")]
    for p in [w for w in want if w] + list(_LANG_FONTS.get(lang, _LATIN_FONTS)):
        if p and Path(p).is_file():
            return str(p)
    key = f"font_{lang}" if lang != "ko" else "font"
    raise FigureTranslateError(f"{lang} 글꼴을 찾지 못했습니다 — config.toml의 [figures] {key}에 글꼴 파일 경로를 적으세요.")


def _bg_color(a, x0: int, y0: int, x1: int, y1: int):  # noqa: ANN001, ANN202 — 상자 둘레의 가장 흔한 색
    import numpy as np

    H, W = a.shape[:2]
    ring = np.concatenate([
        a[max(0, y0 - 3):y0, x0:x1].reshape(-1, 3), a[y1:min(H, y1 + 3), x0:x1].reshape(-1, 3),
        a[y0:y1, max(0, x0 - 3):x0].reshape(-1, 3), a[y0:y1, x1:min(W, x1 + 3)].reshape(-1, 3)])
    if not len(ring):
        ring = a[y0:y1, x0:x1].reshape(-1, 3)
    q = (ring // 8).astype(np.int64)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    k = np.bincount(keys).argmax()
    return ring[keys == k].mean(axis=0)


def _fit(font_path: str, text: str, w: float, h: float):  # noqa: ANN202 — (ImageFont, 크기)
    from PIL import ImageFont

    size = max(6, int(h * 0.92))
    while size > 6:
        f = ImageFont.truetype(font_path, size)
        left, top, right, bottom = f.getbbox(text)
        if right - left <= w * 1.04 and bottom - top <= h * 1.05:
            return f, size
        size -= 1
    return ImageFont.truetype(font_path, 6), 6


def render(png: bytes, lines: list[dict], font_path: str) -> bytes:
    """원본 그림에서 번역할 줄의 글자 픽셀만 지우고 한국어를 그린다 — 상자 밖은 그대로. PNG(원본 dpi 유지)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(io.BytesIO(png)) as src_im:
        dpi = src_im.info.get("dpi")
        a = np.asarray(src_im.convert("RGB")).copy()
    H, W = a.shape[:2]
    todo = [x for x in lines if x.get("action") == "translate" and str(x.get("ko") or "").strip()]
    jobs = []
    erase = np.zeros((H, W), np.uint8)
    for x in todo:
        x0, y0, x1, y1 = (int(round(v)) for v in x["box"])
        x0, y0, x1, y1 = max(0, x0 - _PAD), max(0, y0 - _PAD), min(W, x1 + _PAD), min(H, y1 + _PAD)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        bg = _bg_color(a, x0, y0, x1, y1)
        reg = a[y0:y1, x0:x1].astype(np.float32)
        dist = np.linalg.norm(reg - bg, axis=2)
        m = (dist > _FG_DIST).astype(np.uint8)
        fg = reg[dist >= np.percentile(dist, 90)].mean(axis=0) if m.any() else np.array([0.0, 0.0, 0.0])
        erase[y0:y1, x0:x1] |= cv2.dilate(m, np.ones((3, 3), np.uint8)) * 255
        jobs.append((x, (x0, y0, x1, y1), tuple(int(c) for c in fg)))
    out = cv2.inpaint(a, erase, 3, cv2.INPAINT_TELEA) if erase.any() else a
    im = Image.fromarray(out)
    # 같은 블록(여러 줄 이름표)은 같은 글자 크기로 — 줄마다 따로 맞추면 들쭉날쭉하다
    sizes: dict = {}
    for x, (x0, y0, x1, y1), _fg in jobs:
        w, h = (y1 - y0, x1 - x0) if x.get("vertical") else (x1 - x0, y1 - y0)
        _f, size = _fit(font_path, x["ko"], w - 2 * _PAD, h - 2 * _PAD)
        key = x.get("block", x["id"])
        sizes[key] = min(sizes.get(key, size), size)
    for x, (x0, y0, x1, y1), fg in jobs:
        vertical = bool(x.get("vertical"))
        w, h = (y1 - y0, x1 - x0) if vertical else (x1 - x0, y1 - y0)
        f = ImageFont.truetype(font_path, sizes[x.get("block", x["id"])])
        left, top, right, bottom = f.getbbox(x["ko"])
        tile = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
        spare = (w - 2 * _PAD) - (right - left)
        tx = _PAD - left + (spare / 2 if x.get("align") == "center" and spare > 0 else 0)
        ty = (h - (bottom - top)) / 2 - top
        ImageDraw.Draw(tile).text((tx, ty), x["ko"], font=f, fill=fg + (255,))
        if vertical:  # 세로 축 제목 — 아래에서 위로 읽는 방향(반시계 90°)
            tile = tile.rotate(90, expand=True)
        im.paste(tile, (x0, y0), tile)
    buf = io.BytesIO()
    im.save(buf, format="PNG", **({"dpi": dpi} if dpi else {}))
    return buf.getvalue()


def _align(lines: list[dict]) -> None:
    """여러 줄 블록이 가운데 맞춤이면(왼쪽은 들쭉날쭉, 가운데가 맞음) 줄마다 align=center."""
    blocks: dict = {}
    for x in lines:
        blocks.setdefault(x.get("block", x["id"]), []).append(x)
    for g in blocks.values():
        if len(g) < 2:
            continue
        lefts = [x["box"][0] for x in g]
        mids = [(x["box"][0] + x["box"][2]) / 2 for x in g]
        if max(lefts) - min(lefts) > 8 and max(mids) - min(mids) < 8:
            for x in g:
                x["align"] = "center"


# --- 한 장 · 여러 장 ------------------------------------------------------------------


def _write(wd: WorkDir, src: str, rec: dict, png: bytes, font: str) -> int:
    """기록대로 번역 그림을 그려 out/images에 쓴다 — 번역한 줄 수 (0이면 그림 파일을 지운다)."""
    n = sum(1 for x in rec["lines"] if x.get("action") == "translate" and str(x.get("ko") or "").strip())
    ko = wd.out_images / rec["ko"]
    if n:
        wd.out_images.mkdir(parents=True, exist_ok=True)
        ko.write_bytes(render(png, rec["lines"], font))
    elif ko.exists():
        ko.unlink()
    rec["translated"] = n
    rec["renderer"] = RENDERER
    return n


def translate(wd: WorkDir, out_name: str, provider, *, ocr=None, link: bool = True) -> dict:  # noqa: ANN001
    """그림 한 장을 번역한다 (LLM 한 번). 반환 {name, ko, translated, kind, cost_usd}.

    글자를 못 찾았거나 번역할 글자가 없으면 translated=0 — 기록은 남겨 일괄 실행이 다시 부르지 않게 한다.
    """
    from narani.llm.base import Usage, cost_usd

    _require_supported(wd)
    font = find_font(wd.lang)
    out_name = original_name(wd, out_name)
    src = _source(wd, out_name)
    png = (wd.extract_images / src).read_bytes()
    lines = find_lines(wd, src, png, ocr=ocr)
    kind, cost = "other", 0.0
    if lines:
        u0 = (provider.usage.input_tokens, provider.usage.output_tokens)
        kind, lines = label(provider, wd, png, lines)
        cost = cost_usd(provider.model, Usage(input_tokens=provider.usage.input_tokens - u0[0],
                                              output_tokens=provider.usage.output_tokens - u0[1]))
        _align(lines)
    rec = {"out": out_name, "ko": ko_name(out_name, wd.lang), "src_digest": _digest(png), "kind": kind,
           "lines": lines, "model": f"{getattr(provider, 'name', '')}/{provider.model}",
           "cost_usd": round(cost, 6), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with _LOCK:
        n = _write(wd, src, rec, png, font)
        data = load(wd)
        data[src] = rec
        _save(wd, data)
        if link:
            link_ko(wd)
    return {"name": out_name, "ko": rec["ko"] if n else None, "translated": n, "kind": kind, "cost_usd": cost}


def edit(wd: WorkDir, out_name: str, edits: dict) -> dict:
    """한국어를 고쳐 다시 그린다 (LLM 없음). edits = {줄 id: 한국어} — 빈 글이면 그 줄은 원문 그대로."""
    font = find_font(wd.lang)
    with _LOCK:
        src, rec = _rec_for(wd, out_name)
        if rec is None:
            raise FigureTranslateError("이 그림의 번역 기록이 없습니다.")
        if stale(wd, src, rec):
            raise FigureTranslateError("원본 그림이 바뀌었습니다 — 번역을 다시 만드세요.")
        for x in rec["lines"]:
            key = str(x["id"])
            if key not in {str(k) for k in edits}:
                continue
            ko = str(edits.get(key, edits.get(x["id"], "")) or "").strip()
            if ko and ko != x["en"]:
                x["action"], x["ko"] = "translate", ko
            else:
                x["action"], x["ko"] = "keep", x["en"]
        rec["edited"] = True
        n = _write(wd, src, rec, (wd.extract_images / src).read_bytes(), font)
        data = load(wd)
        data[src] = rec
        _save(wd, data)
        link_ko(wd)
    return {"name": rec["out"], "ko": rec["ko"] if n else None, "translated": n}


def remove(wd: WorkDir, out_name: str) -> bool:
    """번역 그림과 기록을 지운다 — ko.md는 원본 그림으로 돌아간다."""
    with _LOCK:
        src, rec = _rec_for(wd, out_name)
        if rec is None:
            return False
        ko = wd.out_images / rec.get("ko", "")
        if rec.get("ko") and ko.is_file():
            ko.unlink()
        data = load(wd)
        data.pop(src, None)
        _save(wd, data)
        link_ko(wd)
    return True


def info(wd: WorkDir, out_name: str) -> dict:
    """확대 뷰어 메뉴용 — {name, ko, has, stale, kind, translated, lines, model, cost_usd, edited}."""
    name = original_name(wd, out_name)
    src, rec = _rec_for(wd, name)
    if rec is None:
        return {"name": name, "ko": ko_name(name, wd.lang), "has": False, "record": False}
    st = stale(wd, src, rec)
    return {"name": name, "ko": rec.get("ko"), "record": True,
            "has": bool(rec.get("translated")) and (wd.out_images / rec.get("ko", "")).is_file() and not st,
            "stale": st, "kind": rec.get("kind"), "translated": rec.get("translated", 0),
            "model": rec.get("model"), "cost_usd": rec.get("cost_usd", 0.0), "edited": bool(rec.get("edited")),
            "lines": [{"id": x["id"], "en": x["en"], "ko": x.get("ko", ""), "action": x.get("action"),
                       "ui": bool(x.get("ui"))} for x in rec.get("lines", [])]}


def candidates(wd: WorkDir) -> list[str]:
    """번역 대상 그림 — en.md가 참조하는 본문 그림(수식 크롭·번역 그림 빼고), 문서 순서."""
    if not wd.en_md.exists() or not wd.out_images.is_dir():
        return []
    names = []
    for m in _REF_RE.finditer(wd.en_md.read_text(encoding="utf-8")):
        n = m.group(2)
        if is_candidate(n) and n not in names and (wd.out_images / n).is_file():
            names.append(n)
    return names


def todo(wd: WorkDir) -> list[str]:
    """아직 번역하지 않은 그림 — 기록이 없거나 원본이 바뀐 것 (글자가 없던 그림은 다시 부르지 않는다)."""
    data = load(wd)
    done = {rec.get("out") for src, rec in data.items() if not stale(wd, src, rec)}
    return [n for n in candidates(wd) if n not in done]


def estimate_usd(model: str, n: int) -> float:
    from narani.llm.base import Usage, cost_usd

    return n * cost_usd(model, Usage(input_tokens=EST_INPUT_TOKENS, output_tokens=EST_OUTPUT_TOKENS))


def translate_many(wd: WorkDir, provider, names: list[str] | None = None,  # noqa: ANN001
                   on_progress=None, *, ocr=None) -> dict:  # noqa: ANN001
    """여러 장 — names가 없으면 `todo`. 그림 하나가 실패해도 나머지는 계속한다.

    반환 {done(번역 그림 만든 수), empty(번역할 글자 없음), failed{이름: 까닭}, cost_usd}.
    """
    _require_supported(wd)
    names = todo(wd) if names is None else names
    out = {"done": 0, "empty": 0, "failed": {}, "cost_usd": 0.0, "total": len(names)}
    for i, n in enumerate(names):
        if on_progress is not None:
            on_progress(i, len(names))
        try:
            r = translate(wd, n, provider, ocr=ocr)  # 한 장마다 ko.md에 건다 — 도중에 멈춰도 만든 그림은 보인다
        except Exception as e:  # noqa: BLE001 — 한 장의 실패(LLM 거부·그림 없음)가 일괄을 멈추지 않게
            out["failed"][n] = str(e)[:200]
            continue
        out["cost_usd"] += r["cost_usd"]
        out["done" if r["translated"] else "empty"] += 1
    if on_progress is not None:
        on_progress(len(names), len(names))
    return out
