"""용어 예문 — 용어집의 용어가 논문 본문에서 쓰인 문장(원문)과 그 번역 문장을 짝지어 보여 준다.

용어를 어떻게 옮길지 정할 때 가장 믿을 만한 근거는 그 용어가 실제로 놓인 문장이다("mental model"이
'정신 모형'인지 '멘탈 모델'인지는 문맥이 정한다). 뷰어 용어집 시트·컬렉션 용어집 대화상자(충돌 변이마다
그 논문이 실제로 어떻게 옮겼는지)·에이전트 tool이 같은 함수를 쓴다.

찾는 법:
- 뷰어와 같은 정렬 행(`agent.retrieval.paper` — 원문·번역 파일 mtime으로 캐시)을 훑어, 원문 행의 평문에서
  용어를 **단어 시작 경계**로 찾는다(대소문자·공백 무시, 복수형은 잡힌다 — 캐시 키의 `apply.relevant_lines`와
  같은 규칙). 참고문헌 섹션은 뺀다(논문 제목 목록이라 용례가 아니다).
- 그 행의 원문·번역을 문장으로 쪼개 원문 문장 하나를 고르고, 번역 쪽은 번역어(keep이면 원어)가 든 문장을
  먼저, 없으면 같은 비율 위치의 문장을 고른다(`ko_exact`가 그 구분 — 번역어가 안 보이면 다른 말로 옮긴 것).
- 문장이 길면 용어 주변만 잘라 보인다. 표시는 `to_html`이 이스케이프하고 용어·번역어를 `<mark>`로 감싼다.

LLM을 부르지 않는다 — 파일 읽기와 정규식뿐이다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from md4paper.i18n import N_, t
from md4paper.workdir import WorkDir

CLIP = 260            # 문장이 이보다 길면 용어 주변만 보인다
_REF_HEADS = ("reference", "bibliograph", "참고 문헌", "참고문헌")

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_HEAD_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MARK_RE = re.compile(r"(\*\*|__|\*|`|~~)")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
# 문장 끝: 마침표·물음표·느낌표(뒤에 닫는 따옴표·괄호 허용) 다음 공백, 또는 줄바꿈(목록·표 줄)
# 문장 끝 — 영어·한국어는 마침표 뒤 공백, 일본어·중국어(。！？)는 공백 없이 이어 쓰므로 바로 뒤에서 끊는다
_SENT_RE = re.compile(r"(?<=[.!?。])[\"'”’)\]]*\s+|(?<=[。！？])[”’」』）]*|\n+")


@dataclass
class Example:
    row: int                  # 뷰어 정렬 행 번호 (data-row)
    heading: str              # 그 행이 속한 섹션 제목
    en: str                   # 원문 문장 (잘렸으면 앞뒤 …)
    ko: str                   # 짝지은 번역 문장 (번역이 없으면 "")
    ko_exact: bool = False    # 번역 문장에서 번역어(또는 keep 원어)를 찾았는가
    en_spans: list[tuple[int, int]] = field(default_factory=list)   # en 안의 용어 위치
    ko_spans: list[tuple[int, int]] = field(default_factory=list)   # ko 안의 번역어 위치


def plain(md: str) -> str:
    """마크다운 셀 → 평문 (이미지·링크 주소·HTML·강조 기호·헤더 표식·표 구분선을 걷는다)."""
    tv = _IMG_RE.sub("", md or "")
    tv = _LINK_RE.sub(r"\1", tv)
    tv = _TAG_RE.sub("", tv)
    tv = _TABLE_RE.sub("", tv)
    tv = _HEAD_RE.sub("", tv)
    tv = _LIST_RE.sub("", tv)
    tv = _MARK_RE.sub("", tv)
    tv = tv.replace("|", " ")
    return re.sub(r"[ \t]+", " ", tv)


def sentences(text: str) -> list[str]:
    """평문 → 문장들 (빈 것 없이, 앞뒤 공백 없이)."""
    return [s.strip() for s in _SENT_RE.split(text or "") if s and s.strip()]


def term_pattern(term: str) -> re.Pattern | None:
    """용어 → 찾기 정규식. 단어 시작 경계(용어가 글자로 시작할 때만), 공백은 아무 공백 하나 이상, 대소문자 무시."""
    words = (term or "").split()
    if not words:
        return None
    body = r"\s+".join(re.escape(w) for w in words)
    lead = r"(?<!\w)" if re.match(r"\w", words[0]) else ""
    tail = r"\w*" if re.search(r"\w$", words[-1]) else ""   # 복수형·파생형까지 한 덩어리로 표시
    return re.compile(lead + body + tail, re.IGNORECASE)


def korean_pattern(korean: str) -> re.Pattern | None:
    """번역어 → 찾기 정규식. 띄어쓰기 차이('정신모형'·'정신 모형')는 무시한다 — 글자 사이 공백을 선택으로."""
    chars = [c for c in (korean or "") if not c.isspace()]
    if not chars:
        return None
    return re.compile(r"\s*".join(re.escape(c) for c in chars), re.IGNORECASE)


def _spans(pat: re.Pattern | None, text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in pat.finditer(text)] if pat else []


def _clip(text: str, spans: list[tuple[int, int]], limit: int = CLIP) -> tuple[str, list[tuple[int, int]]]:
    """긴 문장은 첫 일치 주변만 — 잘린 쪽에 …를 붙이고 위치를 옮긴다."""
    if len(text) <= limit:
        return text, spans
    center = spans[0][0] if spans else 0
    start = max(0, min(center - limit // 2, len(text) - limit))
    end = min(len(text), start + limit)
    # 낱말 중간에서 자르지 않게 가까운 공백으로
    if start > 0:
        sp = text.find(" ", start)
        start = sp + 1 if 0 <= sp < start + 20 else start
    if end < len(text):
        sp = text.rfind(" ", start, end)
        end = sp if sp > end - 20 else end
    lead = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    body = text[start:end].rstrip()
    moved = [(a - start + len(lead), b - start + len(lead)) for a, b in spans if a >= start and b <= start + len(body)]
    return lead + body + tail, moved


def _pick_ko(ko_sents: list[str], i: int, n_en: int, kpat: re.Pattern | None) -> tuple[str, bool]:
    """원문 i번째 문장(전체 n_en개)에 짝지을 번역 문장 — 번역어가 든 문장 중 비율 위치에 가장 가까운 것."""
    if not ko_sents:
        return "", False
    guess = round(i * (len(ko_sents) - 1) / max(1, n_en - 1)) if n_en > 1 else 0
    if len(ko_sents) == n_en:
        guess = i
    if kpat is not None:
        hits = [j for j, s in enumerate(ko_sents) if kpat.search(s)]
        if hits:
            j = min(hits, key=lambda j: abs(j - guess))
            return ko_sents[j], True
    return ko_sents[min(guess, len(ko_sents) - 1)], False


def find(wd: WorkDir, term: str, korean: str = "", policy: str = "translate", *,
         limit: int = 3) -> tuple[list[Example], int]:
    """이 논문에서 용어가 쓰인 문장 예문 (원문·번역 짝) 최대 limit개와 용어가 나오는 행 수.

    번역어를 찾은 예문(ko_exact)을 먼저, 그다음 문서 순. policy가 keep이면 번역 쪽에서도 원어를 찾는다.
    번역(ko.md)이 없으면 ko는 빈 문자열.
    """
    from md4paper.ui.agent import retrieval

    pat = term_pattern(term)
    if pat is None or not wd.en_md.exists():
        return [], 0
    kpat = term_pattern(term) if policy == "keep" else korean_pattern(korean)
    found: list[Example] = []
    rows = 0
    for c in retrieval.paper(wd).chunks:
        if any(h in (c.heading or "").casefold() for h in _REF_HEADS):
            continue
        en_text = plain(c.en)
        if not pat.search(en_text):
            continue
        rows += 1
        en_sents = sentences(en_text)
        ko_sents = sentences(plain(c.ko or ""))
        for i, s in enumerate(en_sents):
            sp = _spans(pat, s)
            if not sp:
                continue
            ko, exact = _pick_ko(ko_sents, i, len(en_sents), kpat)
            en_c, en_sp = _clip(s, sp)
            ko_c, ko_sp = _clip(ko, _spans(kpat, ko) if exact else [])
            found.append(Example(row=c.row, heading=c.heading, en=en_c, ko=ko_c, ko_exact=exact,
                                 en_spans=en_sp, ko_spans=ko_sp))
            break   # 한 행에서는 한 문장만 — 여러 행에 걸친 쓰임을 보이는 편이 낫다
    found.sort(key=lambda e: (not e.ko_exact, e.row))
    return found[:limit], rows


def across(papers: list[tuple[Path, str, str]], term: str, *, per_paper: int = 1,
           max_papers: int = 4) -> list[dict]:
    """여러 논문에서 — [(작업 폴더, 그 논문의 번역어, policy)] → [{root, title, korean, examples, rows}].

    컬렉션 충돌 변이마다 "이 논문은 이 문장에서 이렇게 옮겼다"를 보이는 용도. 용어가 안 나오는 논문은 뺀다.
    """
    from md4paper.workdir import paper_title

    out: list[dict] = []
    for root, korean, policy in papers:
        if len(out) >= max_papers:
            break
        wd = WorkDir(Path(root))
        try:
            ex, rows = find(wd, term, korean, policy, limit=per_paper)
        except (OSError, ValueError):
            continue
        if ex:
            out.append({"root": wd.root, "title": paper_title(wd.root), "korean": korean,
                        "examples": ex, "rows": rows, "unaligned": unaligned(wd)})
    return out


def _marked(text: str, spans: list[tuple[int, int]]) -> str:
    out, at = [], 0
    for a, b in sorted(spans):
        if a < at:
            continue
        out.append(html.escape(text[at:a]))
        out.append(f"<mark>{html.escape(text[a:b])}</mark>")
        at = b
    out.append(html.escape(text[at:]))
    return "".join(out)


def to_html(ex: Example) -> str:
    """예문 하나 → 이스케이프된 HTML (원문 줄 + 번역 줄, 용어·번역어는 <mark>)."""
    en = f'<div class="md4-tex-en">{_marked(ex.en, ex.en_spans)}</div>'
    if not ex.ko:
        return en
    cls = "md4-tex-ko" + ("" if ex.ko_exact else " miss")
    return en + f'<div class="{cls}">{_marked(ex.ko, ex.ko_spans)}</div>'


CSS = """
.md4-tex { border-left: 3px solid #d6e6f8; padding: 2px 0 2px 8px; margin: 2px 0 4px; }
.md4-tex-en { font-size: 12.5px; color: #37352f; line-height: 1.45; }
.md4-tex-ko { font-size: 12.5px; color: #55534e; line-height: 1.45; margin-top: 1px; }
.md4-tex-ko.miss { color: #9a968e; }
.md4-tex mark { background: #fff3b0; color: inherit; padding: 0 1px; border-radius: 2px; }
.md4-tex-ko mark { background: #d8f0dd; }
.md4-tex-meta { font-size: 11px; color: #9a968e; }
"""


def text_lines(ex: Example) -> str:
    """에이전트·CLI용 평문 두 줄 — 용어·번역어를 «»로 감싼다."""
    def mark(tv: str, spans: list[tuple[int, int]]) -> str:
        out, at = [], 0
        for a, b in sorted(spans):
            if a < at:
                continue
            out += [tv[at:a], "«", tv[a:b], "»"]
            at = b
        return "".join(out + [tv[at:]])

    line = f"EN: {mark(ex.en, ex.en_spans)}"
    if ex.ko:
        line += f"\nKO: {mark(ex.ko, ex.ko_spans)}" + ("" if ex.ko_exact else " (번역어가 안 보임 — 다른 말로 옮김)")
    return line


# --- 화면 조각 (뷰어 용어집 시트·컬렉션 용어집 대화상자가 같이 쓴다) ------------------


def unaligned(wd: WorkDir) -> bool:
    """번역(ko.md)은 있는데 원문과 행이 맞지 않아(헤더 수가 다름) 정렬 행에 번역이 하나도 없는가.

    그러면 예문에 번역 문장을 짝지을 수 없다 — 화면이 '번역이 없다'와 구별해 알린다."""
    from md4paper.ui.agent import retrieval

    if not (wd.ko_md.exists() and wd.en_md.exists()):
        return False
    return not any(c.ko for c in retrieval.paper(wd).chunks)


UNALIGNED_NOTE = N_("번역과 원문의 섹션 수가 맞지 않아(재변환·제목 수준 변경 뒤) 번역 문장을 짝짓지 못했습니다 — "
                  "다시 번역하면 맞춰집니다.")  # 화면엔 t(), 에이전트(LLM)엔 그대로


def render(examples: list[Example], rows: int, *, goto=None, max_note: bool = True,  # noqa: ANN001
           unaligned_note: bool = False) -> None:
    """한 논문의 예문 목록을 지금 슬롯에 그린다. goto(row)를 주면 행 번호가 그 자리로 가는 단추가 된다."""
    from nicegui import ui

    if examples and unaligned_note:
        ui.label(t(UNALIGNED_NOTE)).classes("md4-tex-meta")
    if not examples:
        ui.label(t("본문에서 이 용어를 찾지 못했습니다 (복수형·하이픈 표기 차이일 수 있습니다).")) \
            .classes("md4-tex-meta")
        return
    for e in examples:
        with ui.element("div").classes("md4-tex w-full"):
            ui.html(to_html(e))
            with ui.row().classes("items-center gap-1 no-wrap"):
                where = f"r{e.row}" + (f" · {e.heading[:40]}" if e.heading else "")
                if goto is not None:
                    ui.button(where, on_click=lambda _, r=e.row: goto(r)) \
                        .props("flat dense no-caps size=xs color=grey-7").classes("md4-tex-meta") \
                        .tooltip(t("본문의 이 자리로 이동"))
                else:
                    ui.label(where).classes("md4-tex-meta")
                if e.ko and not e.ko_exact:
                    ui.label(t("· 번역어가 안 보임 — 다른 말로 옮긴 문장")).classes("md4-tex-meta")
    if max_note and rows > len(examples):
        ui.label(t("이 논문에서 {rows}곳에 나옵니다 (여기엔 {n}곳).", rows=rows, n=len(examples))).classes("md4-tex-meta")


def render_across(groups: list[dict]) -> None:
    """여러 논문의 예문 — 논문 제목 머리 + 그 논문의 예문."""
    from nicegui import ui

    if not groups:
        ui.label(t("이 논문들의 본문에서 이 용어를 찾지 못했습니다.")).classes("md4-tex-meta")
        return
    for g in groups:
        tail = t(" · {n}곳", n=g["rows"]) if g.get("rows") else ""
        ui.label(f"{g['title'][:70]}{tail}").classes("md4-tex-meta q-mt-xs").tooltip(g["title"])
        render(g["examples"], g["rows"], max_note=False, unaligned_note=bool(g.get("unaligned")))
