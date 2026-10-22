"""Check the lines of the text layer and sort them into clean rows and quarantine.

A line goes to quarantine only if its values cannot be read: neither UTF-8 nor cp1252, wrong
number of fields, numbers or Veraeusserungsform that do not parse. Every other problem keeps the
row and adds a code to `qa_flags`, so sums of energy and payments stay complete.

Columns that a file does not have (MaStR numbers before 2020, Monat before 2021, EEG_Einnahmen
before 2024, ...) are null, except Monat, which is 0 ("keine monatliche Zuordnung"). The checks of
a column only run when the file has it. Empty amounts are null, as they carry no value.
"""

from __future__ import annotations

import polars as pl

from eeg_bewegungsdaten.schema import (
    CLEAN_SCHEMA,
    QUARANTINE_SCHEMA,
    TEXT_COLUMNS,
    UENB_MASTR_NR,
    VERAEUSSERUNGSFORM_CODES,
)
from eeg_bewegungsdaten.special_rows import special_row

# Quarantine codes, checked in this order; a line gets the first that applies.
QUARANTINE_CODES = {
    "UNDECODABLE": "line is neither UTF-8 nor cp1252",
    "FIELD_COUNT": "number of fields differs from the header",
    "BAD_VERAEUSSERUNGSFORM": "Veraeusserungsform is empty, or neither 1 to 5 nor a known text",
    "BAD_NUMBER": "an amount (Strommenge, EEG_Zahlung, ...) is not a decimal number",
}

QA_FLAGS = {
    "MASTR_NR_MISSING": "EEG_Mastr_Nr is empty",
    "MASTR_NR_IS_UNIT": "EEG_Mastr_Nr holds a unit number (SEE...) instead of an EEG number",
    "MASTR_NR_MULTIPLE": "EEG_Mastr_Nr holds several numbers, separated by ',' or '/'",
    "MASTR_NR_INVALID": "EEG_Mastr_Nr is not EEG or SEE followed by 12 digits",
    "NO_PLANT_ID": "both EEG_Mastr_Nr and EEG_Anlagenschluessel are empty",
    "NB_MASTR_NR_INVALID": "NB_Mastr_Nr is not SNB followed by 12 digits",
    "UENB_MASTR_NR_MISMATCH": "UENB_Mastr_Nr does not belong to the TSO that published the file",
    "MONAT_EMPTY": "Monat was empty and is read as 0 (annual value)",
    "ENCODING_CP1252": "line was encoded in cp1252 instead of UTF-8",
    "EXTRA_VALUE": "value in a column without a header name; only the text layer keeps it",
    "MONAT_INVALID": "Monat is not 0 to 12; a whole number is kept, anything else is null",
}

_MASTR_NR = r"(?:EEG|SEE)\d{12}"
_SINGLE = rf"^{_MASTR_NR}$"
_MULTIPLE = rf"^{_MASTR_NR}(?:\s*[,/]\s*{_MASTR_NR})+$"
_UNIT = r"^SEE\d{12}$"
_SNB = r"^SNB\d{12}$"
# Decimal comma, optionally with thousands separators (50Hertz 2020, TransnetBW 2021, Amprion 2017)
# or a negative exponent (Amprion 2018 and 2019: rounding noise like '3,9886E-06'). A positive
# exponent means that Excel cut off digits, so the value stays unreadable.
_NUMBER = r"^-?(?:\d+|[1-9]\d{0,2}(?:\.\d{3})+)(?:,\d+)?(?:[eE]-\d+)?$"
# Leading zero: Amprion 2021 writes '09' in 54 rows.
_MONAT = r"^(?:0?\d|1[0-2])$"

NUMBER_COLUMNS = ("strommenge", "eeg_zahlung", "eeg_einnahmen", "vermiedene_netzentgelte")

# Flags that check one column; they only apply to files that have this column.
_FLAG_COLUMNS = {
    "MASTR_NR_MISSING": "eeg_mastr_nr",
    "MASTR_NR_IS_UNIT": "eeg_mastr_nr",
    "MASTR_NR_MULTIPLE": "eeg_mastr_nr",
    "MASTR_NR_INVALID": "eeg_mastr_nr",
    "NB_MASTR_NR_INVALID": "nb_mastr_nr",
    "UENB_MASTR_NR_MISMATCH": "uenb_mastr_nr",
    "MONAT_EMPTY": "monat",
}
_PRESENCE_COLUMNS = (*dict.fromkeys(_FLAG_COLUMNS.values()), "veraeusserungsform")


def check(text: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (clean rows, quarantined lines) for lines in the shape of `TEXT_SCHEMA`."""
    if text.is_empty():
        return CLEAN_SCHEMA.to_frame(), QUARANTINE_SCHEMA.to_frame()

    frame = text.with_columns(
        _empty_to_null(_unquote(pl.col(list(TEXT_COLUMNS)))),
        # Absent columns are null in the text layer and empty fields are '', so note which
        # columns the line has before empty fields become null.
        **{f"_has_{c}": pl.col(c).is_not_null() for c in _PRESENCE_COLUMNS},
        _extra=pl.col("zusatzfelder")
        .list.eval(_unquote(pl.element()).filter(_unquote(pl.element()) != ""))
        .list.first(),
    )
    # Zenodo's reshaped files use R's NA marker for missing amounts. Keep it in the text layer.
    frame = frame.with_columns(pl.col(list(NUMBER_COLUMNS)).replace("NA", None))
    frame = frame.with_columns(_form=_veraeusserungsform_code(pl.col("veraeusserungsform")))
    frame = frame.with_columns(_code=_quarantine_code())
    frame = frame.with_columns(_detail=_quarantine_detail())

    quarantine = frame.filter(pl.col("_code").is_not_null()).select(
        "jahr",
        "uenb",
        "quelldatei",
        "zeile",
        fehlercode="_code",
        detail="_detail",
        rohzeile="rohzeile",
    )
    clean = (
        frame.filter(pl.col("_code").is_null())
        .with_columns(qa_flags=_flags())
        .with_columns(
            veraeusserungsform="_form",
            sonderzeile=special_row(pl.col("verguetungskategorie")),
            monat=_monat(pl.col("monat")),
            **{c: _to_float(pl.col(c)) for c in NUMBER_COLUMNS},
        )
        .select(CLEAN_SCHEMA.names())
        .cast(CLEAN_SCHEMA)
    )
    return clean, quarantine.cast(QUARANTINE_SCHEMA)


def _unquote(expr: pl.Expr) -> pl.Expr:
    """Trim spaces and one pair of quotes around the value (50Hertz and TransnetBW 2016, TenneT
    2023). Quotes inside a value do not occur; the two lines of Amprion 2024 with a separator
    inside quotes fail the field count."""
    return expr.str.strip_chars().str.replace(r'^"(.*)"$', "$1").str.strip_chars()


def _empty_to_null(expr: pl.Expr) -> pl.Expr:
    return pl.when(expr.str.len_bytes() > 0).then(expr)


def _veraeusserungsform_code(expr: pl.Expr) -> pl.Expr:
    """Code 1 to 5, or null if the value is empty or unknown."""
    text = expr.str.to_lowercase().str.replace_many(["ä", "ö", "ü", "ß"], ["ae", "oe", "ue", "ss"])
    return text.replace_strict(VERAEUSSERUNGSFORM_CODES, default=None, return_dtype=pl.Int8)


def _to_float(expr: pl.Expr) -> pl.Expr:
    return (
        expr.str.replace_all(".", "", literal=True)
        .str.replace(",", ".", literal=True)
        .cast(pl.Float64)
    )


def _monat(expr: pl.Expr) -> pl.Expr:
    """0 if empty or absent; a whole number as it is, also outside 0 to 12 (Amprion 2023: 13), if
    it fits Int8; anything else null (Amprion 2021: '1-12')."""
    return (
        pl.when(expr.is_null())
        .then(pl.lit("0"))
        .when(expr.str.contains(r"^\d+$"))
        .then(expr)
        .cast(pl.Int8, strict=False)
    )


def _bad_number(column: str) -> pl.Expr:
    return pl.col(column).is_not_null() & ~pl.col(column).str.contains(_NUMBER)


def _quarantine_code() -> pl.Expr:
    bad_form = pl.col("_has_veraeusserungsform") & pl.col("_form").is_null()
    bad_number = pl.any_horizontal([_bad_number(c) for c in NUMBER_COLUMNS])
    return (
        pl.when(pl.col("kodierung") == "undecodable")
        .then(pl.lit("UNDECODABLE"))
        .when(pl.col("felder") != pl.col("felder_kopf"))
        .then(pl.lit("FIELD_COUNT"))
        .when(bad_form)
        .then(pl.lit("BAD_VERAEUSSERUNGSFORM"))
        .when(bad_number)
        .then(pl.lit("BAD_NUMBER"))
    )


def _shown(column: str) -> pl.Expr:
    return pl.format("{}='{}'", pl.lit(column), pl.col(column).fill_null(""))


def _quarantine_detail() -> pl.Expr:
    code = pl.col("_code")
    cases = [
        (
            code == "FIELD_COUNT",
            pl.format("{} fields, expected {}", pl.col("felder"), pl.col("felder_kopf")),
        ),
        (code == "BAD_VERAEUSSERUNGSFORM", _shown("veraeusserungsform")),
    ]
    for column in NUMBER_COLUMNS:
        cases.append(((code == "BAD_NUMBER") & _bad_number(column), _shown(column)))
    # The first matching case wins, like a when/then chain.
    return pl.coalesce([pl.when(condition).then(text) for condition, text in cases])


def _flags() -> pl.Expr:
    mastr_nr = pl.col("eeg_mastr_nr")
    multiple = mastr_nr.str.contains(_MULTIPLE)
    invalid = mastr_nr.is_not_null() & ~mastr_nr.str.contains(_SINGLE) & ~multiple
    monat = pl.col("monat")
    nb = pl.col("nb_mastr_nr")
    uenb_nr = pl.col("uenb_mastr_nr")
    expected_uenb_nr = pl.col("uenb").replace_strict(UENB_MASTR_NR, default=None)
    checks = {
        "MASTR_NR_MISSING": mastr_nr.is_null(),
        "MASTR_NR_IS_UNIT": mastr_nr.str.contains(_UNIT),
        "MASTR_NR_MULTIPLE": multiple,
        "MASTR_NR_INVALID": invalid,
        "NO_PLANT_ID": mastr_nr.is_null() & pl.col("anlagenschluessel").is_null(),
        "NB_MASTR_NR_INVALID": nb.is_null() | ~nb.str.contains(_SNB),
        "UENB_MASTR_NR_MISMATCH": uenb_nr.is_null() | (uenb_nr != expected_uenb_nr),
        "MONAT_EMPTY": monat.is_null(),
        "ENCODING_CP1252": pl.col("kodierung") == "cp1252",
        "EXTRA_VALUE": pl.col("_extra").is_not_null(),
        "MONAT_INVALID": monat.is_not_null() & ~monat.str.contains(_MONAT),
    }
    assert checks.keys() == QA_FLAGS.keys()
    for code, column in _FLAG_COLUMNS.items():
        checks[code] = pl.col(f"_has_{column}") & checks[code]
    return pl.concat_list(
        [pl.when(condition).then(pl.lit(code)) for code, condition in checks.items()]
    ).list.drop_nulls()
