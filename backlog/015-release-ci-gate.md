# 015 - Gate PyPI releases on a passing test run

**Status:** ready
**Touches:** `.github/workflows/release.yml`, optionally `.github/workflows/ci.yml`,
`docs/releasing.md`.
**Related:** none.

## Context

`.github/workflows/release.yml:3-23` triggers on `push: tags: ["v*"]` and runs
`uv build` + `pypa/gh-action-pypi-publish` + `gh release create` with **no `needs:`**
and **no test step**. `.github/workflows/ci.yml:3-7` runs only on `push: branches:
[main]` and `pull_request` - not on tags - so a tag push never re-runs CI. `docs/releasing.md:24`
acknowledges the gap as a manual checklist line ("Make sure `main` is green in CI")
with no enforcement.

The build also runs `uv build` against whatever the tag points at, with no
dependency install or test step at all.

## Goal

Never publish a tag whose code does not pass the test + lint suite. PyPI versions are
irreversible (yank-only), so a bad release cannot be undone.

## Decisions

- **Simplest option (recommended):** add a first step to the existing `release.yml`
  job that runs `uv sync && uv run pytest && uv run ruff check src tests`, so the
  publish steps only run if tests + lint pass. Keeps the single-job workflow shape.
- **Alternative (if preferred):** trigger `ci.yml` on tags too and add a
  `needs: ci` job in `release.yml`. More moving parts; only worth it if CI and release
  should diverge (e.g. different runners).
- Add a tag-regex guard (`^v\d+\.\d+\.\d+$`) so a stray `v*` tag (e.g. a pre-release
  typo) does not trigger a publish unintentionally. Cheap insurance.
- Update `docs/releasing.md` to reflect that the gate is now enforced, not manual.

## Acceptance criteria

- [ ] `release.yml` runs `uv run pytest` and `uv run ruff check src tests` before any
      publish step; a failing test aborts the release.
- [ ] Dependencies are installed (`uv sync`) in the release job before tests run.
- [ ] Tag trigger is gated by a `^v\d+\.\d+\.\d+$` (or agreed) regex.
- [ ] `docs/releasing.md` updated to state the gate is automated.
- [ ] Manual verification: push a throwaway tag from a deliberately-failing test
      branch and confirm the release job fails before publishing (do this on a fork or
      a scratch tag, never on the real `v*` series).

## Notes

Do not add `twine upload --skip-existing` or any "force publish" escape hatch - the
irreversibility is the feature. If a release goes bad, the answer is yank + bump, not
re-upload.
