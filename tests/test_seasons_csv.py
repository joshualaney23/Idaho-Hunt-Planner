"""T-16: the pure CSV layer (`planner/seasons.py`).

The §12 column list is a contract — the template, the exporter and the importer all
read it from `COLUMNS`. These tests pin the wire format (header, CRLF, `;` lists,
deterministic order) and the rule that every problem is reported **with its line
number**, for every bad line in one pass.
"""

from __future__ import annotations

from planner import seasons as csvmod

VOCAB = csvmod.Vocabulary(
    species=frozenset({"deer", "elk"}),
    tag_types=frozenset({"deer_regular", "controlled"}),
    units=frozenset({"39", "43", "4A"}),
    restrictions=frozenset({"antlerless_only", "youth_only"}),
)

ROW = {
    "license_year": 2026,
    "species_id": "deer",
    "tag_type_id": "deer_regular",
    "zone": None,
    "hunt_number": None,
    "hunt_class": "general",
    "weapon": "any",
    "sex": "either",
    "start_date": "2026-10-10",
    "end_date": "2026-10-24",
    "units": ["4A", "39"],  # natural order: 4A sorts before 39
    "restrictions": ["antlerless_only"],
    "notes": None,
    "source_page": 12,
}

HEADER = ",".join(csvmod.COLUMNS)


def _csv(*lines: str) -> str:
    return "\r\n".join([HEADER, *lines]) + "\r\n"


# ───────── export ─────────


def test_header_matches_the_ticket_columns() -> None:
    assert csvmod.COLUMNS == (
        "license_year",
        "species",
        "tag_type",
        "zone",
        "hunt_number",
        "hunt_class",
        "weapon",
        "sex",
        "start_date",
        "end_date",
        "units",
        "restrictions",
        "notes",
        "source_page",
    )


def test_to_csv_writes_rfc4180_with_semicolon_lists() -> None:
    text = csvmod.to_csv([ROW])
    lines = text.split("\r\n")
    assert lines[0] == HEADER
    expected = (
        "2026,deer,deer_regular,,,general,any,either,"
        "2026-10-10,2026-10-24,4A;39,antlerless_only,,12"
    )
    assert lines[1] == expected
    assert lines[2] == ""  # trailing CRLF, no extra blank record


def test_export_order_is_deterministic_not_insertion_order() -> None:
    a = {**ROW, "start_date": "2026-11-01", "end_date": "2026-11-05"}
    b = {**ROW, "species_id": "elk"}
    assert csvmod.to_csv([b, a, ROW]) == csvmod.to_csv([ROW, a, b])
    first = csvmod.to_csv([b, a, ROW]).split("\r\n")[1]
    assert first.startswith("2026,deer,deer_regular,,,general,any,either,2026-10-10")


def test_units_are_naturally_sorted_in_the_export() -> None:
    text = csvmod.to_csv([{**ROW, "units": ["10", "4A", "39", "2"]}])
    assert "2;4A;10;39" in text


def test_notes_with_a_comma_are_quoted_and_survive_a_round_trip() -> None:
    text = csvmod.to_csv([{**ROW, "notes": 'Unit 39, east of the river ("the gap")'}])
    assert '"Unit 39, east of the river (""the gap"")"' in text
    parsed = csvmod.parse_csv(text, VOCAB)
    assert parsed.ok and parsed.rows[0]["notes"] == 'Unit 39, east of the river ("the gap")'


# ───────── import: the happy path ─────────


def test_parse_csv_reads_values_back_into_api_shape() -> None:
    parsed = csvmod.parse_csv(csvmod.to_csv([ROW]), VOCAB)
    assert parsed.ok, parsed.errors
    assert parsed.rows == [ROW]


def test_blank_lines_and_a_bom_are_tolerated() -> None:
    text = "\ufeff" + _csv(
        "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,,",
        "",
        "2026,elk,controlled,,1001,controlled,archery,antlered,2026-09-01,2026-09-10,43,,,",
    )
    parsed = csvmod.parse_csv(text.lstrip("\ufeff"), VOCAB)
    assert parsed.ok, parsed.errors
    assert len(parsed.rows) == 2
    assert parsed.rows[1]["hunt_number"] == "1001"


def test_empty_optional_columns_become_none() -> None:
    parsed = csvmod.parse_csv(
        _csv("2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,,"), VOCAB
    )
    row = parsed.rows[0]
    assert (row["zone"], row["hunt_number"], row["notes"], row["source_page"]) == (
        None,
        None,
        None,
        None,
    )
    assert row["restrictions"] == []


# ───────── import: every error carries a line number ─────────


def test_each_invalid_row_reports_its_own_line() -> None:
    parsed = csvmod.parse_csv(
        _csv(
            "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,,",  # 2 ok
            "2026,unicorn,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,,",  # 3
            "2026,deer,deer_regular,,,general,any,either,2026-10-24,2026-10-10,39,,,",  # 4
            "2026,deer,deer_regular,,,general,bazooka,either,2026-10-10,2026-10-24,39,,,",  # 5
        ),
        VOCAB,
    )
    assert [e.line for e in parsed.errors] == [3, 4, 5]
    assert [e.field for e in parsed.errors] == ["species", "end_date", "weapon"]
    assert len(parsed.rows) == 1  # the good row is still parsed


def test_line_numbers_survive_a_quoted_newline() -> None:
    text = (
        HEADER
        + "\r\n"
        + "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,"
        + '"two\r\nlines",\r\n'
        + "2026,deer,deer_regular,,,general,any,either,2026-13-40,2026-10-24,39,,,\r\n"
    )
    parsed = csvmod.parse_csv(text, VOCAB)
    # The quoted note spans physical lines 2–3, so the bad row is line 4, not 3.
    assert [(e.line, e.field) for e in parsed.errors] == [(4, "start_date")]


def test_missing_required_fields_are_all_named_on_one_line() -> None:
    parsed = csvmod.parse_csv(_csv(",,,,,,,,,,,,,"), VOCAB)
    assert {e.field for e in parsed.errors} >= {
        "license_year",
        "species",
        "tag_type",
        "hunt_class",
        "weapon",
        "sex",
        "start_date",
        "end_date",
        "units",
    }
    assert {e.line for e in parsed.errors} == {2}


def test_unknown_units_and_restrictions_are_rejected_not_invented() -> None:
    parsed = csvmod.parse_csv(
        _csv(
            "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39;99Z,no_such_code,,"
        ),
        VOCAB,
    )
    fields = {e.field: e.message for e in parsed.errors}
    assert "99Z" in fields["units"]
    assert "no_such_code" in fields["restrictions"]
    assert parsed.rows == []


def test_wrong_column_count_and_bad_header_are_reported() -> None:
    short = csvmod.parse_csv(_csv("2026,deer,deer_regular"), VOCAB)
    assert short.errors[0].line == 2 and "14 columns" in short.errors[0].message
    bad = csvmod.parse_csv("year,species\n2026,deer\n", VOCAB)
    assert bad.errors[0].line == 1 and bad.errors[0].field == "header"
    assert csvmod.parse_csv("", VOCAB).errors[0].field == "header"


def test_impossible_date_and_repeated_unit_are_caught() -> None:
    parsed = csvmod.parse_csv(
        _csv(
            "2026,deer,deer_regular,,,general,any,either,2026-02-31,2026-10-24,39,,,",
            "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39;39,,,",
            "1066,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,39,,,",
        ),
        VOCAB,
    )
    assert [(e.line, e.field) for e in parsed.errors] == [
        (2, "start_date"),
        (3, "units"),
        (4, "license_year"),
    ]
