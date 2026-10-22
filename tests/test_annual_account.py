"""Sums of the real files of 2023 to 2025, and of the files before 2016, against the
EEG-Jahresabrechnung. Skipped unless pytest runs with --realdata.

The official figures are the column "gesamt" of the tables "Einspeisevergütungen in Euro",
"Marktprämie in Euro" and "Strommengen in der sonstigen Direktvermarktung in kWh" in the
EEG-Jahresabrechnung of each year on netztransparenz.de, read on 18 October 2026. For the years
before 2016 they come from the copies of the accounts in the Wayback Machine (see
docs/findings.md), read on 22 October 2026.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.download import ArchivedFile
from eeg_bewegungsdaten.ingest import ingest, latest_uploads
from eeg_bewegungsdaten.paths import default_data_dir
from tests.test_realdata import EXPECTED

# (year, TSO): feed-in tariff in euros, market premium in euros, other direct marketing in kWh
OFFICIAL: dict[tuple[int, str], tuple[str, str, str]] = {
    (2023, "50hertz"): ("1.133.479.404,10", "2.120.962.281,13", "7.962.180.487"),
    (2023, "amprion"): ("2.383.981.696,86", "1.176.001.286,70", "5.282.056.819"),
    (2023, "tennet"): ("3.977.713.455,85", "4.470.610.756,61", "11.459.472.665"),
    (2023, "transnetbw"): ("1.585.870.765,08", "524.009.436,91", "1.059.607.070"),
    (2024, "50hertz"): ("1.237.372.675,94", "2.712.114.368,21", "9.751.909.932"),
    (2024, "amprion"): ("2.284.900.024,12", "1.447.789.260,31", "3.948.481.207"),
    (2024, "tennet"): ("3.949.361.995,11", "5.274.913.395,14", "13.938.819.026"),
    (2024, "transnetbw"): ("1.533.180.482,69", "634.096.519,05", "900.582.049"),
    (2025, "50hertz"): ("1.256.376.754,63", "2.230.499.119,13", "11.475.742.298"),
    (2025, "amprion"): ("2.502.574.942,73", "1.183.124.672,81", "4.224.553.500"),
    (2025, "tennet"): ("4.081.530.433,57", "4.163.332.421,17", "17.059.096.259"),
    (2025, "transnetbw"): ("1.618.941.836,82", "555.361.727,34", "1.157.473.564"),
}

# (year, TSO): the "gesamt" columns of the payment tables of the year (feed-in tariff; from 2012
# also market premium and flexibility premium) in euros, and the avoided grid fees in euros. The
# files before 2016 have no Veraeusserungsform, so all payments are compared at once. No account
# of 2012 or 2013 is online, so the files of those years cannot be checked.
OFFICIAL_BEFORE_2016: dict[tuple[int, str], tuple[tuple[str, ...], str]] = {
    (2011, "amprion"): (("3.490.580.815,11",), "72.619.864,64"),
    (2011, "tennet"): (("7.448.754.005,32",), "148.996.087,49"),
    (2014, "50hertz"): (
        ("2.030.406.066,55", "2.937.385.789,18", "3.699.036,38"),
        "240.253.463,96",
    ),
    (2014, "tennet"): (
        ("5.683.541.602,29", "3.705.518.273,83", "9.736.496,45"),
        "307.331.896,76",
    ),
    (2015, "50hertz"): (
        ("2.021.424.234,32", "3.643.053.326,44", "6.529.123,40"),
        "266.489.503,53",
    ),
    (2015, "tennet"): (
        ("5.484.527.333,76", "5.510.420.157,60", "23.309.573,61"),
        "325.180.591,05",
    ),
}


def euros(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def upload_of(jahr: int, uenb: str) -> list[ArchivedFile]:
    """The archived upload with the SHA-256 of `EXPECTED`, or skip the test."""
    uploads = [
        files
        for files in latest_uploads(default_data_dir())
        if (files[0].meta["year"], files[0].meta["uenb"]) == (jahr, uenb)
    ]
    if not uploads or tuple(f.meta["sha256"] for f in uploads[0]) != EXPECTED[(jahr, uenb)][0]:
        pytest.skip(f"{jahr} file of {uenb} not downloaded or changed upstream")
    return uploads[0]


@pytest.mark.realdata
@pytest.mark.parametrize(("jahr", "uenb"), sorted(OFFICIAL), ids=str)
def test_sums_match_the_annual_account(jahr: int, uenb: str, tmp_path: Path) -> None:
    report = ingest(upload_of(jahr, uenb), tmp_path)
    columns = ["veraeusserungsform", "sonderzeile", "strommenge", "eeg_zahlung"]
    rows = pl.read_parquet(report["outputs"]["clean"], columns=columns)
    form = pl.col("veraeusserungsform")
    special = pl.col("sonderzeile")

    # Form 1 leaves out the fallback payment (form 4) and the value paid for plants whose support
    # has ended (legend), which form 5 holds in categories ending in "aMW".
    feed_in = form.is_in([1, 4]) | ((form == 5) & (special == "ENDED_SUPPORT"))
    sums = [
        rows.filter(feed_in)["eeg_zahlung"].sum(),
        rows.filter(form == 2)["eeg_zahlung"].sum(),
        rows.filter(special == "OTHER_DIRECT_MARKETING")["strommenge"].sum(),
    ]
    official = [euros(v) for v in OFFICIAL[(jahr, uenb)]]
    assert sums == pytest.approx(official, rel=1e-4)


@pytest.mark.realdata
@pytest.mark.parametrize(("jahr", "uenb"), sorted(OFFICIAL_BEFORE_2016), ids=str)
def test_sums_before_2016_match_the_annual_account(jahr: int, uenb: str, tmp_path: Path) -> None:
    report = ingest(upload_of(jahr, uenb), tmp_path)

    payments, avoided_fees = OFFICIAL_BEFORE_2016[(jahr, uenb)]
    sums = [report["sums_clean"]["eeg_zahlung"], report["sums_clean"]["vermiedene_netzentgelte"]]
    assert sums == pytest.approx([sum(euros(v) for v in payments), euros(avoided_fees)], rel=1e-4)
