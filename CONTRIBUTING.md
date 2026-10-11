# Contributing

## Set up

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and git.

```bash
git clone https://github.com/GriffinHackley/LLM-WIKI
cd LLM-WIKI
uv sync --extra test                # a .venv with the package and pytest
uv tool install --editable .        # optional: a `wiki` on your PATH that follows the checkout
```

## Test

```bash
uv run --extra test pytest
```

The tests use fake models and download nothing. GitHub Actions runs them on Windows,
macOS and Linux for every push. `tests/test_docs.py` also checks the docs against the
command: a new command or option fails it until `docs/commands.md` describes it, and a
`wiki ...` line in any doc that uses a missing command or flag fails it too.

To measure the cache and search at scale, on a generated wiki:

```bash
uv run python benchmarks/bench_synthetic.py --pages 30000 --relations 100000
```

## Docs

- Each page has one job: [concepts.md](docs/concepts.md) explains, [workflows.md](docs/workflows.md)
  walks through tasks, and the reference pages ([commands.md](docs/commands.md),
  [config.md](docs/config.md), [presets.md](docs/presets.md), [guides.md](docs/guides.md))
  list everything. Put a fact in one place and link to it.
- **Examples are invented.** Never take names, facts or structure from a real wiki, your
  own included: the repository is public.
- Links in the README are absolute, since PyPI shows it away from the repository.

## Releasing

1. Set `__version__` in `src/wiki_cli/__init__.py`.
2. In `CHANGELOG.md`, rename `## Unreleased` to `## <version> (<YYYY-MM-DD>)`, with
   `: <subtitle>` after it if the release has a theme, and start a new, empty
   `## Unreleased` above it. `python .github/release_notes.py <version>` prints the notes
   the release will get.
3. Commit, then tag and push: `git tag v<version>` and `git push origin main v<version>`.
4. The publish workflow runs the tests on every OS, checks the tag matches the version
   and that the changelog has its section, builds, and installs the wheel to try it. It
   then waits for approval on the `pypi` environment before uploading. Once PyPI accepts
   it, it makes a GitHub Release from the changelog section, with the wheel and sdist
   attached.

[PLAN.md](PLAN.md) is the current plan; [FUTURE_IDEAS.md](FUTURE_IDEAS.md) holds ideas
not scheduled yet.
