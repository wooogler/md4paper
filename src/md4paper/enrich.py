"""서지 정보 보강 — 공개 서지 API로 비어 있는 연도·venue를 채운다 (선택 기능).

PDF가 진실원이므로 **비어 있는 필드만** 채운다. 오래된 스캔본이나 프리프린트는 1페이지에
연도·학회가 아예 없어 LLM도 채울 수 없는데, 그럴 때만 온라인 서지를 조회한다.
논문 **제목만** 외부로 나간다 (본문은 전송하지 않는다).

**제목 유사도 게이트가 이 모듈의 핵심이다.** Crossref의 `query.bibliographic`은 유사도와
무관하게 언제나 1등을 돌려주므로, 게이트 없이 쓰면 엉뚱한 논문의 연도가 조용히 들어온다
(실측: "Designing a Meta-Reflective Dashboard…" → 2010년 책 "8. Instructor as Reflective
Practitioner"). 제목이 MIN_SIMILARITY 이상 일치할 때만 채택한다 — cite/parse의 반환각 검증과 같은 규율.

1차는 OpenAlex(키 불필요, 학술지·학회·arXiv 모두 커버, 실측 4/4 정확). arXiv 같은 저장소만
잡히면 출판 venue를 Crossref로 한 번 더 확인한다.
"""

from __future__ import annotations

import difflib
import re

OPENALEX_URL = "https://api.openalex.org/works"
CROSSREF_URL = "https://api.crossref.org/works"
USER_AGENT = "md4paper (+https://github.com/wooogler/md4paper)"
MIN_SIMILARITY = 0.90  # 제목 유사도 하한 (실측: 정상 매치 1.00, 오매치 0.36~0.40)
TIMEOUT = 12.0
# 질의 제목이 쓸 만한지 — 유사도 게이트는 '우리 제목이 맞다'는 전제 위에서만 동작한다.
# 추출이 실패해 제목이 "1 Introduction"이 되면 Crossref의 "Introduction"과 0.92로 붙어 통과한다
# (실측 사고). 그래서 질의 단계에서 막는다.
MIN_TITLE_LEN = 16
MIN_TITLE_WORDS = 3
_SECTION_HEADING_RE = re.compile(
    r"^\d*[\s.)]*(introduction|abstract|related\s+work|background|conclusions?|references|"
    r"methods?|methodology|discussion|results|appendix|acknowledg\w*|preface)\b\s*$", re.I)


def usable_title(title: str | None) -> bool:
    """서지 조회에 쓸 만한 제목인지 (섹션 헤딩·너무 짧은 조각은 조회 자체를 막는다)."""
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    return (len(t) >= MIN_TITLE_LEN and len(t.split()) >= MIN_TITLE_WORDS
            and not _SECTION_HEADING_RE.match(t))

# 논문 템플릿의 자리표시자 — 실제 학회·저널명이 아니다
# (SAGE "Journal Title", ACM "Conference acronym 'XX", "Woodstock '18")
_PLACEHOLDER_VENUE = re.compile(
    r"^(journal title|conference acronym.*|conference'?\s*\d*|woodstock.*|acm conference|"
    r"xx\(x\)|anonymous.*|submitted to.*|under review.*|preprint|manuscript)$", re.I)


def clean_venue(venue: str | None) -> str:
    """템플릿 자리표시자·의미 없는 값을 걸러낸 venue (아니면 공백 정리한 원문)."""
    v = re.sub(r"\s+", " ", str(venue or "")).strip(" ,.;")
    return "" if len(v) < 3 or _PLACEHOLDER_VENUE.match(v) else v


def _norm(title: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(title or "").lower()).split())


def similarity(a: str | None, b: str | None) -> float:
    """제목 유사도 0~1 (대소문자·구두점 무시)."""
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _get(client, url: str, params: dict) -> dict:  # noqa: ANN001 — httpx.Client 호환 객체
    resp = client.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _openalex(client, title: str, mailto: str | None) -> dict | None:  # noqa: ANN001
    params = {"filter": f"title.search:{title}", "per-page": 1}
    if mailto:
        params["mailto"] = mailto
    results = _get(client, OPENALEX_URL, params).get("results") or []
    if not results:
        return None
    r = results[0]
    # venue: 출판처(저널·학회)를 우선하고, 저장소(arXiv)뿐이면 그렇다고 표시해 둔다
    venue, from_repo = "", False
    for loc in [r.get("primary_location"), *(r.get("locations") or [])]:
        src = (loc or {}).get("source") or {}
        name = clean_venue(src.get("display_name"))
        if not name:
            continue
        if src.get("type") == "repository":
            if not venue:
                venue, from_repo = name, True
            continue
        venue, from_repo = name, False
        break
    return {
        "title": r.get("display_name"),
        "year": r.get("publication_year"),
        "venue": venue,
        "from_repository": from_repo,
        "doi": str(r.get("doi") or "").replace("https://doi.org/", ""),
        "source": "openalex",
    }


def _crossref(client, title: str, mailto: str | None) -> dict | None:  # noqa: ANN001
    params = {"query.bibliographic": title, "rows": 1,
              "select": "title,issued,container-title,DOI"}
    if mailto:
        params["mailto"] = mailto
    items = (_get(client, CROSSREF_URL, params).get("message") or {}).get("items") or []
    if not items:
        return None
    it = items[0]
    parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
    return {
        "title": (it.get("title") or [""])[0],
        "year": parts[0] if parts else None,
        "venue": clean_venue((it.get("container-title") or [""])[0]),
        "from_repository": False,
        "doi": it.get("DOI") or "",
        "source": "crossref",
    }


def _verified(fn, client, title: str, mailto: str | None) -> dict | None:  # noqa: ANN001
    """조회 결과가 **같은 논문일 때만** 통과시킨다 (제목 유사도 게이트)."""
    try:
        got = fn(client, title, mailto)
    except Exception:  # noqa: BLE001 — 네트워크·레이트리밋·스키마 변경 어느 것도 파이프라인을 막지 않는다
        return None
    if not got or similarity(title, got.get("title")) < MIN_SIMILARITY:
        return None
    return got


def lookup(title: str, *, mailto: str | None = None, client=None) -> dict | None:  # noqa: ANN001
    """제목으로 서지를 조회 — 같은 논문으로 확정될 때만 dict, 아니면 None.

    반환: {"year", "venue", "doi", "source", "title"}
    """
    title = (title or "").strip()
    if not usable_title(title):
        return None
    if client is not None:
        return _lookup_with(client, title, mailto)
    import httpx

    with httpx.Client(follow_redirects=True) as c:
        return _lookup_with(c, title, mailto)


def _lookup_with(client, title: str, mailto: str | None) -> dict | None:  # noqa: ANN001
    found = _verified(_openalex, client, title, mailto)
    if found and found.get("from_repository"):
        # arXiv 등 저장소만 잡혔다 → 출판 venue가 따로 있는지 Crossref로 확인
        cr = _verified(_crossref, client, title, mailto)
        if cr and cr.get("venue"):
            return {**found, "venue": cr["venue"],
                    "doi": found.get("doi") or cr.get("doi"), "source": "openalex+crossref"}
    return found or _verified(_crossref, client, title, mailto)


def missing_fields(meta: dict) -> list[str]:
    """보강이 필요한 필드 (자리표시자 venue는 '없음'으로 친다)."""
    out = []
    if not meta.get("year"):
        out.append("year")
    if not clean_venue(meta.get("venue")):
        out.append("venue")
    return out


def enrich_meta(meta: dict, *, mailto: str | None = None, client=None) -> tuple[dict, list[str]]:  # noqa: ANN001
    """비어 있는 필드만 채운 meta와 채운 필드 목록. 채울 게 없거나 못 찾으면 (원본, [])."""
    need = missing_fields(meta)
    if not need:
        return meta, []
    found = lookup(meta.get("title", ""), mailto=mailto, client=client)
    if not found:
        return meta, []
    out, filled = dict(meta), []
    for field in need:
        if found.get(field):
            out[field] = found[field]
            filled.append(field)
    if filled:
        out["meta_source"] = found["source"]
        if found.get("doi") and not out.get("doi"):
            out["doi"] = found["doi"]
    return out, filled


def enrich_workdir(wd, *, mailto: str | None = None, client=None) -> list[str]:  # noqa: ANN001
    """작업 디렉토리의 paper_meta.json을 보강해 저장. 반환: 채운 필드 목록."""
    from md4paper import paper_meta
    from md4paper.ir import StoredPaperMeta

    meta = paper_meta.load(wd)
    if not meta:
        return []
    updated, filled = enrich_meta(meta, mailto=mailto, client=client)
    if filled:
        fields = set(StoredPaperMeta.model_fields)
        paper_meta.save(wd, StoredPaperMeta(**{k: v for k, v in updated.items() if k in fields}))
    return filled


def enrich_many(roots, *, mailto: str | None = None, on_progress=None) -> dict:  # noqa: ANN001
    """여러 논문을 순차 보강 (polite pool 예의상 간격을 둔다). 반환: 필드별 채운 수 + 논문 수."""
    import time

    import httpx

    from md4paper.workdir import WorkDir

    counts = {"papers": 0, "year": 0, "venue": 0, "checked": 0}
    with httpx.Client(follow_redirects=True) as client:
        for root in roots:
            counts["checked"] += 1
            filled = enrich_workdir(WorkDir(root), mailto=mailto, client=client)
            if filled:
                counts["papers"] += 1
                for f in filled:
                    counts[f] = counts.get(f, 0) + 1
            if on_progress:
                on_progress(counts["checked"], root, filled)
            time.sleep(0.15)  # 초당 10건 제한(polite pool) 아래로
    return counts
