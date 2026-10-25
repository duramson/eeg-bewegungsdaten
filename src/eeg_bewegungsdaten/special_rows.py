"""Rows whose amounts need care when you add them up, marked in the column `sonderzeile`.

The kinds are recognised by the start or end of `verguetungskategorie`. The rules hold for 2016
to 2025, where no category fits two of them; a category of a later year may need a new rule.
"""

from __future__ import annotations

import polars as pl

# Code -> (pattern of `verguetungskategorie`, meaning)
SPECIAL_ROWS: dict[str, tuple[str, str]] = {
    "REPEATED_ENERGY": (
        r"FLP$",
        "biomass, probably the flexibility premium; strommenge repeats energy of other rows "
        "of the plant",
    ),
    "SELF_CONSUMPTION": (r"SV$", "self-consumed energy, without payment"),
    "PAID_SELF_CONSUMPTION": (
        r"^SgK3341",
        "self-consumed energy, paid at the full tariff (section 33(2) EEG 2009)",
    ),
    "SELF_CONSUMPTION_DEDUCTION": (
        r"^SgK334[23]",
        "the paid self-consumption again, negative, for the deduction from the tariff",
    ),
    "ENDED_SUPPORT": (
        r"aMW$",
        "value paid for plants whose EEG support has ended; part of the feed-in tariff",
    ),
    "OTHER_DIRECT_MARKETING": (r"SO-DV$", "other direct marketing, without EEG payment"),
}

# Energy that was not fed into the grid, or that other rows hold already. The paid
# self-consumption and its deduction cancel out; both are left out, as neither was fed in.
NOT_FED_IN = (
    "REPEATED_ENERGY",
    "SELF_CONSUMPTION",
    "PAID_SELF_CONSUMPTION",
    "SELF_CONSUMPTION_DEDUCTION",
)
# Energy that other rows hold already, or that takes back energy of other rows.
NOT_GENERATED = ("REPEATED_ENERGY", "SELF_CONSUMPTION_DEDUCTION")


def special_row(category: pl.Expr) -> pl.Expr:
    """The code of `SPECIAL_ROWS` for a Verguetungskategorie, or null for an ordinary row."""
    return pl.coalesce(
        [
            pl.when(category.str.contains(pattern)).then(pl.lit(code))
            for code, (pattern, _) in SPECIAL_ROWS.items()
        ]
    )


def grid_feed_in() -> pl.Expr:
    """True for the rows whose `strommenge` is energy fed into the grid. Their sum matches the
    energy of the EEG annual account."""
    return ~pl.col("sonderzeile").is_in(NOT_FED_IN).fill_null(False)


def generation() -> pl.Expr:
    """True for the rows whose `strommenge` is energy the plant generated: grid feed-in plus
    self-consumption, as the AGEE-Stat counts it."""
    return ~pl.col("sonderzeile").is_in(NOT_GENERATED).fill_null(False)
