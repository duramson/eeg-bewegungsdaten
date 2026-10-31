"""The docs: every link leads somewhere, and the tables of columns and codes match the schemas and
the column dictionary."""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.columns import COLUMNS
from eeg_bewegungsdaten.export import EXPORT_SCHEMA
from eeg_bewegungsdaten.mastr import MASTR_SCHEMA
from eeg_bewegungsdaten.schema import CLEAN_SCHEMA, QUARANTINE_SCHEMA, TEXT_SCHEMA
from eeg_bewegungsdaten.special_rows import NOT_FED_IN, NOT_GENERATED, SPECIAL_ROWS
from tests.markdown import DOCUMENTS, ROOT, anchors, broken, links, sections, tables

DOCS_DIR = ROOT / "docs"
# The sections that earlier versions of the README held: the datacards of earlier exports link
# to them
MOVED_SECTIONS = (
    "summing-energy",
    "add-mastr-data",
    "personal-data",
    "data-license",
    "columns-of-the-export",
)
# (page, heading) -> the schema whose columns the table lists
COLUMN_TABLES = {
    ("output.md", "Columns of `text`"): TEXT_SCHEMA,
    ("output.md", "Columns of `clean`"): CLEAN_SCHEMA,
    ("output.md", "Columns of `quarantine`"): QUARANTINE_SCHEMA,
    ("mastr.md", "Columns of `mastr`"): MASTR_SCHEMA,
    # The export's columns that `clean` does not have; the text names the others
    ("export.md", "Columns of the export"): pl.Schema(
        {name: dtype for name, dtype in EXPORT_SCHEMA.items() if name not in CLEAN_SCHEMA}
    ),
}
# Column -> (page, heading) of the table of its codes
CODE_TABLES = {
    "fehlercode": ("output.md", "Quality checks"),
    "hinweise": ("output.md", "Quality checks"),
    "sonderzeile": ("output.md", "Summing energy"),
    "treffer": ("mastr.md", "How rows are matched"),
}


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: str(path.relative_to(ROOT)))
def test_every_link_leads_somewhere(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert {target: why for target in links(text) if (why := broken(target, path))} == {}


def test_every_page_of_docs_is_linked_from_the_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    linked = {target.partition("#")[0] for target in links(readme)}
    assert sorted(f"docs/{path.name}" for path in DOCS_DIR.glob("*.md")) == sorted(
        target for target in linked if target.startswith("docs/")
    )


def test_the_readme_keeps_the_anchors_of_the_moved_sections() -> None:
    readme = anchors(ROOT / "README.md")
    assert [anchor for anchor in MOVED_SECTIONS if anchor not in readme] == []


def table(page: str, heading: str, first: str | None = None) -> tuple[list[str], list[list[str]]]:
    """The header and the rows of the table under `heading`: the first table, or the one whose
    header starts with `first`."""
    found = [rows for rows in tables(sections(DOCS_DIR / page)[heading]) if rows]
    for rows in found:
        if first is None or rows[0][0] == first:
            return rows[0], rows[1:]
    raise AssertionError(f"{page}, {heading}: no table whose header starts with {first}")


def names(cell: str, schema: pl.Schema) -> list[str]:
    """The columns that a cell names: `a`, `b`, or `a` … `b` for those between them in `schema`."""
    named = re.findall(r"`(\w+)`", cell)
    if "…" not in cell:
        return named
    first, last = named
    columns = list(schema)
    return columns[columns.index(first) : columns.index(last) + 1]


def type_text(dtype: pl.DataType) -> str:
    """The type as the tables write it: `List[String]` for `List(String)`."""
    return str(dtype).replace("(", "[").replace(")", "]")


@pytest.mark.parametrize(("page", "heading"), COLUMN_TABLES)
def test_column_tables_list_every_column_with_its_type(page: str, heading: str) -> None:
    schema = COLUMN_TABLES[page, heading]
    header, rows = table(page, heading)
    assert header == ["Column", "Type", "Content"]
    listed = []
    for cell, dtype, content in rows:
        columns = names(cell, schema)
        listed += columns
        for name in columns:
            if dtype:
                assert dtype == type_text(schema[name]), (heading, name)
            else:
                # Key columns without a type are described as those of `clean`
                assert "in `clean`" in content, (heading, name)
                assert schema[name] == CLEAN_SCHEMA[name], (heading, name)
    assert listed == list(schema)


@pytest.mark.parametrize(("page", "heading"), COLUMN_TABLES)
def test_column_tables_name_every_code_of_the_dictionary(page: str, heading: str) -> None:
    schema = COLUMN_TABLES[page, heading]
    _, rows = table(page, heading)
    for cell, _, content in rows:
        columns = names(cell, schema)
        if len(columns) != 1 or columns[0] in CODE_TABLES or not COLUMNS[columns[0]].codes:
            continue
        codes = set(COLUMNS[columns[0]].codes)
        if all(isinstance(code, str) for code in codes):
            # Codes in backticks, such as `amprion`
            named: set[str | int] = set(re.findall(r"`([^`]+)`", content))
        else:
            # Numbers followed by their meaning, such as "1 Einspeisevergütung"
            named = {int(number) for number in re.findall(r"\b(\d+) [A-Z]", content)}
        assert named == codes, (heading, columns[0])


def code(cell: str) -> str:
    """The code in a cell that holds only a code in backticks."""
    match = re.fullmatch(r"`([^`]+)`", cell)
    assert match, cell
    return match[1]


@pytest.mark.parametrize("column", CODE_TABLES)
def test_code_tables_hold_the_codes_of_the_dictionary(column: str) -> None:
    _, rows = table(*CODE_TABLES[column], first=f"`{column}`")
    assert sorted(code(row[0]) for row in rows) == sorted(COLUMNS[column].codes)


def test_summing_energy_names_the_rules_of_the_special_rows() -> None:
    header, rows = table(*CODE_TABLES["sonderzeile"])
    assert header[1:] == ["`verguetungskategorie`", "Content", "Grid feed-in", "Generation"]
    for cell, category, _, fed_in, generated in rows:
        pattern, _ = SPECIAL_ROWS[code(cell)]
        values = re.findall(r"`([^`]+)`", category)
        assert values, cell
        for value in values:
            # The table names the whole start or end: "x" in place of its first or last
            # character no longer fits the rule
            if category.startswith("starts with"):
                example, cut = value + "x", value[:-1] + "x"
            else:
                assert category.startswith("ends in"), cell
                example, cut = "x" + value, "x" + value[1:]
            assert re.search(pattern, example), (cell, value)
            assert not re.search(pattern, cut), (cell, value)
        assert fed_in == ("leave out" if code(cell) in NOT_FED_IN else "keep"), cell
        assert generated == ("leave out" if code(cell) in NOT_GENERATED else "keep"), cell
