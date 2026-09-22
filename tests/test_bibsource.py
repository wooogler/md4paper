"""논문 API에서 받아 오는 서지 원본 — 신원 확인 게이트와 출판 기록 반영.

네트워크는 쓰지 않는다. `bibsource`의 조회 함수는 모두 `client=`를 받으므로 응답을 흉내 내는
가짜 클라이언트를 넣는다(enrich와 같은 규율).

여기서 확인하는 것은 두 가지다: **엉뚱한 논문을 받아들이지 않는가**(게이트), 그리고
**받은 출판 기록이 PDF에서 읽은 값을 이기는가**(연도·학회·종류).
"""

import json

import pytest

from md4paper import bibsource, bibtex, library, projects
from md4paper.workdir import WorkDir

# 실측 사례 — 단어 하나(Saudi)만 다른 논문. 유사도 0.9595로 0.90 게이트를 통과한다.
_OURS = "The Effectiveness of Using Grammarly to Improve Students' Writing Skills"
_IMPOSTOR = "The Effectiveness of Using Grammarly to Improve Saudi Students' Writing Skills"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """재시도 대기를 없앤다 — 여기서 확인하는 건 '실패하면 다음 출처로 간다'이지 대기 시간이 아니다."""
    monkeypatch.setattr(bibsource, "_BACKOFF", ())
    bibsource.reset_sources()  # 쉬는 출처는 프로세스 전역이라 테스트마다 되살린다


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text or ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """URL 조각 → 응답. 부른 URL을 기록해 '어디까지 물어봤는지'도 볼 수 있게 한다."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None, headers=None):  # noqa: ANN001, ARG002
        self.calls.append(url)
        for fragment, resp in self.routes.items():
            if fragment in url:
                return resp(self) if callable(resp) else resp
        return _Resp({}, status=404)


def _s2(title, **extra):
    """Semantic Scholar `search/match` 응답 한 건."""
    paper = {"title": title, "year": extra.get("year"), "externalIds": extra.get("ids", {}),
             "authors": [{"name": n} for n in extra.get("authors", [])],
             "publicationVenue": extra.get("venue_obj", {}), "journal": extra.get("journal", {})}
    return _Resp({"data": [paper]})


def _crossref_items(*items):
    return _Resp({"message": {"items": list(items)}})


def _item(title, year, venue, doi, authors=(), kind="journal-article"):
    return {"title": [title], "issued": {"date-parts": [[year]]}, "container-title": [venue],
            "DOI": doi, "type": kind,
            "author": [{"given": a.split()[0], "family": a.split()[-1]} for a in authors]}


# --- 신원 확인 게이트 -------------------------------------------------------


def test_a_one_word_difference_is_a_different_paper():
    """실측 사고 — 유사도만 보면 0.9595로 통과한다. 저자·연도로 교차 확인해야 걸린다."""
    impostor = {"title": _IMPOSTOR, "authors": ["Fahad Alshahrani"], "year": 2024}
    assert bibsource.matches(_OURS, impostor, authors=["Hui-Wen Huang"], year=2020) is False
    # 저자가 겹치면 같은 저자의 후속 논문일 수 있다 → 받아들인다
    assert bibsource.matches(_OURS, {**impostor, "authors": ["Hui-Wen Huang"]},
                             authors=["Hui-Wen Huang"], year=2020) is True


def test_extraction_typos_still_match():
    """추출이 ff를 흘려 'Efect'가 되어도 정본을 찾아야 한다 (유사도 0.99대)."""
    real = {"title": "The HaLLMark Effect: Supporting Provenance and Transparent Use of "
                     "Large Language Models in Writing with Interactive Visualization"}
    ours = real["title"].replace("Effect", "Efect")
    assert bibsource.matches(ours, real) is True  # 저자·연도 없이도 제목만으로 확정


def test_case_and_punctuation_do_not_matter():
    """대문자로만 추출된 제목도 같은 논문이다 (enrich.similarity가 정규화한다)."""
    assert bibsource.matches("WHO BELONGS IN THE FAMILY?", {"title": "Who Belongs in the Family?"})


def test_a_publisher_record_without_the_subtitle_is_rescued_by_the_authors():
    """실측 — 출판사 기록에 부제가 없어 유사도 0.56. 저자가 겹치면 같은 논문으로 본다."""
    ours = "Keystroke Logging in Writing Research: Using Inputlog to Analyze and Visualize " \
           "Writing Processes"
    short = {"title": "Keystroke Logging in Writing Research", "authors": ["Marielle Leijten"]}
    assert bibsource.matches(ours, short, authors=["Mariëlle Leijten"]) is True
    # 저자가 안 겹치면 부제만 보고 같은 논문이라 할 수 없다
    assert bibsource.matches(ours, short, authors=["Someone Else"]) is False


def test_a_short_shared_prefix_is_not_enough():
    """짧은 제목의 앞부분 일치는 우연일 수 있다 — 길이 하한이 있다."""
    assert bibsource.matches("Cognitive Offloading and More Things",
                             {"title": "Cognitive", "authors": ["Evan Risko"]},
                             authors=["Evan Risko"]) is False


@pytest.mark.parametrize("call", [bibsource.resolve, bibsource.fetch])
def test_an_unusable_title_is_not_queried_at_all(call):
    """추출이 실패해 제목이 섹션 헤딩이면 조회 자체를 하지 않는다 (enrich와 같은 방어).

    `fetch`도 함께 확인한다 — 실제로 논문 폴더가 부르는 건 이쪽이라, 여기서 게이트를 건너뛰면
    방어가 사실상 없는 것과 같다.
    """
    client = _Client({})
    assert call("1 Introduction", client=client) is None
    assert call("Related Work", client=client) is None
    # 두 단어 제목은 저자가 있어야 조회한다 (짧은 제목은 우연히 겹치기 쉽다)
    assert call("Cognitive Offloading", client=client) is None
    assert client.calls == []


# --- 출처 사다리 -----------------------------------------------------------


def test_semantic_scholar_answers_first_and_crossref_is_not_asked():
    client = _Client({"semanticscholar": _s2("Attention Is All You Need", year=2017,
                                             ids={"DOI": "10.5555/3295222"},
                                             venue_obj={"type": "conference", "name": "NeurIPS"},
                                             journal={"name": "Advances in NeurIPS"})})
    got = bibsource.resolve("Attention Is All You Need", client=client)

    assert got["doi"] == "10.5555/3295222"
    assert got["kind"] == "inproceedings"  # publicationVenue.type이 종류를 정한다
    assert got["venue"] == "Advances in NeurIPS"  # 총서명이 아니라 journal.name이 booktitle 감
    assert got["source"] == "semanticscholar"
    assert not any("crossref" in c for c in client.calls)


def test_crossref_picks_the_best_candidate_not_the_first():
    """실측 — Crossref 1위가 오답이고 2위가 정답인 경우가 있다. 순위가 아니라 유사도로 고른다."""
    client = _Client({
        "semanticscholar": _Resp({"data": []}),
        "api.crossref.org": _crossref_items(
            _item(_IMPOSTOR, 2024, "Journal of Research", "10.wrong/1", ["Fahad Alshahrani"]),
            _item(_OURS, 2020, "ICDEL 2020", "10.1145/3402569.3402594", ["Hui-Wen Huang"]),
        ),
    })
    got = bibsource.resolve(_OURS, authors=["Hui-Wen Huang"], year=2020, client=client)

    assert got["doi"] == "10.1145/3402569.3402594"
    assert got["year"] == 2020


def test_nothing_is_adopted_when_no_candidate_is_the_same_paper():
    """실측 — Crossref는 어떤 제목에도 결과를 준다. 틀린 서지가 빈 서지보다 나쁘다."""
    client = _Client({
        "semanticscholar": _Resp({"data": []}),
        "api.crossref.org": _crossref_items(
            _item("World Chronicle or World Formula", 2022, "Cornell", "10.junk/1")),
        "openalex": _Resp({"results": []}),
    })
    assert bibsource.resolve("A Formula for Predicting Readability: Instructions",
                             client=client) is None


def test_a_dead_source_falls_through_to_the_next():
    client = _Client({
        "semanticscholar": _Resp({}, status=500),
        "api.crossref.org": _crossref_items(
            _item("Cognitive Offloading", 2016, "Trends in Cognitive Sciences",
                  "10.1016/j.tics.2016.07.002", ["Evan Risko"])),
    })
    got = bibsource.resolve("Cognitive Offloading", authors=["Evan F. Risko"], year=2016,
                            client=client)

    assert got["source"] == "crossref" and got["year"] == 2016


def test_a_reprint_does_not_beat_the_edition_we_actually_have():
    """실측 — Emig 1977(CCC)의 2020년 Routledge 선집 재수록본이 제목·저자가 똑같이 함께 나온다."""
    client = _Client({
        "semanticscholar": _Resp({"data": []}),
        "api.crossref.org": _crossref_items(
            _item("Writing as a Mode of Learning", 2020, "Landmark Essays",
                  "10.4324/9781003059219-10", ["Janet Emig"], kind="book-chapter"),
            _item("Writing as a Mode of Learning", 1977, "College Composition and Communication",
                  "10.2307/356095", ["Janet Emig"]),
        ),
    })
    got = bibsource.resolve("Writing as a Mode of Learning", authors=["Janet Emig"], year=1977,
                            client=client)

    assert got["year"] == 1977 and got["doi"] == "10.2307/356095"  # 우리가 가진 판본


def test_a_wrong_pdf_year_still_gets_corrected_when_there_is_only_one_candidate():
    """연도가 가까운 쪽을 '우선'할 뿐 멀다고 버리지 않는다 (Flower & Hayes: PDF 2008 → 1981)."""
    client = _Client({
        "semanticscholar": _Resp({"data": []}),
        "api.crossref.org": _crossref_items(
            _item("A Cognitive Process Theory of Writing", 1981,
                  "College Composition and Communication", "10.58680/ccc198115885",
                  ["Linda Flower"])),
    })
    got = bibsource.resolve("A Cognitive Process Theory of Writing", authors=["Linda Flower"],
                            year=2008, client=client)

    assert got["year"] == 1981


def test_a_conference_paper_without_a_crossref_record_stays_a_conference_paper():
    """실측 — 2025년 ACM 논문은 DOI가 아직 Crossref에 없다. 그렇다고 프리프린트가 아니다."""
    client = _Client({
        "semanticscholar": _s2("Writing with AI Lowers Psychological Ownership", year=2024,
                               ids={"DOI": "10.1145/3719160.3736608", "ArXiv": "2404.03108"},
                               authors=["Nikhita Joshi"],
                               venue_obj={"type": "conference"},
                               journal={"name": "Proceedings of the 7th ACM Conference on CUI"}),
        # Crossref에 아직 없다 → 404
    })
    got = bibsource.fetch("Writing with AI Lowers Psychological Ownership",
                          authors=["Nikhita Joshi"], client=client)

    assert got["kind"] == "inproceedings"  # @misc로 내려가지 않는다
    entry = bibtex.fields_from_meta(got, "k")
    assert entry["booktitle"] == "Proceedings of the 7th ACM Conference on CUI"
    assert "eprint" not in entry  # 학회가 확정됐으면 arXiv 번호는 붙이지 않는다


def test_a_genuine_preprint_still_becomes_a_misc_entry():
    client = _Client({
        "semanticscholar": _s2("Some Unpublished Work On Things", year=2025,
                               ids={"ArXiv": "2506.08872"}, authors=["A Person"],
                               venue_obj={}, journal={"name": "ArXiv"}),
    })
    got = bibsource.fetch("Some Unpublished Work On Things", authors=["A Person"], client=client)

    assert got["kind"] == "misc"
    assert bibtex.fields_from_meta(got, "k")["eprint"] == "2506.08872"


def test_a_retracted_paper_is_flagged_and_says_so_in_the_entry():
    """철회된 논문을 인용한 채 제출하는 일은 없어야 한다 (실측: NIRVANA .bib에 한 편 있었다)."""
    client = _Client({
        "semanticscholar": _s2("The effect of ChatGPT on students' learning performance",
                               year=2025, ids={"DOI": "10.1057/s41599-025-04787-y"},
                               authors=["Jin Wang"], venue_obj={"type": "journal"}),
        "transform/application/x-bibtex": _Resp(None, text=
            "@article{Wang_2025, title={RETRACTED ARTICLE: The effect of ChatGPT on students' "
            "learning performance}, journal={Humanities and Social Sciences Communications}, "
            "author={Wang, Jin}, year={2025}, DOI={10.1057/s41599-025-04787-y}}"),
    })
    got = bibsource.fetch("The effect of ChatGPT on students' learning performance",
                          authors=["Jin Wang"], client=client)

    assert got["retracted"] is True
    # 제목에서 지우지 않는다 — 참고문헌 목록에 찍혀야 눈에 띈다
    assert "RETRACTED" in bibtex.fields_from_meta(got, "k")["title"]


def test_an_ordinary_paper_is_not_flagged_as_retracted():
    got = bibsource.fetch("A Cognitive Process Theory of Writing", client=_flower_client())
    assert got["retracted"] is False


# --- 출판 기록이 이긴다 -----------------------------------------------------


_CROSSREF_BIB = """@article{Flower_1981,
  title={A Cognitive Process Theory of Writing},
  DOI={10.58680/ccc198115885},
  journal={College Composition &amp; Communication},
  publisher={National Council of Teachers of English},
  author={Flower, Linda and Hayes, John R.},
  year={1981}, month=Dec, volume={32}, number={4}, pages={365–387},
  url={http://dx.doi.org/10.58680/ccc198115885}, collection={CCC}}
"""


def _flower_client():
    return _Client({
        "semanticscholar": _s2("A Cognitive Process Theory of Writing", year=1981,
                               ids={"DOI": "10.58680/ccc198115885"},
                               authors=["L. Flower", "J. Hayes"],
                               venue_obj={"type": "journal"}),
        "transform/application/x-bibtex": _Resp(None, text=_CROSSREF_BIB),
    })


def test_the_publisher_record_overrides_what_we_read_from_the_pdf():
    """PDF에서 2008로 읽은 논문이 실제로는 1981년 것이다 (실측 — NIRVANA references.bib)."""
    got = bibsource.fetch("A Cognitive Process Theory of Writing",
                          authors=["Linda Flower"], year=2008, client=_flower_client())

    assert got["year"] == 1981  # 우리가 넘긴 2008이 아니라 출판사 기록의 연도
    assert got["volume"] == "32" and got["number"] == "4"
    # 받은 기록은 원문 그대로 둔다 — 엔대시→`--` 같은 BibTeX 관례 변환은 항목을 만들 때 한 번만
    # 한다(`bibtex._escape`). 여기서도 바꾸면 두 군데서 손대게 된다.
    assert got["pages"] == "365–387"
    assert got["publisher"] == "National Council of Teachers of English"
    assert got["venue"] == "College Composition & Communication"  # HTML 엔티티가 풀렸다
    assert got["meta_source"] == "semanticscholar+crossref"


def test_the_fetched_record_renders_into_a_verified_entry():
    record = bibsource.fetch("A Cognitive Process Theory of Writing", client=_flower_client())
    fields = bibtex.fields_from_meta(record, "1981_CognitiveProcess_Flower")
    text = bibtex.render(fields)

    assert bibtex.verify(text, fields) == (True, "확인됨")
    assert text.startswith("@article{1981_CognitiveProcess_Flower,")
    assert r"College Composition \& Communication" in text  # 엔티티는 풀고 LaTeX로 이스케이프
    assert "pages" in text and "365--387" in text  # 엔대시가 BibTeX 관례로 바뀌었다
    assert "url" not in text and "collection" not in text  # doi가 있으니 버리는 필드들


def test_a_preprint_keeps_its_arxiv_number_and_a_published_paper_does_not():
    """실린 논문에 arXiv 번호까지 달면 프리프린트를 인용한 것처럼 보인다."""
    preprint = bibtex.fields_from_meta(
        {"title": "T", "kind": "misc", "arxiv": "2506.08872", "year": 2025}, "k")
    assert preprint["eprint"] == "2506.08872" and preprint["archiveprefix"] == "arXiv"

    published = bibtex.fields_from_meta(
        {"title": "T", "kind": "inproceedings", "arxiv": "2311.13057", "year": 2024,
         "venue": "Proceedings of the CHI Conference"}, "k")
    assert "eprint" not in published and published["booktitle"] == "Proceedings of the CHI Conference"


def test_the_publisher_wins_unless_it_abbreviated_a_given_name():
    """출판사 기록이 대체로 더 정확하다 — 이름이 이니셜로 줄어든 때만 PDF 표기로 되돌린다."""
    # 이름이 줄었다 → PDF 표기를 되살린다
    shortened = {"authors": ["Ye Xiong", "Y. Wu"], "author_latex": "Xiong, Ye and Wu, Y."}
    got = bibsource.prefer_fuller_authors(shortened, ["Ye Xiong", "Yi-Fang Brook Wu"])
    assert got["authors"] == ["Ye Xiong", "Yi-Fang Brook Wu"]
    assert "author_latex" not in got  # 우리 이름으로 다시 접게 한다

    # 가운데 이름 이니셜은 줄임이 아니다 — 'Cecilia D.'가 'Cecilia'보다 자세하다.
    # 게다가 출판사는 PDF의 오타('Tasfa')까지 바로잡아 준다(실측: 정본은 'Tasfia').
    better = {"authors": ["Tasfia Mashiat", "Cecilia D. Shelton"],
              "author_latex": "Mashiat, Tasfia and Shelton, Cecilia D."}
    kept = bibsource.prefer_fuller_authors(better, ["Tasfa Mashiat", "Cecilia Shelton"])
    assert kept["authors"] == ["Tasfia Mashiat", "Cecilia D. Shelton"]
    assert kept["author_latex"] == "Mashiat, Tasfia and Shelton, Cecilia D."

    # 저자 구성이 다르면(편수가 다르거나 다른 사람) 출판사 기록을 믿는다
    assert bibsource.prefer_fuller_authors(
        {"authors": ["A. Kim"]}, ["Ara Kim", "Bo Lee"])["authors"] == ["A. Kim"]


def test_publisher_author_strings_are_not_refolded():
    """'van der Berg, Jan'을 이름 성으로 폈다 접으면 'Berg, Jan van der'가 되어 저자가 바뀐다."""
    fields = bibtex.fields_from_meta(
        {"title": "T", "author_latex": "van der Berg, Jan and Waes, Luuk Van"}, "k")
    assert fields["author"] == "van der Berg, Jan and Waes, Luuk Van"


# --- 논문 폴더에 붙이기 -----------------------------------------------------


@pytest.fixture()
def wd(tmp_path):
    work = WorkDir(tmp_path / "1981_CognitiveProcess_Flower" / "x.md4")
    work.ensure()
    work.paper_meta_json.write_text(json.dumps(
        {"title": "A Cognitive Process Theory of Writing", "authors": ["Linda Flower"],
         "year": 2008, "venue": ""}, ensure_ascii=False), encoding="utf-8")
    return work


def test_the_record_is_cached_and_not_fetched_twice(wd):
    client = _flower_client()
    first = bibsource.update_workdir(wd, client=client)
    calls = len(client.calls)
    second = bibsource.update_workdir(wd, client=client)

    assert first["year"] == 1981 and second == first
    assert len(client.calls) == calls  # 두 번째는 네트워크를 쓰지 않는다
    assert bibsource.load(wd)["year"] == 1981
    assert wd.bib_source_json.exists()


def test_entry_for_prefers_the_fetched_record_but_never_goes_online(wd):
    """내보내기는 언제나 오프라인이다 — 저장된 것만 읽는다."""
    before = bibtex.entry_for(wd)[1]
    assert "2008" in before and "journal" not in before  # 아직 PDF에서 읽은 값

    bibsource.update_workdir(wd, client=_flower_client())
    after = bibtex.entry_for(wd)[1]

    assert "1981" in after and "2008" not in after
    assert "College Composition" in after and "365--387" in after


def test_a_paper_we_cannot_resolve_keeps_the_pdf_values(wd):
    client = _Client({"semanticscholar": _Resp({"data": []}),
                      "api.crossref.org": _crossref_items(),
                      "openalex": _Resp({"results": []})})

    assert bibsource.update_workdir(wd, client=client) is None
    assert not wd.bib_source_json.exists()  # 못 찾았으면 아무것도 캐시하지 않는다
    assert "2008" in bibtex.entry_for(wd)[1]  # 원래 값 그대로 (빈 항목이 되지 않는다)


def test_a_broken_cache_is_ignored_rather_than_crashing(wd):
    wd.bib_source_json.write_text("{ not json", encoding="utf-8")
    assert bibsource.load(wd) is None
    assert bibtex.entry_for(wd)[1].count("2008") == 1  # PDF 값으로 조용히 되돌아간다


# --- 보강 범위와 중지 -------------------------------------------------------


def test_enrichment_is_scoped_to_the_selected_project(tmp_path, monkeypatch):
    """한 번에 한 묶음을 손본다 — 전체를 훑으면 예전 프로젝트까지 API를 두드리게 된다."""
    from md4paper import enrich, projects

    ws = tmp_path / "ws"
    mine = projects.create("지금 쓰는 것")["id"]
    other = projects.create("예전 것")["id"]
    for name, pid in (("a", mine), ("b", other), ("c", None)):
        w = WorkDir(ws / name / f"{name}.md4")
        w.ensure()
        w.sections_yaml.write_text("title: t\n", encoding="utf-8")  # 목록에 잡히는 조건
        w.save_status({"project": pid} if pid else {})
    projects.set_active(mine)

    picked = [p.parent.name for p in enrich.project_roots(workspace=ws)]
    assert picked == ["a"]  # 다른 프로젝트도, 미분류도 아니다
    assert sorted(p.parent.name for p in enrich.project_roots("", ws)) == ["c"]  # 미분류
    assert sorted(p.parent.name for p in enrich.project_roots(enrich.ALL, ws)) == ["a", "b", "c"]


def test_stopping_happens_between_papers_and_keeps_what_was_done(tmp_path, monkeypatch):
    """논문 하나의 중간에서 끊으면 반쯤 보강된 논문이 남는다 — 경계에서만 멈춘다."""
    from md4paper import enrich

    seen = []

    def fake_boost(wd, **kw):  # noqa: ANN001, ARG001
        seen.append(wd.root.parent.name)
        return {"fields": ["year"], "record": True, "refs": {}, "retracted": False}

    monkeypatch.setattr(enrich, "boost_workdir", fake_boost)
    roots = []
    for name in ("a", "b", "c"):
        w = WorkDir(tmp_path / name / f"{name}.md4")
        w.ensure()
        roots.append(w.root)

    counts = enrich.enrich_many(roots, should_stop=lambda: len(seen) >= 2)

    assert seen == ["a", "b"] and counts["stopped"] is True
    assert counts["checked"] == 2 and counts["total"] == 3
    assert counts["papers"] == 2  # 멈추기 전까지 한 것은 그대로 집계된다


# --- 레이트리밋에 걸린 출처는 쉬게 한다 -------------------------------------

_BUDGET_429 = ('{"error":"Rate limit exceeded","message":"Insufficient budget. This request '
               'costs $0.001 but you only have $0.0008 remaining. Resets at midnight UTC."}')


def test_a_hopeless_rate_limit_is_not_retried():
    """실측 — OpenAlex가 종량제로 바뀌었다. 예산이 떨어지면 기다려도 오늘은 안 풀린다."""
    client = _Client({"openalex": _Resp({}, status=429, text=_BUDGET_429)})
    with pytest.raises(bibsource.RateLimited):
        bibsource._get(client, bibsource.OPENALEX_URL, {})

    assert len(client.calls) == 1  # 백오프로 14초를 태우지 않는다


def test_a_rate_limited_source_is_skipped_for_the_rest_of_the_run():
    """같은 벽에 참고문헌 수천 건만큼 부딪히면 몇 시간이 날아간다."""
    client = _Client({"semanticscholar": _Resp({"data": []}),
                      "api.crossref.org": _crossref_items(),
                      "openalex": _Resp({}, status=429, text=_BUDGET_429)})
    title = "Some Title That Will Not Be Found Anywhere"

    bibsource.resolve(title, authors=["A B"], client=client)
    assert bibsource.source_down("openalex") is True
    calls_before = sum(1 for c in client.calls if "openalex" in c)

    bibsource.resolve(title, authors=["A B"], client=client)  # 두 번째 조회
    assert sum(1 for c in client.calls if "openalex" in c) == calls_before  # 더 안 부른다
    assert any("crossref" in c for c in client.calls)  # 다른 출처는 계속 쓴다


def test_a_source_that_is_down_does_not_block_the_others():
    bibsource.trip_source("crossref")
    client = _Client({"semanticscholar": _s2("Attention Is All You Need", year=2017,
                                             ids={"DOI": "10.5555/3295222"},
                                             venue_obj={"type": "conference"})})

    got = bibsource.resolve("Attention Is All You Need", client=client)

    assert got["doi"] == "10.5555/3295222"
    assert not any("crossref" in c for c in client.calls)


# --- 변환 파이프라인 훅 -----------------------------------------------------


def test_bib_lookup_is_on_by_default_and_can_be_set_per_project():
    """변환 직후 보강은 기본 켜짐 — 꺼 두면 .bib이 부실한 걸 알아채기 어렵다."""
    from md4paper import config, projects

    assert config.resolve_bib_lookup() is True
    pid = projects.create("느린 회선")["id"]
    assert config.resolve_bib_lookup(pid) is True  # 안 정하면 전역을 따른다

    projects.set_setting(pid, "bib_lookup", False)
    assert config.resolve_bib_lookup(pid) is False  # 이 프로젝트만 끈다
    assert config.resolve_bib_lookup() is True  # 전역은 그대로

    config.set_section_value("library", "bib_lookup", False)
    projects.set_setting(pid, "bib_lookup", True)
    assert config.resolve_bib_lookup() is False  # 전역 끔
    assert config.resolve_bib_lookup(pid) is True  # 프로젝트가 전역을 이긴다


def test_the_convert_hook_runs_before_the_bib_is_written(tmp_path, monkeypatch):
    """훅이 auto_export보다 먼저 돌아야 논문을 올린 그 시점의 .bib이 이미 정확하다."""
    from md4paper.ui import app as ui_app

    root = tmp_path / "Vault"
    pid = projects.create("서베이", str(root))["id"]
    work = WorkDir(tmp_path / "1981_CognitiveProcess_Flower" / "x.md4")
    work.ensure()
    work.en_md.write_text("# T\n", encoding="utf-8")
    work.paper_meta_json.write_text(json.dumps(
        {"title": "A Cognitive Process Theory of Writing", "authors": ["Linda Flower"],
         "year": 2008, "venue": ""}, ensure_ascii=False), encoding="utf-8")
    work.save_status({"project": pid})

    # boost_workdir 자리에 가짜 출판 기록을 놓는다 (네트워크 없이 순서만 확인)
    def fake_boost(wd, **kw):  # noqa: ANN001, ANN003, ARG001
        from md4paper import bibsource
        return bibsource.update_workdir(wd, client=_flower_client())

    monkeypatch.setattr("md4paper.enrich.boost_workdir", fake_boost)

    assert ui_app._auto_bibsource(work)["year"] == 1981
    library.auto_export(work)

    bib = (root / "references.bib").read_text(encoding="utf-8")
    assert "1981" in bib and "2008" not in bib  # 처음 쌓일 때부터 정확하다
    assert "College Composition" in bib and "365--387" in bib


def test_the_convert_hook_is_skipped_when_the_project_turned_it_off(tmp_path, monkeypatch):
    from md4paper.ui import app as ui_app

    pid = projects.create("느린 회선", str(tmp_path / "V"))["id"]
    projects.set_setting(pid, "bib_lookup", False)
    work = WorkDir(tmp_path / "p" / "x.md4")
    work.ensure()
    work.save_status({"project": pid})

    def boom(wd, **kw):  # noqa: ANN001, ANN003, ARG001
        raise AssertionError("꺼져 있는데 네트워크를 썼다")

    monkeypatch.setattr("md4paper.enrich.boost_workdir", boom)
    assert ui_app._auto_bibsource(work) == {}


def test_a_failing_lookup_never_fails_the_conversion(tmp_path, monkeypatch):
    """네트워크가 없어도 변환은 되어야 한다 — 훅은 조용히 넘어간다."""
    from md4paper.ui import app as ui_app

    work = WorkDir(tmp_path / "p" / "x.md4")
    work.ensure()

    def boom(wd, **kw):  # noqa: ANN001, ANN003, ARG001
        raise OSError("네트워크 없음")

    monkeypatch.setattr("md4paper.enrich.boost_workdir", boom)
    assert ui_app._auto_bibsource(work) == {}
