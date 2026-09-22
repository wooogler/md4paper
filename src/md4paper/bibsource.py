"""정확한 BibTeX를 논문 API에서 **가져온다** — 우리가 PDF에서 읽은 값으로 조립하지 않고.

`bibtex.py`는 `paper_meta.json`(LLM이 front matter에서 읽은 제목·저자·연도·venue)으로 항목을
조립한다. 그 방식의 한계가 실측으로 드러났다 — NIRVANA 프로젝트의 `references.bib` 57개 항목:

| 증상 | 개수 |
|---|---|
| venue가 아예 없음(`@misc`로 떨어짐) | 30 |
| venue가 논문 각주의 약칭(`CHI '24`, `L@S '19`) | 7 |
| 그중 `@article`로 잘못 분류(학회인데 `journal =`) | 5 |
| DOI·pages·volume·publisher | **0** |
| 연도가 틀림(출판사 기록과 대조) | 6 |
| 제목·저자에 추출 손상(`HaLLMark Efect`, `Scafolding`) | 2 |

PDF 1페이지에 없는 것은 LLM도 만들어 낼 수 없다. `enrich.py`가 빈 연도·venue를 채우긴 하지만
그건 **paper_meta의 빈칸 메우기**이고, BibTeX 항목이 필요로 하는 pages·publisher·DOI·정확한
booktitle은 애초에 그 스키마에 없다. 그래서 서지 항목은 조립 대신 **출판 기록을 받아온다**.

## 두 단계로 나눈다 — 신원 확인과 서지 원본은 다른 일이다

1. **신원**: 제목으로 "이 논문이 어느 것인가"를 찾는다. Ai2의 Semantic Scholar가 제일 잘 찾는다
   (실측 63편 중 51편을 `search/match` 한 번에, 추출 오타가 있는 제목까지 — `HaLLMark Efect`
   → 유사도 0.996으로 정본을 찾아냈다).
2. **서지 원본**: 찾은 DOI로 **Crossref**에서 출판사가 등록한 항목을 그대로 받는다.

**두 단계를 나눈 이유가 이 모듈의 핵심이다.** Semantic Scholar의 `year`는 그 논문이 세상에
처음 나온 해(주로 arXiv 프리프린트)라 출판 연도와 다르다 — 실측: Crossref 기록이 있는 48편 중
**10편(21%)에서 연도가 어긋났고, 어긋날 때마다 Crossref가 맞았다**(CHI '26 논문을 2025로,
HaLLMark를 2023으로, AI Ghostwriter를 2023으로). 그러니 S2는 **찾는 데만** 쓰고 값은 쓰지 않는다.

DBLP도 후보였으나 뺐다. booktitle에 개최지·날짜가 박혀 있고(`{CHI} '22: ... New Orleans, LA,
USA, 29 April 2022 - 5 May 2022`), 무엇보다 S2가 준 DBLP 키가 **형제 레코드를 가리킨 실측 사고**가
있다(CS50 논문이 SIGCSE 본문 대신 Volume 2 초록 레코드로 연결됐다). 출처를 늘리는 것보다
출판사 기록 하나를 제대로 쓰는 편이 낫다.

## 제목이 비슷하다고 같은 논문이 아니다 — 게이트가 이 모듈에서 제일 조심스러운 부분

`enrich.py`가 쓰는 "유사도 0.90 이상이면 채택"은 **충분하지 않다**. 실측:

- `"...Grammarly to Improve Students' Writing Skills"`(2020, ICDEL)와
  `"...Grammarly to Improve Saudi Students' Writing Skills"`(2024, 다른 저널)의 유사도는
  **0.9595** — 단어 하나 차이로 게이트를 통과한다. 잘못된 연도가 조용히 들어온다.
- 반대로 `"Keystroke Logging in Writing Research"`(출판사 기록)와 우리가 읽은 부제 포함 제목의
  유사도는 **0.5606** — 같은 논문인데 거절된다.

그래서 제목 하나로 판단하지 않고 **저자 성**과 **연도**로 교차 확인한다(`matches`). 그리고
후보를 하나만 받지 않는다 — Crossref는 순위 1위가 정답이 아닐 때가 있어(위 Grammarly 사례에서
1위가 오답, 2위가 정답) 여러 개를 받아 **유사도가 가장 높은 것**을 고른다.

## 받아온 문자열을 그대로 쓰지 않는다

Crossref BibTeX는 LaTeX가 아니라 유니코드·HTML을 섞어 준다 (실측: `’` 44회, `–` 38회,
`&amp;` 2회, `pages={1–15}`). 그대로 .bib에 넣으면 pdflatex+bibtex에서 깨진다. `normalize`가
타이포그래피를 BibTeX 관례로 바꾸고(`–`→`--`), 나머지 이스케이프는 `bibtex._escape`에 맡긴다.
"""

from __future__ import annotations

import re

from md4paper.enrich import (
    MIN_TITLE_WORDS,
    USER_AGENT,
    clean_venue,
    similarity,
    usable_title,
)

S2_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
S2_FIELDS = "title,year,venue,publicationVenue,authors,externalIds,publicationTypes,journal"
CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"
TIMEOUT = 20.0

# 제목만으로 확신할 수 있는 선 — 추출 오타(‘Efect’→‘Effect’)는 0.99대라 이 아래로 내려가지 않는다.
STRONG_SIMILARITY = 0.97
# 저자·연도로 교차 확인하면 받아들이는 선 (enrich의 0.90과 같은 값이지만 여기서는 단독 근거가 아니다)
MIN_SIMILARITY = 0.90
# 부제가 잘린 기록을 구제할 때 요구하는 최소 길이 — 짧은 제목의 부분일치는 우연일 수 있다
MIN_CONTAINED_LEN = 25
# Crossref에서 받아 볼 후보 수. 1위가 정답이 아닌 실측 사례가 있어 여러 개를 보고 고르는데,
# **5개로는 모자란다**: Emig 1977의 원본(CCC)은 Crossref 순위 **8위**로 나오고 1·2위는 2020·2024년
# 재수록본이다. 12개까지 보면 원본이 들어오고 `_best`의 연도 비교가 제대로 고를 수 있다.
# 요청 수는 그대로(한 번)라 비용 차이가 없다 — 걸러내는 일은 게이트가 어차피 후보마다 한다.
CANDIDATES = 12

# arXiv가 발급하는 DataCite DOI. Crossref에는 없으므로 조회하지 않고 프리프린트로 취급한다.
_ARXIV_DOI_PREFIX = "10.48550/"
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
# 출판사가 철회한 논문은 제목 앞에 그렇게 적어 준다 (실측: Crossref가 "RETRACTED ARTICLE: …").
# 제목에서 지우지 않는다 — 참고문헌 목록에 그대로 찍혀야 눈에 띈다. 다만 표시용으로 따로 기록한다.
_RETRACTED_RE = re.compile(r"^\s*(retracted|withdrawn)\b[\s:.\-–]*", re.I)
# 저장소는 학회·저널이 아니다. S2는 프리프린트의 venue를 "ArXiv"로 주는데, 그걸 booktitle에 적으면
# 프리프린트가 'ArXiv에서 발표된 것'처럼 보인다 — eprint 필드로 적어야 할 정보다.
# (enrich.py가 OpenAlex에서 `from_repository`로 가려내는 것과 같은 구분이다.)
_REPOSITORY_RE = re.compile(
    r"^(arxiv(\.org)?|biorxiv|medrxiv|chemrxiv|ssrn|research\s*square|preprints?(\.org)?|"
    r"zenodo|osf(\.io)?|hal|techrxiv|corr)$", re.I)

# 출판 기록에서 그대로 옮겨 적는 필드. 여기 없는 것은 **의도적으로 버린다**:
#   url        — doi가 있으면 중복이다 (biblatex가 doi로 링크를 만든다)
#   collection — Crossref 전용으로 series와 같은 값이다 (BibTeX 표준 필드가 아니다)
#   copyright/language/abstract — 인용에 쓰이지 않고 항목만 길어진다
_COPIED = ("pages", "volume", "number", "publisher", "series", "address", "editor",
           "school", "institution", "edition", "isbn", "issn", "note")

# HTML 엔티티만 여기서 푼다 (실측: Crossref는 'College Composition &amp; Communication',
# Semantic Scholar는 이중 인코딩된 '&amp;amp;'까지 준다 — 그래서 더 안 풀릴 때까지 반복한다).
# 유니코드 타이포그래피(굽은 따옴표·대시)는 여기서 건드리지 않는다 — 이스케이프와 한 군데서
# 처리해야 어긋나지 않아 `bibtex._escape`가 맡는다(거기 `_TYPO` 주석 참고).
_ENTITIES = (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"),
             ("&#38;", "&"), ("&nbsp;", " "))

# Crossref/OpenAlex의 type → BibTeX 항목 종류
_TYPE_TO_KIND = {
    "journal-article": "article", "proceedings-article": "inproceedings",
    "book": "book", "monograph": "book", "reference-book": "book",
    "book-chapter": "incollection", "book-section": "incollection",
    "dissertation": "phdthesis", "report": "techreport", "posted-content": "misc",
}
# Semantic Scholar의 publicationVenue.type → 항목 종류 (실측상 가장 믿을 만한 종류 신호)
_VENUE_TYPE_TO_KIND = {"conference": "inproceedings", "journal": "article", "book": "book",
                       "bookseries": "incollection"}


def _venue(name: str) -> str:
    """학회·저널명으로 쓸 값 (자리표시자와 **저장소 이름**은 빈 문자열)."""
    clean = clean_venue(name)
    return "" if _REPOSITORY_RE.match(clean) else clean


def _norm_name(name: str) -> str:
    """저자 성만 소문자 ASCII로 — 'S{\\"o}llner' · 'Söllner' · 'Soellner'를 견주기 위한 거친 정규화."""
    plain = re.sub(r"\\[a-zA-Z]+|[{}\\]", "", str(name or ""))
    last = plain.split(",")[0] if "," in plain else (plain.split()[-1] if plain.split() else "")
    return re.sub(r"[^a-z]", "", last.lower())


def surnames(authors) -> set[str]:  # noqa: ANN001 — iterable[str]
    """저자 목록 → 성 집합 (교차 확인용). 한 글자짜리는 이니셜이라 뺀다."""
    out = {_norm_name(a) for a in (authors or [])}
    return {s for s in out if len(s) > 1}


def _contained(a: str, b: str) -> bool:
    """한쪽 제목이 다른 쪽의 앞부분인지 — 출판사 기록에 부제가 없을 때(실측: Leijten 2013)."""
    x, y = sorted((_flat(a), _flat(b)), key=len)
    return len(x) >= MIN_CONTAINED_LEN and y.startswith(x)


def _flat(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(title or "").lower()).split())


def matches(title: str, cand: dict, *, authors=None, year: int | None = None) -> bool:  # noqa: ANN001
    """이 후보가 **같은 논문인가**. 제목 하나로 정하지 않는다 (모듈 주석의 두 실측 사례).

    - 유사도 0.97 이상: 제목만으로 확정 (추출 오타를 견딜 만큼은 느슨하고, 단어가 바뀌면 걸린다)
    - 0.90 이상: 저자 성이 겹치거나 연도가 1년 이내여야 채택
    - 그 아래: 한쪽이 다른 쪽의 앞부분(부제 잘림)이고 **저자까지 겹칠 때만** 채택
    """
    sim = similarity(title, cand.get("title"))
    shared = bool(surnames(authors) & surnames(cand.get("authors")))
    cy, close = cand.get("year"), False
    if year and cy:
        close = abs(int(year) - int(cy)) <= 1
    if sim >= STRONG_SIMILARITY:
        # 짧은 제목은 제목만으로 확정하지 않는다 — "Cognitive Offloading"처럼 두 단어면
        # 같은 제목의 다른 글(사설·서평)이 얼마든지 있다.
        return shared or close if len(str(title).split()) < MIN_TITLE_WORDS else True
    if sim >= MIN_SIMILARITY:
        return shared or close
    return _contained(title, cand.get("title") or "") and shared


# --- 조회 (신원 확인) -------------------------------------------------------


# 재시도 대상 상태 코드. 500이 들어 있는 게 중요하다 — Semantic Scholar는 **정상 질의에도**
# 간헐적으로 500을 낸다(실측: 같은 파라미터로 다시 부르면 200). 500을 '못 찾음'으로 처리하면
# 맞는 논문을 조용히 놓친다. 429에는 Retry-After 헤더가 없어(실측) 대기 시간을 우리가 정한다.
_RETRY_STATUS = (429, 500, 502, 503, 504)
# 한 번만, 짧게 기다린다. 예전에는 (4, 10)이었다 — S2가 **유일한** 신원 해결기이던 시절엔 오래
# 기다려서라도 받아 내는 게 이득이었다. 지금은 출처가 셋이고 회로차단기가 있어서, 오래 기다리는
# 건 손해다: 참고문헌을 8스레드로 훑으면 스레드마다 14초씩 태우고서야 차단기가 걸린다
# (실측: 그 탓에 앞 논문 두 편이 34초·31초, 차단된 뒤 논문들은 0.5초).
_BACKOFF = (2.0,)


class RateLimited(Exception):
    """이 출처가 지금은 못 받는다. `hopeless`면 오늘 안에는 안 풀린다(예산 소진 등)."""

    def __init__(self, message: str, *, hopeless: bool = False) -> None:
        super().__init__(message)
        self.hopeless = hopeless


# 기다려도 오늘 안에는 안 풀리는 429의 표시. **OpenAlex가 종량제로 바뀌었다**(실측 응답:
# "Insufficient budget. This request costs $0.001 but you only have $0.0008 remaining.
# Resets at midnight UTC"). 이걸 모르고 재시도하면 실패 한 건마다 백오프로 14초씩 태운다 —
# 참고문헌 수천 건을 훑는 작업에서는 몇 시간 차이가 난다.
_HOPELESS_RE = re.compile(r"insufficient budget|quota|exceeded your|daily limit", re.I)

# 레이트리밋에 걸린 출처는 한동안 건너뛴다 (프로세스 안에서만 기억한다).
_DOWN: dict[str, float] = {}
# 연속 실패 횟수. **한 번 걸렸다고 바로 쉬게 하지 않는다** — 동시 요청이 잠깐 몰리면 429가 하나쯤
# 나오는데, 그걸로 출처를 막아 버리면 그 뒤 논문들이 0.0초에 아무 일도 안 하고 지나간다(실측).
# 성공하면 0으로 돌아가므로, 정말로 막힌 출처만 STRIKES번 만에 쉬게 된다.
_STRIKES: dict[str, int] = {}
STRIKES = 3
# 429에도 두 종류가 있고 쉬는 시간이 달라야 한다.
#  - 예산 소진(OpenAlex 종량제): 자정까지 안 풀린다 → 길게 쉰다.
#  - 그냥 너무 빨리 보냄: 몇 초면 풀린다 → **짧게** 쉰다. 여기를 길게 잡았더니 Crossref까지
#    2분간 막혀 그 뒤 논문들이 0.0초에 아무것도 못 하고 지나갔다(실측). 빠른데 하는 일이 없으면
#    느린 것만 못하다.
COOLDOWN = 900.0
BRIEF_COOLDOWN = 20.0


def source_down(name: str) -> bool:
    """이 출처가 지금 쉬는 중인지 (레이트리밋에 걸려 잠시 건너뛰는 중)."""
    import time

    return _DOWN.get(name, 0.0) > time.monotonic()


def trip_source(name: str, seconds: float = COOLDOWN) -> None:
    """이 출처를 잠시 쉬게 한다 — 같은 벽에 수백 번 부딪히지 않도록."""
    import time

    _DOWN[name] = time.monotonic() + seconds


def reset_sources() -> None:
    """쉬는 중인 출처를 모두 되살린다 (테스트·새 실행 시작)."""
    _DOWN.clear()
    _STRIKES.clear()


def _agent(mailto: str | None = None) -> str:
    """연락처를 밝힌 User-Agent — Crossref polite pool이 이걸 보고 한도를 올려 준다."""
    return f"{USER_AGENT} (mailto:{mailto})" if mailto else USER_AGENT


def _get(client, url: str, params: dict, *, accept: str | None = None,  # noqa: ANN001, ANN202
         mailto: str | None = None):
    import time

    headers = {"User-Agent": _agent(mailto)}
    if accept:
        headers["Accept"] = accept
    for wait in (*_BACKOFF, None):
        resp = client.get(url, params=params, timeout=TIMEOUT, headers=headers)
        if resp.status_code == 429 and _HOPELESS_RE.search(resp.text or ""):
            raise RateLimited(resp.text[:200], hopeless=True)  # 기다릴 이유가 없다
        if resp.status_code not in _RETRY_STATUS or wait is None:
            if resp.status_code == 429:
                raise RateLimited(f"HTTP 429 ({url})")
            resp.raise_for_status()
            return resp
        time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover — 위 루프가 항상 반환하거나 raise한다


def _s2(client, title: str) -> dict | None:  # noqa: ANN001
    """Ai2 Semantic Scholar `search/match` — 제목으로 딱 한 편. 없으면 404(예외로 새지 않게 처리)."""
    try:
        resp = _get(client, S2_MATCH_URL, {"query": title, "fields": S2_FIELDS})
    except Exception as exc:  # noqa: BLE001 — 404(매치 없음)와 네트워크 오류를 같이 삼킨다
        if "404" not in str(exc):
            raise
        return None
    data = (resp.json() or {}).get("data") or []
    if not data:
        return None
    r = data[0]
    ex = r.get("externalIds") or {}
    pv = r.get("publicationVenue") or {}
    # 종류는 publicationVenue.type으로 고른다. publicationTypes는 못 쓴다 — 실측: CHI 논문 두 편이
    # 모두 ["Book","JournalArticle","Conference"]를 한꺼번에 달고 나오고, 진짜 단행본은 null이었다.
    kind = _VENUE_TYPE_TO_KIND.get(str(pv.get("type") or "").lower(), "")
    return {
        "title": normalize(r.get("title") or ""),
        "authors": [normalize(a.get("name")) for a in (r.get("authors") or []) if a.get("name")],
        # year는 담되 값으로 쓰지 않는다 — 교차 확인 전용 (모듈 주석: S2 연도는 프리프린트 해)
        "year": r.get("year"),
        # booktitle이 될 만한 건 journal.name이다. venue·publicationVenue.name은 총서 이름
        # ("International Conference on Human Factors in Computing Systems")이라 인용에 어울리지 않는다.
        "venue": _venue(normalize((r.get("journal") or {}).get("name")
                                  or pv.get("name") or r.get("venue") or "")),
        "kind": kind,
        "doi": str(ex.get("DOI") or ""),
        "arxiv": str(ex.get("ArXiv") or ""),
        "source": "semanticscholar",
    }


def _crossref_search(client, title: str, mailto: str | None = None) -> list[dict]:  # noqa: ANN001
    """Crossref 제목 검색 — 후보를 여러 개 받는다 (1위가 정답이 아닌 실측 사례가 있다)."""
    params = {"query.bibliographic": title, "rows": CANDIDATES,
              "select": "title,issued,container-title,DOI,type,author"}
    if mailto:  # polite pool — 연락처를 밝히면 레이트리밋이 훨씬 너그럽다
        params["mailto"] = mailto
    items = ((_get(client, CROSSREF_URL, params, mailto=mailto).json()
              .get("message") or {}).get("items") or [])
    out = []
    for it in items:
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append({
            "title": (it.get("title") or [""])[0],
            "authors": [" ".join(x for x in (a.get("given"), a.get("family")) if x)
                        for a in (it.get("author") or [])],
            "year": parts[0] if parts else None,
            "venue": _venue(normalize((it.get("container-title") or [""])[0])),
            "kind": _TYPE_TO_KIND.get(it.get("type") or "", ""),
            "doi": str(it.get("DOI") or ""),
            "arxiv": "",
            "source": "crossref",
        })
    return out


def _openalex_search(client, title: str, mailto: str | None) -> list[dict]:  # noqa: ANN001
    params = {"filter": f"title.search:{title}", "per-page": CANDIDATES}
    if mailto:
        params["mailto"] = mailto
    out = []
    for w in (_get(client, OPENALEX_URL, params).json().get("results") or []):
        src = (w.get("primary_location") or {}).get("source") or {}
        out.append({
            "title": w.get("display_name"),
            "authors": [(a.get("author") or {}).get("display_name")
                        for a in (w.get("authorships") or [])],
            "year": w.get("publication_year"),
            "venue": _venue(normalize(src.get("display_name") or "")),
            "kind": _TYPE_TO_KIND.get(w.get("type_crossref") or w.get("type") or "", ""),
            "doi": str(w.get("doi") or "").replace("https://doi.org/", ""),
            "arxiv": "",
            "source": "openalex",
        })
    return out


def _best(title: str, cands: list[dict], *, authors=None, year=None) -> dict | None:  # noqa: ANN001
    """게이트를 통과한 후보 중 하나를 고른다 — 제목 유사도 먼저, **같은 값이면 연도가 가까운 쪽**.

    연도를 보는 이유는 **재수록본** 때문이다. 실측: Emig의 "Writing as a Mode of Learning"은
    1977년 *College Composition and Communication* 논문인데, Crossref 후보에는 2020년 Routledge
    선집 *Landmark Essays* 재수록본이 함께 나오고 **제목·저자가 똑같아** 유사도만 보면 어느 쪽이
    1등이 될지 알 수 없다. 우리가 변환한 PDF는 그중 한 판본이고, PDF에서 읽은 연도가 어느 판본인지
    말해 준다.

    연도가 가까운 쪽을 **우선**할 뿐 멀다고 버리지는 않는다 — PDF의 연도 자체가 틀린 경우가 있고
    (실측: Flower & Hayes를 2008로 읽었지만 1981이 맞다), 그때는 후보가 하나뿐이라 그게 뽑힌다.
    """
    ok = [c for c in cands if c.get("title") and matches(title, c, authors=authors, year=year)]
    if not ok:
        return None

    def rank(c: dict) -> tuple[float, float]:
        gap = 0.0
        if year and c.get("year"):
            gap = -abs(int(year) - int(c["year"]))
        return round(similarity(title, c["title"]), 3), gap

    return max(ok, key=rank)


SOURCES = ("s2", "crossref", "openalex")  # 기본 사다리 (한 편을 정확히 찾을 때)
# 참고문헌 수십~수백 건을 한 건씩 훑을 때 쓰는 사다리. **Crossref를 먼저** 본다 — DOI 등록기관이라
# 제 일을 하고 레이트리밋도 너그럽다. S2는 뒤에 두되 빼지는 않는다: arXiv 프리프린트는 Crossref에
# 없어서(이 분야 참고문헌에 흔하다) S2가 없으면 영영 못 찾는다. 초당 몇 건에도 429를 내지만
# 그건 이제 회로차단기(`trip_source`)가 처리한다 — 한 번 걸리면 그 실행에서는 건너뛴다.
BULK_SOURCES = ("crossref", "s2", "openalex")


def resolve(title: str, *, authors=None, year: int | None = None,  # noqa: ANN001
            mailto: str | None = None, client=None, sources=SOURCES) -> dict | None:  # noqa: ANN001
    """제목(+저자·연도) → 이 논문의 신원 {title, authors, year, venue, kind, doi, arxiv, source}.

    Ai2 Semantic Scholar > Crossref > OpenAlex 순(`sources`로 바꿀 수 있다). 같은 논문으로
    확정되지 않으면 None (억지로 하나 고르지 않는다 — 틀린 서지가 빈 서지보다 나쁘다).
    """
    if client is not None:
        return _resolve_with(client, title, authors, year, mailto, sources)
    import httpx

    with httpx.Client(follow_redirects=True) as c:
        return _resolve_with(c, title, authors, year, mailto, sources)


def _queryable(title: str, authors) -> bool:  # noqa: ANN001
    """조회를 걸어 볼 만한 제목인지. **모든 조회 경로가 여기를 지난다**(`resolve`도 `fetch`도).

    두 단어짜리 제목("Cognitive Offloading")도 허용하되, 그렇게 짧으면 같은 제목의 다른 글과
    우연히 겹치기 쉬우니 저자로 교차 확인할 수 있을 때만 — 그 확인은 `matches`가 한다.
    """
    if not usable_title(title, min_words=2):
        return False
    return bool(authors) or len(str(title).split()) >= MIN_TITLE_WORDS


def _resolve_with(client, title, authors, year, mailto, sources=SOURCES):  # noqa: ANN001, ANN202
    if not _queryable(title, authors):
        return None
    steps = {"s2": lambda: [_s2(client, title)],
             "crossref": lambda: _crossref_search(client, title, mailto),
             "openalex": lambda: _openalex_search(client, title, mailto)}
    for name in sources:
        if source_down(name):  # 레이트리밋에 걸린 출처는 건너뛴다
            continue
        try:
            cands = [c for c in steps[name]() if c]
        except RateLimited as exc:
            # 예산 소진은 즉시, 단순 과속은 연속 STRIKES번 만에 쉬게 한다
            if exc.hopeless:
                trip_source(name, COOLDOWN)
            else:
                _STRIKES[name] = _STRIKES.get(name, 0) + 1
                if _STRIKES[name] >= STRIKES:
                    trip_source(name, BRIEF_COOLDOWN)
            continue
        except Exception:  # noqa: BLE001 — 한 출처가 죽어도 다음 출처로 넘어간다
            continue
        _STRIKES[name] = 0  # 한 번 성공하면 지금까지의 실패는 잊는다
        found = _best(title, cands, authors=authors, year=year)
        if found:
            return found
    return None


# --- 서지 원본 받아오기 (출판사 기록) ---------------------------------------


def _crossref_bibtex(client, doi: str) -> dict | None:  # noqa: ANN001
    """DOI → 출판사가 등록한 BibTeX 항목 dict. arXiv DOI는 Crossref에 없으므로 건너뛴다."""
    if not doi or doi.startswith(_ARXIV_DOI_PREFIX):
        return None
    from md4paper import bibtex

    url = f"{CROSSREF_URL}/{doi}/transform/application/x-bibtex"
    try:
        text = _get(client, url, {}, accept="application/x-bibtex").text
    except Exception:  # noqa: BLE001 — DOI가 Crossref 것이 아닐 수 있다(DataCite 등)
        return None
    got = bibtex.parse(text)
    return got[0] if got else None


def fetch(title: str, *, authors=None, year: int | None = None,  # noqa: ANN001
          mailto: str | None = None, client=None) -> dict | None:  # noqa: ANN001
    """제목 → 출판 기록으로 채운 서지 dict. 못 찾으면 None.

    반환 형태는 `bibtex.fields_from_meta`가 받는 것과 같은 서지 dict에 필드가 더 붙은 것:
    {title, authors, year, venue, kind, doi, arxiv, pages, volume, number, publisher,
     series, month, editor, address, meta_source}
    """
    if client is not None:
        return _fetch_with(client, title, authors, year, mailto)
    import httpx

    with httpx.Client(follow_redirects=True) as c:
        return _fetch_with(c, title, authors, year, mailto)


def _fetch_with(client, title, authors, year, mailto):  # noqa: ANN001, ANN202
    found = _resolve_with(client, title, authors, year, mailto)
    if not found:
        return None
    record = {k: v for k, v in found.items() if k != "source"}
    record["meta_source"] = found["source"]
    entry = _crossref_bibtex(client, found.get("doi", ""))
    if entry:
        record.update(_from_publisher(entry))
        record["meta_source"] = f"{found['source']}+crossref"
    elif found.get("arxiv") and not record.get("venue"):
        # 출판 기록도 학회명도 없다 → 진짜 프리프린트다. **학회명이 있으면 misc로 내리지 않는다** —
        # 실측: 2025년 ACM 논문들은 DOI가 아직 Crossref에 없어 여기로 떨어지는데, 학회는 이미
        # 확정돼 있다. 그걸 @misc로 만들면 학회 발표 논문이 프리프린트 인용으로 둔갑한다.
        record["kind"] = "misc"
    record["retracted"] = _RETRACTED_RE.match(str(record.get("title") or "")) is not None
    return record


def _from_publisher(entry: dict) -> dict:
    """Crossref BibTeX 항목 → 우리 서지 dict의 값들 (출판사 기록이 이깁니다 — 연도 포함)."""
    out: dict = {}
    kind = str(entry.get("ENTRYTYPE") or "").lower()
    if kind:
        out["kind"] = kind
    if entry.get("title"):
        out["title"] = normalize(entry["title"])
    if entry.get("author"):
        # 출판사 기록의 저자 문자열은 이미 BibTeX 표기('Waes, Luuk Van and ...')다. **그대로 넘긴다** —
        # '이름 성'으로 폈다가 다시 접으면 전치사가 든 성이 망가진다('van der Berg, Jan'을 펴면
        # 'Jan van der Berg', 다시 접으면 'Berg, Jan van der'가 되어 저자가 바뀐다).
        out["author_latex"] = normalize(entry["author"])
        # 목록 표시·검색이 쓰는 '이름 성' 목록도 함께 (여기서는 되접지 않으니 손실이 없다)
        names = []
        for chunk in re.split(r"\s+and\s+", out["author_latex"]):
            part = chunk.strip()
            if not part:
                continue
            names.append(f"{part.split(',', 1)[1].strip()} {part.split(',', 1)[0].strip()}".strip()
                         if "," in part else part)
        if names:
            out["authors"] = names
    venue = _venue(normalize(entry.get("booktitle") or entry.get("journal") or ""))
    if venue:
        out["venue"] = venue
    if entry.get("year"):
        out["year"] = _int(entry["year"])
    for name in _COPIED:
        value = normalize(entry.get(name) or "")
        if value:
            out[name] = value
    if entry.get("month"):
        out["month"] = normalize(entry["month"]).strip(" .").lower()[:3]
    if entry.get("doi") or entry.get("DOI"):
        out["doi"] = str(entry.get("doi") or entry.get("DOI")).replace("https://doi.org/", "")
    return out


def _int(value) -> int | None:  # noqa: ANN001
    m = re.search(r"(1[6-9]\d\d|20\d\d)", str(value or ""))
    return int(m.group(1)) if m else None


def normalize(text: str) -> str:
    r"""받아온 문자열에서 **마크업만** 걷어낸다 — HTML 엔티티를 풀고 공백을 정리한다.

    LaTeX 이스케이프(`&`→`\&`, 악센트)와 타이포그래피(`’`→`'`)는 여기서 하지 않는다.
    `bibtex._escape`가 항목을 만들 때 한 번에 한다 — 두 군데서 하면 이중 이스케이프가 난다
    (`\&` → `\textbackslash\&`). 엔티티만 여기서 푸는 이유는 그게 **LaTeX 문제가 아니라
    전송 형식 문제**여서다: `&amp;`를 그대로 두면 이스케이프를 거쳐 `\&amp;`라는 글자가 된다.
    """
    out = " ".join(str(text or "").split())
    for _ in range(3):  # 이중 인코딩(&amp;amp;)까지 — 더 안 바뀌면 멈춘다
        before = out
        for src, dst in _ENTITIES:
            out = out.replace(src, dst)
        if out == before:
            break
    return out.strip()


# --- 논문 폴더에 붙이기 -----------------------------------------------------


def load(wd) -> dict | None:  # noqa: ANN001 — WorkDir
    """이 논문에 대해 받아 둔 출판 기록 (없거나 깨졌으면 None). 네트워크를 쓰지 않는다."""
    import json

    if not wd.bib_source_json.exists():
        return None
    try:
        got = json.loads(wd.bib_source_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and got.get("title") else None


def update_workdir(wd, *, mailto: str | None = None, client=None,  # noqa: ANN001
                   force: bool = False) -> dict | None:  # noqa: ANN001
    """이 논문의 출판 기록을 받아 `bib_source.json`에 저장. 반환: 받은 기록 (못 찾으면 None).

    이미 받아 둔 게 있으면 다시 받지 않는다(`force`로 강제). **여기서만 네트워크를 쓴다** —
    `bibtex.entry_for`는 저장된 것만 읽으므로 내보내기는 언제나 오프라인이고 빠르다.
    """
    import json

    from md4paper import paper_meta

    if not force:
        cached = load(wd)
        if cached:
            return cached
    meta = paper_meta.load(wd) or {}
    title = str(meta.get("title") or "").strip()
    if not title:
        return None
    got = fetch(title, authors=meta.get("authors"), year=meta.get("year"),
                mailto=mailto, client=client)
    if not got:
        return None
    wd.bib_source_json.write_text(json.dumps(got, ensure_ascii=False, indent=2), encoding="utf-8")
    return got


def update_many(roots, *, mailto: str | None = None, force: bool = False,  # noqa: ANN001
                on_progress=None) -> dict:  # noqa: ANN001
    """여러 논문의 출판 기록을 순차로 받는다. 반환: {checked, found, missed}.

    간격을 두는 이유는 Semantic Scholar의 익명 풀이 초당 몇 건에서도 429를 낸다는 실측 때문이다
    (`_get`이 재시도하지만, 애초에 덜 부딪히는 편이 빠르다).
    """
    import time

    import httpx

    from md4paper.workdir import WorkDir

    counts = {"checked": 0, "found": 0, "missed": 0}
    with httpx.Client(follow_redirects=True) as client:
        for root in roots:
            wd = WorkDir(root)
            counts["checked"] += 1
            try:
                got = update_workdir(wd, mailto=mailto, client=client, force=force)
            except Exception:  # noqa: BLE001 — 한 편이 실패해도 나머지를 계속 받는다
                got = None
            counts["found" if got else "missed"] += 1
            if on_progress:
                on_progress(counts["checked"], root, got)
            time.sleep(1.0)
    return counts


def _is_initial(token: str) -> bool:
    """'Y.' · 'Y' 처럼 이름 한 글자짜리인지 (마침표는 있어도 없어도 된다)."""
    return len(str(token or "").strip().rstrip(".")) == 1


def _abbreviated(theirs: str | None, ours: str | None) -> bool:
    """출판사가 **이름(given name)을 이니셜로 줄였는지** — 'Wu, Y.' vs 'Wu, Yi-Fang Brook'.

    가운데 이름 이니셜은 줄임이 아니다. `Cecilia D. Shelton`은 `Cecilia Shelton`보다 **더**
    자세하므로, 이니셜을 통째로 세면(실측 사고) 출판사 쪽이 줄인 것으로 잘못 읽는다.
    그래서 **첫 이름 토큰만** 견준다.
    """
    t, o = str(theirs or "").split(), str(ours or "").split()
    return bool(t) and bool(o) and _is_initial(t[0]) and not _is_initial(o[0])


def prefer_fuller_authors(record: dict, pdf_authors) -> dict:  # noqa: ANN001
    """같은 저자인데 출판사가 **이름을 이니셜로** 등록해 뒀으면 PDF 쪽 표기를 쓴다.

    출판사 기록이 대체로 정확하지만 저자 이름만은 그렇지 않다 — 이니셜로 등록해 둔 경우가 있다
    (실측: NIRVANA 73항목 중 10항목이 `Wu, Yi-Fang Brook` → `Wu, Y.`로 짧아졌다). 참고문헌
    목록에 이니셜만 남으면 누구인지 알아보기 어렵고, PDF에는 온전한 이름이 있으니 버릴 이유가 없다.

    바꾸는 조건이 두 가지다. **같은 사람들이어야** 하고(성 집합이 같아야 한다), 실제로 **이름이
    이니셜로 줄어든 저자가 있어야** 한다. 둘 중 하나라도 아니면 출판사 기록을 그대로 믿는다 —
    출판사 쪽이 오히려 더 정확할 때가 많다(실측: PDF의 `Tasfa`·`Cecilia`를 출판사는
    `Tasfia`·`Cecilia D.`로 바로잡아 준다).
    """
    ours = [str(a) for a in (pdf_authors or []) if str(a).strip()]
    theirs = [str(a) for a in (record.get("authors") or [])]
    if not ours or not theirs or surnames(ours) != surnames(theirs):
        return record  # 저자 구성이 다르면 출판사 기록을 믿는다
    by_surname = {_norm_name(a): a for a in theirs}
    if not any(_abbreviated(by_surname.get(_norm_name(a)), a) for a in ours):
        return record  # 줄어든 이름이 없다 — 출판사 표기가 더 낫다
    out = dict(record)
    out["authors"] = list(ours)
    out.pop("author_latex", None)  # 우리 이름으로 다시 접게 한다
    return out


def arxiv_id(record: dict) -> str:
    """이 기록의 arXiv 번호 (없으면 빈 문자열). DOI가 `10.48550/arXiv.NNNN.NNNNN` 꼴일 때도 캔다."""
    raw = str(record.get("arxiv") or "")
    if not raw and str(record.get("doi") or "").startswith(_ARXIV_DOI_PREFIX):
        raw = str(record["doi"])
    m = _ARXIV_ID_RE.search(raw)
    return m.group(1) if m else ""
