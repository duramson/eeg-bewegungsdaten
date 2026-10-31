# Data status

Which files of the EEG-Bewegungsdaten exist for which year and TSO, and where. The tool reads 2016
to 2025. The years before are listed too, so that whoever has a missing file can add it.

## By year and TSO

What the cells mean:

- **Page**: on netztransparenz.de, linked from the page.
- **Server**: on netztransparenz.de, but no page links it any more.
- **Wayback**: a copy in the Wayback Machine. From 2016 on, of the file on netztransparenz.de;
  before 2016, of the file on the TSO's own website.
- **Zenodo**: a copy in the Zenodo dataset with the same size as the TSO's file.
- **Zenodo\***: in the Zenodo dataset, but saved again, reshaped or not compared with the TSO's
  file. Its sums can still agree.
- **missing**: no copy known.

| Year | 50Hertz | Amprion | TenneT | TransnetBW |
| --- | --- | --- | --- | --- |
| 2025 | Page, Wayback | Page, Wayback | Page, Wayback | Page, Wayback |
| 2024 | Server, Wayback | Server, Wayback | Server, Wayback; two uploads | Server, Wayback |
| 2023 | Server, Wayback | Server, Wayback | Server, Wayback | Server, Wayback |
| 2022 | Server, Wayback | Server, Wayback | Server, Wayback | Server, Wayback |
| 2021 | Server, Wayback, Zenodo | Server, Wayback, Zenodo | Server, Wayback, Zenodo | Server, Wayback, Zenodo\* |
| 2020 | Wayback, Zenodo\* | Wayback, Zenodo\* | Wayback, Zenodo | Wayback, Zenodo; two uploads of the same size |
| 2019 | Wayback, Zenodo | Wayback, Zenodo | Wayback, Zenodo | Wayback, Zenodo |
| 2018 | Wayback, Zenodo | Wayback, Zenodo\* | Wayback, Zenodo | Wayback, Zenodo |
| 2017 | Wayback, Zenodo\* | Wayback, Zenodo\* | Wayback, Zenodo | Wayback, Zenodo |
| 2016 | Wayback, Zenodo\* | Wayback, Zenodo\* | Wayback, Zenodo\* | Wayback, Zenodo |
| 2015 | Wayback, Zenodo\* | missing | Wayback, Zenodo | Zenodo\* |
| 2014 | Wayback, Zenodo\* | Zenodo\* | Wayback, Zenodo | Zenodo\* |
| 2013 | Zenodo\* | Wayback without the vNNE file, Zenodo\* | Wayback, Zenodo | Zenodo\* |
| 2012 | Zenodo\* | Zenodo\* | Wayback, Zenodo\* | Zenodo\* |
| 2011 | Zenodo\* | Wayback, Zenodo\* | Wayback, Zenodo\* | Zenodo\* |
| 2010 | Zenodo\* | Zenodo\* | Zenodo\* | Zenodo\* |
| 2009 | Zenodo\* | Zenodo\* | Zenodo\* | Zenodo\* |
| 2008 | Zenodo\* | Zenodo\* | Zenodo\* | Zenodo\* |
| 2007 | Zenodo\* | Zenodo\* | Zenodo\* | Zenodo\* |

The two uploads of TenneT 2024 and TransnetBW 2020 are described in
[Uploads published twice](data.md#uploads-published-twice).

## Sources

### netztransparenz.de

- [The page](https://www.netztransparenz.de/de-de/Erneuerbare-Energien-und-Umlagen/EEG/EEG-Abrechnungen/EEG-Jahresabrechnungen/EEG-Bewegungsdaten)
  lists only the latest year.
- The files of 2021 to 2024 are still on the server, but no page links them any more
  (`/xspproxy/api/staticfiles/ntp-relaunch/dokumente/…`). They can disappear at any time.
- The files of 2016 to 2020 (`/portals/1/…`) are gone.
- Checked on 31 October 2026: the page lists the files of 2025, and the server still answers for
  every file of 2021 to 2024 in `OLDER_FILES`.

The tool knows where the earlier files are (`links.py`, `OLDER_FILES`) and downloads them like the
current ones. `OLDER_FILES` also lists the files of 2025. The tool takes them from the page as long
as it lists them, and from `OLDER_FILES` once the page moves on to 2026 (by 15 September 2027).
Whether their URLs keep working then is unknown.

### Wayback Machine

- [web.archive.org](https://web.archive.org/) has one copy of each file of 2016 to 2020; the tool
  fetches the original bytes from there.
- For all 24 files of 2021 to 2024 it has a copy with the same bytes. When netztransparenz.de
  answers 404, `download` takes that copy instead. The files of 2025 and the legend have such a
  copy too.
- Checked on 18 and 19 October 2026. `OLDER_FILES` records the capture of each file, and for
  2021 to 2025 its SHA-256, which `download` checks. For 2016 to 2020 only
  `uv run pytest --realdata` compares the files with known SHA-256.

Before 2016 the data were not on netztransparenz.de: each TSO published them on its own site.
The Wayback Machine holds nine of these files (checked on 22 October 2026, see
[#22](https://github.com/duramson/eeg-bewegungsdaten/issues/22)):

| TSO | Year | File | Site | Captured |
| --- | --- | --- | --- | --- |
| Amprion | 2011 | `Bewegungsdaten_Internet.zip` | amprion.net | 2015-08-20 |
| TenneT | 2011 | `csv-datei-bewegungsdaten-nach-eeg-kategorien-2011.zip` | tennettso.de | 2013-03-26 |
| TenneT | 2012 | `CSV-Datei_Bewegungsdaten__EEG-Kategorien_aus_EEG-Jahresmeldung_2012_.csv.zip` | tennet.eu | 2015-04-23 |
| Amprion | 2013 | `Internet_Bewegungsdaten_VGT_DV_2013.zip` | amprion.net | 2016-01-28 |
| TenneT | 2013 | `TenneT_Bewegungsdaten_2013.zip` | tennet.eu | 2022-03-19 |
| 50Hertz | 2014 | `EEG_JEA_2014.csv` | 50hertz.com | 2018-02-01 |
| TenneT | 2014 | `TenneT_Bewegungsdaten_2014.zip` | tennet.eu | 2022-03-19 |
| 50Hertz | 2015 | `50Hertz_Bewegungsdaten_2015[1].csv` | 50hertz.com | 2017-11-27 |
| TenneT | 2015 | `JM_2015_Bewegungsdaten.zip` | tennet.eu | 2022-03-19 |

### Zenodo

- The dataset [German Renewable Energy Payment Register](https://doi.org/10.5281/zenodo.8010410)
  (Batz Liñeiro and Müsgens, BTU Cottbus, 2023, CC BY 4.0) keeps the TSOs' files of 2007 to 2021
  (50Hertz from 2006) next to its own tables: `PaymentRegister.zip` (2.1 GB), folder
  `<TSO>/Original files/`.
- Many files are dated December 2018: the authors saved them again, so they need not be
  byte-identical with the TSOs' files. TenneT 2012 differs in size from the Wayback copy. The sums
  of TransnetBW 2012, 2014 and 2015 agree with the EEG annual accounts; TransnetBW 2011 is 0.4 %
  (payments) and 1.1 % (avoided grid fees) below. See
  [#24](https://github.com/duramson/eeg-bewegungsdaten/issues/24).
- TransnetBW 2007 to 2010 seem to be put together from ten parts of `Anlagendaten` that span 2007
  to 2013: an R script next to them writes files of nearly the same size.
- Amprion has a `<year>_a.txt` for 2007 to 2014 and 2016 next to its files; for 2012 it is the only
  file.
- These files have the same size as the files in the tool's archive or in the Wayback Machine:
  - 50Hertz 2018, 2019 and 2021
  - Amprion 2019 and 2021; of 2018 only the VGT file
  - TenneT 2013 to 2015 (Wayback Machine) and 2017 to 2021
  - TransnetBW 2016 to 2020; both uploads of 2020 have the same size
- Checked on 31 October 2026: the list of files, read from the end of the ZIP.

## Missing files

- **Amprion 2015** is neither in the Wayback Machine nor in the Zenodo dataset.
- **Before 2007** only 50Hertz 2006 is known, in the Zenodo dataset.
- **The cells with Zenodo\* only** have no copy known to be the TSO's original file.

If you have one of these files as the TSO published it, please open an
[issue](https://github.com/duramson/eeg-bewegungsdaten/issues/new/choose).

## Why the tool reads 2016 to 2025 only

- From 2016 on, every file is on netztransparenz.de or in the Wayback Machine, in a format the tool
  knows (see [Formats of 2016 to 2025](findings.md#formats-of-2016-to-2025)), and its SHA-256 is
  known.
- Before 2016, each file has its own header (`BNetzA-ID`, `Firma`, `MENGE [KWH]`, `kWh`, `Euro`,
  `vNE`, …). The names do not give TSO and year (`Bewegungsdaten_Internet.zip`, `EEG_JEA_2014.csv`),
  and 50Hertz published 2014 and 2015 as bare CSV files. Amprion 2011 adds street, postcode and town
  of the plant.
- To read them, the tool would need a list of the files with TSO, year and SHA-256, a reader for
  bare CSV files, a layout for each header, and, for the Zenodo files, range requests into the ZIP.
  [#22](https://github.com/duramson/eeg-bewegungsdaten/issues/22) and
  [#24](https://github.com/duramson/eeg-bewegungsdaten/issues/24) describe this. Both wait for a
  decision (label `on hold`) and touch personal data (label `privacy`); see also
  [Data license](data-license.md).
