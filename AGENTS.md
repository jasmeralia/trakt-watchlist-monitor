# Agent Development Rules

## Verification (Required on Every Code Change)

After any code change, run:

```bash
make lintfix && make lint && make test
```

All three must pass before the task is considered complete.

## Additional Linting

- **Shell scripts** (`.sh` files): must pass `shellcheck`. Covered by `make lint` automatically.
- **Dockerfile**: must pass `hadolint`. Covered by `make lint` automatically.

## Configuration

- Never use `os.getenv` directly in application code.
- All configuration must be accessed via `from .config import settings` (see
  `trakt_watchlist_monitor/config.py`).
- Never commit `.env` files. Use `.env.example` as the documentation template.

## Code Style

- Python 3.12+ with strict mypy typing
- Line length: 100 characters (enforced by ruff and pylint)
- Formatting: ruff (run `make lintfix` to auto-apply)

## Architecture Reference

See [docs/DESIGN.md](docs/DESIGN.md) for module responsibilities, SQLite schema, configuration
schema, and the discount threshold formula.

## Git Workflow

**Never push directly to `master`.** Every change, no matter how small, must go through
a pull request. This is a hard requirement, not a preference.

### Opening a PR

1. Create a feature branch: `git checkout -b <branch-name>`
2. Commit changes on that branch
3. Push the branch: `git push -u origin <branch-name>`
4. Open a PR with `gh pr create`

PRs are squash-merged only. The repository owner is `@jasmeralia` (see `.github/CODEOWNERS`).

### After a PR is merged

1. Switch back to master and pull: `git checkout master && git pull`
2. Watch the GitHub Actions run on `master` complete successfully before reporting the
   task done. Use `gh run watch $(gh run list --branch master --limit 1 --json databaseId -q '.[0].databaseId')` to stream it live, confirming the release workflow (tag creation and Docker image push to GHCR) finishes without error.

## Importing an Amazon Prime Video library into Trakt

To compare an Amazon library export with a Trakt movie collection, generate a CSV with these
columns: `amazon_title`, `amazon_url`, `trakt_title`, `trakt_year`, `trakt_id`, and `trakt_slug`.
Keep each Amazon entry linked to the specific Trakt movie edition by checking the Amazon detail
page's release year and synopsis against Trakt search results. Do not rely on title-only matches
when remakes or duplicate Trakt records exist. Query the Trakt movie collection and exclude IDs
already present; review unmatched titles separately rather than guessing.

Use `scripts/add_trakt_collection_batch.py --input <csv>` for a preview. It verifies every Trakt
ID and slug and refuses to overwrite items already collected. Only after reviewing the preview,
run with `--commit` to add the batch; `--force` skips its confirmation prompt. The script uses the
project `.env` settings, submits at most 100 movies per request, and verifies the resulting
collection. Its default metadata is `digital`, `hd_720p`, and `2.0` audio channels.
