"""PDF ToUnicode 복구 — 글리프 이름은 멀쩡한데 유니코드 매핑만 깨진 글꼴을 바로잡는다.

macOS 미리보기(Quartz)로 다시 저장한 LaTeX PDF는 합자(fi·ff·fl·ffi·ft·tt)와 스몰캡 글리프를
ToUnicode에 U+FFFF로 적어 버린다. 그러면 docling·pdfium·poppler 모두 그 글자를 잃는다
("di�erent", "A�er", "C�������������" = Conversational). 그런데 같은 글꼴의 /Encoding /Differences에는
원래 글리프 이름(/f_f /f_i /t_t /a.sc …)이 그대로 남아 있어, 이름에서 글자를 확실하게 되살릴 수 있다.

그래서 추출 전에 그 매핑만 고친 사본을 만들어 추출기에 넘긴다. 페이지 내용·글꼴 프로그램은 그대로라
렌더링·레이아웃은 원본과 같다 (증분 저장: 원본 바이트 뒤에 고친 ToUnicode 스트림만 덧붙인다).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_BROKEN = ("�", "￾", "￿")
_SIMPLE_FONTS = {"/Type1", "/MMType1", "/TrueType", "/Type3"}  # /Differences를 쓰는 단일 바이트 글꼴

# AGL 표가 없을 때도 흔한 것은 되살린다 — 합자는 표현형(ﬁ)이 아니라 풀어 쓴 글자로
_LIGATURES = {"ff": "ff", "fi": "fi", "fl": "fl", "ffi": "ffi", "ffl": "ffl", "ft": "ft", "st": "st"}


def _agl(name: str) -> str | None:
    try:  # pypdf가 들고 있는 Adobe Glyph List (내부 모듈이라 없으면 조용히 포기)
        from pypdf._codecs.adobe_glyphs import adobe_glyphs
    except ImportError:
        return None
    return adobe_glyphs.get("/" + name)


def _component(part: str) -> str | None:
    if len(part) == 1 and part.isascii() and part.isalnum():
        return part
    if part in _LIGATURES:
        return _LIGATURES[part]
    if m := re.fullmatch(r"uni((?:[0-9A-F]{4})+)", part):
        return "".join(chr(int(m.group(1)[i:i + 4], 16)) for i in range(0, len(m.group(1)), 4))
    if m := re.fullmatch(r"u([0-9A-F]{4,6})", part):
        return chr(int(m.group(1), 16))
    text = _agl(part)
    return unicodedata.normalize("NFKC", text) if text else None


def glyph_text(name: str) -> str | None:
    """글리프 이름 → 글자 (AGL 규칙 축약): 'f_f_i'→'ffi', 'a.sc'→'a', 'uni00E9'→'é'. 모르면 None."""
    base = name.lstrip("/").split(".", 1)[0]
    if not base:
        return None
    parts = [_component(p) for p in base.split("_")]
    if any(p is None for p in parts):
        return None
    text = "".join(parts)
    return text if text and not any(c in text for c in _BROKEN) else None


# --- ToUnicode CMap ----------------------------------------------------------------

_HEX = r"<([0-9A-Fa-f\s]*)>"


def _utf16(hex_text: str) -> str:
    raw = bytes.fromhex(re.sub(r"\s", "", hex_text))
    return raw.decode("utf-16-be", errors="replace") if raw else ""


def parse_cmap(data: bytes) -> tuple[dict[int, str], str, int] | None:
    """ToUnicode CMap → (코드→글자, codespace 블록, 코드 바이트 수). 읽을 수 없으면 None."""
    text = data.decode("latin-1")
    space = re.search(r"\d+\s+begincodespacerange.*?endcodespacerange", text, re.S)
    if not space:
        return None
    width = re.search(_HEX, space.group(0))
    nbytes = max(1, len(re.sub(r"\s", "", width.group(1))) // 2) if width else 1
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(_HEX + r"\s*" + _HEX, block):
            mapping[int(src, 16)] = _utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst, arr in re.findall(_HEX + r"\s*" + _HEX + r"\s*(?:" + _HEX + r"|\[([^\]]*)\])", block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if hi_i < lo_i or hi_i - lo_i > 0xFFFF:
                return None
            if arr:
                for i, d in enumerate(re.findall(_HEX, arr)):
                    mapping[lo_i + i] = _utf16(d)
                continue
            base = bytes.fromhex(re.sub(r"\s", "", dst))
            if len(base) < 2:
                return None
            head, last = base[:-2], int.from_bytes(base[-2:], "big")
            for i in range(hi_i - lo_i + 1):  # 범위는 대상의 마지막 코드 단위를 하나씩 올린다
                unit = (last + i) & 0xFFFF
                mapping[lo_i + i] = (head + unit.to_bytes(2, "big")).decode("utf-16-be", errors="replace")
    return mapping, space.group(0), nbytes


def emit_cmap(mapping: dict[int, str], codespace: str, nbytes: int) -> bytes:
    """코드→글자 사전을 표준 ToUnicode CMap으로 (bfchar만, 블록당 100개 — CMap 규격 한도)."""
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def", codespace,
    ]
    items = sorted(mapping.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, text in chunk:
            lines.append(f"<{code:0{nbytes * 2}X}> <{text.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("latin-1")


def _differences(font) -> dict[int, str]:  # noqa: ANN001 — pypdf DictionaryObject
    enc = font.get("/Encoding")
    enc = enc.get_object() if enc is not None else None
    if not hasattr(enc, "get"):  # 이름 인코딩(/MacRomanEncoding 등)에는 글리프 이름 목록이 없다
        return {}
    out: dict[int, str] = {}
    code = 0
    for item in enc.get("/Differences") or []:
        item = item.get_object() if hasattr(item, "get_object") else item
        if isinstance(item, int):
            code = int(item)
        else:
            out[code] = str(item)
            code += 1
    return out


def fix_font(font) -> int:  # noqa: ANN001
    """글꼴 하나의 깨진 ToUnicode 항목을 글리프 이름으로 고친다. 반환: 고친 코드 수."""
    if font.get("/Subtype") not in _SIMPLE_FONTS or "/ToUnicode" not in font:
        return 0
    names = _differences(font)
    if not names:
        return 0
    stream = font["/ToUnicode"].get_object()
    parsed = parse_cmap(stream.get_data())
    if parsed is None:
        return 0
    mapping, codespace, nbytes = parsed
    named = {code: t for code, name in names.items() if (t := glyph_text(name))}
    # 수식 글꼴은 깨진 표시(U+FFFF) 대신 엉뚱한 글자로 적히기도 한다: 𝑡→'C', ℓ→'✓', ∈→'2'.
    # 이름이 풀리는 코드의 과반이 ToUnicode와 어긋나면 그 글꼴의 ToUnicode는 통째로 믿지 않는다.
    wrong = sum(1 for code, t in named.items() if mapping.get(code) != t)
    distrust = wrong * 2 > len(named)
    fixed = 0
    for code, good in named.items():
        text = mapping.get(code)
        if text == good:
            continue
        broken = not text or text == "\x00" or any(c in text for c in _BROKEN)
        if broken or distrust:
            mapping[code] = good
            fixed += 1
    if fixed:
        stream.set_data(emit_cmap(mapping, codespace, nbytes))
    return fixed


def _fonts(resources, seen: set[int]):  # noqa: ANN001, ANN202 — 페이지·Form XObject의 글꼴을 재귀로
    resources = resources.get_object() if resources is not None else None
    if not hasattr(resources, "get"):
        return
    fonts = resources.get("/Font")
    for ref in (fonts.get_object().values() if fonts is not None else []):
        key = getattr(ref, "idnum", None)
        if key is not None and key in seen:
            continue
        seen.add(key)
        yield ref.get_object()
    xobjects = resources.get("/XObject")
    for ref in (xobjects.get_object().values() if xobjects is not None else []):
        key = getattr(ref, "idnum", None)
        if key is not None and key in seen:
            continue
        seen.add(key)
        xo = ref.get_object()
        if xo.get("/Subtype") == "/Form":
            yield from _fonts(xo.get("/Resources"), seen)


def repair(source: Path, dest: Path) -> int:
    """source의 깨진 ToUnicode를 고친 사본을 dest에 쓴다. 반환: 되살린 글리프 코드 수.

    고칠 것이 없거나(대부분의 PDF) PDF를 읽지 못하면 0을 돌려주고 dest를 만들지 않는다.
    """
    try:
        from pypdf import PdfWriter

        writer = PdfWriter(str(source), incremental=True)
        seen: set[int] = set()
        fixed = sum(fix_font(f) for page in writer.pages for f in _fonts(page.get("/Resources"), seen))
        if fixed:
            with open(dest, "wb") as fh:
                writer.write(fh)
        return fixed
    except Exception:  # noqa: BLE001 — 안전망: 못 고치면 원본으로 추출한다
        return 0
