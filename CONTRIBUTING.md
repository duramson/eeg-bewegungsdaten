# Contributing

Set up the project as described in [Development](README.md#development) in the README. Bugs and
ideas go into the [issue forms](https://github.com/duramson/eeg-bewegungsdaten/issues/new/choose).

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
- Close the issue the pull request fixes with `Closes #<number>` in the description.
