# Testing standards

Test coverage is non-negotiable. Maury is a tool that touches your
config across hosts and trust boundaries — bugs aren't just "broken
features," they can mean leaked content, lost work, or silent drift.
The test suite is part of the safety story.

## What "full coverage" means here

Every module ships with tests covering:

1. **Happy path** — the normal use case works.
2. **Edge cases** — empty inputs, single-element inputs, very large
   inputs, malformed inputs.
3. **Error paths** — every `raise` is exercised; every error message
   that names a specific condition has a test that triggers it.
4. **Validation failures** — every schema rule has a test that
   produces the expected error.
5. **Round-trips where applicable** — load + dump + load produces the
   same result.

Test count rough target: **~1:1 ratio of test code to source code**.
At the time of writing we're at ~1500 lines of tests vs ~1700 of
source, which is the right neighborhood.

## Module-level expectations

| Module | Coverage status | Tests |
|---|---|---|
| `rules/` | full | 36 — engine, loader, schema, trace, validation, seed YAML |
| `manifest.py` | full | 30 — parse, validate, dump, lookup, inheritance, surrogate keys |
| `capability/` | full | 30 — OS detection, userland, tools, GUI, MDM, end-to-end probe with mocked shims |
| `doctor/` | full | 24 — every check (length, platitude, standard-convention, tutorial-style, file-by-file), text + JSON renderers |
| `render/` | not yet built | will require integration tests against fixture repos |
| `mining/` | not yet built | LLM calls mocked; pattern detection on fixture transcripts |
| `proposals/` | not yet built | full review-flow simulation |
| `bootstrap/` | not yet built | gh subprocess mocked; SSH key gen real |
| `secrets/` (v1.1) | not yet built | per-backend (keychain/secret-tool/age) with real backends in CI matrix |

## Test conventions

- **Tests live under `tests/unit/` for unit tests and `tests/integration/` for cross-module flows.** Per the project layout already in pyproject.
- **Pytest is the runner.** No other framework.
- **Fixtures use `json.dumps()` or proper Python builders, never concatenated f-strings.** The `f' "x": {{"a": 1}}'` pattern with brace-escaping is a footgun (we hit it once in `test_manifest.py`). Always serialize from a real dict.
- **Mocks at the abstraction boundary.** The capability probe pattern (monkey-patch `_system`, `_run`, `_which`) is the model — small surface, easy to fake. Avoid mocking deep into stdlib.
- **No network calls in unit tests.** No git operations, no HTTP, no Anthropic API calls. Integration tests can shell out to local-only operations (`git init` in `tmp_path`); anything else is mocked.
- **Every CLI command has at least one smoke test** that exercises the click runner via `click.testing.CliRunner`. Currently informal — formalize as Phase 4 lands.

## Definition of done for any phase

A phase is not "complete" until:

1. All new code is covered per the module-level expectations above.
2. `uv run pytest tests/unit/ -v` passes 100%.
3. The phase's CLI commands have been smoke-tested manually (and ideally via CliRunner).
4. The session-state.md test count reflects the new total.
5. (Once we have CI) the CI run is green.

## Test code is product code

Treat test code with the same review rigor as source. Bad tests
(non-deterministic, overly mocked, asserting nothing meaningful) are
worse than no tests because they create false confidence. If a test
breaks during a refactor, first ask whether the test was actually
testing something or just the implementation accidentally; refactor
the test to assert the contract, not the call shape.

## Continuous integration (post-v1)

Not yet wired up. Plan: GitHub Actions matrix running `uv sync` +
`uv run pytest` on macOS + Ubuntu (FreeBSD is a Tier 2 concern;
add a self-hosted runner on kamek if maury is ever built there).
For now, the standard is "green locally before any commit."
