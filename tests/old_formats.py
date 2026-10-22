"""Headers and lines in the layouts of 2011 to 2024, after the real files.

The headers are copied from the files; all IDs and names in the lines are made up and only follow
the shape of the real ones. Most files up to 2023 are cp1252 throughout, see `cp1252_csv`.
"""

from __future__ import annotations

from tests.builders import UENB_NR

# 2017 to 2019, all TSOs except Amprion 2018 and 2019.
HEADER_2017 = (
    "EEG_Anlagenschluessel;NB_BNR;Netzbetreiber;Verguetungskategorie;Strommenge;EEG_Zahlung;"
    "Vermiedene_Netzentgelte"
)
# 2016: one layout per TSO.
HEADER_50HERTZ_2016 = (
    '"ROWNUM";"BNETZANUMMER";"NAME";"ANLAGENNUMMER";"EEG_VERGUETUNGSGRUPPE";"PHYS [KWH]";'
    '"FINZ [€]";"VNNE [€]"'
)
HEADER_TENNET_2016 = (
    "Anlagenschlüssel;Netzbetreiber Betriebsnummer;Netzbetreiber;Kategorie;Einspeisung;Vergütung;"
    "vNNE"
)
HEADER_TRANSNETBW_2016 = (
    " ID;Netzbetreiber Betriebsnummer;Netzbetreiber Name;Anlagenschlüssel;Kategorie;"
    "in 2016 eingespeiste Jahresarbeit [kWh];"
    "Vergütung für in 2016 abgenommene EEG-Einspeisung [EUR];"
    "vermiedene Netznutzungsentgelte (vNNE) [EUR]"
)
HEADER_AMPRION_2016_VNE = "ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;VNNE_EURO;BEMESSUNGSLEISTUNG_KW"
HEADER_AMPRION_2016_VGT = "ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;MENGE_KWH;BETRAG_EURO"
# Amprion 2017, file "nur vNE"; the other file has the same columns without ZEILEN_NR.
HEADER_AMPRION_2017 = (
    "ZEILEN_NR;ANLAGENSCHLUESSEL;NETZBETREIBER_BETRIEBSNUMMER;NETZBETREIBER_NAME;KATEGORIE;"
    "JAHRESARBEIT_KWH;VERGUETUNG_EURO;VNNE_EURO"
)
# Amprion 2018: TYP in all files except "nur vNE"; 2019 has no TYP at all.
HEADER_AMPRION_2018 = (
    "EEG_ANLAGENSCHLUESSEL;NB_BNR;UENB_BNR;VERGUETUNGSKATEGORIE;STROMMENGE;EEG_ZAHLUNG;"
    "VERMIEDENE_NETZENTGELTE;TYP"
)
HEADER_AMPRION_2018_VNE = HEADER_AMPRION_2018.removesuffix(";TYP")
# 2020: MaStR numbers and Veraeusserungsform as text.
HEADER_2020 = (
    "EEG_Anlagenschlüssel;EEG_Mastr_Nr;NB_BNR;NB_Mastr_Nr;Netzbetreiber;ÜNB_Mastr_Nr;"
    "Vergütungskategorie;Strommenge;EEG_Zahlung;Vermiedene_Netzentgelte;Veräußerungsform"
)
HEADER_TRANSNETBW_2020 = (
    "EEG-Anlagenschlüssel;EEG_Mastr_Nr;NB_BNR;NB_Mastr_Nr;Netzbetreiber;ÜNB_Mastr_Nr;"
    "Vergütungskategorie;Strommenge (in kWh);EEG_Zahlung (in Euro);"
    "Vermiedene_Netzentgelte (in Euro);Veräußerungsform"
)
# 2021 and 2022: Monat added.
HEADER_2021 = HEADER_2020 + ";Monat(0 = keine monatl. Zuordnung)"
HEADER_TRANSNETBW_2022 = (
    "EEG-Anlagenschluessel;EEG_Mastr_Nr;NB_BNR;NB_Mastr_Nr;Firma Netzbetreiber;UENB_Mastr_Nr;"
    "Verguetungskategorie;Strommenge;EEG_Zahlung;Vermiedene_Netzentgelte;Veraeusserungsform;"
    "Monat (0 = keine monatliche Zuordnung)"
)
# Amprion 2022 and 2023, TransnetBW 2023: the layout of 2024 with vNNE instead of EEG_Einnahmen.
HEADER_AMPRION_2022 = (
    "EEG_Mastr_Nr;EEG_Anlagenschluessel;NB_Mastr_Nr;UENB_Mastr_Nr;Veraeusserungsform;"
    "Verguetungskategorie;Strommenge;EEG_Zahlung;Vermiedene_Netzentgelte;Monat"
)
# TenneT 2024, first upload (September 2025, three parts): the layout of 2024 without Monat.
HEADER_TENNET_2024_FIRST = (
    "EEG_Mastr_Nr;EEG_Anlagenschlüssel;NB_Mastr_Nr;ÜNB_Mastr_Nr;Veräußerungsform;"
    "Vergütungskategorie;Strommenge;EEG_Zahlung;EEG_Einnahmen"
)
# TenneT 2023: a TXT file, every field quoted except the Veraeusserungsform code.
HEADER_TENNET_2023 = (
    '"EEG_Anlagenschlüssel";"EEG_Mastr_Nr";"NB_BNR";"NB_Mastr_Nr";"Netzbetreiber";'
    '"ÜNB_Mastr_Nr";"Vergütungskategorie";"Strommenge";"EEG_Zahlung";"Vermiedene_Netzentgelte";'
    '"Veräußerungsform";"Monat"'
)

# Before 2016, from the TSOs' own sites: one layout per file.
HEADER_TENNET_2011 = "ÜNB;BNetzA-ID;Firma;Anlagenschlüssel;Kategorie;Einspeisung;Vergütung;vNNE"
HEADER_TENNET_2012 = (
    "Firma;Jahr;Anlagenschlüssel;Kategorie;Einsatzstoff Biomasse;Einspeisung [kWh];Vergütung [Euro]"
)
HEADER_TENNET_2013 = "BNetzA-ID;Firma;Anlagenschlüssel;Kategorie;Einspeisung;Vergütung;vNNE"
HEADER_TENNET_2014 = "BNetzA-ID;Anlagenschlüssel;Kategorie;Einspeisung;Vergütung;vNNE"
HEADER_AMPRION_2011 = (
    "LFD_NR;ANLAGENSCHLUESSEL;STRASSE;PLZ;ORT;BUNDESLAND;VERGUETUNGSKATEGORIE;MENGE;EURO;vNe"
)
HEADER_AMPRION_2013 = "ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;MENGE [KWH];BETRAG [EURO]"
HEADER_50HERTZ_2014 = "NETZBETREIBER;ANLAGENSCHLUESSEL;VERGUETUNGSKATEGORIE;KWH;VERGUETUNG;VNNE"
HEADER_50HERTZ_2015 = (
    "ROWNUM;BNETZANUMMER;NAME;ANLAGENSCHLUESSEL;EEG_VERGUETUNGSGRUPPE;PHYS [KWH];VERGÜTUNG [€];"
    "VNNE [€]"
)

KEY = "E90000000000000000000000000000002"


def row_2020(
    *,
    key: str = KEY,
    mastr_nr: str = "EEG900000000001",
    nb_bnr: str = "10009001",
    nb: str = "SNB900000000001",
    name: str = "Elektrizitätswerk Musterdorf GmbH",
    uenb: str = "tennet",
    category: str = "SgK4820--Mrz19",
    energy: str = "6210,000",
    payment: str = "697,38",
    vnne: str = "0,00",
    form: str = "Einspeisevergütung",
    month: str | None = None,
) -> str:
    """One line in the layout of 2020, or of 2021 and 2022 with `month`."""
    fields = [key, mastr_nr, nb_bnr, nb, name, UENB_NR[uenb], category, energy, payment, vnne]
    fields.append(form)
    if month is not None:
        fields.append(month)
    return ";".join(fields)


def cp1252_csv(header: str, *lines: str) -> bytes:
    """A whole file in cp1252 with CRLF, like most files up to 2023."""
    return "".join(f"{line}\r\n" for line in [header, *lines]).encode("cp1252")
