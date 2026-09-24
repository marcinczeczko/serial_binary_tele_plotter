# ADR-0001: Keep project records in `docs/`

- Status: Accepted
- Date: 2026-09-24

## Context

The project has changed quickly (about 50 commits in 3 months: a threaded worker,
refactors, a config editor, a uv migration). Its history lives only in terse commit
messages ("Fixes 1", "Solid implementation"). The reasons behind design choices, known
defects and planned work aren't written down. Contributors, human or AI, have to
rediscover them.

## Decision

Keep four kinds of records in git under `docs/`:

| Record | Path | Purpose | When to update |
|---|---|---|---|
| Architecture Decision Records | `docs/adr/NNNN-title.md` | Why a significant design choice was made, and what was rejected | New ADR for each significant decision. Never rewrite an accepted ADR: supersede it with a new one and link both ways |
| Reviews | `docs/reviews/YYYY-MM-DD-topic.md` | Point-in-time audits with stable finding IDs | When a review is done. Findings stay as written. Resolution is tracked in the roadmap and log |
| Roadmap | `docs/roadmap.md` | Living plan: work items, finding refs, done criteria, checkboxes | Tick items in the commit that completes them. Edit scope freely, and log why |
| Project log | `docs/project-log.md` | Dated, newest-first journal: what changed, measured numbers, lessons learned | Every meaningful change or finding |

`CLAUDE.md` at the repository root is the entry point. It summarises the architecture and
conventions and links to these records.

ADR format: Status (Proposed / Accepted / Superseded by NNNN / Rejected), Date, Context,
Decision, Consequences, and optionally Alternatives.

## Consequences

- Each non-trivial PR touches at least `project-log.md`, and the roadmap if it completes
  an item.
- Anyone, including an AI assistant starting a fresh session, can get oriented from
  `CLAUDE.md` → `roadmap.md` → the latest log entries.
- The records need a little upkeep. Keeping entries short (a few lines) keeps that cheap.
