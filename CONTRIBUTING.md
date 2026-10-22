# Contributing

Set up the project as described in [Development](README.md#development) in the README. Bugs and
ideas go into the [issue forms](https://github.com/duramson/eeg-bewegungsdaten/issues/new/choose).

## Before a pull request

- Open an issue first. For larger changes (new data sources, file formats, the schema, the
  matching or the export) wait for an answer from a maintainer before you write the code: the
  output is published as a dataset, so such changes are agreed on first. Small fixes can come as
  a pull request right away.
- One topic per pull request. If one builds on another, say so in the description.

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

## Labels

Maintainers set the labels; you can suggest one in the description. Besides the GitHub defaults
(`bug`, `enhancement`, `documentation`, …):

- `changes output`: the change alters the columns, types or values of `text`, `clean`,
  `quarantine`, `mastr` or the export, so outputs must be built again and differ from earlier
  ones. Examples: a renamed column, a rule that moves rows to the quarantine, another match for
  some rows. Not for changes that keep the content, such as smaller files, nor for new years that
  leave the other years as they are. The title gets a `!` only if the change breaks scripts that
  read the output, such as a renamed column.
- `privacy`: touches personal data, such as the plant keys, the pseudonymization of the export
  (`anlage_id`) or what the export and the analyses show of small plants.
- `upstream`: the cause lies in the files of the TSOs or in open-MaStR, not in the tool. The
  tool may still need a workaround.
- `on hold`: waits for a decision, for example on scope or license; no work on it until then.
- `data <year>`: the issues of the yearly upkeep, opened by the workflow `data-issues.yml` (see
  [Development](README.md#development)).
