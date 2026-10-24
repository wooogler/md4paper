"""인라인 수식 복원 — Docling이 납작하게 편 본문 수식을 PDF 글자 좌표로 $LaTeX$로 되살린다.

Docling은 첨자를 기준선과 크기 정보 없이 글자만 이어 "x ( k ) i ∈ [ 0 , 1 ] d k"처럼 낸다. 그런데
PDF 텍스트 레이어에는 답이 다 있다: **글꼴**은 무엇이 수식인지(LibertineMathMI·txsys·CMMI …),
**기준선과 크기**는 무엇이 위·아래 첨자인지 알려 준다. 그래서 규칙으로 추측하지 않고 그 증거로
"$x_{i}^{(k)} \\in [0,1]^{d_{k}}$"를 조립한다.

1) 페이지 글자를 기준선으로 줄을 만들고(첨자는 가장 가까운 본문 줄에 붙인다) x 순으로 정렬한다.
2) 수식 글꼴·수학 기호·첨자가 낀 낱말을 씨앗으로, 사이의 숫자·괄호·연산자를 이어 '수식 섬'을 만든다.
3) 섬을 기준선·크기로 재귀 조립해 LaTeX로.
4) 섬의 글자(스트림 순서 — Docling도 이 순서로 낸다)와 앞뒤 문맥으로 마크다운에서 자리를 찾아 바꾼다.
   못 찾거나 모호하면 그대로 둔다(확신 없으면 추측하지 않는다).
"""

from __future__ import annotations

import ctypes
import re
import statistics
import unicodedata
from dataclasses import dataclass, field

_MATH_FONT = re.compile(
    r"Math|^tx(mi|sy|ex|sys|exs|sym|mia)|^NewTX|^CM(MI|SY|EX|BSY)|^cm(mi|sy|ex|bsy)|^MSAM|^MSBM|"
    r"^rsfs|^eufm|^stix.*math|^STIXMath|^esint|^MnSymbol|^Asana|^Cambria.?Math", re.I)
_SLANT_FONT = re.compile(r"Ital|Oblique|-It\b|-It[A-Z]|TI$|BI$", re.I)
_BOLD_FONT = re.compile(r"Bold|Semibold|Heavy|Black|TB$|TZ$|MIB|BX|bx\d", re.I)
_OPEN, _CLOSE = "([{⟨", ")]}⟩"
_DELIM = set(_OPEN + _CLOSE + "|‖")
_TRAIL = set(".,;:")
_ACCENTS = {"˜": "tilde", "~": "tilde", "̃": "tilde", "¯": "bar", "ˉ": "bar", "̄": "bar", "ˆ": "hat",
            "^": "hat", "̂": "hat", "˙": "dot", "̇": "dot", "¨": "ddot", "̈": "ddot", "⃗": "vec"}
_RELATIONS = set("=<>+-−×·∈∉⊂⊆⊃⊇≤≥≠≈∼≃≡≻≺⪰⪯→←↦⇒⇔∝:/") | {"∗"}

_SYMBOLS = {
    "∈": r"\in", "∉": r"\notin", "≻": r"\succ", "≺": r"\prec", "⪰": r"\succeq", "⪯": r"\preceq",
    "⊂": r"\subset", "⊆": r"\subseteq", "⊃": r"\supset", "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap",
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx", "∼": r"\sim", "≃": r"\simeq",
    "≡": r"\equiv", "∝": r"\propto", "×": r"\times", "·": r"\cdot", "⋅": r"\cdot", "∗": "*",
    "−": "-", "±": r"\pm", "∓": r"\mp", "→": r"\to", "←": r"\leftarrow", "↦": r"\mapsto",
    "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow", "∞": r"\infty", "∂": r"\partial", "∆": r"\Delta", "∇": r"\nabla",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "√": r"\sqrt{}", "ℓ": r"\ell", "★": r"\star",
    "⋆": r"\star", "′": "'", "″": "''", "∀": r"\forall", "∃": r"\exists", "∅": r"\emptyset",
    "∧": r"\wedge", "∨": r"\vee", "¬": r"\neg", "⟨": r"\langle", "⟩": r"\rangle", "‖": r"\|",
    "…": r"\ldots", "⋯": r"\cdots", "°": r"^{\circ}", "∘": r"\circ", "⊤": r"\top", "⊥": r"\perp",
    "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&", "_": r"\_", "$": r"\$", "\\": r"\backslash",
}
_GREEK_VARIANT = {"ϵ": r"\epsilon", "ε": r"\varepsilon", "ϑ": r"\vartheta", "ϕ": r"\phi", "φ": r"\varphi",
                  "ϱ": r"\varrho", "ς": r"\varsigma", "ϖ": r"\varpi"}
_GREEK_LATIN_CAPS = set("ABEZHIKMNOPTXY")  # 라틴 대문자와 같은 그리스 대문자는 LaTeX 명령이 없다


@dataclass
class Glyph:
    idx: int  # 페이지 텍스트 스트림 위치 — Docling의 글자 순서와 같다
    ch: str
    font: str
    size: float
    x0: float
    x1: float
    y: float  # 기준선
    gap: bool = False  # 줄 안에서 앞 글자와 사이에 공백이 있다
    style: int = 0  # 글꼴의 굵게(1)·기울임(2) 비트 — 강조 복원(emphasis)이 쓴다


@dataclass
class Island:
    glyphs: list[Glyph]
    latex: str
    before: str  # 같은 줄 앞 문맥 (정규화)
    after: str
    page: int = 0
    keys: list[str] = field(default_factory=list)


# --- 글자 읽기 -------------------------------------------------------------------------

# 글꼴 이름으로 본 굵게·기울임. 서술자(굵기·기울기)가 틀린 글꼴이 흔해(LinLibertineTBI는 굵기 375,
# LinBiolinumTBO는 기울기 0) 이름과 서술자 중 하나라도 그렇다고 하면 그런 것으로 본다.
_BOLD_NAME = re.compile(r"Bold|Black|Heavy|Semi-?bold|Demi|Medium|Medi$|Bd\b|CMBX|CMB\d|SFBX|LMRomanDemi",
                        re.I)
_BOLD_SUFFIX = re.compile(r"-B[IO]?$|,Bold|T[BZ][IO]?$|bx\d")
_ITALIC_NAME = re.compile(r"Ital|Oblique|Slant|Kursiv|CMTI|CMSL|CMBXTI|CMBXSL|SFTI|SFSL|SFIT", re.I)
_ITALIC_SUFFIX = re.compile(r"-It\b|-It[A-Z]|-B?[IO]$|,Italic|T[BZ]?[IO]$")


def font_style(name: str, weight: int, angle: int | None) -> int:
    """글꼴의 굵게(1)·기울임(2) 비트 — 이름 또는 서술자(굵기 ≥ 600, 기울기 ≠ 0)."""
    bold = bool(_BOLD_NAME.search(name) or _BOLD_SUFFIX.search(name)) or weight >= 600
    italic = bool(_ITALIC_NAME.search(name) or _ITALIC_SUFFIX.search(name)) or bool(angle)
    return int(bold) | (2 if italic else 0)


def _page_glyphs(textpage) -> list[Glyph]:  # noqa: ANN001
    import pypdfium2.raw as C

    names: dict[int, str] = {}
    styles: dict[int, int] = {}
    out: list[Glyph] = []
    n = textpage.count_chars()
    i = 0
    m = C.FS_MATRIX()
    x, y = ctypes.c_double(), ctypes.c_double()
    while i < n:
        u = C.FPDFText_GetUnicode(textpage, i)
        step = 1
        if 0xD800 <= u <= 0xDBFF and i + 1 < n:
            lo = C.FPDFText_GetUnicode(textpage, i + 1)
            if 0xDC00 <= lo <= 0xDFFF:
                u, step = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00), 2
        ch = chr(u) if u else ""
        if not ch or ch.isspace() or C.FPDFText_IsGenerated(textpage, i) == 1:
            i += step
            continue
        obj = C.FPDFText_GetTextObject(textpage, i)
        font = C.FPDFTextObj_GetFont(obj) if obj else None
        key = ctypes.cast(font, ctypes.c_void_p).value if font else None
        if key is not None and key not in names:
            k = C.FPDFFont_GetBaseFontName(font, None, 0)
            buf = ctypes.create_string_buffer(k)
            C.FPDFFont_GetBaseFontName(font, buf, k)
            names[key] = re.sub(r"^[A-Z]{6}\+", "", buf.value.decode("latin-1"))
            angle = ctypes.c_int(0)
            has_angle = C.FPDFFont_GetItalicAngle(font, ctypes.byref(angle))
            styles[key] = font_style(names[key], C.FPDFFont_GetWeight(font),
                                     angle.value if has_angle else None)
        scale = 1.0
        if C.FPDFText_GetMatrix(textpage, i, m):
            scale = abs(m.a * m.d - m.b * m.c) ** 0.5 or 1.0
        size = float(C.FPDFText_GetFontSize(textpage, i)) * scale
        C.FPDFText_GetCharOrigin(textpage, i, x, y)
        left, _, right, _ = textpage.get_charbox(i)
        if size > 0:
            out.append(Glyph(i, ch, names.get(key, ""), size, left, right, y.value,
                             style=styles.get(key, 0)))
        i += step
    return out


def _lines(glyphs: list[Glyph]) -> list[list[Glyph]]:
    """기준선이 같은 글자끼리 줄로 묶고, 첨자·큰 괄호만 있는 줄은 가장 가까운 본문 줄에 붙인다."""
    if not glyphs:
        return []
    body = statistics.median(g.size for g in glyphs)
    rows: list[list[Glyph]] = []
    for g in sorted(glyphs, key=lambda g: -g.y):
        if rows and abs(rows[-1][0].y - g.y) <= 0.15 * body:
            rows[-1].append(g)
        else:
            rows.append([g])
    segs: list[list[Glyph]] = []
    for row in rows:  # 같은 기준선이라도 단(column)이 다르면 다른 줄
        row.sort(key=lambda g: g.x0)
        cur = [row[0]]
        for g in row[1:]:
            if g.x0 - cur[-1].x1 > 2.5 * body:  # 단 사이
                segs.append(cur)
                cur = []
            cur.append(g)
        segs.append(cur)
    segs = [p for seg in segs for p in _peel_line_numbers(seg, body)]

    def minor(seg: list[Glyph]) -> bool:
        return all(g.ch in _DELIM for g in seg) or statistics.median(g.size for g in seg) < 0.85 * body

    hosts = [s for s in segs if not minor(s)]
    lines = {id(s): list(s) for s in hosts}
    for seg in segs:
        if not minor(seg):
            continue
        best, dist = None, None
        for h in hosts:
            if seg[0].x0 < h[0].x0 - 0.5 * body or seg[0].x0 > h[-1].x1 + 0.5 * body:
                continue
            dy = seg[0].y - h[0].y
            if -0.65 * body <= dy <= 0.85 * body and (dist is None or abs(dy) < dist):
                best, dist = h, abs(dy)
        if best is None:
            lines[id(seg)] = list(seg)
        else:
            lines[id(best)].extend(seg)
    out = []
    for line in lines.values():
        line.sort(key=lambda g: (g.x0, g.idx))
        out.append(line)
    return out


def _peel_line_numbers(seg: list[Glyph], body: float) -> list[list[Glyph]]:
    """줄 앞뒤 여백의 줄 번호(심사용 원고)를 따로 뗀다 — 문맥과 첨자 판정을 흐리지 않게."""
    out = []
    k = 0
    while k < len(seg) and seg[k].ch.isdigit():
        k += 1
    if 0 < k < len(seg) and seg[k].x0 - seg[k - 1].x1 > body:
        out.append(seg[:k])
        seg = seg[k:]
    k = len(seg)
    while k > 0 and seg[k - 1].ch.isdigit():
        k -= 1
    if 0 < k < len(seg) and seg[k].x0 - seg[k - 1].x1 > body:
        return out + [seg[:k], seg[k:]]
    return out + [seg]


# --- 수식 섬 ---------------------------------------------------------------------------

def _is_math_char(ch: str) -> bool:
    o = ord(ch)
    if 0x1D400 <= o <= 0x1D7FF or 0x370 <= o <= 0x3FF or 0x2190 <= o <= 0x22FF or 0x27C0 <= o <= 0x2AFF:
        return True
    return unicodedata.category(ch) == "Sm" and ch not in "+=<>|~^-"


def _line_metrics(line: list[Glyph]) -> tuple[float, float]:
    big = max(g.size for g in line)
    main = [g for g in line if g.size >= 0.85 * big and g.ch not in _DELIM] or line
    size = statistics.median(g.size for g in main)
    base = statistics.mode(round(g.y, 1) for g in main)
    return size, base


def _is_script(g: Glyph, size: float, base: float) -> bool:
    if g.ch in _DELIM and g.size >= 0.85 * size:
        return False
    return g.size < 0.85 * size or abs(g.y - base) > 0.2 * size


def _islands(line: list[Glyph], text_font: str) -> list[list[Glyph]]:
    size, base = _line_metrics(line)
    # 낱말(토큰)로 자르기 — 첨자는 앞 글자에 겹치거나 붙어 있어 공백으로 치지 않는다
    tokens: list[list[Glyph]] = []
    prev_x1 = None
    for g in line:
        scr = _is_script(g, size, base)
        g.gap = prev_x1 is not None and g.x0 - prev_x1 > 0.17 * size and not (
            scr and g.x0 - prev_x1 < 0.3 * size)
        if not tokens or g.gap:
            tokens.append([])
        tokens[-1].append(g)
        prev_x1 = g.x1 if prev_x1 is None else max(prev_x1, g.x1) if scr else g.x1

    def lone_letter(t: int) -> bool:
        core = [g for g in tokens[t]
                if g.ch not in _ACCENTS and g.ch not in _TRAIL and not _is_script(g, size, base)]
        if (len(core) != 1 or not core[0].ch.isalpha() or not text_font or core[0].font == text_font
                or not (_BOLD_FONT.search(core[0].font) or _SLANT_FONT.search(core[0].font))):
            return False
        near = [tokens[n] for n in (t - 1, t + 1) if 0 <= n < len(tokens)]
        return not any(g.font == core[0].font and g.ch.isalpha() for tok in near for g in tok)

    def wordlike(tok: list[Glyph]) -> bool:
        run = 0
        for g in tok:
            plain = g.ch.isalpha() and not _MATH_FONT.search(g.font) and not _is_script(g, size, base)
            run = run + 1 if plain else 0
            if run >= 2:
                return True
        return False

    def mathy(tok: list[Glyph]) -> bool:
        if wordlike(tok):
            return False
        for k, g in enumerate(tok):
            if _is_math_char(g.ch) or (_MATH_FONT.search(g.font) and (
                    g.ch.isalpha() or unicodedata.category(g.ch)[0] == "S")):
                return True
            if _is_script(g, size, base) and k:
                host = tok[k - 1]
                if host.ch.isdigit() or host.ch in _CLOSE or _MATH_FONT.search(host.font):
                    return True
        return False

    def neutral(tok: list[Glyph]) -> bool:  # 숫자·괄호·연산자만 — 수식 사이에 있으면 수식의 일부
        return all(not g.ch.isalpha() for g in tok)

    # 본문 글꼴과 다른 글꼴의 외톨이 글자도 수식이다 (굵은 벡터 x 등)
    def bridge(k: int) -> bool:
        word = tokens[k]
        if len(word) > 4 or not all(g.ch.isascii() for g in word):
            return False
        nxt = tokens[k + 1][0].ch if k + 1 < len(tokens) else ""
        return tokens[k - 1][-1].ch in _RELATIONS - {":"} or tokens[k - 1][-1].ch == "," and nxt in _RELATIONS

    kinds = ["m" if mathy(t) or lone_letter(k) else "n" if neutral(t) else "t" for k, t in enumerate(tokens)]
    out: list[list[Glyph]] = []
    i = 0
    while i < len(tokens):
        if kinds[i] != "m":
            i += 1
            continue
        start, j = i, i + 1
        while j < len(tokens):
            if kinds[j] == "m":
                j += 1
                continue
            # 수식 사이의 중립 토큰(숫자·괄호), 그리고 관계 기호에 맞닿은 짧은 이름(= clip, SD =)은
            # 뒤에 수식이 이어질 때만 붙인다
            k = j
            while k < len(tokens) and (kinds[k] == "n" or (kinds[k] == "t" and bridge(k))):
                k += 1
            if k < len(tokens) and kinds[k] == "m" and k > j:
                j = k
                continue
            # 끝이 관계 기호면 그 피연산자(숫자)까지: "p < .001", "2560 × 1440"
            if kinds[j] == "n" and (tokens[j - 1][-1].ch in _RELATIONS or tokens[j][0].ch in _RELATIONS):
                j += 1
                continue
            break
        # 앞쪽: 바로 앞의 여는 괄호 토큰도 포함
        while start > 0 and kinds[start - 1] == "n" and all(g.ch in _OPEN for g in tokens[start - 1]):
            start -= 1
        glyphs = [g for t in tokens[start:j] for g in t]
        glyphs = _trim(glyphs)
        # 기댈 글자 없이 시작하는 첨자(알고리즘 줄 번호 "2:" 등)는 뗀다 — 위아래로 겹친 분수(∂L/∂x)는 둔다
        while glyphs and _is_script(glyphs[0], size, base) and not _stacked(glyphs, size, base):
            glyphs = _trim(glyphs[1:])
        base_glyphs = [g for g in glyphs if not _is_script(g, size, base)]
        # 기준선에 변수·숫자·연산자(∇ ∂ ∑)가 있어야 수식이다 — 산문 속 화살표(→, 위에 라벨 붙은 것
        # 포함)는 그대로. 숫자만이면 첨자가 있어야(10⁻⁴). 분수는 기준선이 비어도 수식이다.
        has_var = any(unicodedata.category(g.ch)[0] == "L" or _is_math_char(g.ch) and not _is_arrowish(g.ch)
                      for g in base_glyphs)
        has_num = any(g.ch.isdigit() for g in base_glyphs) and len(base_glyphs) < len(glyphs)
        if glyphs and (has_var or has_num or _stacked(glyphs, size, base)):
            out.append(glyphs)
        i = j
    return out


def _trim(glyphs: list[Glyph]) -> list[Glyph]:
    for k, g in enumerate(glyphs):  # 하이픈 뒤 본문 글꼴 낱말("t-tests")은 수식이 아니다
        rest = glyphs[k + 1:]
        if g.ch in "-‐" and len(rest) >= 2 and all(h.ch.isalpha() and not _MATH_FONT.search(h.font)
                                                    for h in rest[:2]):
            glyphs = glyphs[:k]
            break
    while glyphs and (glyphs[-1].ch in _TRAIL or _is_arrowish(glyphs[-1].ch) or (
            glyphs[-1].ch in _CLOSE and _balance(glyphs) < 0)):
        glyphs = glyphs[:-1]
    while glyphs and (glyphs[0].ch in _TRAIL or (glyphs[0].ch in _OPEN and _balance(glyphs) > 0)):
        glyphs = glyphs[1:]
    # 본문 괄호로 감싼 수식은 괄호를 밖에 둔다: "(N = 16)" → ($N=16$)
    while (len(glyphs) > 2 and glyphs[0].ch == "(" and glyphs[-1].ch == ")"
           and not _MATH_FONT.search(glyphs[0].font) and _balance(glyphs[1:-1]) == 0
           and _outer_pair(glyphs)):
        glyphs = glyphs[1:-1]
    return glyphs


def _is_arrowish(ch: str) -> bool:
    return 0x2190 <= ord(ch) <= 0x21FF or 0x27F0 <= ord(ch) <= 0x27FF or ch in _RELATIONS


def _stacked(glyphs: list[Glyph], size: float, base: float) -> bool:
    """맨 앞 첨자 묶음이 위·아래로 겹쳐 있나 — 인라인 분수(∂L/∂x)의 분자·분모."""
    run = []
    for g in glyphs:
        if not _is_script(g, size, base):
            break
        run.append(g)
    ups = [g for g in run if g.y >= base]
    downs = [g for g in run if g.y < base]
    return bool(ups and downs) and min(g.x1 for g in ups) > max(g.x0 for g in downs) - size


def _outer_pair(glyphs: list[Glyph]) -> bool:
    depth = 0
    for k, g in enumerate(glyphs):
        depth += (g.ch in _OPEN) - (g.ch in _CLOSE)
        if depth == 0:
            return k == len(glyphs) - 1
    return False


def _balance(glyphs: list[Glyph]) -> int:
    return sum(1 for g in glyphs if g.ch in _OPEN) - sum(1 for g in glyphs if g.ch in _CLOSE)


# --- LaTeX 조립 ------------------------------------------------------------------------

def _tex_char(g: Glyph) -> str:
    ch = g.ch
    if ch in _SYMBOLS:
        return _SYMBOLS[ch]
    if ch in _GREEK_VARIANT:
        return _GREEK_VARIANT[ch]
    o = ord(ch)
    name = unicodedata.name(ch, "")
    if 0x1D400 <= o <= 0x1D7FF:
        base = unicodedata.normalize("NFKC", ch)
        inner = _tex_char(Glyph(g.idx, base, g.font, g.size, g.x0, g.x1, g.y)) if base != ch else base
        if "BOLD" in name:
            return rf"\boldsymbol{{{inner}}}" if inner.startswith("\\") else rf"\mathbf{{{inner}}}"
        if "DOUBLE-STRUCK" in name:
            return rf"\mathbb{{{inner}}}"
        if "SCRIPT" in name:
            return rf"\mathcal{{{inner}}}"
        if "FRAKTUR" in name:
            return rf"\mathfrak{{{inner}}}"
        return inner
    if name.startswith("GREEK SMALL LETTER ") or name.startswith("GREEK CAPITAL LETTER "):
        word = name.split("LETTER ", 1)[1].split()[0].lower()
        if name.startswith("GREEK CAPITAL"):
            if ch in _GREEK_LATIN_CAPS or unicodedata.normalize("NFD", ch)[0] in "ΑΒΕΖΗΙΚΜΝΟΡΤΧΥ":
                return {"Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
                        "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": r"\Upsilon"}.get(ch, ch)
            word = word.capitalize()
        if word in ("omicron",):
            return "o"
        if word == "lamda":
            word = "lambda"
        return "\\" + word
    if ch.isalpha() and _BOLD_FONT.search(g.font):
        return rf"\mathbf{{{ch}}}"
    return ch


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out and re.search(r"\\[A-Za-z]+$", out) and p[:1].isalpha():
            out += " "
        out += p
    return out


def _wrap(s: str) -> str:
    return s if len(s) == 1 else "{" + s + "}"


def _render_group(grp: list[Glyph]) -> str:
    """첨자 묶음을 그 묶음의 기준선·크기로 다시 조립 (첨자의 첨자: d_k)."""
    big = max(h.size for h in grp)
    main = [h for h in grp if h.size >= 0.85 * big] or grp
    return render(grp, statistics.median(h.size for h in main), statistics.mode(round(h.y, 1) for h in main))


def render(glyphs: list[Glyph], size: float, base: float) -> str:
    """기준선·크기로 첨자를 재귀 조립한 LaTeX (x 순으로 정렬된 글자)."""
    atoms: list[str] = []
    atom_box: list[tuple[float, float]] = []
    i = 0
    while i < len(glyphs):
        g = glyphs[i]
        if g.ch in _ACCENTS and atoms and g.x0 < atom_box[-1][1] and g.x1 > atom_box[-1][0] and (
                not g.ch.isalnum()):
            atoms[-1] = "\\" + _ACCENTS[g.ch] + "{" + atoms[-1] + "}"
            i += 1
            continue
        if _is_script(g, size, base):
            j = i
            while j < len(glyphs) and _is_script(glyphs[j], size, base) and glyphs[j].ch not in _ACCENTS:
                j += 1
            if j == i:  # 첨자 위치의 악센트 글자 — 덮을 대상이 없으면 그냥 글자로
                atoms.append(_tex_char(g))
                atom_box.append((g.x0, g.x1))
                i += 1
                continue
            run = glyphs[i:j]
            subs = [h for h in run if h.y < base]
            sups = [h for h in run if h.y >= base]
            if subs and sups and (not atoms or run[0].x0 - atom_box[-1][1] > 0.15 * size):
                num, den = (_render_group(grp) for grp in (sups, subs))
                atoms.append(r"\frac{" + num + "}{" + den + "}")
                atom_box.append((run[0].x0, max(h.x1 for h in run)))
                i = j
                continue
            tail = ""
            for mark, grp in (("_", subs), ("^", sups)):
                if grp:
                    tail += mark + "{" + _render_group(grp) + "}"
            if not atoms:
                atoms.append("{}")
                atom_box.append((g.x0, g.x0))
            atoms[-1] += tail
            atom_box[-1] = (atom_box[-1][0], max(h.x1 for h in run))
            i = j
            continue
        # 본문 글꼴의 영문 낱말(clip, exp) — 한 덩어리 \mathrm (굵으면 \mathbf)
        if g.ch.isascii() and g.ch.isalpha() and not _MATH_FONT.search(g.font):
            j = i + 1
            while (j < len(glyphs) and glyphs[j].ch.isascii() and glyphs[j].ch.isalpha()
                   and glyphs[j].font == g.font and not glyphs[j].gap and not _is_script(glyphs[j], size, base)):
                j += 1
            if j - i > 1:
                cmd = r"\mathbf" if _BOLD_FONT.search(g.font) else r"\mathrm"
                atoms.append(cmd + "{" + "".join(h.ch for h in glyphs[i:j]) + "}")
                atom_box.append((g.x0, glyphs[j - 1].x1))
                i = j
                continue
        atoms.append(_tex_char(g))
        atom_box.append((g.x0, g.x1))
        i += 1
    return _join(atoms)


def _simplify(tex: str) -> str:
    # x_{j} → x_j : 한 글자 첨자는 중괄호를 뺀다 (명령·중괄호가 아닌 한 글자만)
    return re.sub(r"([_^])\{([A-Za-z0-9*'])\}", r"\1\2", tex)


# --- 마크다운에 맞추기 -----------------------------------------------------------------

def norm(text: str) -> tuple[str, list[int]]:
    """비교용 정규화(공백·결합 부호 제거, NFKC, −→-) + 원문 위치 표."""
    out: list[str] = []
    pos: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace() or unicodedata.category(c) == "Mn" or c in "\\":
                continue
            out.append({"−": "-", "’": "'", "‘": "'", "“": '"', "”": '"'}.get(c, c))
            pos.append(i)
    return "".join(out), pos


_PROTECT = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$|`[^`\n]*`|!\[[^\]]*\]\([^)]*\)|<!--.*?-->|\(images/[^)]*\)",
                      re.DOTALL)


def collect(pdf) -> list[Island]:  # noqa: ANN001 — 경로 | bytes
    from narani import pdfio

    islands: list[Island] = []
    with pdfio.open_document(pdf) as doc:
        pages = [_page_glyphs(doc[pi].get_textpage()) for pi in range(len(doc))]
    fonts = [g.font for glyphs in pages for g in glyphs if g.ch.isalpha() and not _MATH_FONT.search(g.font)]
    text_font = statistics.mode(fonts) if fonts else ""
    for pi, glyphs in enumerate(pages):
        if len(glyphs) < 40:
            continue
        for line in _lines(glyphs):
            if len(line) < 2:
                continue
            size, base = _line_metrics(line)
            for isl in _islands(line, text_font):
                lo = line.index(isl[0])
                hi = line.index(isl[-1])
                before = norm("".join(g.ch for g in line[max(0, lo - 12):lo]))[0][-10:]
                after = norm("".join(g.ch for g in line[hi + 1:hi + 13]))[0][:10]
                tex = _simplify(render(isl, size, base)).strip()
                islands.append(Island(isl, tex, before, after, pi + 1))
    return islands


def apply(md: str, islands: list[Island]) -> tuple[str, int]:
    """섬들을 마크다운의 제자리에 $LaTeX$로 바꿔 넣는다. 반환: (새 마크다운, 바꾼 수)."""
    flat, pos = norm(md)
    blocked = bytearray(len(md))
    for m in _PROTECT.finditer(md):
        blocked[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    edits: list[tuple[int, int, str]] = []
    cursor = 0
    for isl in islands:
        key = norm("".join(g.ch for g in sorted(isl.glyphs, key=lambda g: g.idx)))[0]
        if not key or not isl.latex:
            continue
        hit = None
        for nb, na in ((10, 10), (6, 6), (10, 0), (0, 10)):
            b, a = isl.before[-nb:] if nb else "", isl.after[:na]
            if len(b) + len(a) < 4 and len(key) < 4:
                continue
            pat = b + key + a
            found = []
            k = flat.find(pat)
            while k != -1:
                s, e = k + len(b), k + len(b) + len(key)
                ms, me = pos[s], pos[e - 1] + 1
                span = md[ms:me]
                if "\n" not in span and not any(blocked[ms:me]) and not any(
                        ms < ee and es < me for es, ee, _ in edits):
                    found.append((ms, me))
                k = flat.find(pat, k + 1)
            if len(found) == 1:
                hit = found[0]
            elif found and len(b) + len(a) >= 8:
                after_cursor = [f for f in found if f[0] >= cursor]
                hit = after_cursor[0] if after_cursor else None
            if hit:
                break
        if not hit:
            continue
        ms, me = hit
        # 정규화에서 사라진 앞쪽 악센트(˜ x)도 바꿀 범위에 넣는다
        k = ms
        while k > 0 and md[k - 1] in " \t":
            k -= 1
        if k > 0 and md[k - 1] in _ACCENTS and any(g.ch in _ACCENTS for g in isl.glyphs):
            ms = k - 1
        # 낱말 중간을 자르지 않는다 ("at"의 t)
        if (ms > 0 and md[ms - 1].isalnum()) or (me < len(md) and md[me].isalnum()):
            continue
        edits.append((ms, me, "$" + isl.latex + "$"))
        cursor = me
    edits.sort()
    out, last = [], 0
    for ms, me, rep in edits:
        head = md[last:ms]
        if head.endswith(" ") and head.rstrip().endswith(("(", "[")):
            head = head.rstrip()
        out.append(head)
        out.append(rep)
        last = me
        # "$x$ ," → "$x$,"
        m = re.match(r"[ \t]+(?=[,.;:)\]])", md[last:])
        if m:
            last += m.end()
    out.append(md[last:])
    return "".join(out), len(edits)


def restore(md: str, pdf) -> tuple[str, int]:  # noqa: ANN001
    """PDF 좌표로 인라인 수식을 되살린다. 텍스트 레이어를 못 읽으면 그대로."""
    try:
        islands = collect(pdf)
    except Exception:  # noqa: BLE001 — 안전망: 못 하면 원문 그대로
        return md, 0
    return apply(md, islands)
