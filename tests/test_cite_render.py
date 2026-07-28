"""참고문헌 렌더 테스트 — 앵커, DOI/arXiv 링크, 단축명."""

from md4paper.cite import render
from md4paper.ir import RefEntry


def test_arxiv_link():
    e = RefEntry(label="1", authors=["Ashish Vaswani"], year=2017, title="Attention Is All You Need",
                 short_name="Transformer", venue="NeurIPS", arxiv_id="1706.03762")
    line = render.render_entry(e)
    assert '<a id="ref-1"></a>' in line
    assert "[Attention Is All You Need](https://arxiv.org/abs/1706.03762)" in line
    assert "*(Transformer)*" in line
    assert "NeurIPS" in line and "2017" in line


def test_doi_link():
    e = RefEntry(label="2", authors=["A. Author"], year=2020, title="Some Paper", doi="10.1000/xyz")
    line = render.render_entry(e)
    assert "[Some Paper](https://doi.org/10.1000/xyz)" in line


def test_no_url_plain_title():
    e = RefEntry(label="3", authors=["B. Author"], year=2019, title="No Link Paper")
    line = render.render_entry(e)
    assert "No Link Paper" in line
    assert "http" not in line


def test_reference_links_off():
    e = RefEntry(label="1", title="Attention", arxiv_id="1706.03762")
    line = render.render_entry(e, reference_links=False)
    assert "http" not in line
    assert "Attention" in line


def test_url_precedence_doi_over_arxiv():
    e = RefEntry(label="1", title="T", doi="10.1/x", arxiv_id="1706.03762")
    assert e.url() == "https://doi.org/10.1/x"


def test_render_list_has_blank_separators():
    refs = [RefEntry(label="1", title="A"), RefEntry(label="2", title="B")]
    lines = render.render_reference_list(refs)
    assert "" in lines  # 항목 사이 빈 줄
