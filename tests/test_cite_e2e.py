"""cite 엔드투엔드 — FakeProvider로 실제 API 없이 전체 흐름 검증."""

from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.cite.apply import run_cite
from md4paper.ir import ReferenceList, RefEntry
from md4paper.llm import FakeProvider
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"

# 픽스처의 References 원문과 일치하는 raw (반환각 검증 통과용)
FAKE_REFS = ReferenceList(
    references=[
        RefEntry(
            label="1", authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], year=2017,
            title="Attention is all you need",
            short_name="Transformer", venue="NeurIPS", arxiv_id="1706.03762",
            raw="[1] Ashish Vaswani et al. Attention is all you need. NeurIPS 2017.",
        ),
        RefEntry(
            label="2", authors=["Dzmitry Bahdanau"], year=2015,
            title="Neural machine translation by jointly learning to align and translate",
            venue="ICLR", doi="10.1000/nmt",
            raw="[2] Dzmitry Bahdanau et al. Neural machine translation by jointly learning to align and translate. ICLR 2015.",
        ),
        RefEntry(
            label="3", authors=["Kyunghyun Cho"], year=2014,
            title="Learning phrase representations using RNN encoder-decoder", venue="EMNLP",
            raw="[3] Kyunghyun Cho et al. Learning phrase representations using RNN encoder-decoder. EMNLP 2014.",
        ),
    ]
)


@pytest.fixture
def cited_wd(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_cite.md", wd)
    fake = FakeProvider(parse_fn=lambda s, u, schema: FAKE_REFS, model="fake-model")
    summary = run_cite(wd, fake, parts=["number"], reference_links=True)
    return wd, summary


def test_cite_summary(cited_wd):
    _, summary = cited_wd
    assert summary["parsed"] == 3
    assert summary["rejected"] == 0
    # [1], [2,3], [1-3], [2] → 1 + 2 + 3 + 1 = ... 마커 4곳 치환
    assert summary["linked"] == 4
    assert summary["skipped_range"] == 1  # [0,1]


def test_intext_links_and_anchors(cited_wd):
    wd, _ = cited_wd
    out = wd.en_md.read_text(encoding="utf-8")
    assert "[[1](#ref-1)]" in out  # keep 스타일 링크
    assert "[[2](#ref-2), [3](#ref-3)]" in out  # multi
    assert '<a id="ref-1"></a>' in out  # 참고문헌 앵커
    # DOI/arXiv 링크
    assert "https://arxiv.org/abs/1706.03762" in out
    assert "https://doi.org/10.1000/nmt" in out
    assert "*(Transformer)*" in out  # 단축명


def test_math_and_code_preserved(cited_wd):
    wd, _ = cited_wd
    out = wd.en_md.read_text(encoding="utf-8")
    assert "[0,1]" in out  # 수학 구간 그대로
    assert "`arr[2]`" in out  # 인라인 코드 그대로


def test_authoryear_style(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_cite.md", wd)
    fake = FakeProvider(parse_fn=lambda s, u, schema: FAKE_REFS, model="fake-model")
    run_cite(wd, fake, parts=["authoryear"])
    out = wd.en_md.read_text(encoding="utf-8")
    assert "[Vaswani et al. 2017](#ref-1)" in out


def test_rejects_hallucinated_entry(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_cite.md", wd)
    bad = ReferenceList(references=[
        RefEntry(label="1", authors=["Ghost"], year=1999, title="Fabricated",
                 raw="이 텍스트는 원문에 존재하지 않는다"),
    ])
    fake = FakeProvider(parse_fn=lambda s, u, schema: bad, model="fake-model")
    from md4paper.cite.apply import CiteError
    with pytest.raises(CiteError):  # 전부 검증 실패 → 유효 항목 없음
        run_cite(wd, fake, parts=["number"])
