# Data license

The license of the EEG-Bewegungsdaten is not settled. This project does not redistribute any
data: it downloads the files to your machine, and you decide what you do with them. This section
is not legal advice.

## What the law allows

§ 51 (8) EnFG ([wording](https://www.gesetze-im-internet.de/enfg/__51.html)):

> Die nach den Absätzen 1 bis 3 veröffentlichten Angaben dürfen zu kommerziellen und
> nichtkommerziellen Zwecken verwendet werden.

The data published under paragraphs 1 to 3 may be used for commercial and non-commercial
purposes.

- The rule came in 2016 as § 77 (5) EEG 2017. Its explanatory memorandum says "Eine Nutzung ist
  grundsätzlich unbeschränkt möglich": use is in principle unrestricted
  ([BT-Drs. 18/10209](https://dserver.bundestag.de/btd/18/102/1810209.pdf), p. 121). The EnFG
  took the rule over unchanged in 2022 (BT-Drs. 20/1630, p. 233).
- netztransparenz.de names no license for the files.
- Open: neither the law nor the memorandum mentions redistributing whole files. The TSOs may
  also hold a database right (§ 87a UrhG).

## Years no longer on the page

- § 51 (5) EnFG sets a minimum period for keeping the data online, not an order to delete them.
  By its wording, § 51 (8) covers older years too.
- The tool downloads these years from paths on netztransparenz.de that no page links any more,
  or from the Wayback Machine (see [Data status](data-status.md)).
- In their [FAQ on the EEG](https://www.netztransparenz.de/de-de/FAQ/FAQErneuerbareEnergienGesetz-EEG),
  the TSOs write that delivering data beyond the legal requirement is "aus rechtlichen Gründen
  nicht möglich" (not possible for legal reasons). They do not say which reasons.
- The tool fetches each file once and keeps it in the local archive. The
  [terms of use](https://archive.org/about/terms) of the Internet Archive apply to the copies
  from the Wayback Machine.

Decide yourself whether you download these years. `download --year 2025` loads the files of
2025, the year on the page in October 2026, and every legend, including the older ones from
paths that no page links any more.

## Personal data

- The TSOs must not publish data on operators who are natural persons, nor the street and house
  number of plants up to 30 kW (§ 51 (3) nos. 1 and 2 EnFG).
- Still, some plant keys (`EEG_Anlagenschluessel`, `anlagenschluessel` in `clean`) contain
  names of people or streets.
- Many rows belong to small PV plants of private persons. With the MaStR number, anyone can look
  up the town, capacity and start of operation of such a plant. The EEG payment is a financial
  figure.
- If you share data derived from the files, you process personal data and are responsible for
  it under the GDPR.

Before you share data:

- Leave out `anlagenschluessel`. A hash does not help: anyone with the raw file can compute it.
  `export` replaces it with a random number, see
  [Export a dataset to share](export.md).
- Show plants up to 30 kW only in aggregates, never row by row.
- Hide every aggregate with fewer than 10 plants, and every aggregate in which one plant makes
  up almost the whole sum.

## MaStR

The MaStR data that `mastr` reads are under the
[Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0)
(DL-DE-BY-2.0). The register says so on its
[download page](https://www.marktstammdatenregister.de/MaStR/Datendownload) and in its
[legal notice](https://www.marktstammdatenregister.de/MaStR/Startseite/Impressum). The license
asks you to name the Bundesnetzagentur, link the license, point to the dataset and mark your
changes. If you share results that contain MaStR data, a source note can read:

> Datenquelle: Marktstammdatenregister der Bundesnetzagentur, Gesamtdatenexport vom TT.MM.JJJJ,
> https://www.marktstammdatenregister.de/MaStR/Datendownload, Datenlizenz Deutschland –
> Namensnennung – Version 2.0 (www.govdata.de/dl-de/by-2-0); Daten verändert (bereinigt,
> verknüpft, aggregiert).

Replace `TT.MM.JJJJ` with the date of the MaStR export you used.
