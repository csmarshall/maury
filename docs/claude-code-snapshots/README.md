# Claude Code documentation snapshots

In-repo snapshots of Anthropic's Claude Code documentation pages
that maury depends on, captured at specific verification dates.

## Why these exist

ADRs and `docs/claude-code-contract.md` cite Anthropic's live docs
at `https://code.claude.com/docs/en/...`. Live URLs can change
silently — Anthropic could rewrite a page, move it, or remove it
without warning, and our ADRs would be referencing something
different from what they were verified against.

These snapshots are the **canonical version of what the docs said
when we verified our claims.** Two purposes:

1. **Audit trail.** A future reader can see exactly what the docs
   said when an ADR was written, even if the live page has moved
   on.
2. **Drift detection.** A periodic re-fetch + hash comparison
   surfaces upstream changes proactively. When a hash differs,
   a human reviews the diff and decides whether the change
   affects any of our cited claims.

## Layout

```
docs/claude-code-snapshots/
  ├── README.md          ← you are here
  └── <YYYY-MM-DD>/      ← one directory per verification pass
      ├── MANIFEST.txt   ← name / bytes / sha256 / url for each snapshot
      ├── hooks.html
      ├── sessions.html
      ├── memory.html
      └── ...
```

## Refreshing snapshots

A periodic refresh process is **planned but not implemented yet**.
The mechanical sketch:

```sh
# Future: maury verify-cc-contract --refresh
# - re-fetches each URL listed in MANIFEST.txt
# - computes new sha256 against the stored value
# - diffs HTML where hashes differ
# - reports changes for human review
```

Until that command exists, manual refresh:

```sh
DATE=$(date +%Y-%m-%d)
mkdir -p docs/claude-code-snapshots/$DATE
# copy MANIFEST.txt from previous date as starting point
cp docs/claude-code-snapshots/<prev>/MANIFEST.txt \
   docs/claude-code-snapshots/$DATE/
# re-fetch each URL listed in MANIFEST.txt
# regenerate MANIFEST.txt with new sizes/hashes
# diff against the prior snapshot for each file
# update docs/claude-code-contract.md and any affected ADRs
```

## What lives in each snapshot

Raw HTML as fetched from the live URL (with `User-Agent: Mozilla/5.0
maury-doc-snapshot`). Content is server-side rendered, so the doc
text is in the HTML — no JavaScript execution needed.

Volatile bits (build IDs, asset hashes, generated timestamps) are
expected to change between fetches even if the documented behavior
hasn't changed. Drift detection uses hash mismatch as a SIGNAL to
look at a diff, not as a verdict that something broke. A human
reviews the diff and judges whether the change touches a behavior
we cite.

## Why not Wayback Machine?

Wayback snapshots (`web.archive.org/save/`) are a legitimate
alternative — third-party archive, zero repo storage, well-known
service. We chose in-repo snapshots as the primary mechanism
because:

- We own them. No third-party dependency for a load-bearing audit
  trail.
- They live in our git history alongside the ADRs that cite them.
- Diffs are trivially scriptable.
- Wayback may not snapshot frequently enough for our purposes,
  and saving a snapshot every time we cite a URL is a lot of
  third-party traffic.

Wayback as a belt-and-suspenders backup is still on the table —
once the main snapshot system is humming, we can add a follow-up
process to also save to Wayback for resilience.
