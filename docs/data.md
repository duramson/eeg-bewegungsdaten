# The data

For every plant paid under the EEG, the files list the energy fed into the grid, the EEG
payment and the EEG receipts (`EEG_Einnahmen`, from 2024; until 2023 the avoided grid fees,
`Vermiedene_Netzentgelte`), split by sales form (`Veraeusserungsform`) and payment category.
Market premium rows are mostly monthly from 2021 on; most other rows are annual values.

- 50Hertz, Amprion, TenneT and TransnetBW publish the files on
  [netztransparenz.de](https://www.netztransparenz.de/de-de/Erneuerbare-Energien-und-Umlagen/EEG/EEG-Abrechnungen/EEG-Jahresabrechnungen/EEG-Bewegungsdaten).
  The official legend (XLSX) is on the same page.
- They publish the data for the previous year by 15 September (§ 51 (1) no. 2 EnFG).
- They only have to keep the data online until the end of the following calendar year
  (§ 51 (5) EnFG). Older years disappear. On 18 October 2026 the page listed only the files for
  2025. This is why the tool keeps every download in a local archive.
- Each TSO publishes one ZIP per year with 1 to 14 CSV files (TenneT 2024: four ZIPs), 9.3
  million lines in total for 2025. The TSOs differ in encoding, line endings, column names and
  number of columns, and the layout changed several times, see
  [Formats of 2016 to 2025](findings.md#formats-of-2016-to-2025).

Which files exist for which year and TSO, and where the tool gets them, is in
[Data status](data-status.md).

## Uploads published twice

All files of 2016 to 2024 are 899 MB. Two years were uploaded twice; the tool archives both
uploads and `ingest` reads the one published last:

- **TenneT 2024:** four parts of December 2025 replace three parts of September 2025 (66 MB
  more). The first upload has no `Monat`; the second has the same plants and the same sums, but
  splits 847,335 rows into months, mostly Marktprämie.
- **TransnetBW 2020:** the upload of August 2021 replaces the one of July 2021. Only the row
  order and 102,031 categories differ: `…K33b1eMPM<month>` became `…K33b1-MPM<month>`.
