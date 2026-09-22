"""참고문헌 DOI 채우기 — 한 번에 받아 오는 길, 한 건씩 찾는 길, 그리고 '고치지 않는다'는 규율.

네트워크는 쓰지 않는다 (test_bibsource와 같은 가짜 클라이언트 방식).
"""

import json
import re

import pytest

from md4paper import bibsource
from md4paper.cite import resolve as cite_resolve
from md4paper.ir import RefEntry
from md4paper.workdir import WorkDir


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(bibsource, "_BACKOFF", ())


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None, headers=None):  # noqa: ANN001, ARG002
        self.calls.append(url)
        for fragment, resp in self.routes.items():
            if fragment in url:
                return resp
        return _Resp({}, status=404)


def _cited(title, year, doi="", arxiv="", authors=()):
    ex = {}
    if doi:
        ex["DOI"] = doi
    if arxiv:
        ex["ArXiv"] = arxiv
    return {"citedPaper": {"title": title, "year": year, "externalIds": ex,
                           "authors": [{"name": a} for a in authors], "venue": "Some Venue"}}


def _ref(label, title, **kw):
    return RefEntry(label=label, title=title, raw=title, **kw)


def test_one_bulk_call_fills_the_whole_list():
    """62건짜리 목록을 62번 조회하는 것과 한 번에 받는 것은 레이트리밋 앞에서 다른 이야기다."""
    client = _Client({"/references": _Resp({"data": [
        _cited("Attention Is All You Need", 2017, doi="10.5555/3295222", authors=["Ashish Vaswani"]),
        _cited("Deep Residual Learning for Image Recognition", 2016, doi="10.1109/CVPR.2016.90",
               authors=["Kaiming He"]),
    ]})})
    refs = [_ref("1", "Attention Is All You Need", authors=["Ashish Vaswani"], year=2017),
            _ref("2", "Deep Residual Learning for Image Recognition", authors=["Kaiming He"],
                 year=2016)]

    counts = cite_resolve.fill(refs, paper_doi="10.1/x", client=client)

    assert counts["bulk"] == 2 and counts["single"] == 0
    assert refs[0].doi == "10.5555/3295222" and refs[1].doi == "10.1109/CVPR.2016.90"
    assert len(client.calls) == 1  # 한 번의 호출로 끝났다


def test_entries_the_bulk_call_misses_are_looked_up_one_by_one():
    """실측 — S2는 옛날 출판사 논문의 참고문헌을 아예 안 준다. 그때가 링크가 제일 아쉬운 때다."""
    client = _Client({
        "/references": _Resp({"data": []}),  # S2가 이 논문의 참고문헌을 모른다
        "api.crossref.org": _Resp({"message": {"items": [{
            "title": ["Perceived Usefulness, Perceived Ease of Use, and User Acceptance"],
            "issued": {"date-parts": [[1989]]}, "container-title": ["MIS Quarterly"],
            "DOI": "10.2307/249008", "type": "journal-article",
            "author": [{"given": "Fred", "family": "Davis"}]}]}}),
    })
    refs = [_ref("1", "Perceived Usefulness, Perceived Ease of Use, and User Acceptance",
                 authors=["Fred D. Davis"], year=1989)]

    counts = cite_resolve.fill(refs, paper_doi="10.1/x", client=client)

    assert counts["single"] == 1 and refs[0].doi == "10.2307/249008"


def test_crossref_is_asked_before_semantic_scholar():
    """Crossref가 DOI 등록기관이고 레이트리밋도 너그럽다 — S2는 arXiv를 위해 뒤에 둔다."""
    client = _Client({"/references": _Resp({"data": []}),
                      "api.crossref.org": _Resp({"message": {"items": []}}),
                      "semanticscholar": _Resp({"data": []}),
                      "openalex": _Resp({"results": []})})
    cite_resolve.fill([_ref("1", "Some Paper Title That Is Long Enough", authors=["A B"])],
                      paper_doi="10.1/x", client=client)

    order = [c for c in client.calls if "crossref" in c or "search/match" in c]
    assert order and "crossref" in order[0]
    assert any("search/match" in c for c in client.calls)  # arXiv를 위해 S2도 본다


def test_lookups_run_in_parallel():
    """직렬이면 대기 시간이 그대로 더해진다 — 실측 한 논문 26.7초. 겹쳐 던져야 한다."""
    import threading
    import time
    live, peak, lock = 0, 0, threading.Lock()

    class _Slow(_Client):
        def get(self, url, params=None, timeout=None, headers=None):  # noqa: ANN001, ARG002
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1
            return super().get(url, params, timeout, headers)

    client = _Slow({"/references": _Resp({"data": []}),
                    "api.crossref.org": _Resp({"message": {"items": []}}),
                    "semanticscholar": _Resp({"data": []}),
                    "openalex": _Resp({"results": []})})
    refs = [_ref(str(i), f"Some Distinct Paper Title Number {i}", authors=["A B"])
            for i in range(8)]

    cite_resolve.fill(refs, paper_doi="10.1/x", client=client, workers=4)

    assert peak > 1  # 정말 겹쳐서 나갔다


def test_entries_that_already_have_a_link_are_left_alone():
    client = _Client({"/references": _Resp({"data": [
        _cited("Attention Is All You Need", 2017, doi="10.WRONG/1")]})})
    refs = [_ref("1", "Attention Is All You Need", doi="10.5555/3295222")]

    counts = cite_resolve.fill(refs, paper_doi="10.1/x", client=client)

    assert counts["already"] == 1 and counts["filled"] == 0
    assert refs[0].doi == "10.5555/3295222"  # 원문에 인쇄된 DOI가 이긴다


def test_only_empty_fields_are_filled_never_corrected():
    """참고문헌은 '저자가 그렇게 인용했다는 사실'이다 — 제목·연도를 갈아치우면 원문과 어긋난다."""
    client = _Client({"/references": _Resp({"data": [
        _cited("Attention Is All You Need", 2017, doi="10.5555/3295222",
               authors=["Ashish Vaswani"])]})})
    refs = [_ref("1", "Attention Is All You Need", authors=["Ashish Vaswani"], year=1999,
                 venue="원문에 적힌 학회")]

    cite_resolve.fill(refs, paper_doi="10.1/x", client=client)

    assert refs[0].year == 1999 and refs[0].venue == "원문에 적힌 학회"  # 그대로
    assert refs[0].doi == "10.5555/3295222"  # 비어 있던 것만 채워졌다


def test_a_wrong_paper_is_not_adopted_from_the_bulk_list():
    """한 번에 받아 온 목록도 게이트를 지난다 — 제목이 비슷하다고 같은 논문이 아니다."""
    client = _Client({"/references": _Resp({"data": [
        _cited("Attention Is Not All You Need Anyway", 2021, doi="10.WRONG/1",
               authors=["Someone Else"])]})})
    refs = [_ref("1", "Attention Is All You Need", authors=["Ashish Vaswani"], year=2017)]

    counts = cite_resolve.fill(refs, paper_doi="10.1/x", client=client)

    assert counts["filled"] == 0 and not refs[0].doi


def test_the_one_by_one_pass_is_capped_and_says_how_many_it_skipped():
    """서베이 논문은 참고문헌이 수백 개다 — 조용히 자르지 않고 몇 건을 못 봤는지 알린다."""
    client = _Client({"/references": _Resp({"data": []}),
                      "api.crossref.org": _Resp({"message": {"items": []}}),
                      "openalex": _Resp({"results": []})})
    refs = [_ref(str(i), f"Some Distinct Paper Title Number {i}", authors=["A B"])
            for i in range(10)]

    counts = cite_resolve.fill(refs, paper_doi="10.1/x", client=client, max_lookups=3)

    assert counts["skipped"] == 7


def test_no_paper_doi_means_no_bulk_call_but_the_one_by_one_pass_still_runs():
    client = _Client({"api.crossref.org": _Resp({"message": {"items": [{
        "title": ["Some Distinct Paper Title Here"], "issued": {"date-parts": [[2020]]},
        "container-title": ["A Journal"], "DOI": "10.1/found", "type": "journal-article",
        "author": [{"given": "A", "family": "B"}]}]}})})
    refs = [_ref("1", "Some Distinct Paper Title Here", authors=["A B"], year=2020)]

    counts = cite_resolve.fill(refs, paper_doi="", client=client)

    assert not any("/references" in c for c in client.calls)
    assert counts["single"] == 1 and refs[0].doi == "10.1/found"


def _rendered_md(n: int) -> str:
    """cite 단계가 이미 렌더해 둔 참고문헌 목록이 든 마크다운."""
    body = ["# Paper", "", "Some body citing [1].", "", "# References", ""]
    for i in range(1, n + 1):
        body += [f'<a id="ref-{i}"></a>**[{i}]** Author, A.. Title {i}. Venue {i} 2020', ""]
    return "\n".join(body) + "\n"


def test_relisting_replaces_the_list_instead_of_appending_to_it(tmp_path):
    """실측 사고 — 줄 번호로 구역을 찾으면 목록 뒷부분이 남아 참고문헌이 109개→159개가 됐다."""
    wd = WorkDir(tmp_path / "p" / "x.md4")
    wd.ensure()
    wd.en_md.parent.mkdir(parents=True, exist_ok=True)
    wd.en_md.write_text(_rendered_md(5), encoding="utf-8")
    refs = [_ref(str(i), f"Title {i}", doi=f"10.1/{i}") for i in range(1, 6)]

    assert cite_resolve.relist(wd, refs) is True
    text = wd.en_md.read_text(encoding="utf-8")

    anchors = re.findall(r'<a id="ref-(\d+)"></a>', text)
    assert len(anchors) == 5 and len(set(anchors)) == 5  # 늘지도, 중복되지도 않는다
    assert "Some body citing [1]." in text  # 본문은 그대로
    assert text.count("# References") == 1


def test_relisting_does_nothing_when_no_list_has_been_rendered_yet(tmp_path):
    wd = WorkDir(tmp_path / "p" / "x.md4")
    wd.ensure()
    wd.en_md.parent.mkdir(parents=True, exist_ok=True)
    wd.en_md.write_text("# Paper\n\nNo references section here.\n", encoding="utf-8")

    assert cite_resolve.relist(wd, [_ref("1", "T")]) is False
    assert wd.en_md.read_text(encoding="utf-8") == "# Paper\n\nNo references section here.\n"


def test_update_workdir_writes_back_and_leaves_a_paperless_workdir_alone(tmp_path):
    wd = WorkDir(tmp_path / "2017_Attention_Vaswani" / "x.md4")
    wd.ensure()
    assert cite_resolve.update_workdir(wd, client=_Client({}))["total"] == 0  # cite 전 — 조용히

    wd.cite.mkdir(parents=True, exist_ok=True)
    wd.references_json.write_text(json.dumps({"accepted": [
        {"label": "1", "title": "Attention Is All You Need", "authors": ["Ashish Vaswani"],
         "year": 2017, "raw": "..."}], "rejected": []}), encoding="utf-8")
    # 논문 자기 DOI는 앞 단계(§bibsource)가 받아 둔다 — 그게 있어야 참고문헌을 한 번에 받아 온다
    wd.bib_source_json.write_text(json.dumps({"title": "T", "doi": "10.1/paper"}), encoding="utf-8")
    client = _Client({"/references": _Resp({"data": [
        _cited("Attention Is All You Need", 2017, doi="10.5555/3295222",
               authors=["Ashish Vaswani"])]})})

    counts = cite_resolve.update_workdir(wd, client=client)

    assert counts["filled"] == 1
    back = json.loads(wd.references_json.read_text(encoding="utf-8"))
    assert back["accepted"][0]["doi"] == "10.5555/3295222"
    assert back["rejected"] == []  # 나머지 구조는 그대로 둔다
