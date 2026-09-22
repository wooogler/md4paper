"""수식 → LaTeX — 크롭 PNG를 LLM에 보여주고 받아 적는다.

추출 단계(`extract/formulas.py`)가 수식 자리를 크롭 그림으로 만들어 두면, 여기서 그 그림을
LLM에 그대로 먹여 LaTeX를 받는다. 손으로 스크린샷 떠서 붙여넣던 것과 같은 일이다.

**왜 규칙이 아니라 LLM인가.** PDF 텍스트 레이어에 남은 수식 원문(`orig`)은
"⟨ Π ∗ , Θ ∗ ⟩ Φ = arg max ⟨ Π , Θ ⟩ Φ E …"처럼 기호는 살아 있지만 **구조가 없다** —
무엇이 첨자였고 무엇이 분수의 분모였는지가 좌표에만 있고 문자열에는 없다. 그 구조는
그림에 그대로 남아 있으므로, 그림을 보는 쪽이 원문을 파싱하는 쪽보다 근본적으로 유리하다.
그래도 `orig`을 프롬프트에 같이 넣는다: 그림만으로는 갈리는 글자(ℓ/l, ∗/*, ν/v)를
텍스트 레이어가 확정해 준다. 둘은 경쟁 관계가 아니라 서로의 빈 곳을 메운다.

**믿을 수 있는 만큼만 반영한다.** 돌아온 LaTeX는 렌더 가능한지(latex2mathml) 검사하고,
실패하면 그 수식만 그림으로 남긴다 — 깨진 `$$…$$`는 아무것도 없는 것보다 나쁘다.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel, Field

from md4paper.extract.formulas import IMAGE_RE
from md4paper.workdir import WorkDir

# 수식 하나에 4096은 넉넉하지만 줄이면 안 된다 — 추론 모델은 추론 토큰까지 이 한도에서
# 쓰므로, 1024로 조였더니 26개 중 1개가 답을 다 쓰기 전에 잘려 파싱 실패로 돌아왔다(실측).
_MAX_TOKENS = 4096
# 구조화 출력의 JSON을 거치며 LaTeX 백슬래시가 먹히는 자리 — `\text`가 JSON에서는 TAB+"ext"다.
# 이 네 글자는 LaTeX 수식에 제어문자로 나올 이유가 전혀 없으니, 뒤에 글자가 붙어 있으면
# 100% 삼켜진 명령어다 (\t: text·tau·times·theta·to, \f: frac·forall·phi, \b: beta·bar·begin,
# \r: rangle·rho·right).
_ESCAPE_DAMAGE = {"\t": "t", "\f": "f", "\b": "b", "\r": "r"}
# 반대 방향 사고 — 백슬래시를 **두 번** 이스케이프해 `\\sum`으로 오는 경우.
# LaTeX에서 `\\`는 줄바꿈이므로 `\\sum`은 "줄바꿈 뒤에 sum이라는 글자"가 되고,
# latex2mathml은 그걸 글자로 읽어 **통과시킨다** — 먹힌 백슬래시와 똑같이 조용히 틀린다.
# 다만 `\\`는 aligned의 행 구분자로 정당하게 쓰이고 그 뒤에 글자가 바로 올 수도 있어서
# (`x&=1\\y&=2`), 무턱대고 줄이면 멀쩡한 수식을 깬다. 그래서 두 갈래로만 고친다:
# (1) 홑백슬래시 명령어가 **하나도 없는** 문자열 — 통째로 두 번 이스케이프된 것이다.
# (2) `\\` 뒤에 흔한 명령어 이름이 붙은 자리 — 줄바꿈+글자로 읽힐 리가 없다.
_SINGLE_CMD_RE = re.compile(r"(?<!\\)\\[A-Za-z]")
_DOUBLE_CMD_RE = re.compile(r"\\\\[A-Za-z]")
_COMMON_CMDS = (
    "text|frac|sum|prod|int|left|right|begin|end|sqrt|operatorname|"
    "mathrm|mathbb|mathcal|mathbf|mathit|mathsf|boldsymbol|"
    "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|"
    "mu|nu|xi|rho|varrho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|"
    "Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|"
    "partial|nabla|infty|cdot|cdots|dots|ldots|times|div|pm|mp|"
    "leq|geq|neq|approx|equiv|sim|simeq|propto|subset|subseteq|cup|cap|"
    "in|notin|forall|exists|quad|qquad|langle|rangle|lVert|rVert|lfloor|rfloor|"
    "log|ln|exp|max|min|arg|sup|inf|lim|hat|bar|tilde|vec|overline|underline|"
    "triangleq|xrightarrow|rightarrow|leftarrow|to|mid|colon|space"
)
_DOUBLED_CMD_RE = re.compile(r"\\\\(?=(?:" + _COMMON_CMDS + r")(?![A-Za-z]))")


class FormulaRead(BaseModel):
    """수식 그림 한 장을 읽은 결과."""

    kind: Literal["display", "inline", "code", "text"] = Field(
        description="display=독립된 수식, inline=문장에 섞이는 짧은 수식, "
                    "code=소스코드 한 줄, text=수식이 아닌 평범한 글")
    latex: str = Field(
        description="kind가 display/inline이면 LaTeX 본문 ($ 기호 없이). "
                    "code/text면 보이는 그대로의 평문.")


_SYSTEM = """You transcribe ONE cropped image from an academic paper into LaTeX.

The crop was cut from the PDF at the coordinates where the layout model found a formula, so it is
usually a display equation - but the detector is not perfect, so it is sometimes a line of source
code or an ordinary sentence. Say which it is in `kind`, and transcribe accordingly.

Rules for `latex`:
- Transcribe ONLY what is in the image. Never solve, simplify, explain, or complete anything.
- Emit the math body WITHOUT delimiters: no $, no $$, no \\begin{equation}, no \\[ \\].
- Multi-line systems: use \\begin{aligned}...\\end{aligned} (it nests inside display math).
- Use standard LaTeX that KaTeX/MathML can render. Prefer \\frac, \\sum, \\int, \\mathbb, \\mathcal,
  \\langle, \\rangle, \\arg\\max, \\sim, \\leq. Do NOT use \\tag - the equation number is handled
  separately, so leave it out of `latex` entirely.
- Words inside math must be \\text{...} or \\mathrm{...}, never bare letters: an equation reading
  "Prediction = LLM(Prompt)" is \\text{Prediction} = \\text{LLM}(\\text{Prompt}), NOT
  P r e d i c t i o n = L L M (...). Getting this wrong turns a readable line into spaced-out
  italic soup, which is worse than the original.
- If kind is `code` or `text`, put the visible characters in `latex` as plain text, not LaTeX.
- Your answer is carried in JSON, so every backslash must survive it: write \\text, \\frac,
  \\alpha. A single backslash before t/f/b/r is read as a control character and destroys the
  command; and never write \\\\text or \\\\frac either - a doubled backslash is a line break,
  not a command.

You are also given the PDF's own text layer for the same region. It has the right characters but
lost the structure (subscripts, fractions, limits). Use it to settle ambiguous glyphs
(l vs \\ell, * vs \\ast, v vs \\nu); use the IMAGE to decide the structure. Where they disagree
about layout, the image wins."""


def _repair_escapes(latex: str) -> str:
    r"""JSON을 거치며 제어문자로 먹힌 LaTeX 명령어를 되살린다 (TAB+"ext{" → `\text{`).

    이걸 안 하면 조용히 틀린다: `\text{Prompt}`가 `ext{Prompt}`가 되어도 latex2mathml은
    글자로 읽어 **렌더에 성공하므로**, 검증을 통과한 채 본문에 들어간다(실측).
    """
    out: list[str] = []
    for i, ch in enumerate(latex):
        letter = _ESCAPE_DAMAGE.get(ch)
        nxt = latex[i + 1] if i + 1 < len(latex) else ""
        out.append("\\" + letter if letter and nxt.isascii() and nxt.isalpha() else ch)
    return "".join(out)


def _undouble_escapes(latex: str) -> str:
    r"""두 번 이스케이프된 백슬래시를 되돌린다 (`\\sum` → `\sum`).

    aligned의 행 구분자(`\\`)를 건드리지 않도록, 확실한 두 경우에만 손댄다:
    홑백슬래시 명령어가 아예 없는 문자열 전체, 그리고 흔한 명령어 이름 앞의 `\\`.
    """
    if not _SINGLE_CMD_RE.search(latex) and _DOUBLE_CMD_RE.search(latex):
        latex = latex.replace("\\\\", "\\")
    return _DOUBLED_CMD_RE.sub("\\\\", latex)


def _clean(latex: str) -> str:
    """모델이 습관적으로 덧붙이는 껍데기를 벗긴다 (펜스·구분자).

    이스케이프 복구가 **strip보다 먼저**다 — 맨 앞 `\text{`가 TAB으로 시작하면 strip이
    그 TAB을 지워, 되살릴 단서까지 없앤다.
    """
    text = _undouble_escapes(_repair_escapes(latex)).strip()
    fence = re.match(r"^```(?:latex|tex|math)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if text.startswith(opener) and text.endswith(closer) and len(text) > len(opener) + len(closer):
            text = text[len(opener): -len(closer)].strip()
    return re.sub(r"\\tag\s*\{[^}]*\}", "", text).strip()


def renderable(latex: str) -> bool:
    """이 LaTeX가 실제로 MathML로 변환되는지 — 우리 프리뷰가 쓰는 바로 그 변환기로 확인한다.

    깨진 수식을 `$$…$$`로 넣으면 프리뷰가 그 자리에서 예외를 내거나 원문을 날것으로 뱉는다.
    그럴 바에는 크롭 그림을 그대로 두는 편이 낫다 — 그래서 통과한 것만 반영한다.
    """
    if not latex or len(latex) > 4000:
        return False
    if latex.count("{") != latex.count("}"):
        return False
    # 줄바꿈 말고 제어문자가 남았다면 이스케이프 복구가 못 살린 자리다 — latex2mathml은
    # 그런 문자열도 '글자'로 받아 통과시키므로, 여기서 걸러야 조용히 틀리지 않는다.
    if any(ch != "\n" and ord(ch) < 0x20 for ch in latex):
        return False
    if _DOUBLED_CMD_RE.search(latex):
        return False  # 되돌리지 못한 이중 이스케이프 — 이것도 latex2mathml은 통과시킨다
    try:
        from latex2mathml.converter import convert
    except ImportError:
        return True  # 검증기가 없으면 막지 않는다 (UI 없이 CLI만 설치한 경우)
    try:
        return bool(convert(latex))
    except Exception:  # noqa: BLE001 — 변환기가 내는 모든 실패가 곧 "렌더 불가"다
        return False


# 한 번 더 물어볼 때 덧붙이는 말 — 실측 실패는 둘 다 일회성 미끄러짐이었다
# (`\\mathrm`의 백슬래시 누락, 백슬래시를 무한히 반복하다 토큰 한도 초과).
# 같은 그림을 다시 보여주면 대개 바로 맞으므로, 한 번만 더 묻는다.
_RETRY_NUDGE = ("\n\nYour previous attempt at this image was rejected: {why}. "
                "Read the image again and answer with valid, self-contained LaTeX.")


def _attempt(provider, record: dict, png: bytes, extra: str) -> dict:  # noqa: ANN001
    """한 번의 시도 — 성공하면 latex가 찬 레코드, 실패하면 error가 찬 레코드."""
    hint = record.get("orig") or "(the text layer gave nothing for this region)"
    user = f"PDF text layer for this region:\n{hint}\n\nTranscribe the image.{extra}"
    try:
        read = provider.parse_image(_SYSTEM, user, [png], FormulaRead, max_tokens=_MAX_TOKENS)
    except Exception as e:  # noqa: BLE001 — 한 수식의 실패가 나머지를 막지 않게
        return {**record, "latex": "", "kind": "", "error": str(e)[:200]}
    latex = _clean(read.latex)
    if read.kind in ("display", "inline") and not renderable(latex):
        # 반려한 LaTeX를 남긴다 — 없애 버리면 왜 그림으로 남았는지 사후에 알 길이 없다
        return {**record, "latex": "", "kind": read.kind, "rejected": latex[:500],
                "error": "렌더 불가한 LaTeX"}
    return {**record, "latex": latex, "kind": read.kind, "rejected": "", "error": ""}


def _ask(provider, record: dict, images_dir) -> dict:  # noqa: ANN001
    """수식 한 장 → 레코드에 latex/kind를 채워 돌려준다. 한 번 실패하면 한 번만 다시 묻는다."""
    png = (images_dir / record["file"]).read_bytes()
    first = _attempt(provider, record, png, "")
    if not first.get("error"):
        return first
    return _attempt(provider, record, png, _RETRY_NUDGE.format(why=first["error"][:120]))


def _block(record: dict) -> str:
    """레코드 → 마크다운 한 블록. 수식이 아니면 수식으로 만들지 않는다."""
    latex, kind = record.get("latex", ""), record.get("kind", "")
    if not latex:
        return f"![{record['id']}]({record['file']})"  # 실패 — 그림 그대로 (아무것도 잃지 않는다)
    if kind == "code":
        return f"```\n{latex}\n```"
    if kind == "text":
        return latex
    if kind == "inline":
        return f"${latex}$"
    number = record.get("number")
    return f"$${latex} \\tag{{{number}}}$$" if number else f"$${latex}$$"


def apply(md: str, records: list[dict]) -> tuple[str, int]:
    """raw.md의 수식 그림 참조를 결과 블록으로 교체. (새 마크다운, 교체한 개수)."""
    by_id = {r["id"]: r for r in records}
    out: list[str] = []
    replaced = 0
    for line in md.split("\n"):
        m = IMAGE_RE.match(line.strip())
        rec = by_id.get(m.group(1)) if m else None
        if rec is None:
            out.append(line)
            continue
        out.append(_block(rec))
        if rec.get("latex"):
            replaced += 1
    return "\n".join(out), replaced


def load(wd: WorkDir) -> list[dict]:
    """formulas.json — 없거나 깨졌으면 빈 목록 (수식 없는 논문이 대부분이다)."""
    try:
        return json.loads(wd.formulas_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def run(wd: WorkDir, provider, *, force: bool = False, workers: int | None = None,
        on_progress=None) -> dict:  # noqa: ANN001
    """수식 그림 → LaTeX → raw.md 반영. 요약 dict 반환.

    이미 `latex`가 있는 레코드는 건너뛴다(캐시) — 같은 그림을 다시 물어볼 이유가 없다.
    """
    from md4paper import config

    records = load(wd)
    if not records:
        return {"total": 0, "converted": 0, "failed": 0, "skipped": True}

    todo = [i for i, r in enumerate(records) if force or not r.get("latex")]
    done = [0]

    def work(i: int) -> None:
        records[i] = _ask(provider, records[i], wd.extract_images)
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], len(todo))

    if todo:
        max_workers = workers if workers is not None else config.resolve_translate_workers()
        if max_workers <= 1 or len(todo) <= 1:
            for i in todo:
                work(i)
        else:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(todo))) as ex:
                for fut in as_completed([ex.submit(work, i) for i in todo]):
                    fut.result()

    wd.formulas_json.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    md = wd.raw_md.read_text(encoding="utf-8")
    new, replaced = apply(md, records)
    if new != md:
        wd.raw_md.write_text(new, encoding="utf-8")
    failures = {r["id"]: r["error"] for r in records if r.get("error")}
    return {"total": len(records), "converted": replaced, "failed": len(failures),
            "asked": len(todo), "failures": failures}
