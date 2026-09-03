"""앞부분(front matter) 정규화 — LLM 라벨+재조립(FakeProvider) + 규칙 폴백 + 검증."""

from md4paper.extract import front_matter as fm
from md4paper.ir import FrontMatterLayout
from md4paper.llm import FakeProvider

# 다열 저자 그리드가 흩어지고 boilerplate가 낀 전형적 ACM 첫 페이지
SCATTERED = (
    "## An Empirical Study on Whatever\n\n"                                  # 0 title
    "## [Andrew Jelson](https://orcid.org/1)\n\n"                            # 1 author heading
    "Computer Science Virginia Tech Blacksburg, Virginia, USA jelson@vt.edu\n\n"  # 2 affil
    "## [Daniel Dunlap](https://orcid.org/2)\n\n"                           # 3 author heading
    "Computer Science Virginia Tech Blacksburg, Virginia, USA dunlapd@vt.edu\n\n"  # 4 affil
    "## Abstract\n\n"                                                        # 5 keep heading
    "The abstract body is real content and must be kept intact here.\n\n"    # 6 keep body
    "## ACM Reference Format:\n\n"                                           # 7 junk heading
    "Andrew Jelson et al. 2026. Whatever. In CHI 2026.\n\n"                  # 8 junk body
    "ACM\n\nISBN\n\n979-8-4007-2278-3/26/04\n\n"                            # 9,10,11 junk
    "Daniel Manesh Virginia Tech, USA danielmanesh@vt.edu "                  # 12 two authors flattened
    "Alice Jang Virginia Tech, USA ajjang@vt.edu\n\n"
    "## 1 Introduction\n\n"                                                  # 13 body start
    "Writing is fundamental and this is the real body.\n"                    # 14 body
)


def _fake(layout):
    return FakeProvider(parse_fn=lambda s, u, sc: layout, model="fake")


# --- 규칙 폴백 -------------------------------------------------------------
def test_heuristic_gathers_scattered_authors_and_drops_boilerplate():
    out = fm.normalize_heuristic(SCATTERED)
    front = out.split("## Abstract")[0]
    for who in ("Andrew Jelson", "Daniel Dunlap", "Daniel Manesh", "Alice Jang"):
        assert who in front, who
    assert "**[Andrew Jelson]" in front and "## [Andrew Jelson]" not in out  # 헤더 강등
    assert "979-8-4007" not in out and "## ACM Reference Format" not in out
    assert "The abstract body is real content" in out
    assert "## 1 Introduction" in out and "real body" in out


def test_heuristic_noop_on_clean_paper():
    md = ("## Clean Title\n\nJane Doe, University X, jane@x.edu\n\n"
          "## Abstract\n\nBody.\n\n## 1 Introduction\n\nIntro.\n")
    assert fm.normalize_heuristic(md) == md  # 파편화 아님


def test_heuristic_skips_without_body_section():
    md = "## Title\n\nJohn Roe, MIT, roe@mit.edu\n\nText.\n\nMore text.\n"
    assert fm.normalize_heuristic(md) == md


# --- LLM 라벨 + 재조립 -----------------------------------------------------
def test_llm_reassembles_from_original_blocks():
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3, 4, 12], sections=[5, 6], body_start=13)
    out = fm.normalize_llm(_fake(layout), SCATTERED)
    front = out.split("## 1 Introduction")[0]
    assert "**[Andrew Jelson](https://orcid.org/1)**" in front  # 헤더 저자 → 굵게
    # 뭉친 저자줄이 분리됨
    assert "Daniel Manesh Virginia Tech, USA danielmanesh@vt.edu" in front
    assert "Alice Jang Virginia Tech, USA ajjang@vt.edu" in front
    # 라벨 안 된 boilerplate는 제거
    assert "979-8-4007" not in out and "ACM Reference Format" not in out
    # 유지 섹션·본문 보존
    assert "The abstract body is real content" in out
    assert out.rstrip().endswith("real body.")


def test_llm_keeps_frontmatter_teaser_image():
    # 1페이지 상단 teaser 그림+캡션은 sections에 라벨 안 돼도 버리지 않고 유지 (콘텐츠)
    md = (
        "## Paper Title\n\n"                             # 0 title
        "## [Alice](https://orcid.org/1)\n\n"            # 1 author
        "Uni, alice@x.edu\n\n"                           # 2 affil
        "![Image](img-01.png)\n\n"                       # 3 teaser image (라벨 안 됨)
        "Figure 1: The overview of the workflow.\n\n"    # 4 teaser caption (라벨 안 됨)
        "## Abstract\n\n"                                # 5 keep
        "The abstract body here is real content kept intact.\n\n"  # 6 keep
        "## 1 Introduction\n\n"                          # 7 body start
        "Real body text.\n"                              # 8
    )
    layout = FrontMatterLayout(title=0, authors=[1, 2], sections=[5, 6], body_start=7)
    out = fm.normalize_llm(_fake(layout), md)
    assert "![Image](img-01.png)" in out            # teaser 이미지 유지
    assert "Figure 1: The overview of the workflow." in out  # 캡션도 유지
    assert "The abstract body here is real content" in out
    assert out.rstrip().endswith("Real body text.")


def test_llm_body_is_verbatim_slice():
    layout = FrontMatterLayout(title=0, authors=[1, 2], sections=[5, 6], body_start=13)
    out = fm.normalize_llm(_fake(layout), SCATTERED)
    body = out.split("## 1 Introduction", 1)[1]
    assert body == "\n\nWriting is fundamental and this is the real body.\n"


# 2단 조판 첫 페이지에서 추출기가 좌우 단을 뒤집어 읽은 경우 — 오른쪽 단(초록 꼬리 + 본문 시작)이
# 먼저 나오고 왼쪽 단의 "Abstract"가 Introduction 뒤로 밀린다 (NIRVANA 첫 페이지 실패 재현).
SWAPPED = (
    "## Paper Title Spanning Two Lines\n\n"                                  # 0 title
    "## [Alice Kim](https://orcid.org/1)\n\n"                                # 1 author heading
    "Virginia Tech Blacksburg, Virginia, USA alice@vt.edu\n\n"               # 2 affil
    "Bob Park NAVER AI Lab Seongnam, South Korea bob@naver.com\n\n"          # 3 author + affil
    "naturalistic writing interactions, and (4) a replay tool. Together, "   # 4 초록 꼬리(문장 중간 시작)
    "these contributions enable future research.\n\n"
    "## ACM Reference Format:\n\n"                                           # 5 junk heading
    "Alice Kim and Bob Park. 2026. Paper Title. In . ACM.\n\n"               # 6 junk body
    "## 1 Introduction\n\n"                                                  # 7 body start
    "First intro paragraph carrying the real body text.\n\n"                 # 8 body
    "Second intro paragraph continuing the argument.\n\n"                    # 9 body
    "## Abstract\n\n"                                                        # 10 왼쪽 단 — 밀려남
    "We introduce a dataset of student writing. This work contributes: "     # 11 왼쪽 단 — 밀려남
    "(1) a publicly available dataset of\n\n"
    "## 2 Related Work\n\n"                                                  # 12 body
    "Related work body text.\n\n"                                            # 13 body
    "Another related work paragraph.\n"                                      # 14 body
)


def test_llm_pulls_frontmatter_back_when_columns_are_swapped():
    # 읽기 순서: Abstract 헤딩 → 초록 본문 → 초록 꼬리 조각 (본문 뒤로 밀린 10, 11을 앞으로)
    # 끌어올리기는 추출 단계가 '되돌리지 못한 단 뒤집힘'을 잰 논문에서만 켜진다(allow_pull).
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[10, 11, 4], body_start=7)
    out = fm.normalize_llm(_fake(layout), SWAPPED, allow_pull=True)
    front, body = out.split("## 1 Introduction", 1)
    assert "## Abstract" in front and "## Abstract" not in body   # 초록이 본문 앞으로
    assert front.index("We introduce a dataset") < front.index("naturalistic writing interactions")
    assert out.count("We introduce a dataset") == 1               # 본문 쪽에서는 제거(중복 없음)
    # 본문은 순서·내용 그대로, 초록이 중간에 끼지 않는다
    assert body.split("## 2 Related Work")[0].strip() == (
        "First intro paragraph carrying the real body text.\n\n"
        "Second intro paragraph continuing the argument.")
    assert "ACM Reference Format" not in out                      # boilerplate는 계속 제거
    assert out.rstrip().endswith("Another related work paragraph.")


def test_llm_leaves_body_prose_in_place_when_mislabelled():
    # LLM이 본문 문단(8, 9)까지 초록으로 잘못 지목해도, front matter 헤딩으로 시작하는 연속 구간이
    # 아니므로 앞으로 끌려오지 않고 본문 제자리에 남는다.
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[10, 11, 8, 9, 4], body_start=7)
    out = fm.normalize_llm(_fake(layout), SWAPPED, allow_pull=True)
    front, body = out.split("## 1 Introduction", 1)
    assert "First intro paragraph" not in front and "Second intro paragraph" not in front
    assert body.split("## 2 Related Work")[0].strip() == (
        "First intro paragraph carrying the real body text.\n\n"
        "Second intro paragraph continuing the argument.")
    assert "## Abstract" in front                                 # 진짜 front matter는 그대로 복구


def test_llm_rejects_layout_that_moves_body_prose():
    # 본문 시작 블록 자체를 옮기는 라벨은 여전히 거부 → 규칙 폴백
    bad = FrontMatterLayout(title=0, authors=[1, 2], sections=[7], body_start=7)
    assert fm.normalize_llm(_fake(bad), SWAPPED, allow_pull=True) is None


def test_llm_clamps_overreaching_sections_instead_of_rejecting():
    """지목이 과해도 레이아웃을 통째로 버리지 않는다 — 끌어올리기만 닻에서 잘린다.

    예전에는 개수 상한을 넘기면 _valid가 전체를 거부해 규칙 폴백(저자 뒤섞임)으로 떨어졌다.
    받아들이는 쪽보다 거부하는 쪽이 나빴던 절벽이라, 상한을 _pullable의 절단으로 옮겼다.
    """
    layout = FrontMatterLayout(title=0, authors=[1, 2],
                               sections=[8, 9, 10, 11, 12, 13, 14, 4], body_start=7)
    out = fm.normalize_llm(_fake(layout), SWAPPED, allow_pull=True)
    assert out is not None                      # 폴백으로 떨어지지 않는다
    front, body = out.split("## 1 Introduction", 1)
    assert "## Abstract" in front               # 진짜 front matter만 앞으로
    # 다음 헤딩(## 2 Related Work)에서 끊기므로 12·13·14는 본문에 남는다
    assert "## 2 Related Work" in body
    assert "Related work body text." in body and "Another related work paragraph." in body
    assert "First intro paragraph" in body      # 헤딩으로 시작하지 않는 지목은 제자리


def test_pullable_is_off_unless_an_inversion_was_measured():
    """뒤집힘이 측정되지 않은 논문에서는 끌어올리기 경로가 통째로 no-op이다."""
    blocks = fm._split_blocks(SWAPPED)
    assert fm._pullable(blocks, [10, 11, 4], 7) == set()                # 기본값(꺼짐)
    assert fm._pullable(blocks, [10, 11, 4], 7, allow=True) == {10, 11}  # 켜면 원래 구간 그대로


def test_llm_without_pull_permission_leaves_sections_in_index_order():
    """allow_pull이 꺼지면 Abstract는 본문 뒤 제자리에 남고, 섹션은 인덱스 오름차순으로 나간다."""
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[10, 11, 4], body_start=7)
    out = fm.normalize_llm(_fake(layout), SWAPPED)
    front, body = out.split("## 1 Introduction", 1)
    assert "## Abstract" in body and "## Abstract" not in front  # 손대지 않는다 (가시적·무손실)
    assert "naturalistic writing interactions" in front          # 본문 앞 꼬리 조각은 그대로 유지
    assert out.count("We introduce a dataset") == 1              # 아무것도 잃지 않는다


# --- 끌어올리기 구간의 끝 닻 (R1~R4) ---------------------------------------
# NIRVANA 첫 페이지 실패 재현: "## Abstract" 뒤에 **다음 쪽 본문**이 그대로 이어 붙어 있어
# 끝을 정하는 것이 없으면 문단 3개와 기여 목록까지 통째로 초록으로 딸려 온다.
NIRVANA_LIKE = [
    "## 1 Introduction",                                                    # 0 body_start
    "…we present NIRVANA (Naturalistic Interactions and",                   # 1
    "## Abstract",                                                          # 2 밀려난 왼쪽 단
    "With the rapid adoption of AI writing assistants in education, this "  # 3 초록 + 2쪽 본문 용접
    "work contributes four resources: (1) a publicly available dataset "
    "with timestamps.",
    "To illustrate the dataset's analytical potential, we conducted "       # 4 2쪽 본문
    "exploratory analyses.",
    "In conclusion, NIRVANA enables a scalable approach. This paper makes "  # 5 2쪽 본문
    "four primary contributions:",
    "- (1) NIRVANA dataset - a publicly available dataset.\n"               # 6 기여 목록
    "- (2) Quantitative analysis revealing patterns.",
]


def test_pullable_stops_at_terminal_punctuation():
    """종결부호로 끝나는 블록에서 구간이 끝난다 — 단이 잘린 조각은 문장 중간에서 끊긴다.

    닻이 없던 때 이 지목은 {2,3,4,5,6}(2쪽 본문 문단 3개 + 기여 목록)을 통째로 끌어올렸다.
    """
    got = fm._pullable(NIRVANA_LIKE, [2, 3, 4, 5, 6, 1], body_start=0, allow=True)
    assert got == {2, 3}


def test_pullable_never_absorbs_a_list():
    """목록 블록은 삼키지 않는다 — 초록 뒤에 붙은 기여 목록이 딸려 오던 자리.

    앞 블록이 종결부호 없이 끝나 R1으로는 멈추지 않는 배치를 일부러 만든다.
    """
    blocks = ["## 1 Introduction", "## Abstract",
              "The abstract body that stops mid-sentence at a column break and",
              "- (1) NIRVANA dataset - a publicly available dataset.\n- (2) Quantitative analysis."]
    got = fm._pullable(blocks, [1, 2, 3], body_start=0, allow=True)
    assert got == {1, 2}  # 목록(3)은 삼키지 않는다


def test_pullable_stops_at_the_next_heading_and_caps_the_run():
    blocks = ["## 1 Intro", "b", "## Abstract", "no terminal punctuation here and",
              "still no terminal punctuation and", "## 2 Related Work", "body"]
    got = fm._pullable(blocks, [2, 3, 4, 5, 6], body_start=0, allow=True)
    assert got == {2, 3, 4}  # 다음 헤딩에서 끊기고, 구간 상한(_MAX_RUN=3)도 넘지 않는다


def test_pullable_total_is_capped():
    """여러 구간이 있어도 전체 상한을 넘겨 끌어올리지 않는다."""
    blocks = (["## 1 Intro"]
              + ["## Abstract", "tail one and", "tail two and"]
              + ["## Keywords", "kw tail and", "kw tail two and"]
              + ["## CCS Concepts", "ccs tail and", "ccs tail two and"])
    got = fm._pullable(blocks, list(range(1, 10)), body_start=0, allow=True)
    assert len(got) <= fm._MAX_PULLED


def test_llm_window_reaches_past_estimated_body_start():
    # 밀려난 Abstract를 지목하려면 프롬프트 창이 본문 시작 추정 지점 뒤까지 닿아야 한다
    seen: dict[str, str] = {}

    def capture(system, user, schema):
        seen["user"] = user
        return FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[10, 11, 4], body_start=7)

    fm.normalize_llm(FakeProvider(parse_fn=capture, model="fake"), SWAPPED)
    assert "[10] ## Abstract" in seen["user"]


def test_llm_invalid_layout_returns_none():
    for bad in (
        FrontMatterLayout(title=999, authors=[1], sections=[], body_start=13),   # 범위 밖
        FrontMatterLayout(title=0, authors=[], sections=[5], body_start=13),      # 저자 없음
        FrontMatterLayout(title=5, authors=[1], sections=[], body_start=2),       # body_start<=title
        FrontMatterLayout(title=0, authors=[13], sections=[], body_start=13),     # 저자가 본문 이후
    ):
        assert fm.normalize_llm(_fake(bad), SCATTERED) is None


def test_llm_exception_returns_none():
    def boom(s, u, sc):
        raise RuntimeError("api down")

    assert fm.normalize_llm(FakeProvider(parse_fn=boom, model="f"), SCATTERED) is None


# --- 진입점 orchestration --------------------------------------------------
def test_normalize_uses_llm_when_valid_else_heuristic():
    good = FrontMatterLayout(title=0, authors=[1, 2, 3, 4, 12], sections=[5, 6], body_start=13)
    assert fm.normalize(_fake(good), SCATTERED) == fm.normalize_llm(_fake(good), SCATTERED)
    # provider None → 규칙
    assert fm.normalize(None, SCATTERED) == fm.normalize_heuristic(SCATTERED)

    # LLM이 검증 실패 → 규칙 폴백
    bad = FrontMatterLayout(title=0, authors=[], sections=[], body_start=13)
    assert fm.normalize(_fake(bad), SCATTERED) == fm.normalize_heuristic(SCATTERED)


# --- 구조화 저자(authors_detail): 근거 통과 시 일관 형식, 실패 시 원본 블록 폴백 ---
def test_structured_authors_render_uniformly_when_grounded():
    from md4paper.ir import AuthorEntry

    layout = FrontMatterLayout(
        title=0, authors=[1, 2, 3, 4, 12], sections=[5, 6], body_start=13,
        authors_detail=[
            AuthorEntry(name="Andrew Jelson", emails=["jelson@vt.edu"],
                        affiliations=["Computer Science Virginia Tech Blacksburg, Virginia, USA"]),
            AuthorEntry(name="Daniel Dunlap", emails=["dunlapd@vt.edu"],
                        affiliations=["Computer Science Virginia Tech Blacksburg, Virginia, USA"]),
            AuthorEntry(name="Daniel Manesh", emails=["danielmanesh@vt.edu"], affiliations=["Virginia Tech, USA"]),
            AuthorEntry(name="Alice Jang", emails=["ajjang@vt.edu"], affiliations=["Virginia Tech, USA"]),
        ],
    )
    out = fm.normalize_llm(_fake(layout), SCATTERED)
    front = out.split("## Abstract")[0]
    # 네 저자 모두 동일한 형식(굵은 이름) — 헤더/평문 뒤섞임 없음
    for who in ("**Andrew Jelson**", "**Daniel Dunlap**", "**Daniel Manesh**", "**Alice Jang**"):
        assert who in front, who
    assert "jelson@vt.edu" in front and "ajjang@vt.edu" in front
    assert "## [Andrew Jelson]" not in out  # 원본 헤더 형식 잔재 없음
    assert "The abstract body is real content" in out and "real body" in out


def test_structured_authors_rejected_when_email_not_in_source():
    from md4paper.ir import AuthorEntry

    # 원문에 없는 이메일(환각) → 검증 실패 → 기존 블록 재조립으로 폴백
    layout = FrontMatterLayout(
        title=0, authors=[1, 2], sections=[5, 6], body_start=13,
        authors_detail=[AuthorEntry(name="Andrew Jelson", emails=["jelson@hallucinated.com"])],
    )
    out = fm.normalize_llm(_fake(layout), SCATTERED)
    assert "jelson@hallucinated.com" not in out                # 환각 이메일은 절대 안 나감
    assert "**[Andrew Jelson](https://orcid.org/1)**" in out    # 폴백(원본 블록) 형식


def test_grounded_authors_unit():
    from md4paper.ir import AuthorEntry

    src = "Anna Neumann\n\nanna.neumann1@uni-due.de Research Centre Trust, UA Ruhr University, Germany"
    ok = fm._grounded_authors(
        [AuthorEntry(name="Anna Neumann", emails=["anna.neumann1@uni-due.de"],
                     affiliations=["Research Centre Trust, UA Ruhr University, Germany"])], src)
    assert ok and ok[0].name == "Anna Neumann"
    # 소속 구두점이 달라도(영숫자 정규화) 통과
    assert fm._grounded_authors(
        [AuthorEntry(name="Anna Neumann",
                     affiliations=["Research Centre Trust — UA Ruhr University, Germany"])], src) is not None
    assert fm._grounded_authors([AuthorEntry(name="Ghost Author")], src) is None  # 원문에 없는 이름
    assert fm._grounded_authors([], src) is None  # 빈 목록 → 폴백


def test_render_authors_detail_format_and_parts():
    from md4paper.ir import AuthorEntry

    e = [AuthorEntry(name="A B", emails=["a@x.edu", "a2@x.edu"], affiliations=["Uni X, City", "Uni Y"])]
    txt = fm._render_authors_detail(e)  # parts None → 둘 다
    assert txt.startswith("**A B**")
    assert "a@x.edu · a2@x.edu" in txt
    assert "**A B**  \n" in txt  # 마크다운 하드 브레이크로 줄 나눔
    assert "Uni X, City" in txt and "Uni Y" in txt
    # parts로 선택 — 이메일만
    only_email = fm._render_authors_detail(e, ["email"])
    assert "a@x.edu" in only_email and "Uni X" not in only_email
    # 소속만
    only_aff = fm._render_authors_detail(e, ["affiliation"])
    assert "Uni X, City" in only_aff and "a@x.edu" not in only_aff
    # 둘 다 끔 → 이름만
    name_only = fm._render_authors_detail(e, [])
    assert name_only == "**A B**"


# --- 저자 주석(∗ …) 배치 + 각주 마커 결정성 -------------------------------
NOTED = (
    "## Paper With An Author Note\n\n"                                       # 0 title
    "## [Sang Won Lee ∗](https://orcid.org/9)\n\n"                           # 1 author heading + 마커
    "Virginia Tech Blacksburg, Virginia, USA sangwonlee@vt.edu\n\n"          # 2 affil
    "Daniel Manesh Virginia Tech, USA danielmanesh@vt.edu "                  # 3 두 저자 평탄화
    "Alice Jang Virginia Tech, USA ajjang@vt.edu\n\n"
    '<a id="fn-author-1"></a>∗ Sang Won Lee conducted this work while at NAVER AI Lab.\n\n'  # 4 저자 주석
    "## Abstract\n\n"                                                        # 5
    "The abstract body is real content.\n\n"                                 # 6
    "## 1 Introduction\n\n"                                                  # 7 body start
    "Real body text here.\n"                                                 # 8
)


def test_author_note_sits_with_authors_not_at_document_end():
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[5, 6], body_start=7)
    out = fm.normalize_llm(_fake(layout), NOTED)
    front = out.split("## Abstract")[0]
    assert "conducted this work while at NAVER AI Lab" in front  # 저자 블록 뒤, 초록 앞
    assert "**[Sang Won Lee](https://orcid.org/9)**" in front    # 이름의 ∗ 마커는 제거
    assert "Sang Won Lee ∗" not in out
    # 규칙 폴백에서도 저자 주석은 버려지지 않고 저자 뒤에 남는다
    heur = fm.normalize_heuristic(NOTED)
    assert "conducted this work while at NAVER AI Lab" in heur.split("## Abstract")[0]


def test_author_marks_are_stripped_deterministically():
    for raw, want in (
        ("Sang Won Lee ∗", "Sang Won Lee"),
        ("Young-Ho Kim†", "Young-Ho Kim"),
        ("Alice Jang 1,2", "Alice Jang"),
        ("Bob Park¹", "Bob Park"),
        ("Jane Doe", "Jane Doe"),
        ("∗", "∗"),                       # 이름이 통째로 마커면 원본 유지 (빈 이름 방지)
    ):
        assert fm.strip_author_mark(raw) == want


def test_structured_author_names_lose_markers():
    from md4paper.ir import AuthorEntry

    src = "Sang Won Lee ∗\n\nVirginia Tech\n\nsangwonlee@vt.edu"
    entries = [AuthorEntry(name="Sang Won Lee ∗", emails=["sangwonlee@vt.edu"],
                           affiliations=["Virginia Tech"])]
    got = fm._grounded_authors(entries, src)
    assert got is not None and got[0].name == "Sang Won Lee"


# --- 저자 주석 앵커는 정규화 출력에 남지 않는다 ----------------------------
def test_author_note_anchor_is_stripped_from_output():
    layout = FrontMatterLayout(title=0, authors=[1, 2, 3], sections=[5, 6], body_start=7)
    out = fm.normalize_llm(_fake(layout), NOTED)
    assert "conducted this work while at NAVER AI Lab" in out  # 주석 본문은 남고
    assert "fn-author" not in out                              # 앵커는 사라진다
    heur = fm.normalize_heuristic(NOTED)
    assert "conducted this work while at NAVER AI Lab" in heur and "fn-author" not in heur


# --- 저자 순서: 원문 등장 순서가 이긴다 ------------------------------------
def test_grounded_authors_restore_source_order():
    """LLM이 다열 그리드를 열 우선으로 내놔도 원문(읽기 순서) 위치로 다시 세운다."""
    from md4paper.ir import AuthorEntry

    src = ("Ann Kim ann@x.edu\n\nBob Lee bob@x.edu\n\n"
           "Cara Park cara@x.edu\n\nDan Choi dan@x.edu")
    entries = [  # 열 우선(A, C, B, D)로 뒤섞인 응답
        AuthorEntry(name="Ann Kim", emails=["ann@x.edu"], affiliations=[]),
        AuthorEntry(name="Cara Park", emails=["cara@x.edu"], affiliations=[]),
        AuthorEntry(name="Bob Lee", emails=["bob@x.edu"], affiliations=[]),
        AuthorEntry(name="Dan Choi", emails=["dan@x.edu"], affiliations=[]),
    ]
    got = fm._grounded_authors(entries, src)
    assert [e.name for e in got] == ["Ann Kim", "Bob Lee", "Cara Park", "Dan Choi"]


def test_grounded_authors_keep_order_when_already_ascending():
    from md4paper.ir import AuthorEntry

    src = "Ann Kim ann@x.edu\n\nBob Lee bob@x.edu"
    entries = [AuthorEntry(name="Ann Kim", emails=[], affiliations=[]),
               AuthorEntry(name="Bob Lee", emails=[], affiliations=[])]
    assert [e.name for e in fm._grounded_authors(entries, src)] == ["Ann Kim", "Bob Lee"]


# --- 저자 그리드 순서는 추출기 것이 정답 (규칙 경로) ------------------------
# 다열 그리드를 행 우선으로 바르게 읽어 온 앞부분. 이름·이메일·소속이 한 블록에 다 있는 저자도,
# 세 블록에 흩어진 저자도 섞여 있다 — 종류별로 모으면 순서가 무너지는 모양.
GRID = (
    "## A Paper With A Three Column Author Grid\n\n"          # 0 title
    "Ann Kim Uni A Seoul, Korea\n\n"                          # 1 이름+소속(이메일은 다음 블록)
    "ann@a.edu\n\n"                                           # 2 이메일만
    "Bob Lee Uni B Busan, Korea bob@b.edu\n\n"                 # 3 한 블록에 다
    "## Cara Park\n\n"                                        # 4 저자 헤딩
    "Uni C Daegu, Korea cara@c.edu\n\n"                       # 5 그 소속
    "∗ Cara Park is the corresponding author.\n\n"            # 6 저자 주석 (앵커 없음)
    "## Abstract\n\n"                                         # 7
    "The abstract body is real content.\n\n"                  # 8
    "## 1 Introduction\n\n"                                   # 9 body start
    "Real body text.\n"                                       # 10
)


def _author_order(md: str, names) -> list[str]:
    front = md.split("## Abstract")[0]
    return [n for _, n in sorted((front.find(n), n) for n in names) if front.find(n) >= 0]


def test_heuristic_keeps_the_extractors_author_order():
    """규칙 경로가 저자 순서를 바꾸지 않는다 — 저자 순서는 논문에서 사실이다.

    이메일만 든 줄·이메일 없는 이름 줄은 '저자'로 인식되지 않아 예전엔 뒤로 밀렸고,
    행 우선으로 바르게 읽어 온 순서가 규칙 경로에서 다시 뒤엉켰다.
    """
    names = ("Ann Kim", "Bob Lee", "Cara Park")
    out = fm.normalize_heuristic(GRID)
    assert _author_order(out, names) == list(names)
    assert _author_order(GRID, names) == list(names)  # 입력이 이미 옳았다는 확인
    assert "ann@a.edu" in out.split("## Abstract")[0]  # 이메일 조각도 저자 밴드에 남는다


def test_heuristic_is_idempotent():
    """두 번 정규화해도 같은 문서 — 앵커가 떨어진 저자 주석·굵은 저자 줄이 사라지면 안 된다."""
    once = fm.normalize_heuristic(GRID)
    assert once == fm.normalize_heuristic(once)
    assert "corresponding author" in once and "corresponding author" in fm.normalize_heuristic(once)
    assert "**Cara Park**" in fm.normalize_heuristic(once)


def test_normalize_authors_is_idempotent_without_provider():
    once, _ = fm.normalize_authors(None, NOTED)
    twice, _ = fm.normalize_authors(None, once)
    assert "conducted this work while at NAVER AI Lab" in twice  # 주석이 2회차에 지워지지 않는다
    assert once == twice


def test_author_note_recognised_without_the_anchor():
    """앵커는 최종 출력에서 떼므로, 앵커 없이도 주석을 알아봐야 재실행이 안전하다."""
    assert fm._is_author_note("∗ Alice is the corresponding author.")
    assert fm._is_author_note("†Both authors contributed equally.")
    assert fm._is_author_note("Corresponding author: alice@x.edu")
    # 마크다운 강조·글머리표·다음 섹션 헤딩은 주석이 아니다
    assert not fm._is_author_note("**Sang Won Lee**")
    assert not fm._is_author_note("* a bullet item in the abstract")
    assert not fm._is_author_note("## ∗ Some Heading")
    # 각주 기호로 시작하는 서지 상용구는 주석이 아니라 상용구다
    assert not fm._is_author_note("∗ ACM ISBN 978-1-4503-0000-0/26/04")


# --- ACM Reference Format: 공백이 빠져도 상용구다 ---------------------------
RUNIN_REF = (
    "## Paper With A Runin Reference Format\n\n"                  # 0 title
    "Ann Kim Uni A Seoul, Korea ann@a.edu\n\n"                    # 1 저자
    "Bob Lee Uni B Busan, Korea bob@b.edu\n\n"                    # 2 저자
    "## ACMReference Format:\n\n"                                 # 3 상용구 헤딩(공백 유실)
    "Ann Kim and Bob Lee. 2026. Paper Title. In . ACM.\n\n"       # 4 그 본문
    "## Abstract\n\nThe abstract body is real content.\n\n"       # 5,6
    "## 1 Introduction\n\nBody.\n"                                # 7,8
)


def test_runin_acm_reference_format_is_dropped_whole():
    """추출기가 공백을 잃어 'ACMReference Format:'으로 내보내도 헤딩+본문을 함께 버린다.

    못 알아보면 헤딩만 남아 **빈 섹션**이 되고(본문은 junk 규칙이 지운다), 콜론이 없는 변형은
    _is_person을 통과해 저자로 둔갑한다.
    """
    out = fm.normalize_heuristic(RUNIN_REF)
    assert "ACMReference Format" not in out and "Paper Title. In . ACM." not in out
    assert "The abstract body is real content" in out and "## 1 Introduction" in out
    assert not fm._is_person("ACMReference Format")   # 저자로 오인하지 않는다
    assert not fm._is_person("ACM Reference Format")


# --- 저자 순서 재정렬은 '읽기 순서를 확인한 페이지'에서만 -------------------
def test_grounded_authors_keep_llm_order_when_source_order_untrusted():
    """추출이 되돌리지 못한 뒤집힘이 남은 페이지에서는 원문 순서가 정답이 아니다.

    그때 원문 순서로 되세우면 틀린 순서를 고치는 게 아니라 맞은 답을 망가뜨린다.
    """
    from md4paper.ir import AuthorEntry

    src = ("Ann Kim ann@x.edu\n\nCara Park cara@x.edu\n\n"   # 열 우선로 뒤엉킨 채 추출된 블록
           "Bob Lee bob@x.edu\n\nDan Choi dan@x.edu")
    entries = [AuthorEntry(name=n, emails=[], affiliations=[])
               for n in ("Ann Kim", "Bob Lee", "Cara Park", "Dan Choi")]
    kept = fm._grounded_authors(entries, src, trust_order=False)
    assert [e.name for e in kept] == ["Ann Kim", "Bob Lee", "Cara Park", "Dan Choi"]
    # 순서를 믿을 수 있는 페이지에서는 종전대로 원문 순서가 이긴다
    resorted = fm._grounded_authors(entries, src, trust_order=True)
    assert [e.name for e in resorted] == ["Ann Kim", "Cara Park", "Bob Lee", "Dan Choi"]


def test_prompt_order_rule_follows_trust_order():
    """프롬프트의 순서 지시와 코드의 재정렬은 같은 전제를 써야 한다."""
    seen: dict[str, str] = {}

    def capture(system, user, schema):
        seen["system"] = system
        return FrontMatterLayout(title=0, authors=[1, 2, 3, 4, 12], sections=[5, 6], body_start=13)

    fm.normalize_llm(FakeProvider(parse_fn=capture, model="f"), SCATTERED, trust_order=True)
    assert "KEEP that order" in seen["system"]
    fm.normalize_llm(FakeProvider(parse_fn=capture, model="f"), SCATTERED, trust_order=False)
    assert "may be OUT of reading order" in seen["system"]


# --- 끌어올릴 구간은 인덱스 인접성으로 묶는다 ------------------------------
def test_pullable_groups_by_index_not_listing_order():
    """sections가 오름차순이 아니어도 한 구간이 쪼개지지 않는다.

    [10, 4, 11]에서 4는 본문 앞 꼬리 조각이다. 나열 순서로만 이으면 11이 새 구간이 되고,
    헤딩으로 시작하지 않는다는 이유로 버려져 초록 본문이 본문 속에 남았다.
    """
    blocks = [""] * 13
    blocks[4] = "…a tail fragment of the abstract"
    blocks[5] = "## 1 Introduction"
    blocks[10] = "## Abstract"
    blocks[11] = "The abstract text continues here."
    assert fm._pullable(blocks, [10, 4, 11], 5, allow=True) == {10, 11}
    assert fm._pullable(blocks, [10, 4, 11], 5) == set()  # 게이트가 꺼져 있으면 그대로 no-op
