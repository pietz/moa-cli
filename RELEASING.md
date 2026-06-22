# Releasing moa-cli

## Pipelines

- **CI** (`.github/workflows/ci.yml`) - runs on every push to `main` and every PR.
  Installs with `uv`, then `ruff check` + `pytest` on Python 3.12 and 3.13.
- **Release** (`.github/workflows/release.yml`) - runs when a `v*` tag is pushed.
  Builds the sdist + wheel with `uv build`, publishes to PyPI via **trusted
  publishing** (OIDC, no API token), and creates a GitHub Release with the built
  artifacts and auto-generated notes.

## One-time PyPI setup (required before the first release)

Trusted publishing means no API tokens stored in GitHub. Configure it once:

1. Claim the `moa-cli` name on PyPI (the name must be available).
2. On PyPI -> the project -> Settings -> Publishing -> add a **trusted publisher**:
   - Owner: `pietz`
   - Repository: `moa-cli`
   - Workflow filename: `release.yml`
   - Environment: `release`
3. In the GitHub repo: Settings -> Environments -> create an environment named
   `release`.

> Prefer an API token instead of OIDC? Drop the `id-token` permission and add a
> `PYPI_API_TOKEN` repo secret plus a `password:` input on the publish step.

## Cutting a release

1. Make sure `main` is green in CI.
2. Bump the version in `pyproject.toml` and `src/moa_cli/__init__.py`.
3. Commit, then tag and push:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. The Release workflow builds, publishes to PyPI, and creates the GitHub Release.
   Once it lands, `uvx --from moa-cli moa ask "..."` works for anyone.
