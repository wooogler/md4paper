"""모든 파일 아티팩트의 pydantic 스키마 — 단일 진실원.

manifest(sections.yaml), blocks.json, references.json, glossary 등이 여기서 정의된다.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, Field


# --- 번호 체계 -------------------------------------------------------------


class Scheme(str, Enum):
    """헤더 번호 체계."""

    DOTTED_ARABIC = "dotted-arabic"  # 1, 1.1, 2.3.1
    ROMAN = "roman"  # I, II, IV
    LETTER = "letter"  # A, B, A.1 (주로 부록)
    UNNUMBERED = "unnumbered"  # Abstract, References, Acknowledgments...
    NONE = "none"  # 번호 감지 실패


class Flavor(str, Enum):
    """이미지 임베딩 방식 — 마크다운 뷰어마다 문법이 다르다."""

    STANDARD = "standard"  # ![label](images/fig-01.jpeg) + 캡션 이탤릭 (GitHub 등 범용)
    OBSIDIAN = "obsidian"  # ![[images/fig-01.jpeg]] 위키링크 임베드
    NOTION = "notion"  # ![label: caption](images/fig-01.jpeg) (alt를 캡션으로 취급)
    HTML = "html"  # <img src=... width=...> (크기 조절 가능)


class Detected(BaseModel):
    """한 헤더에서 감지한 번호 정보."""

    scheme: Scheme
    number: str = ""  # 매칭된 번호 토큰, 예: "3.1", "II", "A"
    depth: int = 1  # 점(.) 깊이 — "3"→1, "3.1"→2
    title: str = ""  # 번호를 제거한 헤더 텍스트


# --- manifest (sections.yaml) ---------------------------------------------

# level 필드: 정수(1-6) 또는 편집 연산
# skip=헤더만 해제(본문 유지) · merge-up=위에 흡수 · drop=섹션 통째 제거 · italic=이탤릭 표기(run-in)
LevelValue = Union[int, Literal["skip", "merge-up", "drop", "italic"]]


class ManifestSection(BaseModel):
    """리뷰 대상 헤더 항목 하나 (ATX 헤더 또는 본문 내 run-in 헤더)."""

    id: str
    text: str  # 헤더 텍스트 (번호 포함)
    line: int  # raw.md에서의 라인 인덱스 (0-based) — 재작성 앵커
    page: int | None = None  # 원본 PDF 페이지 (1-based) — 클릭 시 대조용
    detected: Detected | None = None
    marker_level: int = 2  # 추출기가 준 레벨 (# 개수)
    level: LevelValue = 2  # 재도출/승인된 레벨 (사용자가 고치는 값)
    auto_level: LevelValue | None = None  # 자동 계산값 — level과 다르면 '사용자 교정'으로 기억
    context: str | None = None  # 뒤따르는 본문 스니펫 (블라인드 편집 방지)
    needs_review: bool = False  # 낮은 신뢰도 — 조용히 적용하지 않고 표시
    is_title: bool = False  # 문서 제목 (보통 첫 무번호 헤더) — h1 고정, 별도 취급
    translate: bool = True  # 이 섹션을 번역할지 (번역 단계 Tree Select)
    # 본문 줄 안에 있던 run-in 헤더("4.1.3 제목. 본문…")면 True + 나머지 본문
    runin: bool = False
    runin_body: str | None = None



class Manifest(BaseModel):
    """sections.yaml 전체 — 헤더 트리 + 문서 수준 출력 설정."""

    title: str = ""
    # 본문 인용에 넣을 요소들(조합 가능): number([1]) · authoryear(Vaswani et al. 2017) · short(Transformer)
    citation_parts: list[Literal["number", "authoryear", "short"]] = Field(default_factory=lambda: ["number"])
    reference_links: bool = True  # 참고문헌을 DOI/arXiv URL로 하이퍼링크
    flavor: Flavor = Flavor.STANDARD  # 이미지 임베딩 방식 (뷰어별)
    # 그림/표 캡션을 본문과 구별하는 표기: bold-italic(**라벨:** *설명*) | blockquote(> 인용) | italic(*전체*)
    caption_style: Literal["bold-italic", "blockquote", "italic"] = "bold-italic"
    # 번호형 run-in 헤더(4.1.3 제목. 본문…)를 본문에서 감지해 처리: off | header(승격) | italic
    runin_headings: Literal["off", "header", "italic"] = "off"
    korean_style: str = "해라체"  # 해라체 | 합니다체 | 해요체 | custom:"..."
    translate_headers: bool = True  # 섹션 제목도 번역 (False면 헤더는 영어 유지)
    translate_references: bool = False  # 참고문헌 섹션 번역 (기본 False — 영어 원문 유지)
    # 저자 블록에 표시할 요소(이름은 항상): email(이메일) · affiliation(소속). 재조립 시 즉시 반영.
    author_parts: list[Literal["email", "affiliation"]] = Field(
        default_factory=lambda: ["email", "affiliation"])
    sections: list[ManifestSection] = Field(default_factory=list)


class AuthorEntry(BaseModel):
    """저자 한 명의 구조화된 정보. 값은 전부 원문 텍스트에서 그대로 와야 한다(근거 검증 대상)."""

    name: str = Field(description="Author's full name exactly as written")
    emails: list[str] = Field(default_factory=list, description="Email addresses, exactly as written")
    affiliations: list[str] = Field(default_factory=list,
                                    description="Affiliation lines (institution, city, country), as written")


class FrontMatterLayout(BaseModel):
    """LLM이 앞부분 블록에 매긴 라벨 (블록 인덱스). 코드가 이걸로 원본 블록만 재조립한다.

    LLM은 '어느 블록이 무엇인지'만 판단하고 출력 텍스트는 전부 원본에서 온다 → 환각(내용 조작) 없음.
    authors_detail은 저자 그리드를 저자별로 재구성한 것(선택) — 모든 값이 원문에 있어야만 채택한다.
    """

    title: int = Field(description="Index of the block holding the paper title")
    authors: list[int] = Field(default_factory=list,
                               description="Indices of author name/affiliation blocks, in reading order")
    sections: list[int] = Field(default_factory=list,
                                description="Indices of front-matter sections to KEEP (Abstract, CCS, "
                                            "Keywords and their body), in order")
    body_start: int = Field(description="Index of the first main body section (e.g. Introduction / '1 ...')")
    authors_detail: list[AuthorEntry] = Field(
        default_factory=list,
        description="Authors re-grouped one-per-entry (name + their emails + their affiliations), in reading "
                    "order. Use ONLY text present in the author blocks; do not invent or fix anything.")


class RuninDecision(BaseModel):
    """run-in 후보 하나에 대한 LLM 판정 (인덱스로 지목). 텍스트는 생성하지 않는다(환각 없음)."""

    index: int = Field(description="The candidate index being judged")
    is_heading: bool = Field(
        description="True if this is a genuine subsection heading; False if it is a bulleted/definition "
                    "list lead-in, a figure/table caption, or an ordinary sentence (i.e. NOT a heading)")
    level: int = Field(default=0,
                       description="Heading level 2-6 when is_heading (siblings share a level, one deeper "
                                   "than the parent numbered/ATX heading). 0 when not a heading.")


class SectionLayout(BaseModel):
    """본문 run-in 후보들의 역할·레벨 판정 묶음 (구조 단계 LLM 하이브리드)."""

    decisions: list[RuninDecision] = Field(default_factory=list)


# --- 이미지/캡션 -----------------------------------------------------------


class FigurePair(BaseModel):
    """그림/표와 그 캡션의 짝."""

    kind: Literal["figure", "table"] = "figure"
    image_path: str | None = None  # 표는 이미지가 없을 수 있음
    label: str | None = None  # "Figure 3", "Table 2"
    caption: str | None = None
    image_line: int | None = None
    caption_line: int | None = None
    # 이미지와 메인 캡션 사이의 서브피규어 라벨("(a) …", "(b) …"). 원위치 줄은 제거(consumed_lines)하고
    # 텍스트(subcaptions)는 그림 블록 안(이미지 아래·캡션 위)으로 옮겨 살린다.
    consumed_lines: list[int] = Field(default_factory=list)
    subcaptions: list[str] = Field(default_factory=list)


# --- 참고문헌 (cite 단계) --------------------------------------------------


class RefEntry(BaseModel):
    """파싱된 참고문헌 항목 하나."""

    label: str  # 본문에서 쓰인 번호, 예: "12" (대괄호 없이)
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    title: str = ""
    short_name: str = ""  # 널리 알려진 별칭(예: "Transformer") 또는 부제 뗀 단축 제목; 제목 없을 때만 빈 문자열
    venue: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    raw: str = ""  # 원문 (반환각 검증용)

    def url(self) -> str | None:
        """DOI > arXiv 순으로 직접 접근 가능한 URL."""
        if self.doi:
            doi = self.doi.strip()
            if doi.startswith("http"):
                return doi
            return f"https://doi.org/{doi.removeprefix('doi:').strip()}"
        if self.arxiv_id:
            aid = self.arxiv_id.strip().removeprefix("arXiv:").removeprefix("arxiv:").strip()
            return f"https://arxiv.org/abs/{aid}"
        return None

    def author_year(self) -> str:
        """저자-연도 라벨: 'Vaswani et al. 2017'."""
        year = str(self.year) if self.year else ""
        if not self.authors:
            return year or self.label
        surname = self.authors[0].split()[-1] if self.authors[0].split() else self.authors[0]
        if len(self.authors) == 1:
            base = surname
        elif len(self.authors) == 2:
            second = self.authors[1].split()[-1] if self.authors[1].split() else self.authors[1]
            base = f"{surname} & {second}"
        else:
            base = f"{surname} et al."
        return f"{base} {year}".strip()


class ReferenceList(BaseModel):
    """structured-output 래퍼 (프로바이더는 단일 모델을 요구하므로 리스트를 감싼다)."""

    references: list[RefEntry] = Field(default_factory=list)


class PaperMeta(BaseModel):
    """논문 서지 정보 (front matter에서 추출) — 목록 표시·검색·정렬용."""

    title: str = ""
    authors: list[str] = Field(default_factory=list)  # 저자 이름 목록 (원문 표기)
    year: int | None = None  # 출판/발표 연도 (없으면 None)
    venue: str = ""  # 학회 proceeding·저널명 (없으면 빈 문자열)
    short_title: str = ""  # 파일명용 CamelCase 약칭 (예: "ContinualHITLOptimization"); 폴더 자동 명명에 사용


# --- 용어집 (translate 단계) ----------------------------------------------


class GlossaryEntry(BaseModel):
    term: str
    korean: str
    policy: Literal["translate", "transliterate", "keep", "병기-first-use"] = "translate"


class GlossaryList(BaseModel):
    """structured-output 래퍼."""

    entries: list[GlossaryEntry] = Field(default_factory=list)
