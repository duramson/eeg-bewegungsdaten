# Findings

What the files of 2011 to 2025 hold, and how well `eeg-bewegungsdaten` reads them and
matches them with the MaStR. The [README](../README.md) describes the tool and its output.
`uv run pytest --realdata` checks the line counts and the sums against the annual account.

## Reading the files

### Formats of 2011 to 2025

| Years | Layout | Notes |
| --- | --- | --- |
| 2011–2015 | own layout per file | the nine files from the TSOs' own sites (see [Older years](../README.md#older-years)); TenneT 2011, 2013 and 2015 and 50Hertz 2015 have the columns of 2016; TenneT 2014 has the BDEW number of the grid operator without its name, 50Hertz 2014 the name without the number; TenneT 2012 has no vNNE, and a year and a biomass fuel column; Amprion 2011 adds the plant address (kept only in `rohzeile`); of Amprion 2013 only the payments file survives, not the vNNE file; TenneT 2015 is a TXT file, 50Hertz 2014 and 2015 a bare CSV |
| 2016 | own layout per TSO | other column names, Amprion in two files (payments, vNNE), 50Hertz and TransnetBW quote names and add a row number |
| 2017–2019 | `EEG_Anlagenschluessel;NB_BNR;Netzbetreiber;Verguetungskategorie;Strommenge;EEG_Zahlung;Vermiedene_Netzentgelte` | no MaStR numbers, no `Veraeusserungsform` (Amprion 2018: `TYP`), no `Monat`; Amprion 2018–2019 without name, with the TSO's BDEW number |
| 2020 | 11 columns | MaStR numbers added, `Veraeusserungsform` as text |
| 2021–2022 | 12 columns | `Monat` added; Amprion 2022 uses the 10 columns of 2023 |
| 2023 | 12 or 10 columns | `Veraeusserungsform` as code 1 to 5; TenneT one TXT file with quoted fields |
| 2024 | 10 columns | `EEG_Einnahmen` instead of `Vermiedene_Netzentgelte`; TenneT in four ZIPs |
| 2025 | 10 columns | TenneT adds `NB_BNR` and `Netzbetreiber` (12 columns) |

Up to 2023 most files are cp1252 throughout (50Hertz 2014: UTF-8 with a byte-order mark). In
2024 and 2025, Amprion and TransnetBW use
cp1252, 50Hertz and TenneT UTF-8. Some files use thousands separators (`138.981,03`), Amprion
2018 and 2019 pad values with spaces and write rounding noise as `3,9886E-06`.

### Results for 2025

| TSO | Lines | `clean` | `quarantine` | Most frequent `qa_flags` |
| --- | ---: | ---: | ---: | --- |
| 50Hertz | 1,400,862 | 1,400,862 | 0 | 9,606 `MASTR_NR_MISSING`, 2 `MASTR_NR_INVALID` |
| Amprion | 2,626,635 | 2,626,635 | 0 | 185,245 `MASTR_NR_IS_UNIT`, 26,354 `MASTR_NR_MISSING`, 4,483 `MASTR_NR_INVALID`, 199 `ENCODING_CP1252` |
| TenneT | 3,791,801 | 3,791,801 | 0 | 89,829 `MASTR_NR_MISSING`, 2,666 `MASTR_NR_MULTIPLE` |
| TransnetBW | 1,499,225 | 1,499,225 | 0 | 1,016,002 `MONAT_EMPTY`, 6,135 `MASTR_NR_MISSING`, 152 `ENCODING_CP1252` |

### Results for 2011 to 2024

Lines per year and TSO; in brackets the lines in quarantine. Before 2016 only the nine files
that survive.

| Year | 50Hertz | Amprion | TenneT | TransnetBW |
| --- | ---: | ---: | ---: | ---: |
| 2011 | – | 801,547 (8) | 1,226,229 | – |
| 2012 | – | – | 1,620,764 | – |
| 2013 | – | 826,698 | 1,828,808 | – |
| 2014 | 567,565 | – | 1,947,532 | – |
| 2015 | 614,756 (1) | – | 2,022,732 | – |
| 2016 | 551,649 | 1,378,047 | 2,062,201 | 779,341 |
| 2017 | 580,962 | 1,431,901 | 2,131,567 | 807,715 |
| 2018 | 610,064 | 1,396,775 | 2,218,870 | 870,025 |
| 2019 | 865,543 | 1,464,286 | 2,331,902 | 911,459 |
| 2020 | 728,044 (8) | 1,095,300 | 2,283,220 | 658,298 |
| 2021 | 819,125 | 1,642,213 | 2,184,870 | 880,281 |
| 2022 | 872,951 | 1,800,954 | 2,562,077 | 1,027,094 |
| 2023 | 1,017,446 | 2,056,831 | 2,853,278 | 1,169,258 |
| 2024 | 1,158,674 | 2,249,058 (2) | 3,404,359 | 1,367,361 |

What stands out:

- **Amprion 2011:** 4 rows of one plant have a line break inside the quoted town, so 8 lines
  fail `FIELD_COUNT`.
- **50Hertz 2015:** the last line is a totals line (`5.671.006.684,16 €`), `BAD_NUMBER`.
- **50Hertz 2020:** 8 plant keys contain DOS umlauts (`0x81` for `ü`), so the lines are
  `UNDECODABLE`.
- **Amprion 2021:** 3 rows have `Monat` `1-12`. They get `MONAT_INVALID`, and `monat` is null.
- **Amprion 2023:** 994 rows in the DV file have `Monat` `13`, with 0.64 million euros of market
  premium. They get `MONAT_INVALID`, and `monat` stays 13.
- **Amprion 2024:** 2 lines hold two MaStR numbers in quotes, separated by `;`
  (`FIELD_COUNT`).
- **TransnetBW 2024:** the header of part 2 ends with `;`, and 3,622 rows have a `1` in that
  unnamed column (`EXTRA_VALUE`). These rows hold 1.95 million euros and 20 GWh. The `1` is no
  shifted month: in 3,619 of them `Monat` is empty, and they are mostly annual rows of the feed-in
  tariff (form 1).
- **TransnetBW 2021** has exactly 1,048,576 lines, Excel's row limit, but the last 168,294 are
  empty (`;;;;;;;;;;;`); the data are complete.
- **TransnetBW 2024 part 1** also has exactly 1,048,576 lines, with data up to the last one, but
  no rows are missing. Part 1 holds only categories starting with `Sg` (probably solar on
  buildings). The 102,167 `Sg` rows that did not fit are in part 2, together with all other
  categories; the last plant of part 1 continues there. The sums match the EEG annual account (see
  [Checks against the EEG annual account](#checks-against-the-eeg-annual-account)). In 2025,
  TransnetBW cuts part 1 the same way at 1,000,000 rows. If you use the CSV files directly,
  always read both parts.
- **TransnetBW 2020** appends `AN` to every `NB_Mastr_Nr` (`NB_MASTR_NR_INVALID`).
- **TenneT 2024** has 937 rows with `SNBFIKTIV…` (`NB_MASTR_NR_INVALID`).
- `ENCODING_CP1252` marks every line with an umlaut in the cp1252 files, up to 2.4 million per
  file.

## Energy and payments

### Sums per year

Sums over all clean rows, in TWh and billion euros. The energy in FLP rows repeats energy of
other rows (see [Special rows](#special-rows)), in every year:

| Year | `strommenge` | of it in FLP rows | without FLP rows | `eeg_zahlung` |
| --- | ---: | ---: | ---: | ---: |
| 2016 | 167.2 | 4.9 | 162.3 | 24.35 |
| 2017 | 194.2 | 5.8 | 188.5 | 26.03 |
| 2018 | 203.7 | 7.0 | 196.6 | 25.71 |
| 2019 | 222.8 | 9.5 | 213.4 | 27.63 |
| 2020 | 234.4 | 10.6 | 223.8 | 29.84 |
| 2021 | 217.0 | 11.5 | 205.5 | 19.65 |
| 2022 | 234.0 | 11.6 | 222.3 | 12.32 |
| 2023 | 249.1 | 11.4 | 237.7 | 17.60 |
| 2024 | 250.4 | 10.1 | 240.3 | 19.30 |
| 2025 | 251.0 | 6.9 | 244.0 | 17.82 |

Per plant and year, the FLP energy mostly equals or stays below the energy of the plant's other
rows. Where it is larger, the excess adds up to at most 0.06 TWh per TSO and year (TenneT 2024,
101 plants). Amprion writes 0 kWh in all its FLP rows, so its sums are not affected.

### Special rows

What the kinds of rows in `sonderzeile` hold (see
[Summing energy](../README.md#summing-energy)):

- **Repeated energy:** The `FLP` rows probably carry the flexibility premium. Their `strommenge`
  repeats energy that the other rows of the same plant already contain. In 2025 these are 3,256
  rows with 6.9 TWh: 1,863 match the other rows of the plant exactly, and only 18 are larger.
  The older years have the same rows, with 4.9 to 11.6 TWh per year (see
  [Sums per year](#sums-per-year)). The annual account counts none of this energy. Keep these
  rows for payments.
- **Self-consumption:** The `SV` rows hold 0.8 TWh in 2016 and 2.1 to 2.6 TWh per year since
  2021, mostly from PV on buildings. The annual account counts none of this energy. The
  AGEE-Stat counts it as generation (see
  [Full-load hours of PV](../analysis/README.md#full-load-hours-of-pv)).
- **Paid self-consumption (EEG 2009):** PV plants of 2009 to 2012 list their paid
  self-consumption twice: positive at the full tariff and negative for the deduction of 16.38
  or 12 ct/kWh. Both sides hold 0.78 to 0.89 TWh per year and cancel out to less than 0.2 GWh
  in every year from 2016 to 2025.
- **Ended support:** The `aMW` rows are in `veraeusserungsform` 5, but the annual account counts
  their payment to the feed-in tariff.
- **Other direct marketing:** In 2025, 34 TWh are in `SO-DV` rows, without any EEG payment. The
  annual account lists them as "sonstige Direktvermarktung" and counts them as grid feed-in.
  Whether you need them depends on your question.
- **Not marked:** A few categories have `SV` inside, such as `SgK-kVG--SV--0` and
  `SgK2511-SV---0`. They hold 2 to 19 GWh per year and almost no payment; what they mean is
  unclear.

### Checks against the EEG annual account

The TSOs publish the audited sums of each year as the
[EEG-Jahresabrechnung](https://www.netztransparenz.de/de-de/Erneuerbare-Energien-und-Umlagen/EEG/EEG-Abrechnungen/EEG-Jahresabrechnungen).
For 2023 to 2025, the Bewegungsdaten match it for every TSO:

| Annual account | Rows of the Bewegungsdaten | Difference |
| --- | --- | --- |
| Einspeisevergütung (feed-in tariff), euros | `veraeusserungsform` 1 and 4, and categories ending in `aMW` | at most 0.003 % |
| Marktprämie (market premium), euros | `veraeusserungsform` 2 | at most 0.0001 % |
| Sonstige Direktvermarktung (other direct marketing), kWh | categories ending in `SO-DV` | less than 3 kWh |
| Mieterstromzuschlag (tenant electricity), kWh | `veraeusserungsform` 3 | less than 3 kWh |
| All energy tables together, kWh | all rows except categories ending in `FLP` and `SV` | at most 0.02 % |

- Form 1 leaves out the fallback payment (form 4) and the payment for plants whose EEG support
  has ended (legend). The latter is in form 5, in categories ending in `aMW`: 0.56 TWh and
  22.5 million euros in 2025.
- The account includes the rows with `EXTRA_VALUE` of TransnetBW 2024 and with `Monat` `13` of
  Amprion 2023 (see [Results for 2011 to 2024](#results-for-2011-to-2024)). `clean` holds them
  with their flag.
- Amprion 2025 puts 33,264 self-consumption rows (`SV`, 0.14 TWh, no payment) in form 1.

The files before 2016 have no `Veraeusserungsform`, so all payments are compared at once with
the payment tables of the account (feed-in tariff, market premium and flexibility premium), and
the avoided grid fees with the vNE table. The accounts of
[2011](https://web.archive.org/web/20160703024843/http://www.netztransparenz.de/de/file/2011_EEG-Jahresabrechnung.pdf),
[2014](https://web.archive.org/web/20160415101748/http://www.netztransparenz.de/de/file/EEG-Jahresabrechnung_2014.pdf)
and
[2015](https://web.archive.org/web/20171225124934/https://www.netztransparenz.de/portals/1/Content/Erneuerbare-Energien-Gesetz/Jahresabrechnungen/EEG-Jahresabrechnung_2015.pdf)
are in the Wayback Machine; 2012 and 2013 were not found.

| File | Payments | Avoided grid fees |
| --- | ---: | ---: |
| Amprion 2011 | 611 euros less (0.00002 %) | 6 euros more |
| TenneT 2011 | 22,195 euros less (0.0003 %) | 705 euros more (0.0005 %) |
| 50Hertz 2014 | exact | exact |
| TenneT 2014 | 91,763 euros more (0.001 %) | 5,825 euros less (0.002 %) |
| 50Hertz 2015 | exact | exact |
| TenneT 2015 | 176 euros less | 103 euros less |

This also settles the year of the Amprion file `Bewegungsdaten_Internet.zip`, whose name has
none: Amprion's page of 2011 linked it, and its sums are those of 2011.

`uv run pytest --realdata tests/test_annual_account.py` repeats the check of the payments and of
other direct marketing, and the check of the files before 2016.

## Matching with the MaStR

### The bridge

How the bridge works is in [The bridge](../README.md#the-bridge) in the README. It matches 0.31
to 0.41 million rows per year in 2016 to 2019 (1.41 to 1.51 billion euros, 5.4 to 5.8 % of the
payments), and 8,235 to 87,926 rows per year in 2020 to 2025 (0.01 to 0.44 billion euros).

The rows of 2020 to 2025 that are matched by number tie 4,510,610 Anlagenschluessel to one EEG
plant and 21,954 to several. The bridge leaves out the latter: for 16,712 of them the plant
changes over the years, for 4,313 it changes and later changes back, and for 929 the rows of one
year name two plants.

Checks of the bridge:

- Compared with a run without the bridge, all rows outside `ANLAGENSCHLUESSEL_BRUECKE` keep
  their match. Without the bridge, its rows are 1,437,595 `NOT_FOUND` and 163,014 `AMBIGUOUS`.
- In 2016 to 2019, 99.8 % of the bridge rows with a solar, biomass, wind or hydro category are
  matched to a plant of that energy source (99.9 % of their payments). Per year and category the
  share is at least 98.7 % of the rows (hydro) and 99.8 % of the payments. Most of the other
  rows are 2,661 rows with a PV category matched to an EEG storage plant; the rows from 2020 on
  name that storage plant by number too. Over 2020 to 2025 the share is 98.8 %, in 2025 alone
  94.3 % (8,200 rows): the rows left over in later years fit less well.
- Per plant and year, bridge matches give full-load hours like the other matches. In 2019 the
  median is 862 h for PV (other matches 889 h), 1,529 h for wind (1,710 h) and 5,158 h for
  biomass (5,727 h). The effect on the wind analysis is in
  [Full-load hours of onshore wind per district](../analysis/README.md#full-load-hours-of-onshore-wind-per-district).
- The bridge carries an error of the rows from 2020 on back to earlier years:
  some grid operators give the EEG number of one plant in the rows of a neighbouring plant whose
  Anlagenschluessel is not in the MaStR (see step 1 of
  [How rows are matched](../README.md#how-rows-are-matched)). In 2019, 2,476 PV plants get rows of
  several Anlagenschluessel through the bridge, and 575 of them reach more than 1,500 full-load
  hours. In 2020, matching by number gives 2,533 such PV plants, 1,293 of them above 1,500 h.
  Leaving out every Anlagenschluessel whose EEG number has other Anlagenschluessel too would
  also drop correct matches, such as biomass plants with one Anlagenschluessel per engine (0.36
  billion euros in 2019).
- By the MaStR, 222 biomass plants (0.13 billion euros) and 469 PV plants (1.3 million euros)
  that the bridge matches in 2019 started EEG operation only after 2019. Most of these biomass
  plants have a date in 2025 or 2026, probably because an existing plant that wins a tender
  counts as newly commissioned. Matches by Anlagenschluessel show the same: 893 biomass plants
  with 0.53 billion euros in 2019.
- The bridge does not compare the grid operator. 36,462 rows of 2019 that it matches (0.14
  billion euros) name another BDEW number of the grid operator than the rows from 2020 on.
  Mostly these are the same operators under a new BDEW number, such as E.DIS Netz and Avacon
  Netz, or grid areas that another operator took over.

### Match rates for 2016 to 2025

Share of the rows matched with the MaStR export of 17 October 2026, without → with the bridge:

| Year | 50Hertz | Amprion | TenneT | TransnetBW |
| --- | ---: | ---: | ---: | ---: |
| 2016 | 89.9 → 96.3 % | 92.3 → 97.8 % | 89.9 → 97.7 % | 93.7 → 98.9 % |
| 2017 | 90.3 → 96.7 % | 92.6 → 98.2 % | 90.0 → 97.8 % | 93.7 → 98.9 % |
| 2018 | 90.5 → 96.9 % | 93.0 → 98.7 % | 90.1 → 98.0 % | 93.5 → 99.0 % |
| 2019 | 89.7 → 96.7 % | 92.8 → 98.8 % | 89.1 → 98.2 % | 93.4 → 99.0 % |
| 2020 | 94.7 → 97.6 % | 96.9 → 99.1 % | 97.0 → 98.4 % | 97.8 → 99.3 % |
| 2021 | 97.5 → 97.6 % | 98.5 → 99.4 % | 97.9 → 98.7 % | 98.9 → 99.6 % |
| 2022 | 99.2 → 99.2 % | 98.9 → 99.3 % | 98.3 → 98.9 % | 99.4 → 99.7 % |
| 2023 | 99.5 → 99.5 % | 98.9 → 99.1 % | 98.3 → 98.8 % | 99.6 → 99.7 % |
| 2024 | 99.4 → 99.5 % | 99.0 → 99.2 % | 98.0 → 98.6 % | 99.6 → 99.7 % |
| 2025 | 99.3 → 99.4 % | 99.0 → 99.1 % | 98.3 → 98.4 % | 99.6 → 99.7 % |

Share of the EEG payments matched, without → with the bridge:

| Year | 50Hertz | Amprion | TenneT | TransnetBW |
| --- | ---: | ---: | ---: | ---: |
| 2016 | 93.0 → 96.7 % | 88.9 → 96.2 % | 91.6 → 98.0 % | 92.7 → 97.3 % |
| 2017 | 93.8 → 97.2 % | 89.8 → 97.3 % | 92.6 → 98.5 % | 93.1 → 97.8 % |
| 2018 | 93.9 → 97.6 % | 90.4 → 97.9 % | 92.8 → 98.8 % | 93.4 → 98.1 % |
| 2019 | 94.5 → 97.9 % | 90.7 → 98.3 % | 93.4 → 99.1 % | 93.4 → 98.1 % |
| 2020 | 96.4 → 98.2 % | 96.6 → 98.8 % | 98.4 → 99.5 % | 96.9 → 98.5 % |
| 2021 | 97.9 → 98.0 % | 98.3 → 99.1 % | 98.8 → 99.5 % | 98.3 → 98.9 % |
| 2022 | 99.3 → 99.4 % | 99.2 → 99.4 % | 99.0 → 99.4 % | 99.1 → 99.3 % |
| 2023 | 99.6 → 99.6 % | 99.2 → 99.3 % | 99.3 → 99.5 % | 99.4 → 99.4 % |
| 2024 | 99.7 → 99.7 % | 99.4 → 99.5 % | 99.4 → 99.6 % | 99.5 → 99.6 % |
| 2025 | 99.7 → 99.7 % | 99.6 → 99.7 % | 99.6 → 99.6 % | 99.4 → 99.6 % |

- Without the bridge, the rates jump from 2019 to 2020, when the files got MaStR numbers. With
  it, 2016 to 2019 reach 96.3 to 99.0 % of the rows and 96.2 to 99.1 % of the payments, and 2019
  differs from 2020 by less than 1 percentage point.
- The rest of 2016 to 2019 is mostly `NOT_FOUND`: 6,202 to 45,016 rows per TSO and year. Their
  Anlagenschluessel is not in the MaStR, and the rows from 2020 on name no single plant for it,
  for example because the plant stopped before 2020 or got a new Anlagenschluessel.

### Match results for 2025

With the MaStR export of 17 October 2026, 98.4 to 99.7 % of the rows and 99.6 to 99.7 % of the
EEG payments are matched:

| TSO | Rows | Matched rows | Matched payments | `EEG_NR` | `UNIT_NR` | `MULTIPLE` | `ANLAGENSCHLUESSEL` | `KWK_NR` | `ANLAGENSCHLUESSEL_BRUECKE` | `EEG_NR_CONFLICT` | `AMBIGUOUS` | `DELETED` | `NOT_FOUND` | `NO_ID` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50Hertz | 1,400,862 | 99.4 % | 99.7 % | 1,389,414 | 0 | 0 | 2,178 | 0 | 230 | 0 | 85 | 0 | 8,953 | 2 |
| Amprion | 2,626,635 | 99.1 % | 99.7 % | 2,404,217 | 184,666 | 1 | 10,311 | 99 | 2,614 | 216 | 150 | 185 | 24,176 | 0 |
| TenneT | 3,791,801 | 98.4 % | 99.6 % | 3,697,009 | 0 | 2,666 | 27,991 | 0 | 3,749 | 183 | 617 | 0 | 59,586 | 0 |
| TransnetBW | 1,499,225 | 99.7 % | 99.6 % | 1,487,707 | 0 | 0 | 5,457 | 0 | 1,642 | 8 | 495 | 0 | 3,916 | 0 |

- Most `NOT_FOUND` rows have no MaStR number and an Anlagenschluessel that is not in the MaStR:
  90,404 of 96,631.
- 4,578 different `EEG…` numbers (12,363 rows) are not in the MaStR. For 6,473 of these rows
  the Anlagenschluessel leads to exactly one plant, which has a different EEG number in the
  MaStR. 882 rows are matched by the bridge, 436 are `AMBIGUOUS`, 4,572 stay `NOT_FOUND`.
- Of Amprion's `SEE…` numbers, 579 rows are not found as units. 79 of them are matched by
  Anlagenschluessel and 3 by the bridge, 1 is `AMBIGUOUS`, 185 (62 numbers, 0.08 million euros)
  are `DELETED` and 311 stay `NOT_FOUND`. The list of deleted units cannot explain the `EEG…`
  numbers above, as it holds unit numbers only.
- Amprion has 661 rows with a KWK number. 552 are matched by Anlagenschluessel, 99 by KWK
  number; 10 stay `NOT_FOUND`. In 25 rows, the KWK number leads to a neighbouring plant that
  reports under its own EEG number, while the Anlagenschluessel fits the energy reported.
- 5,317 `EEG…` numbers appear with more than one Anlagenschluessel. For 1,323 of them, one
  Anlagenschluessel belongs to another plant: 3,085 rows (56 GWh) are matched to that plant by
  Anlagenschluessel, 407 rows (8 GWh) get `EEG_NR_CONFLICT`. The other numbers keep `EEG_NR`,
  as their further Anlagenschluessel lead to no other plant. Most of them are small PV plants;
  most of their energy is at biomass plants, perhaps with one Anlagenschluessel per engine.
  2020 to 2024 look alike, with 4,600 to 10,200 rows matched to another plant per year. In
  2023, the PV plants whose number is given for other plants too have a median of 1,391
  full-load hours without this step and 782 h with it.
- Matches agree with the payment category: rows with a solar, biomass, wind or hydro category
  are matched to a plant of that energy source in at least 99.1 % of cases, also for matches
  by Anlagenschluessel. Matches by the bridge agree less in 2025: 94.3 % of 8,200 rows (see
  [The bridge](#the-bridge)).
- Comparing the Anlagenschluessel without case and spaces would match only 39 more rows.
