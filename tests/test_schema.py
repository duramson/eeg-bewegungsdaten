from __future__ import annotations

import pytest

from eeg_bewegungsdaten.schema import (
    IGNORED_PREFIX,
    LAYOUTS,
    FormatError,
    map_header,
    normalize_header,
)
from tests.builders import (
    HEADER,
    HEADER_AMPRION_EXTRA,
    HEADER_TENNET,
    HEADER_TRANSNETBW_1,
    HEADER_TRANSNETBW_2,
)
from tests.old_formats import (
    HEADER_50HERTZ_2014,
    HEADER_50HERTZ_2015,
    HEADER_50HERTZ_2016,
    HEADER_2017,
    HEADER_AMPRION_2011,
    HEADER_AMPRION_2013,
    HEADER_AMPRION_2016_VGT,
    HEADER_AMPRION_2016_VNE,
    HEADER_AMPRION_2017,
    HEADER_AMPRION_2018,
    HEADER_AMPRION_2018_VNE,
    HEADER_AMPRION_2022,
    HEADER_TENNET_2011,
    HEADER_TENNET_2012,
    HEADER_TENNET_2013,
    HEADER_TENNET_2014,
    HEADER_TENNET_2016,
    HEADER_TENNET_2023,
    HEADER_TENNET_2024_FIRST,
    HEADER_TRANSNETBW_2016,
    HEADER_TRANSNETBW_2020,
    HEADER_TRANSNETBW_2022,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EEG_Mastr_Nr", "eeg_mastr_nr"),
        ("﻿EEG_Mastr_Nr", "eeg_mastr_nr"),
        ("EEG-Anlagenschluessel", "eeg_anlagenschluessel"),
        ("Veraeusserungsform (Code)", "veraeusserungsform"),
        ("Veräußerungsform", "veraeusserungsform"),
        (" Monat ", "monat"),
        ("", ""),
        ("ÜNB_Mastr_Nr", "uenb_mastr_nr"),
        ("Veräusserungsform", "veraeusserungsform"),  # TransnetBW 2021
        ("Monat(0 = keine monatl. Zuordnung) ", "monat"),
        ('"Strommenge"', "strommenge"),  # TenneT 2023
        ('"FINZ [€]"', "finz"),  # 50Hertz 2016
        ("vermiedene Netznutzungsentgelte (vNNE) [EUR]", "vermiedene_netznutzungsentgelte"),
        ("in 2016 eingespeiste Jahresarbeit [kWh]", "in_eingespeiste_jahresarbeit"),
        (" ID", "id"),
        ("BNetzA-ID", "bnetza_id"),  # TenneT 2011
        ("MENGE [KWH]", "menge"),  # Amprion 2013
        ("VERGÜTUNG [€]", "verguetung"),  # 50Hertz 2015
    ],
)
def test_normalize_header(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


@pytest.mark.parametrize(
    "header", [HEADER, HEADER_TRANSNETBW_1, HEADER_TRANSNETBW_2, HEADER_AMPRION_EXTRA]
)
def test_all_ten_column_layouts_map_to_the_same_names(header: str) -> None:
    mapped = map_header(header.split(";"), "x.csv")

    assert mapped[:10] == [
        "eeg_mastr_nr",
        "anlagenschluessel",
        "nb_mastr_nr",
        "uenb_mastr_nr",
        "veraeusserungsform",
        "verguetungskategorie",
        "strommenge",
        "eeg_zahlung",
        "eeg_einnahmen",
        "monat",
    ]
    assert mapped[10:] == [None] * (len(mapped) - 10)


def test_tennet_layout_keeps_its_extra_columns() -> None:
    mapped = map_header(HEADER_TENNET.split(";"), "x.csv")

    assert mapped[2] == "nb_bnr"
    assert mapped[4] == "netzbetreiber"


UP_TO_2019 = [
    "anlagenschluessel",
    "nb_bnr",
    "netzbetreiber",
    "verguetungskategorie",
    "strommenge",
    "eeg_zahlung",
    "vermiedene_netzentgelte",
]
FROM_2020 = [
    "anlagenschluessel",
    "eeg_mastr_nr",
    "nb_bnr",
    "nb_mastr_nr",
    "netzbetreiber",
    "uenb_mastr_nr",
    "verguetungskategorie",
    "strommenge",
    "eeg_zahlung",
    "vermiedene_netzentgelte",
    "veraeusserungsform",
]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param(
            HEADER_TENNET_2011,
            [
                IGNORED_PREFIX + "uenb",
                "nb_bnr",
                "netzbetreiber",
                "anlagenschluessel",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
            ],
            id="tennet-2011",
        ),
        pytest.param(
            HEADER_TENNET_2012,
            [
                "netzbetreiber",
                IGNORED_PREFIX + "jahr",
                "anlagenschluessel",
                "verguetungskategorie",
                IGNORED_PREFIX + "einsatzstoff_biomasse",
                "strommenge",
                "eeg_zahlung",
            ],
            id="tennet-2012",
        ),
        pytest.param(
            HEADER_TENNET_2013,
            ["nb_bnr", "netzbetreiber", *UP_TO_2019[:1], *UP_TO_2019[3:]],
            id="tennet-2013-and-2015",
        ),
        pytest.param(
            HEADER_TENNET_2014, ["nb_bnr", *UP_TO_2019[:1], *UP_TO_2019[3:]], id="tennet-2014"
        ),
        pytest.param(
            HEADER_AMPRION_2011,
            [
                IGNORED_PREFIX + "lfd_nr",
                "anlagenschluessel",
                IGNORED_PREFIX + "strasse",
                IGNORED_PREFIX + "plz",
                IGNORED_PREFIX + "ort",
                IGNORED_PREFIX + "bundesland",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
            ],
            id="amprion-2011",
        ),
        pytest.param(
            HEADER_AMPRION_2013,
            ["anlagenschluessel", "verguetungskategorie", "strommenge", "eeg_zahlung"],
            id="amprion-2013",
        ),
        pytest.param(
            HEADER_50HERTZ_2014,
            [
                "netzbetreiber",
                "anlagenschluessel",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
            ],
            id="50hertz-2014",
        ),
        pytest.param(
            HEADER_50HERTZ_2015,
            [
                IGNORED_PREFIX + "rownum",
                "nb_bnr",
                "netzbetreiber",
                "anlagenschluessel",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
            ],
            id="50hertz-2015",
        ),
        pytest.param(
            HEADER_50HERTZ_2016,
            [
                IGNORED_PREFIX + "rownum",
                "nb_bnr",
                "netzbetreiber",
                "anlagenschluessel",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
            ],
            id="50hertz-2016",
        ),
        pytest.param(HEADER_TENNET_2016, UP_TO_2019, id="tennet-2016"),
        pytest.param(
            HEADER_TRANSNETBW_2016,
            [IGNORED_PREFIX + "id", "nb_bnr", "netzbetreiber", *UP_TO_2019[:1], *UP_TO_2019[3:]],
            id="transnetbw-2016",
        ),
        pytest.param(
            HEADER_AMPRION_2016_VNE,
            [
                "anlagenschluessel",
                "verguetungskategorie",
                "vermiedene_netzentgelte",
                IGNORED_PREFIX + "bemessungsleistung_kw",
            ],
            id="amprion-2016-vne",
        ),
        pytest.param(
            HEADER_AMPRION_2016_VGT,
            ["anlagenschluessel", "verguetungskategorie", "strommenge", "eeg_zahlung"],
            id="amprion-2016-vgt",
        ),
        pytest.param(
            HEADER_AMPRION_2017, [IGNORED_PREFIX + "zeilen_nr", *UP_TO_2019], id="amprion-2017"
        ),
        pytest.param(HEADER_2017, UP_TO_2019, id="2017-2019"),
        pytest.param(
            HEADER_AMPRION_2018,
            [
                "anlagenschluessel",
                "nb_bnr",
                IGNORED_PREFIX + "uenb_bnr",
                *UP_TO_2019[3:],
                "veraeusserungsform",
            ],
            id="amprion-2018",
        ),
        pytest.param(
            HEADER_AMPRION_2018_VNE,
            ["anlagenschluessel", "nb_bnr", IGNORED_PREFIX + "uenb_bnr", *UP_TO_2019[3:]],
            id="amprion-2018-vne",
        ),
        pytest.param(HEADER_TRANSNETBW_2020, FROM_2020, id="transnetbw-2020"),
        pytest.param(HEADER_TRANSNETBW_2022, [*FROM_2020, "monat"], id="transnetbw-2022"),
        pytest.param(
            HEADER_AMPRION_2022,
            [
                "eeg_mastr_nr",
                "anlagenschluessel",
                "nb_mastr_nr",
                "uenb_mastr_nr",
                "veraeusserungsform",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "vermiedene_netzentgelte",
                "monat",
            ],
            id="amprion-2022",
        ),
        pytest.param(HEADER_TENNET_2023, [*FROM_2020, "monat"], id="tennet-2023"),
        pytest.param(
            HEADER_TENNET_2024_FIRST,
            [
                "eeg_mastr_nr",
                "anlagenschluessel",
                "nb_mastr_nr",
                "uenb_mastr_nr",
                "veraeusserungsform",
                "verguetungskategorie",
                "strommenge",
                "eeg_zahlung",
                "eeg_einnahmen",
            ],
            id="tennet-2024-first-upload",
        ),
    ],
)
def test_older_headers_map_to_the_common_names(header: str, expected: list[str]) -> None:
    assert map_header(header.split(";"), "x.csv") == expected


def test_layouts_are_distinct() -> None:
    assert len(set(LAYOUTS)) == len(LAYOUTS)


def test_columns_of_different_years_do_not_mix() -> None:
    """vNNE was dropped when EEG_Einnahmen came in 2024."""
    with pytest.raises(FormatError, match=r"unexpected columns \['vermiedene_netzentgelte'\]"):
        map_header([*HEADER.split(";"), "Vermiedene_Netzentgelte"], "x.csv")


def test_unknown_column_is_an_error() -> None:
    with pytest.raises(FormatError, match="unknown column 'Leistung'"):
        map_header([*HEADER.split(";"), "Leistung"], "x.csv")


def test_missing_column_is_an_error() -> None:
    header = HEADER.replace("Strommenge;", "").split(";")
    with pytest.raises(FormatError, match=r"missing columns \['strommenge'\]"):
        map_header(header, "x.csv")


def test_duplicate_column_is_an_error() -> None:
    with pytest.raises(FormatError, match="duplicate"):
        map_header([*HEADER.split(";"), "Monat"], "x.csv")
