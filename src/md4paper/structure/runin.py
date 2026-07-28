"""본문 줄 안에 있는 run-in 헤더 감지.

논문은 소제목을 별도 줄이 아니라 문단 첫머리에 붙여 쓴다:
  "4.1.3 A Living Test Set. Each discovered case is…"  /  "*Length.* Prompt instructions were…"
이런 것도 섹션 트리에 나타나야 사용자가 레벨을 조정하거나 본문으로 되돌릴 수 있다.
"""

from __future__ import annotations

import re

# Docling이 run-in 헤더를 리스트 항목으로 뭉개는 일이 잦다("- 4.2.1 제목. 본문…") → 마커를 걷고 본다
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# 번호형: "4.1.3 A Living Test Set. 본문…"
# 앞의 점 번호(4.1.3)가 이미 강한 판별자라 제목 길이 제한은 넉넉히 둔다
# (예전 80자/12단어 제한이 "4.1.1 From Prompt to Data: Using the Specification…"(13단어)을 놓쳤다).
_RUNIN_NUM_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+([A-Z][^.]{2,140}?)\.\s+(\S.*)$")
_NUM_TITLE_MAX_WORDS = 20
# 강조형(이탤릭·볼드, 번호 유무 무관): "*Length.* 본문…" / "**Length.** 본문…" / "*4.1.3 Title.* 본문…"
# 여는·닫는 마커는 같은 종류·개수(1~2개)여야 한다(백레퍼런스 \1).
_RUNIN_EMPH_RE = re.compile(r"^([*_]{1,2})((?:\d[\d.]*\s+)?[A-Z][^*_]{1,60}?)\.\1\s+([A-Z]\S.*)$")
# 콜론형(번호도 강조도 없음): "Prompt Instruction Editor: 본문…"
# 추출기가 굵게 표시를 잃어버리면 일반 문장과 형태가 같아진다 → 콜론 앞이 "짧은 타이틀케이스 구"이고
# 뒤가 대문자로 시작하는 충분히 긴 문장일 때만 소제목으로 본다.
_RUNIN_COLON_RE = re.compile(r"^([A-Z][A-Za-z0-9/&'’\-]*(?:\s+[A-Za-z0-9/&'’\-]+){0,6}):\s+([A-Z].*)$")
# 마침표 종결 평문형(추출기가 굵게를 아예 떼어낸 경우): "Categorization. 본문…" /
# "Step 1: Discovering Failures. 본문…" / "Simulated Personas (Oracles). 본문…".
# 강조·번호·콜론 신호가 없어 일반 문장과 형태가 같으므로 아래 _is_runin_title로 엄격히 거른다.
_RUNIN_PERIOD_RE = re.compile(r"^([A-Z][^.]{1,80}?)\.\s+([A-Z]\S.{30,})$")
_TITLE_STOPWORDS = frozenset(
    "a an the of in on for and or to with from by as at is are via".split()
)
# 표·그림·수식 참조/캡션(캡션은 따로 처리) — 마침표형에서 제외 ('Table 1.' 'Figure 2.' 오탐 방지)
_CAP_REF_WORDS = frozenset(
    "table figure fig tab eq eqn equation vol algorithm alg theorem thm lemma corollary appendix".split()
)
_COLON_TITLE_MAX_WORDS = 7
_COLON_BODY_MIN = 30


def _is_title_phrase(phrase: str) -> bool:
    """소제목처럼 보이는 짧은 타이틀케이스 구인지 (불용어는 소문자 허용)."""
    words = phrase.split()
    if not 1 <= len(words) <= _COLON_TITLE_MAX_WORDS:
        return False
    # 소문자가 전혀 없는 단일 토큰은 약칭·열거 라벨(RQ4, TAM, CSI, PO)이라 소제목이 아니다.
    # (예: 리스트 항목 '- RQ4: How does…'를 제목/본문으로 잘못 쪼개는 것을 막는다.)
    if len(words) == 1 and not any(c.islower() for c in words[0]):
        return False
    return all(w[:1].isupper() or w.lower() in _TITLE_STOPWORDS for w in words)


def _is_runin_title(phrase: str) -> bool:
    """마침표형 run-in 제목 후보인지 — 모든 단어가 대문자 시작/숫자/불용어라야 (일반 문장 배제)."""
    words = phrase.split()
    if not 1 <= len(words) <= 8:
        return False
    first = re.sub(r"[^A-Za-z]", "", words[0]).lower()
    if first in _CAP_REF_WORDS:  # 'Table 1.' 'Figure 2.' 같은 캡션·참조는 제외
        return False
    for w in words:
        core = w.strip("()[]{}.,:;'’\"")
        if not core:
            continue  # 순수 구두점 토큰
        if core[0].isupper() or core.isdigit() or core.lower() in _TITLE_STOPWORDS:
            continue
        return False  # 소문자 내용 단어가 있으면 일반 문장 → 제목 아님
    return True


def detect(line: str) -> tuple[str | None, str, str, bool] | None:
    """run-in 헤더 감지. 반환: (번호|None, 제목, 나머지 본문, 이탤릭이었나) 또는 None."""
    unlisted = _LIST_PREFIX_RE.sub("", line, count=1)  # 리스트로 뭉개진 run-in도 잡는다
    was_list = unlisted != line  # 원래 리스트 항목('- …')이었나
    line = unlisted
    m = _RUNIN_NUM_RE.match(line)
    if m and len(m.group(2).split()) <= _NUM_TITLE_MAX_WORDS:
        return m.group(1), m.group(2).strip(), m.group(3), False
    # 콜론형은 리스트 항목('- Label: 문장')엔 적용하지 않는다. 굵게를 잃은 정의형 불릿 목록
    # (예: 'Design Implications'의 · Systematize Iteration: …)은 소제목이 아니라 본문 목록이다.
    if not was_list:
        m = _RUNIN_COLON_RE.match(line)
        if m and _is_title_phrase(m.group(1)) and len(m.group(2)) >= _COLON_BODY_MIN:
            return None, m.group(1).strip(), m.group(2), False
    m = _RUNIN_EMPH_RE.match(line)  # 이탤릭/볼드
    if m:
        phrase, body = m.group(2).strip(), m.group(3)
        nm = re.match(r"^(\d[\d.]*)\s+(.+)$", phrase)
        if nm:  # 번호 붙은 강조
            return nm.group(1), nm.group(2).strip(), body, True
        if len(phrase.split()) <= 8:  # 번호 없는 짧은 강조 제목
            return None, phrase, body, True
    # 마침표 종결 평문형 — 강조·번호·콜론이 없는(굵게 떼어진) run-in. 리스트 항목엔 미적용.
    if not was_list:
        m = _RUNIN_PERIOD_RE.match(line)
        if m and _is_runin_title(m.group(1)):
            return None, m.group(1).strip(), m.group(2), False
    return None
