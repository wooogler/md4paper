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
