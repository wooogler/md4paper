"""표로 잡힌 참고문헌 목록 → 항목 목록 (NIRVANA [63]–[70])."""

from narani.extract import ref_tables


def _doc(rows, heading="References", caption=None):
    """rows: [(번호 칸, 본문 칸)] — 번호 열 x 72–83, 본문 열 x 88–500 (내어쓰기)."""
    from docling_core.types.doc import (BoundingBox, CoordOrigin, DoclingDocument, TableCell,
                                        TableData)

    d = DoclingDocument(name="t")
    d.add_heading(text=heading)
    grp = d.add_list_group()
    d.add_list_item(text="[62] Azmine Toushik Wasi. 2024. LLMs as Writing Assistants. arXiv:2404.00027",
                    parent=grp)
    cells = []
    for r, row in enumerate(rows):
        for c, (text, (left, right)) in enumerate(zip(row, [(72, 83), (88, 500)])):
            box = BoundingBox(l=left, t=100 + 10 * r, r=right, b=107 + 10 * r, coord_origin=CoordOrigin.TOPLEFT)
            cells.append(TableCell(text=text, bbox=box, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                                   start_col_offset_idx=c, end_col_offset_idx=c + 1))
    t = d.add_table(data=TableData(num_rows=len(rows), num_cols=2, table_cells=cells))
    if caption:
        t.captions.append(d.add_text(label="caption", text=caption).get_ref())
    return d


ROWS = [
    ("", "[cs]."),
    ("[63]", "Florian Weber, Thiemo Wambsganss, and Matthias Soellner. 2024. LegalWriter: An Intelligent"),
    ("", "Writing Support System. In Proceedings of the 2024 CHI Conference. doi:10.1145/"),
    ("", "3613904.3642743"),
    ("[64]", "David James Woo and April Ka Yeng Fung. 2025. Product vs. Process."),
]


def _md(d):
    return d.export_to_markdown()


def test_reference_table_becomes_one_list_item_per_reference():
    d = _doc(ROWS)
    assert ref_tables.unwrap(d, "/nonexistent.pdf") == 2  # PDF가 없으면 Docling 셀 행으로
    md = _md(d)
    assert "|" not in md
    assert "- [62] Azmine Toushik Wasi. 2024. LLMs as Writing Assistants. arXiv:2404.00027 [cs]." in md
    assert ("- [63] Florian Weber, Thiemo Wambsganss, and Matthias Soellner. 2024. LegalWriter: An Intelligent "
            "Writing Support System. In Proceedings of the 2024 CHI Conference. doi:10.1145/3613904.3642743") in md
    assert "- [64] David James Woo and April Ka Yeng Fung. 2025. Product vs. Process." in md


def test_baselines_split_a_cell_that_merged_two_references():
    # Docling은 "[63] 꼬리 + [64] 머리"를 한 셀로 뭉쳤다 — 번호가 찍힌 기준선이 새 항목의 시작이다.
    marks = [(112.0, "[63]"), (142.0, "[64]")]
    lines = [(103.0, 7.0, "[cs]."), (112.0, 7.0, "Florian Weber. 2024. LegalWriter: An"),
             (122.0, 7.0, "Intelligent Writing Support System. doi:10.1145/3613904.3642743"),
             (132.0, 7.0, "Systems (CHI '24)."),
             (141.8, 7.0, "David James Woo. 2025. Editing of AI-"), (152.0, 7.0, "generated Text.")]
    lead, entries = ref_tables.group_lines(marks, lines)
    assert lead == ["[cs]."]
    assert [m for m, _ in entries] == ["[63]", "[64]"]
    assert ref_tables.join_lines(entries[1][1]) == "David James Woo. 2025. Editing of AI-generated Text."
    assert entries[0][1][-1] == "Systems (CHI '24)."


def test_real_tables_are_left_alone():
    # 번호가 아닌 첫 열 · 참고문헌 절 밖 · 캡션 달린 표 · 번호가 건너뛰는 표는 그대로
    cases = [
        _doc([("Model", "Accuracy"), ("A", "0.91"), ("B", "0.87")]),
        _doc(ROWS, heading="Results"),
        _doc(ROWS, caption="Table 3. Sources."),
        _doc([("[63]", "a"), ("[65]", "b")]),
    ]
    for d in cases:
        assert ref_tables.unwrap(d, "/nonexistent.pdf") == 0
        assert "|" in _md(d)
