# 💣 BlastRadius

AI-assisted **regression test selection** for Python repositories.

BlastRadius compares two Git revisions, combines dependency evidence with a
Gemma model, selects tests deterministically, and
executes the selected tests.
suite.

## Current workflow

```text
base revision → patch analysis → dependency graph → model risks
             → deterministic test selection → selected pytest run
                                      └──────→ optional full-suite comparison
```

BlastRadius is a tool for another repository. The target repository owns the
application code, tests, Python environment, and Git history.

## Install locally

From the BlastRadius repository:

```bash
uv sync
uv run blastradius --help
```

Install as a local command:

```bash
uv tool install --editable /absolute/path/to/blastradius
blastradius --help
```

Install target dependencies and point BlastRadius at target repository's
Python environment:

```bash
cd /absolute/path/to/benchmark
uv sync --all-groups
export BLASTRADIUS_PYTHON="$PWD/.venv/bin/python"
```

## Configuration

BlastRadius requires a Gemma model. The model can run locally or through a
hosted provider such as Ollama, OpenRouter, DigitalOcean, or another service
that exposes the required chat API.

Environment variables:

```bash
export BLASTRADIUS_BASE_URL="http://localhost:11434/v1"
export BLASTRADIUS_MODEL="gemma-local"
export BLASTRADIUS_API_KEY=""
```

For local use, place these values in the target repository's `.env`. The file
is loaded automatically and must not be committed. Copy the example first:

```bash
cp /absolute/path/to/blastradius/.env.example .env
```

Hosted provider example:

```bash
export BLASTRADIUS_BASE_URL="https://openrouter.ai/api/v1"
export BLASTRADIUS_MODEL="google/gemma-3-27b-it"
export BLASTRADIUS_API_KEY="your-key"
```

The endpoint must expose `/chat/completions`.

## Local usage

The target must have the desired target revision checked out. The base and
target must be real Git revisions, not placeholder names.

For one commit:

```bash
BASE=$(git rev-parse HEAD^)
TARGET=$(git rev-parse HEAD)

blastradius run \
  --repo . \
  --base "$BASE" \
  --target "$TARGET"
```

For a local PR branch:

```bash
BASE=$(git merge-base main HEAD)

blastradius run \
  --repo . \
  --base "$BASE" \
  --target HEAD
```

Analysis only, without pytest execution:

```bash
blastradius run \
  --repo . \
  --base "$BASE" \
  --target HEAD \
  --no-run
```

The command reports changed files, graph size, model risks, selected tests,
and selected-suite results. Add `--compare` for full-suite results and timing
comparison:

```bash
blastradius run \
  --repo . \
  --base "$BASE" \
  --target HEAD \
  --compare
```

## External banking benchmark

The benchmark is a separate repository, not a BlastRadius subdirectory:

```bash
cd /absolute/path/to/benchmark
uv sync --all-groups

BASE=$(git rev-parse HEAD^)
TARGET=$(git rev-parse HEAD)

blastradius run \
  --repo . \
  --base "$BASE" \
  --target "$TARGET" \
  --python .venv/bin/python
```

The benchmark has 22 tests, a FastAPI transaction surface, and optional test
cost simulation:

```bash
BENCHMARK_TEST_DELAY=0.05 uv run pytest
```

The delay is useful for measuring selection savings. Keep it disabled for
normal development.

## Test patches and evaluation

For a normal PR that does not modify tests, no test-patch step is needed.
Existing target tests form the catalog.

For controlled evaluation, keep application and test changes separate:

```text
baseline commit
  └── application patch commit
        └── test patch commit
```

Analyze the application commit while making the test patch available only in
the working tree:

```bash
git checkout APPLICATION_COMMIT
git restore --source TEST_COMMIT --worktree -- tests

blastradius run \
  --repo . \
  --base BASE_COMMIT \
  --target APPLICATION_COMMIT
```

This prevents newly added answer-key tests from influencing model selection,
while still allowing them to run during evaluation.

The `evaluation/` directory contains controlled patch fixtures and a manifest.
Those files document expected detection results; BlastRadius does not consume
the answer key during normal selection.

## CI routing usage

CI can use BlastRadius as a test router: run likely affected tests first. Add
`--compare` when CI also needs full-suite comparison or confidence reporting.

Generic CI sequence:

```bash
git checkout "$CI_COMMIT_SHA"
BASE=$(git merge-base origin/main HEAD)

blastradius run \
  --repo . \
  --base "$BASE" \
  --target HEAD
```

Provider credentials belong in CI secrets:

```text
BLASTRADIUS_BASE_URL
BLASTRADIUS_MODEL
BLASTRADIUS_API_KEY
```

No BlastRadius server deployment is required. Install it as a CI tool, then
run it inside the checked-out target repository.

Example GitHub Actions workflow for a target repository:

```yaml
name: BlastRadius

on: pull_request

permissions:
  contents: read

jobs:
  regression-selection:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
      - run: uv tool install blastradius
      - run: uv sync
      - name: Run BlastRadius
        env:
          BLASTRADIUS_BASE_URL: ${{ secrets.BLASTRADIUS_BASE_URL }}
          BLASTRADIUS_MODEL: ${{ secrets.BLASTRADIUS_MODEL }}
          BLASTRADIUS_API_KEY: ${{ secrets.BLASTRADIUS_API_KEY }}
        run: |
          base=$(git merge-base origin/main HEAD)
          blastradius run \
            --repo . \
            --base "$base" \
            --target HEAD \
            --python .venv/bin/python \
            --format json \
            --record blastradius-result.json
```

The workflow belongs in the target repository. It is documentation here, not
an executable workflow for BlastRadius itself.

## Relevant problems

These are known follow-up improvements, not current correctness blockers:

- Some less common built-ins or dynamic calls may still be treated as unknown
  dependency edges, triggering the conservative full-suite fallback.
- Model recommendations are provider-dependent. Repeated runs can select
  different valid subsets unless the provider is configured deterministically.
- Dynamic dispatch and framework-generated routes remain only partially
  represented in the dependency graph.
