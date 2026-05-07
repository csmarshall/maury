<!--
Thanks for the PR! A few quick checks before you submit.

Default reviewer: @csmarshall (set automatically via CODEOWNERS).
-->

## Summary

<!-- One paragraph: what does this change do, and why? -->

## Type of change

<!-- Check what applies -->
- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (requires manifest schema bump or migration)
- [ ] Documentation only
- [ ] Refactor / internal cleanup
- [ ] Test or CI infrastructure

## Related ADR / issue

<!-- Link to ADR(s) this implements/amends, or to the issue this closes. -->
<!-- Example: Implements ADR-0024. Closes #42. -->

## Tenet check

<!--
Maury is built on 11 tenets (docs/tenets.md). Anything that touches
state-changing behavior should call out which tenets are load-bearing
for the change. Most relevant for cross-boundary work, render/sync
changes, and trust-model adjustments.
-->

- [ ] Tenet 1 (first, do no harm) — no risk of losing user-added state
- [ ] Tenet 3 (trust boundaries are physical) — no boundary changes
- [ ] Tenet 5 (the user arbitrates ambiguity) — no silent precedence
- [ ] Tenet 7 (provenance is mandatory) — every fragment is traceable
- [ ] N/A — internal change, no tenet implications

## Test plan

<!-- Bulleted checklist of how this was verified or how to verify it. -->

- [ ] `uv run pytest`
- [ ] `uv run ruff check && uv run ruff format --check`
- [ ] `uv run mypy src/`
- [ ] Manual smoke test: <!-- describe -->

## Docs

<!-- Did you touch docs? If yes, was a fresh-context doc-review run? (CLAUDE.local.md rule) -->

- [ ] No docs changed
- [ ] Docs changed; doc-review subagent run + fixes applied before this PR
- [ ] Docs changed; doc-review queued as a follow-up commit
