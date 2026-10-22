# eeg-bewegungsdaten

- Check: `./check`; full validation also runs `uv run pre-commit run --all-files`.
- Integration: PR without merge, against `duramson/eeg-bewegungsdaten:main` from the fork.
- `zenodo-source` builds on PR #23 (`pre-2016`); leave that branch unchanged.
- Full tests run on p620-dev. Set `EEG_BEWEGUNGSDATEN_DIR` to the downloaded archive for `--realdata`.
- Package installs require releases at least seven days old and wheels; report blocked pins without overriding the policy.
- Fetch Zenodo members with `eeg-archive-check/0.1` and HTTP ranges only. Never fetch the whole archive.
- Keep dataset bytes and generated outputs outside Git. Use synthetic plant identities in fixtures.
- Zenodo's reshaped files use decimal commas and numeric `NA`. Keep their published text unchanged.
- Amprion 2013 combines Wayback payments with Zenodo fees. Its parts share `upload_id`, not a publication date.
- As of 2026-10-22, Ruff 0.17.1 is blocked by the seven-day package rule. Existing environments can run checks with `UV_NO_SYNC=1` and Ruff 0.17.0; record that exception in the PR.
