"""참고문헌 파서 — 항목 분할 + 동시 호출 로직 (FakeProvider로 API 없이)."""

import re

from md4paper.cite.parse import _split_entries, parse_references
from md4paper.ir import ReferenceList, RefEntry
from md4paper.llm import FakeProvider

_ENTRY = re.compile(r"\[(\d+)\]\s+(Author \d+)\. (Title \d+)\. (Venue \d+), (\d{4})\.")


def _numbered_body(n: int) -> str:
    return "\n".join(f"[{i}] Author {i}. Title {i}. Venue {i}, 20{10 + i:02d}." for i in range(1, n + 1))


def _chunk_parser(calls: list[str]):
    """받은 청크에서 항목을 정규식으로 뽑아 RefEntry로 (청크별 파싱 흉내)."""
    def parse_fn(system, user, schema):  # noqa: ANN001
        calls.append(user)
        refs = [
            RefEntry(label=m.group(1), authors=[m.group(2)], year=int(m.group(5)),
                     title=m.group(3), venue=m.group(4), raw=m.group(0))
            for m in _ENTRY.finditer(user)
        ]
        return ReferenceList(references=refs)
    return parse_fn


def test_split_entries_detects_numbered_list():
    entries = _split_entries(_numbered_body(6))
    assert entries is not None and len(entries) == 6
    assert entries[0].startswith("[1]") and entries[5].startswith("[6]")


def test_split_entries_none_for_few_or_unnumbered():
    assert _split_entries(_numbered_body(3)) is None          # 항목 적음
    assert _split_entries("Some prose without any numbered references at all.") is None


def test_parallel_chunking_covers_all_entries():
    calls: list[str] = []
    fake = FakeProvider(parse_fn=_chunk_parser(calls), model="fake")
    accepted, rejected = parse_references(_numbered_body(10), fake, batch_size=3)
    assert len(calls) >= 2                                    # 분할되어 여러 번 호출
    assert [r.label for r in accepted] == [str(i) for i in range(1, 11)]  # 전부·정렬됨
    assert rejected == []


def test_single_call_when_below_batch():
    calls: list[str] = []
    fake = FakeProvider(parse_fn=_chunk_parser(calls), model="fake")
    accepted, _ = parse_references(_numbered_body(5), fake, batch_size=12)
    assert len(calls) == 1                                    # 임계 미만 → 단일 호출
    assert len(accepted) == 5


def test_duplicate_labels_across_chunks_deduped():
    # 모든 청크가 같은 항목을 되돌려도 라벨로 중복 제거
    fake = FakeProvider(
        parse_fn=lambda s, u, schema: ReferenceList(references=[
            RefEntry(label="1", authors=["Author 1"], year=2011,
                     title="Title 1", venue="Venue 1", raw="[1] Author 1. Title 1. Venue 1, 2011.")]),
        model="fake",
    )
    accepted, _ = parse_references(_numbered_body(8), fake, batch_size=2)
    assert [r.label for r in accepted] == ["1"]
