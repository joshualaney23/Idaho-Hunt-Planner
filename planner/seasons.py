"""Season CSV round-trip (DESIGN.md §6 `GET /api/export.csv` · `POST /api/import.csv`, T-16).

Pure text ↔ rows: no database, no Flask. The API layer supplies the vocabularies
(species, tag types, units, restriction codes) so every value is checked against the
seeded data — a CSV never invents a unit or a restriction code (D3).

The column list is fixed by §12 T-16; `units` and `restrictions` are `;`-separated:

    license_year,species,tag_type,zone,hunt_number,hunt_class,weapon,sex,
    start_date,end_date,units,restrictions,notes,source_page

Export writes RFC 4180 (CRLF, minimal quoting) in a deterministic order, so
`export → import → export` is byte-identical.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

COLUMNS: tuple[str, ...] = (
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

#: Columns that must carry a value on every imported row.
REQUIRED: tuple[str, ...] = (
    "license_year",
    "species",
    "tag_type",
    "hunt_class",
    "weapon",
    "sex",
    "start_date",
    "end_date",
    "units",
)

HUNT_CLASSES: tuple[str, ...] = ("general", "controlled", "extra", "youth", "outfitter", "other")
WEAPONS: tuple[str, ...] = ("any", "archery", "muzzleloader", "short_range", "shotgun", "falconry")
SEXES: tuple[str, ...] = ("antlered", "antlerless", "either", "any", "male", "na")

MIN_YEAR, MAX_YEAR = 1900, 2100
LIST_SEP = ";"
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class RowError:
    """One problem with one CSV line. `line` is the physical line in the file."""

    line: int
    field: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class Vocabulary:
    """What the seeded database allows. Empty set = don't check that column."""

    species: frozenset[str] = frozenset()
    tag_types: frozenset[str] = frozenset()
    units: frozenset[str] = frozenset()
    restrictions: frozenset[str] = frozenset()


@dataclass
class ParsedCsv:
    """The outcome of `parse_csv`: clean rows, and every problem with a line number."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def natural_key(unit_id: str) -> tuple[int, str]:
    """`'4A'` → `(4, 'A')`: the natural unit order used for stable export output."""
    m = re.match(r"^(\d+)(.*)$", str(unit_id).strip())
    if not m:
        return (10**6, str(unit_id).strip().upper())
    return (int(m.group(1)), m.group(2).strip().upper())


def _join(values: Iterable[Any]) -> str:
    return LIST_SEP.join(str(v) for v in values)


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def season_to_row(season: dict[str, Any]) -> dict[str, str]:
    """One season dict (API shape) → one CSV record."""
    return {
        "license_year": _cell(season.get("license_year")),
        "species": _cell(season.get("species_id")),
        "tag_type": _cell(season.get("tag_type_id")),
        "zone": _cell(season.get("zone")),
        "hunt_number": _cell(season.get("hunt_number")),
        "hunt_class": _cell(season.get("hunt_class")),
        "weapon": _cell(season.get("weapon")),
        "sex": _cell(season.get("sex")),
        "start_date": _cell(season.get("start_date")),
        "end_date": _cell(season.get("end_date")),
        "units": _join(sorted(season.get("units") or [], key=natural_key)),
        "restrictions": _join(season.get("restrictions") or []),
        "notes": _cell(season.get("notes")),
        "source_page": _cell(season.get("source_page")),
    }


def export_sort_key(season: dict[str, Any]) -> tuple:
    """Deterministic export order, independent of insertion order or row ids."""
    hunt_no = str(season.get("hunt_number") or "")
    return (
        int(season.get("license_year") or 0),
        str(season.get("species_id") or ""),
        str(season.get("tag_type_id") or ""),
        str(season.get("zone") or ""),
        (0, int(hunt_no)) if hunt_no.isdigit() else (1, 0),
        hunt_no,
        str(season.get("hunt_class") or ""),
        str(season.get("weapon") or ""),
        str(season.get("sex") or ""),
        str(season.get("start_date") or ""),
        str(season.get("end_date") or ""),
        _join(sorted(season.get("units") or [], key=natural_key)),
    )


def to_csv(seasons: Sequence[dict[str, Any]]) -> str:
    """Render seasons as the §12 T-16 CSV (CRLF, header row, deterministic order)."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    for season in sorted(seasons, key=export_sort_key):
        writer.writerow(season_to_row(season))
    return buf.getvalue()


def _year(raw: str) -> int:
    value = int(raw)
    if not (MIN_YEAR <= value <= MAX_YEAR):
        raise ValueError(f"license_year must be between {MIN_YEAR} and {MAX_YEAR}")
    return value


def _date(raw: str) -> str:
    if not _ISO_RE.match(raw):
        raise ValueError("expected an ISO date (YYYY-MM-DD)")
    _dt.date.fromisoformat(raw)  # raises ValueError on 2026-02-31
    return raw


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(LIST_SEP) if part.strip()]


def _check_header(header: Sequence[str] | None) -> list[RowError]:
    if header is None:
        return [RowError(1, "header", "the file is empty: expected a header row")]
    got = [h.strip().lstrip("\ufeff") for h in header]
    if tuple(got) == COLUMNS:
        return []
    missing = [c for c in COLUMNS if c not in got]
    extra = [c for c in got if c not in COLUMNS]
    detail = []
    if missing:
        detail.append(f"missing {', '.join(missing)}")
    if extra:
        detail.append(f"unexpected {', '.join(extra)}")
    if not detail:
        detail.append("columns are out of order")
    return [
        RowError(
            1,
            "header",
            f"header must be exactly: {','.join(COLUMNS)} ({'; '.join(detail)})",
        )
    ]


def parse_csv(text: str, vocab: Vocabulary | None = None) -> ParsedCsv:
    """Parse CSV text into season dicts, collecting every problem with its line number.

    Validation is per field and never stops at the first bad row, so one upload tells
    the user every line to fix. A row with any error is dropped from `rows`.
    """
    vocab = vocab or Vocabulary()
    out = ParsedCsv()
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        header = None
    header_errors = _check_header(header)
    if header_errors:
        out.errors.extend(header_errors)
        return out

    for record in reader:
        line = reader.line_num
        if not record or (len(record) == 1 and not record[0].strip()):
            continue  # blank separator line (a row of empty *cells* is a real, invalid row)
        if len(record) != len(COLUMNS):
            out.errors.append(
                RowError(
                    line,
                    "row",
                    f"expected {len(COLUMNS)} columns, got {len(record)}",
                )
            )
            continue
        raw = {name: (value or "").strip() for name, value in zip(COLUMNS, record, strict=True)}
        errors = _row_errors(raw, line, vocab)
        if errors:
            out.errors.extend(errors)
            continue
        out.rows.append(_row_values(raw))
    return out


def _row_errors(raw: dict[str, str], line: int, vocab: Vocabulary) -> list[RowError]:
    errors: list[RowError] = []
    add = lambda name, msg: errors.append(RowError(line, name, msg))  # noqa: E731

    for name in REQUIRED:
        if not raw[name]:
            add(name, f"{name} is required")

    if raw["license_year"]:
        try:
            _year(raw["license_year"])
        except ValueError as e:
            add("license_year", str(e) if "between" in str(e) else "must be a year, e.g. 2026")

    for name, column, allowed in (
        ("species", "species", vocab.species),
        ("tag_type", "tag_type", vocab.tag_types),
    ):
        value = raw[name]
        if value and allowed and value not in allowed:
            add(column, f"unknown {column} '{value}'")

    for name, allowed in (
        ("hunt_class", HUNT_CLASSES),
        ("weapon", WEAPONS),
        ("sex", SEXES),
    ):
        value = raw[name]
        if value and value not in allowed:
            add(name, f"{name} must be one of {', '.join(allowed)}")

    for name in ("start_date", "end_date"):
        if not raw[name]:
            continue
        try:
            _date(raw[name])
        except ValueError:
            add(name, f"'{raw[name]}' is not a valid date (YYYY-MM-DD)")
    if raw["start_date"] and raw["end_date"] and not errors:
        if raw["start_date"] > raw["end_date"]:
            add("end_date", f"end_date {raw['end_date']} is before start_date {raw['start_date']}")

    units = _split(raw["units"])
    if raw["units"] and not units:
        add("units", "units is required")
    if vocab.units:
        unknown = [u for u in units if u not in vocab.units]
        if unknown:
            add("units", f"unknown unit(s): {', '.join(unknown)}")
    dupes = sorted({u for u in units if units.count(u) > 1})
    if dupes:
        add("units", f"repeated unit(s): {', '.join(dupes)}")

    codes = _split(raw["restrictions"])
    if vocab.restrictions:
        unknown = [c for c in codes if c not in vocab.restrictions]
        if unknown:
            add("restrictions", f"not in the §5.1 vocabulary: {', '.join(unknown)}")

    if raw["source_page"]:
        if not raw["source_page"].isdigit() or int(raw["source_page"]) < 1:
            add("source_page", "source_page must be a positive page number")

    return errors


def _row_values(raw: dict[str, str]) -> dict[str, Any]:
    return {
        "license_year": _year(raw["license_year"]),
        "species_id": raw["species"],
        "tag_type_id": raw["tag_type"],
        "zone": raw["zone"] or None,
        "hunt_number": raw["hunt_number"] or None,
        "hunt_class": raw["hunt_class"],
        "weapon": raw["weapon"],
        "sex": raw["sex"],
        "start_date": raw["start_date"],
        "end_date": raw["end_date"],
        "units": sorted(set(_split(raw["units"])), key=natural_key),
        "restrictions": _split(raw["restrictions"]),
        "notes": raw["notes"] or None,
        "source_page": int(raw["source_page"]) if raw["source_page"] else None,
    }
