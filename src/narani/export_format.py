"""내보내기 형식(export target)별 마크다운 변환.

en.md/ko.md는 범용(canonical) 형태로 저장한다 — 표준 마크다운 이미지 `![alt](images/x)`,
인용 `[label](#ref-N)`, 각주 `<sup class="md-fn"><a href="#fn-N">N</a></sup>`. 다운로드 시
대상 뷰어에 맞춰 이 함수로 변환한다:

- universal(범용): 그대로 (GitHub 등 표준 마크다운).
- notion: 내부 앵커 링크(#ref-N·#fn-N)는 Notion 임포트에서 about:blank#... 로 깨지므로 제거한다.
  인용은 라벨만 남기고, 각주 마커는 유니코드 위첨자로, 빈 앵커(<a id=…>)는 삭제.
- obsidian: 이미지를 위키 임베드 `![[images/x]]`로. 인용의 내부 앵커(#ref-N)는 Obsidian이 못 읽으므로,
  Obsidian 노트 내 블록 링크로 바꾼다 — 인용 `[label](#^ref-N)` + 참고문헌 줄 끝에 블록 id `^ref-N`.
  이러면 인용 클릭 시 해당 참고문헌으로 점프하고, 뒤로가기(Cmd+←)로 읽던 위치에 복귀한다. 각주도 동일(#^fn-N).
"""

from __future__ import annotations

import re

TARGETS = ("universal", "notion", "obsidian")

# 본문 인용 링크: [label](#ref-N) → label (내부 앵커 제거, 라벨 유지).
# 라벨 안에 대괄호 쌍이 있어도(예: 'Smith et al. [2020]') 바깥 ']'까지 정확히 잡도록 균형 괄호를 허용한다
# (인용 링커가 '(Smith et al. [2020])' 같은 저자·연도 주석을 라벨에 넣을 수 있음). 앞선 일반 링크는 안 삼킴.
# 주의: 반복 원자는 '한 글자'([^\[\]]) 또는 '균형 괄호쌍'만 — '+'로 묶으면 바깥 '*'와 겹쳐
# catastrophic backtracking(긴 한국어 줄에서 앱이 먹통)이 난다. 반드시 단일 글자로 둘 것.
# group(1)=표시 라벨, group(2)=참고문헌 번호(#ref-N 의 N) — Notion에서 DOI 링크로 바꿀 때 쓴다.
_CITE_LINK_RE = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(#ref-([^)]+)\)")
# 각주 마커: <sup class="md-fn"><a href="#fn-N">M</a></sup> → group(1)=앵커 번호, group(2)=표시 번호
_FN_MARK_RE = re.compile(r'<sup class="md-fn"><a href="#fn-(\d+)">(\d+)</a></sup>')
# 빈 앵커 타깃: <a id="ref-N"></a> / <a id="fn-N"></a> (Notion에선 무의미하고 리터럴로 보일 수 있음)
_ID_ANCHOR_RE = re.compile(r'<a id="[^"]*"></a>\s*')
# 참고문헌/각주 정의 줄의 HTML 앵커 → Obsidian 블록 id. <a id="ref-54"></a>… → … ^ref-54
# (Obsidian은 [txt](#^ref-54) 노트 내 링크로 이 블록에 점프한다. HTML id 앵커 자체는 인식 못 함.)
_ID_ANCHOR_LINE_RE = re.compile(r'^(\s*)<a id="((?:ref|fn)-[^"]+)"></a>\s*(.*)$')
# 표준 이미지 → Obsidian 위키 임베드. images/ 경로를 유지한다(파일명만 쓰면 여러 논문 vault에서 충돌).
# zip에 담을 때 논문 폴더(<이름>-<which>) 접두를 붙여 vault 내 유일 경로로 만든다(controller).
_IMG_RE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
# 코드 펜스(``` / ~~~) — 그 안의 텍스트는 변환하지 않는다(코드 예시 훼손 방지). cite/link.py와 같은 관례.
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# 블록 요소(헤더·이미지) — Notion 등 엄격한 파서는 블록 사이 빈 줄이 없으면 헤더·인용을 인식 못 한다.
_HEADING_S = re.compile(r"#{1,6}\s")


def _is_block_line(s: str) -> bool:
    return bool(_HEADING_S.match(s)) or s.startswith("![")


def _ensure_block_spacing(md: str) -> str:
    """헤더·이미지·인용(캡션) 등 블록 요소 앞뒤로 빈 줄을 보장 (Notion 임포트 호환).

    번역 조립에서 헤더 앞 빈 줄이 사라지거나, 이미지 바로 뒤에 캡션 인용이 붙는 경우를 바로잡는다.
    코드 펜스 안은 건드리지 않고, 인용 여러 줄 연속(> … > …)은 붙여 둔다.
    """
    out: list[str] = []
    in_fence = False
    for ln in md.split("\n"):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            out.append(ln)
            continue
        if not in_fence and out and out[-1].strip() and ln.strip():
            prev_s, cur_s = out[-1].lstrip(), ln.lstrip()
            block = _is_block_line(cur_s) or _is_block_line(prev_s)
            quote_start = cur_s.startswith(">") and not prev_s.startswith(">")
            if block or quote_start:
                out.append("")  # 블록 경계에 빈 줄 삽입
        out.append(ln)
    return "\n".join(out)

_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
# 이미지 alt — Notion은 이미지 alt를 자동으로 캡션으로 표시한다. 우리는 캡션을 별도 블록('> **Figure N:** …')
# 으로 두므로, alt를 비워야 Notion이 캡션을 중복 표시하지 않는다(그리고 캡션 블록은 볼드 표기를 유지).
_IMG_ALT_RE = re.compile(r"^(\s*)!\[[^\]]*\]\(([^)]+)\)(\s*)$")


def _superscript(num: str) -> str:
    return "".join(_SUP.get(c, c) for c in num)


def _notion_empty_image_alt(line: str) -> str:
    """이미지 줄의 alt를 비운다 — '![Figure 2](x)' → '![](x)'. (캡션은 아래 별도 블록이 담당)"""
    return _IMG_ALT_RE.sub(r"\1![](\2)\3", line)


def _apply_outside_fences(md: str, fn) -> str:  # noqa: ANN001 — fn: (str)->str
    """코드 펜스 바깥 줄에만 fn을 적용 (펜스 안 코드 예시는 그대로)."""
    out: list[str] = []
    in_fence = False
    for line in md.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
        else:
            out.append(line if in_fence else fn(line))
    return "\n".join(out)


def _notion_line(line: str, ref_urls: dict[str, str] | None) -> str:
    line = _notion_empty_image_alt(line)  # 이미지 alt 비우기 → Notion이 캡션을 중복 표시하지 않게
    def cite(m: re.Match) -> str:
        # 인용: DOI/arXiv URL이 있으면 그 링크로(바로 논문 접근), 없으면 라벨만(내부 앵커 제거)
        url = (ref_urls or {}).get(m.group(2))
        return f"[{m.group(1)}]({url})" if url else m.group(1)
    line = _CITE_LINK_RE.sub(cite, line)
    line = _FN_MARK_RE.sub(lambda m: _superscript(m.group(2)), line)  # 각주: 위첨자 유니코드(표시 번호)
    return _ID_ANCHOR_RE.sub("", line)                               # 빈 앵커 제거


def _obsidian_line(line: str) -> str:
    """Obsidian 노트 내 링크로 변환 — 인용/각주는 블록 링크로, 이미지는 위키 임베드로."""
    anchor = _ID_ANCHOR_LINE_RE.match(line)
    if anchor:  # 참고문헌/각주 정의 줄: HTML 앵커 제거 + 줄 끝에 블록 id(^ref-N) 부착
        return f"{anchor.group(1)}{anchor.group(3)} ^{anchor.group(2)}".rstrip()
    # 본문 인용: [label](#ref-N) → [label](#^ref-N) (해당 참고문헌 블록으로 점프)
    line = _CITE_LINK_RE.sub(lambda m: f"[{m.group(1)}](#^ref-{m.group(2)})", line)
    # 각주 마커: <sup><a href="#fn-N">M</a></sup> → 위첨자 링크 [ᴹ](#^fn-N)
    line = _FN_MARK_RE.sub(lambda m: f"[{_superscript(m.group(2))}](#^fn-{m.group(1)})", line)
    return _IMG_RE.sub(r"![[\1]]", line)  # 이미지 → 위키 임베드


def to_export_target(md: str, target: str, ref_urls: dict[str, str] | None = None) -> str:
    """범용 마크다운 md를 대상(target) 뷰어 형식으로 변환. 코드 펜스 내부는 손대지 않는다.

    ref_urls({참고문헌 번호: DOI/arXiv URL})가 주어지면 Notion에서 인용을 그 외부 링크로 건다
    (Notion은 in-page 앵커 링크를 지원하지 않으므로, 논문에 바로 접근되게 DOI로).
    """
    md = _ensure_block_spacing(md)  # 블록 사이 빈 줄 보장 (Notion 등에서 헤더·캡션 인식되게)
    if target == "notion":
        return _apply_outside_fences(md, lambda line: _notion_line(line, ref_urls))
    if target == "obsidian":
        return _apply_outside_fences(md, _obsidian_line)  # 인용/각주: 노트 내 블록 링크, 이미지: 위키 임베드
    return md  # universal
