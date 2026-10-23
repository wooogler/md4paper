"""UI 문구를 `t()`로 감싸는 도우미 (§md4paper/i18n.py) — 파일 하나씩 옮길 때 기계적인 부분만.

    uv run python tools/i18n_wrap.py src/md4paper/ui/home.py            # 감싸고 남은 것 목록
    uv run python tools/i18n_wrap.py src/md4paper/ui/home.py --dry      # 바꿀 곳만 보기
    uv run python tools/i18n_wrap.py src/md4paper/ui/app.py --lines 2700-3100 --funcs cap,section_title

감싸는 것: 한글이 든 문자열(f-string 포함)이 화면에 나가는 자리에 있을 때 —
- `ui.<label|button|notify|…>(…)`의 위치 인자, `.tooltip(…)`·`.set_text(…)`
- 아무 `ui.*` 호출의 `label=`·`placeholder=`·`title=`·`text=`·`message=`·`hint=`·`suffix=`·`prefix=` 인자
- `--funcs`로 준 이 파일의 화면용 도우미(예: cap)의 인자
- 그 자리의 `"a" + x`, `a if c else "b"` 안쪽 문자열, `ui.select/toggle/radio`에 바로 넘긴 dict의 값
f-string은 `t("…{n}…", n=식)`으로 바꾼다(이름은 식에서 따온다). 독스트링·주석·로그·LLM 프롬프트는 건드리지 않는다.

감싸지 않은 한글 문자열은 줄 번호와 함께 보여 준다 — JS 문자열(md4T로), 모듈 상수(쓰는 자리에서 t), 예외 메시지 등은
사람이 판단한다. 이름 `t`를 지역 변수로 쓰는 함수는 import한 t를 가리므로 따로 경고한다.
"""

from __future__ import annotations

import argparse
import ast
import keyword
import re
import sys
from pathlib import Path

HANGUL = re.compile(r"[가-힣]")
UI_FUNCS = {"label", "button", "notify", "menu_item", "tab", "switch", "checkbox", "expansion", "markdown",
            "chip", "badge", "link", "item_label", "tooltip", "radio", "toggle", "select", "input", "textarea",
            "number", "upload", "step", "timeline_entry", "splitter"}
METHODS = {"tooltip", "set_text"}
KWARGS = {"label", "placeholder", "title", "text", "message", "hint", "suffix", "prefix", "caption", "header",
          "subtitle", "description"}
OPTION_FUNCS = {"select", "toggle", "radio"}


def has_hangul(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(HANGUL.search(node.value))
    if isinstance(node, ast.JoinedStr):
        return any(has_hangul(v) for v in node.values)
    return False


def is_str(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)) or isinstance(node, ast.JoinedStr)


class Finder(ast.NodeVisitor):
    def __init__(self, funcs: set[str]) -> None:
        self.targets: list[ast.AST] = []   # 감쌀 문자열 노드
        self.funcs = funcs
        self.in_t = 0

    def _ui_call(self, call: ast.Call) -> str | None:
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "ui":
            return "ui." + f.attr
        if isinstance(f, ast.Attribute) and f.attr in METHODS:
            return "." + f.attr
        if isinstance(f, ast.Name) and f.id in self.funcs:
            return f.id
        return None

    def _take(self, node: ast.AST) -> None:
        """화면에 나가는 자리의 식에서 감쌀 문자열을 고른다 — 문자열 자체, +, 조건식, 괄호."""
        if is_str(node):
            if has_hangul(node):
                self.targets.append(node)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            self._take(node.left)
            self._take(node.right)
        elif isinstance(node, ast.IfExp):
            self._take(node.body)
            self._take(node.orelse)

    def visit_Call(self, call: ast.Call) -> None:
        if isinstance(call.func, ast.Name) and call.func.id in ("t", "N_", "md4T"):
            return  # 이미 감쌌다
        kind = self._ui_call(call)
        if kind is not None:
            positional = kind.startswith(".") or kind in self.funcs or kind[3:] in UI_FUNCS
            if positional:
                for a in call.args:
                    self._take(a)
            for kw in call.keywords:
                if kw.arg in KWARGS:
                    self._take(kw.value)
            if kind.startswith("ui.") and kind[3:] in OPTION_FUNCS and call.args and isinstance(call.args[0], ast.Dict):
                for v in call.args[0].values:
                    self._take(v)
        self.generic_visit(call)


def _name_for(expr: ast.AST, src: str, used: dict[str, str]) -> str:
    seg = ast.get_source_segment(src, expr) or ""
    for name, s in used.items():
        if s == seg:
            return name
    if isinstance(expr, ast.Name):
        base = expr.id
    elif isinstance(expr, ast.Attribute):
        base = expr.attr
    elif isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str):
        base = expr.slice.value
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "len":
        base = "n"
    else:
        base = "v"
    base = re.sub(r"\W", "_", base).strip("_") or "v"
    if keyword.iskeyword(base) or base[0].isdigit() or base == "msg":
        base = "v_" + base
    name, i = base, 2
    while name in used:
        name, i = f"{base}{i}", i + 1
    used[name] = seg
    return name


def render(node: ast.AST, src: str) -> str | None:
    """감싼 식의 소스 — 일반 문자열은 원래 글 그대로 t(…)로, f-string은 t("…{n}…", n=식)으로."""
    seg = ast.get_source_segment(src, node)
    if seg is None:
        return None
    if isinstance(node, ast.Constant):
        return f"t({seg})"
    parts: list[str] = []
    used: dict[str, str] = {}
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
            continue
        if not isinstance(v, ast.FormattedValue):
            return None
        spec = ""
        if v.format_spec is not None:
            if not all(isinstance(x, ast.Constant) for x in v.format_spec.values):
                return None  # 중첩 자리표시 — 사람이
            spec = ":" + "".join(str(x.value) for x in v.format_spec.values)
        conv = "" if v.conversion == -1 else "!" + chr(v.conversion)
        parts.append("{" + _name_for(v.value, src, used) + conv + spec + "}")
    text = "".join(parts)
    lit = repr(text) if '"' in text and "'" not in text else '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lit = lit.replace("\n", "\\n")
    args = ", ".join(f"{k}={s}" for k, s in used.items())
    return f"t({lit}, {args})" if args else f"t({lit})"


def leftovers(tree: ast.AST, done: set[int], lo: int, hi: int) -> list[tuple[int, str]]:
    doc: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and is_str(n.body[0].value):
            doc.add(id(n.body[0].value))
    wrapped: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("t", "N_", "md4T"):
            for x in ast.walk(n):
                wrapped.add(id(x))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for x in n.values:
                wrapped.add(id(x))  # f-string 조각은 f-string 단위로 센다
    for n in ast.walk(tree):
        if is_str(n) and has_hangul(n) and id(n) not in doc and id(n) not in done and id(n) not in wrapped \
                and lo <= n.lineno <= hi:
            text = n.value if isinstance(n, ast.Constant) else ast.unparse(n)
            out.append((n.lineno, text.replace("\n", "⏎")[:110]))
    return sorted(out)


def shadowing(tree: ast.AST) -> list[tuple[int, str]]:
    """이름 t를 지역에 묶는 함수(인자·대입·for·with) — import한 t가 그 함수 안에서 가려진다."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = fn.args
        names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
        body = fn.body if isinstance(fn.body, list) else [fn.body]
        binds = any(n == "t" for n in names)
        for st in body:
            for x in ast.walk(st):
                if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                if isinstance(x, ast.Name) and x.id == "t" and isinstance(x.ctx, ast.Store):
                    binds = True
        if binds:
            out.append((fn.lineno, getattr(fn, "name", "lambda")))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--lines", default=None, help="이 줄 범위만 (예: 2700-3100)")
    ap.add_argument("--funcs", default="", help="화면용 도우미 함수 이름들 (쉼표)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    src = a.file.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lo, hi = (map(int, a.lines.split("-")) if a.lines else (1, 10**9))
    finder = Finder({f for f in a.funcs.split(",") if f})
    finder.visit(tree)
    targets = [n for n in finder.targets if lo <= n.lineno <= hi]
    # 같은 노드가 두 번 잡히지 않게, 겹치면 바깥 것만 (f-string 안의 상수 조각 등)
    lines = src.splitlines(keepends=True)
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))

    def pos(lineno: int, col: int) -> int:
        return offs[lineno - 1] + len(lines[lineno - 1].encode("utf-8")[:col].decode("utf-8"))

    edits = []
    skipped = []
    for n in targets:
        new = render(n, src)
        if new is None:
            skipped.append(n)
            continue
        edits.append((pos(n.lineno, n.col_offset), pos(n.end_lineno, n.end_col_offset), new, n))
    edits.sort(key=lambda e: e[0])
    clean, last = [], -1
    for e in edits:
        if e[0] >= last:
            clean.append(e)
            last = e[1]
    out = src
    for s, e, new, _n in reversed(clean):
        out = out[:s] + new + out[e:]
    if a.dry:
        for s, e, new, n in clean:
            print(f"{n.lineno}: {new[:120]}")
    else:
        if clean and not re.search(r"^from md4paper\.i18n import .*\bt\b", out, flags=re.M):
            m = re.search(r"^from md4paper(\.\S+)? import .*$", out, flags=re.M)
            if m:
                out = out[:m.start()] + "from md4paper.i18n import t\n" + out[m.start():]
            else:  # md4paper import가 없으면 맨 위 import 묶음 뒤에 (from __future__보다 앞이면 안 된다)
                body = ast.parse(out).body
                last = max((n.end_lineno for n in body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
                lines_out = out.splitlines(keepends=True)
                out = "".join(lines_out[:last]) + "\nfrom md4paper.i18n import t\n" + "".join(lines_out[last:])
        compile(out, str(a.file), "exec")
        a.file.write_text(out, encoding="utf-8")
    print(f"감쌈 {len(clean)}개" + (f" · 사람이 볼 f-string {len(skipped)}개" if skipped else ""))
    for n in skipped:
        print(f"  ! {n.lineno}: {ast.get_source_segment(src, n)[:100]}")
    done = {id(e[3]) for e in clean}
    left = leftovers(ast.parse(out) if not a.dry else tree, set() if not a.dry else done, lo, hi)
    print(f"남은 한글 문자열 {len(left)}개 (JS·상수·예외·LLM 글 등 — 사람이 판단)")
    for ln, text in left:
        print(f"  {ln}: {text}")
    sh = shadowing(ast.parse(out))
    if sh and (clean or "import t" in out):
        print("⚠ t를 지역 이름으로 쓰는 함수 — import한 t가 가려진다(이름을 바꿀 것):")
        for ln, name in sh:
            print(f"  {ln}: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
