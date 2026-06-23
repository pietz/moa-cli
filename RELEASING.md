# Releasing moa-cli

## Pipelines

- **CI** (`.github/workflows/ci.yml`) - runs on every push to `main` and every PR.
  Installs with `uv`, then `ruff check` + `pytest` on Python 3.12 and 3.13.
- **Release** (`.github/workflows/release.yml`) - runs when a `v*` tag is pushed.
  Builds the sdist + wheel with `uv build`, publishes to PyPI using the
  `PYPI_API_TOKEN` repo secret, and creates a GitHub Release with the built
  artifacts and auto-generated notes.

## One-time PyPI setup (required before the first release)

Publishing uses a PyPI API token stored as a GitHub Actions secret.

1. At https://pypi.org -> Account settings -> API tokens, create a token. For the
   first upload of a brand-new project, scope it to **"Entire account"**
   (project-scoped tokens only exist once the project does). Rotate to a
   project-scoped token after the first release if you like.
2. Store it as the repo secret `PYPI_API_TOKEN`:
   ```bash
   gh secret set PYPI_API_TOKEN   # paste the token when prompted
   ```

The `moa-cli` name is claimed automatically by the first successful upload, as long
as it is still available.

## Cutting a release

1. Make sure `main` is green in CI.
2. Bump the version in `pyproject.toml` and `src/moa_cli/__init__.py`.
3. Commit, then tag and push:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. The Release workflow builds, publishes to PyPI, and creates the GitHub Release.
   Once it lands, `uvx moa-cli ask "..."` works for anyone.
