# Analyses

Scripts that use the output of `eeg-bewegungsdaten` and compare it with other sources. They
are not part of the package. They need `ingest` and `mastr` for the years and the database
of open-MaStR (see
[Matching with the Marktstammdatenregister](../README.md#matching-with-the-marktstammdatenregister)).

## Full-load hours of PV

The AGEE-Stat report on PV
([UBA, Climate Change 61/2025](https://www.umweltbundesamt.de/system/files/medien/11850/publikationen/61_2025_cc.pdf),
Fraunhofer ISE) links the MaStR and the Bewegungsdaten as well. Its tables 16 and 17 give the
full-load hours of PV plants by size class. `analysis/agee_pv_full_load_hours.py` recomputes the
column without estimated self-consumption from `clean`, `mastr` and the open-MaStR database.
Like the report (page 65 and the notes to tables 16 and 17), it divides the energy of the year,
grid feed-in plus recorded self-consumption, by the gross capacity of the plants commissioned up
to the end of the previous year:

```bash
uv run python analysis/agee_pv_full_load_hours.py 2023
```

In h/a:

| Plant type | Capacity [kW] | 2023 report | 2023 here | 2022 report | 2022 here |
| --- | --- | ---: | ---: | ---: | ---: |
| Plug-in | x ≤ 2 | 432 | 437 | | |
| Building | x < 10 | 701 | 702 | 814 | 812 |
| Building | 10 ≤ x < 20 | 816 | 817 | 953 | 951 |
| Building | 20 ≤ x < 30 | 858 | 863 | 966 | 969 |
| Building | 30 ≤ x < 100 | 833 | 833 | 953 | 949 |
| Building | 100 ≤ x < 500 | 786 | 788 | 905 | 904 |
| Building | 500 ≤ x ≤ 750 | 766 | 765 | 879 | 877 |
| Building | 750 < x ≤ 1000 | 799 | 798 | 909 | 906 |
| Building | x > 1000 | 764 | 758 | 901 | 895 |
| Ground-mounted | x ≤ 750 | 910 | 936 | 1029 | 1038 |
| Ground-mounted | 750 < x ≤ 1000 | 931 | 935 | 1042 | 1042 |
| Ground-mounted | x > 1000 | 917 | 916 | 1035 | 1035 |
| Total | | 828 | 828 | 946 | 945 |

- All classes but one match the report to 1.2 %. Ground-mounted plants up to 750 kW come out
  2.9 % (2023) and 0.9 % (2022) higher; the cause is open. The report corrects implausible
  capacities in the MaStR (annex A.1) and used the MaStR of April 2024 (2022: July 2023), this
  check the uncorrected one of October 2026.
- Leave out the deduction rows of self-consumption paid under section 33(2) EEG 2009. Plants
  of 2009 to 2012 report their self-consumed energy at the full tariff (`SgK3341…`) and the
  same energy again, negative, for the deduction from the tariff (`SgK3342…` at 16.38 ct/kWh,
  `SgK3343…` at 12 ct/kWh). Summed up, about 0.8 TWh per year cancel out. With these rows,
  buildings up to 500 kW come out 1 to 3.7 % lower, plants commissioned in 2011 even 6.5 %.
- The report also gives the full-load hours by year of commissioning (figures 16 and 17).
  From 2002 on, every year matches to 0.6 %. 2000 and 2001 (0.1 GW) come out up to 6.5 % lower.
- Some grid operators give the EEG number of one plant in the rows of other plants too. `mastr`
  matches these rows by Anlagenschluessel (see `EEG_NR_CONFLICT`); without this step, the values
  are up to 4 h higher.
- Without the self-consumption rows (`SV`), buildings come out 1.3 to 9.7 % below the report.
  The report counts them.

## Full-load hours of onshore wind per district

`analysis/wind_full_load_hours.py` computes the full-load hours of onshore wind turbines per
district (Kreis) from `clean`, `mastr` and the open-MaStR database: energy fed in over gross
capacity, for turbines that ran the whole year. It writes one row per district and year:

```bash
uv run python analysis/wind_full_load_hours.py 2016 2025 wind.parquet
```

| Year | Capacity used [GW] | Energy [TWh] | Full-load hours [h] |
| --- | ---: | ---: | ---: |
| 2016 | 35.6 | 55.1 | 1,546 |
| 2017 | 39.1 | 70.5 | 1,802 |
| 2018 | 45.7 | 78.9 | 1,728 |
| 2019 | 49.3 | 93.4 | 1,892 |
| 2020 | 49.2 | 94.6 | 1,924 |
| 2021 | 50.2 | 82.1 | 1,634 |
| 2022 | 50.2 | 89.1 | 1,776 |
| 2023 | 52.0 | 105.9 | 2,035 |
| 2024 | 54.1 | 100.0 | 1,849 |
| 2025 | 54.4 | 90.4 | 1,662 |

- The energy is what the turbines fed in after curtailment by the grid operators, not what they
  could have produced.
- The values show the turbines as built, not only the wind at the site. Over 2016 to 2025 the
  Werra-Meißner-Kreis reaches 2,494 h with a median hub height of 149 m; Lüchow-Dannenberg
  reaches 951 h with 64 m.
- Some grid operators book the energy of several turbines on one of them, up to 21,000 h for a
  single turbine. Where the rows keep each turbine's own Anlagenschluessel, `mastr` already
  matches them to the right turbine (see `EEG_NR_CONFLICT`), which adds 0.03 to 0.3 GW per year
  to the capacity used. Mostly, though, the rows carry the number and Anlagenschluessel of the
  one turbine. The script therefore adds up energy and capacity per site (Lokation in the
  MaStR). It leaves out sites with a turbine that started or stopped during the year, sites where
  a turbine has no rows or lacks months, and sites above 5,000 h or below 100 h. It prints the
  capacity it leaves out for each reason.
- Matches by the bridge (`ANLAGENSCHLUESSEL_BRIDGE`) add 1.2 to 1.9 GW to the capacity used in
  2016 to 2019. The full-load hours change by 3 h at most.
- For districts with at least 20 MW, the values of consecutive years correlate with 0.77 to 0.92.
- The script writes every district, also those with only a few turbines. Before you share the
  values, hide the districts as [Personal data](../README.md#personal-data) advises.
