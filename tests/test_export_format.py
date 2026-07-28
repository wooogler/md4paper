"""내보내기 형식 변환 — universal/notion/obsidian."""

from md4paper.export_format import to_export_target

CANON = (
    "# Title\n\n"
    "Body with a citation [[54](#ref-54); [55](#ref-55)] and text.\n\n"
    'A claim.<sup class="md-fn"><a href="#fn-3">3</a></sup>\n\n'
    "![Figure 1](images/figure-1.png)\n\n"
    "**Figure 1:** *The overview.*\n\n"
    "## References\n\n"
    '<a id="ref-54"></a>**[54]** Vaswani et al. 2017.\n\n'
    "## Footnotes\n\n"
    '<a id="fn-3"></a>**3.** https://example.com/note\n'
)


def test_universal_unchanged():
    assert to_export_target(CANON, "universal") == CANON


def test_notion_strips_internal_anchor_links():
    out = to_export_target(CANON, "notion")
    # 인용: 내부 앵커 링크 제거, 라벨만 (about:blank#ref 방지)
    assert "(#ref-54)" not in out and "(#ref-55)" not in out
    assert "[54; 55]" in out
    # 각주: 위첨자 링크 → 유니코드 위첨자
    assert 'href="#fn-3"' not in out
    assert "A claim.³" in out
    # 빈 앵커 타깃 제거
    assert '<a id="ref-54">' not in out and '<a id="fn-3">' not in out
    assert "**[54]** Vaswani" in out  # 참고문헌 본문은 유지
    # 이미지 alt는 비움(Notion 중복 캡션 방지), 캡션 블록은 그대로(볼드 유지)
    assert "![](images/figure-1.png)" in out
    assert "**Figure 1:**" in out


def test_obsidian_wiki_embed_images():
    out = to_export_target(CANON, "obsidian")
    assert "![[images/figure-1.png]]" in out  # 위키 임베드(images/ 경로 유지)
    assert "![Figure 1](images/figure-1.png)" not in out


def test_obsidian_citations_become_in_note_block_links():
    # 인용은 노트 내 블록 링크 [label](#^ref-N)로, 참고문헌 줄 끝엔 블록 id ^ref-N (클릭 시 점프)
    out = to_export_target(CANON, "obsidian")
    assert "[54](#^ref-54)" in out and "[55](#^ref-55)" in out  # 인용 → 블록 링크
    assert "(#ref-54)" not in out                               # 죽은 HTML 앵커 링크 제거
    assert "**[54]** Vaswani et al. 2017. ^ref-54" in out       # 참고문헌 블록 id 부착
    assert '<a id="ref-54">' not in out                         # HTML 앵커 제거
    # 각주도 동일 — 위첨자 링크 + 정의 줄 블록 id
    assert "A claim.[³](#^fn-3)" in out
    assert "**3.** https://example.com/note ^fn-3" in out
    assert 'href="#fn-3"' not in out


def test_notion_strips_citation_with_bracketed_label():
    # 저자·연도 인용 라벨에 대괄호가 있어도(예: 'Smith et al. [2020]') #ref 앵커를 제거한다
    md = "See ([Smith et al. [2020]](#ref-3)) for details.\n"
    out = to_export_target(md, "notion")
    assert "#ref-3" not in out
    assert "(Smith et al. [2020])" in out


def test_notion_does_not_mangle_preceding_markdown_link():
    # 인용 앞의 일반 마크다운 링크는 삼키지 않는다
    md = "A [website](https://x.com) and a cite [54](#ref-54) here.\n"
    out = to_export_target(md, "notion")
    assert "[website](https://x.com)" in out  # 일반 링크 보존
    assert "#ref-54" not in out and "cite 54 here" in out


def test_notion_links_citations_to_doi_when_urls_given():
    # ref_urls가 있으면 Notion 인용을 DOI/arXiv 외부 링크로 (내부 앵커 대신 논문에 바로 접근)
    md = "See [[12, Vaswani et al. 2017, Attention](#ref-12); [7](#ref-7); [99](#ref-99)] here.\n"
    urls = {"12": "https://doi.org/10.1145/x", "7": "https://arxiv.org/abs/1706.03762"}
    out = to_export_target(md, "notion", urls)
    assert "[12, Vaswani et al. 2017, Attention](https://doi.org/10.1145/x)" in out
    assert "[7](https://arxiv.org/abs/1706.03762)" in out
    assert "; 99]" in out and "#ref-99" not in out  # URL 없는 것은 평문 라벨(바깥 대괄호 안)
    assert "#ref-" not in out


def test_notion_no_catastrophic_backtracking_on_long_lines():
    # 긴 한 줄에 '](#ref-'로 안 이어지는 '['가 있어도 지수적 백트래킹 없이 즉시 끝나야 함(먹통 방지).
    # 예전 정규식 (?:[^\[\]]+|...)* 이면 이 입력에서 프리즈된다.
    md = "본문 [" + "가나다라 " * 200 + "] 그리고 인용 [3](#ref-3) 끝.\n"
    out = to_export_target(md, "notion")
    assert "#ref-3" not in out and "[3](#ref-3)" not in out  # 진짜 인용은 여전히 제거
    assert "그리고 인용 3 끝." in out


def test_ensures_blank_line_before_headings_and_captions():
    # 번역 조립에서 헤더 앞 빈 줄이 사라진 경우 → export 시 보장 (Notion 헤더 인식)
    md = (
        "앞 문단이 여기서 끝납니다.\n"
        "## 2.2 상호작용형 기계학습\n\n"
        "본문 시작.\n\n"
        "![Figure 1](images/figure-1.png)\n"
        "> **Figure 1:** 캡션 설명입니다.\n"
    )
    for target in ("universal", "notion", "obsidian"):
        out = to_export_target(md, target)
        assert "끝납니다.\n\n## 2.2" in out           # 헤더 앞 빈 줄 삽입
    # universal/obsidian: 이미지와 인용 캡션 사이 빈 줄 삽입 (obsidian은 ![[ ]] 로)
    assert "![Figure 1](images/figure-1.png)\n\n> **Figure 1:**" in to_export_target(md, "universal")
    assert "![[images/figure-1.png]]\n\n> **Figure 1:**" in to_export_target(md, "obsidian")
    # notion: 이미지 alt 비움 + 캡션 블록 유지(볼드) → 캡션 중복 없이 'Figure 1:' 볼드
    no = to_export_target(md, "notion")
    assert "![](images/figure-1.png)\n\n> **Figure 1:** 캡션 설명입니다." in no


def test_block_spacing_keeps_multiline_quote_and_code_together():
    from md4paper.export_format import _ensure_block_spacing

    # 인용 여러 줄 연속은 붙여 둔다 + 코드 펜스 안은 안 건드린다
    md = "> line one\n> line two\n\n```\n## not a heading\ncode\n```\n"
    out = _ensure_block_spacing(md)
    assert "> line one\n> line two" in out          # 연속 인용 유지
    assert "```\n## not a heading\ncode\n```" in out  # 펜스 내부 그대로


def test_transforms_skip_fenced_code_blocks():
    # 코드 펜스 안의 예시는 변환하지 않는다 (코드 훼손 방지)
    md = (
        "Body ![Figure 1](images/figure-1.png) and cite [7](#ref-7).\n\n"
        "```md\n![](images/example.png)\n[7](#ref-7)\n```\n"
    )
    ob = to_export_target(md, "obsidian")
    assert "![[images/figure-1.png]]" in ob          # 본문 이미지는 변환
    assert "![](images/example.png)" in ob           # 펜스 안 예시는 그대로
    no = to_export_target(md, "notion")
    assert "cite 7." in no or "cite 7 " in no         # 본문 인용은 변환
    assert "[7](#ref-7)" in no                        # 펜스 안 예시는 그대로


def test_notion_empties_image_alt_keeps_bold_caption_block():
    # Notion은 이미지 alt를 캡션으로 자동 표시 → alt를 비워 중복을 없애고, 캡션 블록(볼드)은 그대로 둔다
    md = (
        "본문 문단.\n\n"
        "![Figure 2](images/figure-2.png)\n\n"
        "> **Figure 2:** 공동 진화 인터페이스. 테스트 세트를 관리합니다.\n\n"
        "다음 문단.\n"
    )
    out = to_export_target(md, "notion")
    assert "![](images/figure-2.png)" in out          # alt 비움 (중복 캡션 방지)
    assert "> **Figure 2:** 공동 진화 인터페이스" in out  # 캡션 블록 유지 + 'Figure 2:' 볼드
    # 다른 형식은 alt 유지
    assert "![Figure 2](images/figure-2.png)" in to_export_target(md, "universal")
