"""서지 보강 — 제목 유사도 게이트가 오매치를 막는지가 핵심 (네트워크 없이 가짜 클라이언트로)."""

import pytest

from md4paper import enrich

TITLE = "Designing a Meta-Reflective Dashboard for Instructor Insight into Student-AI Interactions"


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class FakeClient:
    """url별 응답을 미리 넣어 두는 httpx.Client 대역. 호출된 url을 기록한다."""

    def __init__(self, openalex=None, crossref=None, fail=()):
        self.openalex, self.crossref, self.fail = openalex, crossref, fail
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):  # noqa: ANN001
        self.calls.append(url)
        if url in self.fail:
            raise RuntimeError("네트워크 오류")
        return FakeResp(self.openalex if url == enrich.OPENALEX_URL else self.crossref)


def _oa(title, year=2026, venue="arXiv (Cornell University)", vtype="repository"):
    return {"results": [{
        "display_name": title, "publication_year": year, "doi": "https://doi.org/10.5555/x",
        "primary_location": {"source": {"display_name": venue, "type": vtype}}, "locations": [],
    }]}


def _cr(title, year=2010, venue="Some Book"):
    return {"message": {"items": [{
        "title": [title], "issued": {"date-parts": [[year]]},
        "container-title": [venue], "DOI": "10.1000/y",
    }]}}


def test_similarity_gate_rejects_wrong_paper():
    """Crossref는 유사도와 무관하게 1등을 돌려준다 — 다른 논문이면 반드시 기각해야 한다.

    실측 사례: 이 제목으로 물으면 2010년 책 '8. Instructor as Reflective Practitioner'가 온다.
    """
    client = FakeClient(openalex={"results": []},
                        crossref=_cr("8. Instructor as Reflective Practitioner", 2010))
    assert enrich.lookup(TITLE, client=client) is None


def test_accepts_matching_title():
    client = FakeClient(openalex=_oa(TITLE, 2026, "arXiv", "repository"),
                        crossref={"message": {"items": []}})
    got = enrich.lookup(TITLE, client=client)
    assert got["year"] == 2026 and got["source"] == "openalex"


def test_repository_venue_upgraded_via_crossref():
    """OpenAlex가 arXiv만 주면 출판 venue를 Crossref에서 보강한다 (제목이 맞을 때만)."""
    client = FakeClient(openalex=_oa(TITLE, 2024, "arXiv (Cornell University)", "repository"),
                        crossref=_cr(TITLE, 2024, "Proceedings of the CHI Conference"))
    got = enrich.lookup(TITLE, client=client)
    assert got["venue"] == "Proceedings of the CHI Conference"
    assert got["source"] == "openalex+crossref" and got["year"] == 2024


def test_published_venue_skips_crossref():
    client = FakeClient(openalex=_oa(TITLE, 1986, "Psychological Bulletin", "journal"),
                        crossref=_cr(TITLE, 1985, "wrong"))
    got = enrich.lookup(TITLE, client=client)
    assert got["venue"] == "Psychological Bulletin" and got["year"] == 1986
    assert enrich.CROSSREF_URL not in client.calls  # 출판 venue를 이미 얻었으면 더 묻지 않는다


def test_network_failure_is_silent():
    client = FakeClient(fail=(enrich.OPENALEX_URL, enrich.CROSSREF_URL))
    assert enrich.lookup(TITLE, client=client) is None


@pytest.mark.parametrize("bad", ["LLM", "1 Introduction", "Introduction", "3. Related Work",
                                 "Conclusion", "Abstract", "References"])
def test_unusable_titles_are_not_queried(bad):
    """제목 추출이 실패해 섹션 헤딩이 들어오면 조회 자체를 막는다.

    유사도 게이트는 '우리 제목이 맞다'는 전제 위에서만 동작한다 — 실측 사고: 제목이
    "1 Introduction"이던 논문이 Crossref의 "Introduction"(유사도 0.92)과 붙어
    엉뚱한 언어학 저널이 venue로 들어갔다.
    """
    client = FakeClient(openalex=_oa("Introduction", 2011, "ENERGEIA", "journal"))
    assert enrich.lookup(bad, client=client) is None
    assert not client.calls  # 네트워크를 쓰지도 않는다


def test_real_titles_are_usable():
    for good in ["Attention Is All You Need", "Deep Residual Learning",
                 "GEPA: Reflective Prompt Evolution Can Outperform RL",
                 "Introduction to Statistical Learning with Applications"]:
        assert enrich.usable_title(good), good


@pytest.mark.parametrize("venue", ["Journal Title", "Conference acronym 'XX", "Woodstock '18",
                                   "XX(X)", "  ", "preprint"])
def test_template_placeholders_are_not_venues(venue):
    assert enrich.clean_venue(venue) == ""


def test_real_venue_kept():
    assert enrich.clean_venue(" Psychological  Bulletin, ") == "Psychological Bulletin"


def test_enrich_meta_fills_only_missing():
    """PDF에서 읽은 값이 진실원 — 이미 있는 필드는 절대 덮어쓰지 않는다."""
    client = FakeClient(openalex=_oa(TITLE, 2026, "Nature", "journal"))
    meta = {"title": TITLE, "year": 1999, "venue": ""}  # 연도는 이미 있음
    out, filled = enrich.enrich_meta(meta, client=client)
    assert filled == ["venue"]
    assert out["year"] == 1999 and out["venue"] == "Nature"
    assert out["meta_source"] == "openalex"


def test_enrich_meta_treats_placeholder_venue_as_missing():
    client = FakeClient(openalex=_oa(TITLE, 2026, "Nature", "journal"))
    out, filled = enrich.enrich_meta({"title": TITLE, "year": 2026, "venue": "Journal Title"},
                                     client=client)
    assert filled == ["venue"] and out["venue"] == "Nature"


def test_enrich_meta_noop_when_complete():
    client = FakeClient(openalex=_oa(TITLE))
    meta = {"title": TITLE, "year": 2024, "venue": "CHI"}
    assert enrich.enrich_meta(meta, client=client) == (meta, [])
    assert not client.calls  # 채울 게 없으면 네트워크를 쓰지 않는다


def test_enrich_workdir_saves(tmp_path):
    from md4paper import paper_meta
    from md4paper.ir import PaperMeta
    from md4paper.workdir import WorkDir

    wd = WorkDir(tmp_path / "p.md4")
    wd.root.mkdir(parents=True)
    paper_meta.save(wd, PaperMeta(title=TITLE, short_title="MetaReflective", authors=["Boxuan Ma"]))
    client = FakeClient(openalex=_oa(TITLE, 2026, "Nature", "journal"))

    assert sorted(enrich.enrich_workdir(wd, client=client)) == ["venue", "year"]
    saved = paper_meta.load(wd)
    assert saved["year"] == 2026 and saved["venue"] == "Nature"
    assert saved["meta_source"] == "openalex" and saved["doi"] == "10.5555/x"
    # 보강 후 이름 규칙에 연도가 들어간다
    assert paper_meta.folder_base(saved) == "2026_MetaReflective_Ma"
