# Contributing

Set up the project as described in [Development](README.md#development) in the README. Bugs and
ideas go into the [issue forms](https://github.com/duramson/eeg-bewegungsdaten/issues/new/choose).

## Before a pull request

- Open an issue first. For larger changes (new data sources, file formats, the schema, the
  matching or the export) wait for an answer from a maintainer before you write the code: the
  output is published as a dataset, so such changes are agreed on first. Small fixes can come as
  a pull request right away.
- One topic per pull request. If one builds on another, say so in the description.

## Names

- Column names are German. Most come from the legend of the TSOs (`strommenge`, `eeg_zahlung`,
  `veraeusserungsform`); the columns the tool adds follow them (`sonderzeile`, `treffer`,
  `hinweise`).
- Codes in cells are English (`MASTR_NR_MISSING`, `REPEATED_ENERGY`, `NOT_FOUND`), as are the
  values of `energietraeger`. Terms of the legend may stay inside a code, as in `EEG_NR`,
  `KWK_NR` and `ANLAGENSCHLUESSEL`.
- Keys in reports and other JSON files, the README and the docs are English. The source notes in
  `sources.txt` stay German, as they follow the wording of the licenses.

## What stays out of a pull request

- Files of your own tools and editors: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/`,
  `.cursor/`, `.vscode/`, `.idea/` and the like. They describe your machine and your way of
  working, not the project. `.gitignore` lists them.
- Scripts and notes for your own setup, such as a wrapper around the checks or the name of your
  build host.
- Downloaded data and generated outputs. Tests use small synthetic fixtures with made-up plant
  keys.
- Changes the topic does not need: reformatting, renamings, updated dependencies.

## AI tools

You may use them; the same standards apply as to any other contribution. You answer for every
line and for the review yourself. Keep descriptions short and in your own words. Do not credit
AI tools: no `Co-authored-by` lines for them in commits and no "generated with" notes in the
description.

## Pull requests

- The title follows [Conventional Commits](https://www.conventionalcommits.org/): a type, an
  optional scope, a colon and a short summary, for example `fix: retry transient download
  errors` or `feat(export): write a dataset card`.
- Types: `feat` (new feature), `fix` (bug fix), `docs`, `refactor`, `perf`, `test`, `build`
  (packaging, dependencies), `ci` (workflows), `chore` (everything else), `style`, `revert`.
  Add `!` after the type for a change that breaks existing output or commands: `feat!: …`.
- The workflow `pr-title.yml` checks the title. If it fails, edit the title; the check runs again.
- Pull requests are squash-merged with their title as the commit message. The commits inside a
  pull request can be named freely.
- Close the issue the pull request fixes with `Closes #<number>` in the description, and sum up
  what was agreed on in it.
- Say how you tested it: the commands, and for changes to the data the files and numbers you
  compared.
