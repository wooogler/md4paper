"""참고문헌 목록에 DOI를 채운다 — 인용 하나하나가 눌리는 링크가 되도록.

`cite` 단계는 논문의 References 절을 LLM으로 파싱해 `references.json`에 넣는다. 거기서 나온
`RefEntry.url()`이 DOI나 arXiv 번호로 링크를 만드는데, **원문에 그 번호가 인쇄돼 있을 때만**
링크가 생긴다. 실측(변환한 논문 124편, 참고문헌 7,746건): **56%가 DOI도 arXiv 번호도 없어
링크 없이 나간다.** 오래된 논문일수록 심해서, 참고문헌 목록 전체에 링크가 하나도 없는 논문이
드물지 않다.

## 한 번에 다 받아 오는 길과, 한 건씩 찾는 길

Semantic Scholar에는 `/paper/{id}/references`가 있다 — **논문 하나의 참고문헌 전체를 한 번의
호출로** 준다(제목·연도·venue·DOI까지). 62건짜리 목록을 62번 조회하는 것과 한 번에 받는 것은
레이트리밋 앞에서 전혀 다른 이야기라, 이 길을 먼저 쓴다.

다만 **커버리지가 반반이다**(실측):

| 논문 | S2가 준 참고문헌 | 우리 목록과 매칭 | 새로 생긴 링크 |
|---|---|---|---|
| TextGrad (2024, arXiv) | 107건 | 99/109 (90%) | **72** |
| HaLLMark (2024, CHI) | 83건 | 62/74 (83%) | 6 |
| Cognitive Offloading (2016, Elsevier) | **0건** | — | 0 |
| Davis (1989, MIS Quarterly) | **0건** | — | 0 |

S2는 오픈액세스·arXiv 계열의 참고문헌은 갖고 있지만 옛날 출판사 논문은 아예 없다. 그런데
**링크가 없어 곤란한 쪽이 바로 그 옛날 논문들**이라, 한 번에 받아 오는 길만 두면 정작 필요한
논문에서 아무 일도 일어나지 않는다. 그래서 남은 항목은 한 건씩 찾는다(§bibsource).

한 건씩 찾을 때는 **Crossref부터** 간다(`bibsource.BULK_SOURCES`) — DOI 등록기관이라 제 일을 하고
레이트리밋도 너그럽다. S2는 뒤에 두되 빼지 않는다: arXiv 프리프린트는 Crossref에 없어서(이 분야
참고문헌에 흔하다) S2가 없으면 영영 못 찾는다. 429는 회로차단기가 처리한다(§bibsource).

그리고 **동시에** 던진다. 순수 IO(HTTP 대기)라 직렬로 돌리면 대기 시간이 그대로 더해진다 —
실측: 한 논문(참고문헌 14건)이 26.7초였다. 요청 8개를 겹쳐 던지면 그 대기가 겹친다.

## 채우기만 하고 고치지는 않는다

`bibsource`가 논문 **자기 서지**를 다룰 때는 출판사 기록이 이긴다(PDF에서 잘못 읽은 연도를
바로잡는 게 목적이니까). 참고문헌은 반대다 — 거기 적힌 건 **저자가 그렇게 인용했다는 사실**이고,
우리가 제목·연도를 출판사 기록으로 갈아치우면 원문과 어긋난 참고문헌 목록이 된다. 그래서
**비어 있는 칸만** 채운다(`enrich`와 같은 규율).
"""

from __future__ import annotations

import re

from md4paper import bibsource

S2_REFS_URL = "https://api.semanticscholar.org/graph/v1/paper/{pid}/references"
S2_REFS_FIELDS = "title,year,venue,externalIds,authors"
# 한 번에 받아 볼 참고문헌 수 (S2 상한). 이보다 긴 목록은 뒷부분을 한 건씩 찾게 된다.
BULK_LIMIT = 1000
# 한 건씩 조회할 최대 건수 — 참고문헌이 수백 개인 서베이 논문에서 몇십 분씩 걸리지 않게.
# 넘치면 `on_progress`로 몇 건을 못 봤는지 알린다(조용히 자르지 않는다).
MAX_LOOKUPS = 60


def _bulk(client, doi: str) -> list[dict]:  # noqa: ANN001
    """이 논문의 참고문헌 전체를 S2에서 한 번에. 없거나 실패하면 빈 목록."""
    if not doi:
        return []
    try:
        resp = bibsource._get(client, S2_REFS_URL.format(pid=f"DOI:{doi}"),
                              {"fields": S2_REFS_FIELDS, "limit": BULK_LIMIT})
    except Exception:  # noqa: BLE001 — 이 길이 막히면 한 건씩 찾는 길로 간다
        return []
    out = []
    for row in (resp.json().get("data") or []):
        p = row.get("citedPaper") or {}
        if not p.get("title"):
            continue
        ex = p.get("externalIds") or {}
        out.append({
            "title": bibsource.normalize(p["title"]),
            "authors": [a.get("name") for a in (p.get("authors") or []) if a.get("name")],
            "year": p.get("year"),
            "venue": bibsource._venue(bibsource.normalize(p.get("venue") or "")),
            "doi": str(ex.get("DOI") or ""),
            "arxiv": str(ex.get("ArXiv") or ""),
            "source": "semanticscholar",
        })
    return out


def _linked(ref) -> bool:  # noqa: ANN001 — RefEntry
    """이미 링크를 만들 수 있는 항목인지 (DOI나 arXiv 번호가 있다)."""
    return bool(str(ref.doi or "").strip() or str(ref.arxiv_id or "").strip())


def _apply(ref, found: dict) -> list[str]:  # noqa: ANN001 — RefEntry
    """찾은 기록으로 **빈 칸만** 채운다. 반환: 채운 필드 이름들."""
    filled = []
    if not str(ref.doi or "").strip() and found.get("doi"):
        ref.doi = found["doi"]
        filled.append("doi")
    arxiv = bibsource.arxiv_id(found)
    if not str(ref.arxiv_id or "").strip() and arxiv:
        ref.arxiv_id = arxiv
        filled.append("arxiv")
    if not ref.year and found.get("year"):
        ref.year = found["year"]
        filled.append("year")
    if not str(ref.venue or "").strip() and found.get("venue"):
        ref.venue = found["venue"]
        filled.append("venue")
    return filled


# 한 건씩 찾을 때 동시에 던질 요청 수. 참고문헌 조회는 순수 IO(HTTP 대기)라 직렬로 돌리면
# 대기 시간만 더한 값이 된다 — 100건이면 몇 분. 너무 올리면 레이트리밋에 걸리므로 8로 둔다
# (Crossref polite pool은 초당 50건까지 받아 준다).
WORKERS = 5


def fill(refs, *, paper_doi: str = "", mailto: str | None = None,  # noqa: ANN001
         client=None, max_lookups: int = MAX_LOOKUPS, on_progress=None,  # noqa: ANN001
         workers: int = WORKERS) -> dict:
    """참고문헌 목록의 빈 칸을 채운다 (제자리 수정). 반환: {total, already, bulk, single, filled, skipped}.

    `paper_doi`가 있으면 S2에서 참고문헌 전체를 한 번에 받아 먼저 맞춰 보고, 그래도 링크가 없는
    항목만 한 건씩 **동시에** 찾는다(최대 `max_lookups`건).
    """
    counts = {"total": len(refs), "already": 0, "bulk": 0, "single": 0, "filled": 0, "skipped": 0}
    if client is not None:
        return _fill_with(client, refs, paper_doi, mailto, max_lookups, on_progress, counts,
                          workers)
    import httpx

    with httpx.Client(follow_redirects=True) as c:
        return _fill_with(c, refs, paper_doi, mailto, max_lookups, on_progress, counts, workers)


def _fill_with(client, refs, paper_doi, mailto, max_lookups, on_progress, counts,  # noqa: ANN001
               workers=WORKERS):  # noqa: ANN001
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    counts["already"] = sum(1 for r in refs if _linked(r))
    todo = [r for r in refs if not _linked(r)]

    pool = _bulk(client, paper_doi)  # 한 번의 호출로 받은 후보들
    still = []
    for ref in todo:
        found = bibsource._best(ref.title or "", pool, authors=ref.authors, year=ref.year) \
            if pool else None
        if found and _apply(ref, found):
            counts["bulk"] += 1
            counts["filled"] += 1
        else:
            still.append(ref)

    if len(still) > max_lookups:
        counts["skipped"] = len(still) - max_lookups
        still = still[:max_lookups]
    if not still:
        return counts

    lock = Lock()  # counts와 on_progress를 여러 스레드가 함께 만진다

    def one(ref):  # noqa: ANN001, ANN202
        try:
            found = bibsource.resolve(ref.title or "", authors=ref.authors, year=ref.year,
                                      mailto=mailto, client=client,
                                      sources=bibsource.BULK_SOURCES)
        except Exception:  # noqa: BLE001 — 한 건이 실패해도 나머지를 계속 찾는다
            found = None
        # `_apply`는 자기 ref만 건드리므로 락 밖에서 해도 안전하다
        filled = bool(found and _apply(ref, found))
        with lock:
            if filled:
                counts["single"] += 1
                counts["filled"] += 1
            if on_progress:
                try:
                    on_progress(counts)
                except Exception:  # noqa: BLE001 — 표시 실패가 조회를 막지 않게
                    pass

    # httpx.Client는 스레드 안전하다 — 같은 연결 풀을 나눠 쓰는 게 오히려 빠르다
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(still)))) as ex:
        list(ex.map(one, still))
    return counts


def update_workdir(wd, *, mailto: str | None = None, client=None,  # noqa: ANN001
                   max_lookups: int = MAX_LOOKUPS, on_progress=None) -> dict:  # noqa: ANN001
    """이 논문의 `references.json`을 보강하고, 마크다운의 참고문헌 목록도 다시 렌더한다.

    참고문헌을 아직 파싱하지 않았으면(=`cite` 단계 전) 아무것도 하지 않는다.
    """
    import json

    from md4paper import bibsource as bs
    from md4paper.cite import apply as cite_apply

    refs = cite_apply.load_cached_refs(wd)
    if not refs:
        return {"total": 0, "already": 0, "bulk": 0, "single": 0, "filled": 0, "skipped": 0}
    paper_doi = str((bs.load(wd) or {}).get("doi") or "")
    counts = fill(refs, paper_doi=paper_doi, mailto=mailto, client=client,
                  max_lookups=max_lookups, on_progress=on_progress)
    if not counts["filled"]:
        return counts

    data = json.loads(wd.references_json.read_text(encoding="utf-8"))
    data["accepted"] = [r.model_dump() for r in refs]
    wd.references_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    counts["relisted"] = relist(wd, refs)
    return counts


# 이미 렌더된 참고문헌 항목 한 줄 — `<a id="ref-12"></a>**[12]** …`
_RENDERED_RE = re.compile(r'^<a id="ref-\d+"></a>')


def relist(wd, refs) -> bool:  # noqa: ANN001
    """마크다운의 참고문헌 목록을 새로 렌더한 것으로 갈아 끼운다. 반환: 바꿨는지.

    **`cite.apply_links`를 쓰지 않는다.** 그쪽은 참고문헌 구역을 `sections.map.json`의 줄 번호로
    찾는데, 그 줄 번호는 마크다운을 조립할 때 기록된 것이라 `cite` 단계가 원문 참고문헌을 렌더된
    목록으로 갈아치운 뒤에는 **어긋나 있다**. 실측: 그대로 부르면 구역 끝을 목록 중간으로 잡아
    뒷부분 50개 항목이 지워지지 않고 남아 **참고문헌이 109개에서 159개로 늘었다**(중복 50건).

    그래서 줄 번호를 믿지 않고 **이미 렌더돼 있는 항목 줄(`<a id="ref-N">`)의 처음과 끝**을 찾아
    그 사이만 바꾼다. 본문은 손대지 않는다 — 본문 인용은 `#ref-N` 앵커를 가리키고 그 앵커는
    그대로이므로, DOI가 채워졌다고 본문이 달라질 일이 없다.
    """
    from md4paper.cite import render
    from md4paper.review import manifest as manifest_io

    if not wd.en_md.exists():
        return False
    lines = wd.en_md.read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(lines) if _RENDERED_RE.match(ln)]
    if not hits:  # 아직 렌더된 목록이 없다 (cite 전이거나 다른 형식) — 건드리지 않는다
        return False
    try:  # manifest가 없는 작업 폴더도 있다 — 링크 표시 설정은 기본값으로 두고 계속한다
        links = manifest_io.load(wd).reference_links
    except (OSError, ValueError):
        links = True
    rendered = render.render_reference_list(refs, links)
    new = lines[:hits[0]] + rendered + lines[hits[-1] + 1:]
    wd.en_md.write_text("\n".join(new) + "\n", encoding="utf-8")
    return True
