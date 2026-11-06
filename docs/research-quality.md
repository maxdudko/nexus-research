# Research depth and analytical reports

New research runs accept `depth: "standard" | "deep"`; omission means `standard`.
The form offers both modes. Limits are chosen by the server and stored with the
run and checkpoint. The request remains connected; closing it cancels the run.

| Limit | Standard | Deep |
|---|---:|---:|
| Total time | 180 s | 600 s |
| Search rounds | 2 | 4 |
| Search calls | 6 | 12 |
| Attempted web pages (including failures) | 8 | 20 |
| Selected passages | 24 | 50 |
| Characters per passage | 2000 | 2000 |
| Logical model calls | 20 | 40 |
| Time reserved for writing/checking | 60 s | 150 s |

The current model remains `gpt-4o-mini` unless the operator already configured
another model. Profiles are upper limits, not promises to spend every call or
retrieve every passage. Standard collection has up to 120 seconds; deep collection
has up to 450 seconds. Stage-level timeouts protect the writing/checking reserve.
The existing hard request timeout retains its five-second cleanup grace.

The graph plans tasks and sufficiency criteria, searches each task in turn, ranks
candidate pages, ingests public documents, retrieves balanced task evidence,
analyzes claims and counterevidence, evaluates coverage, then repeats searches for
specific gaps. Tavily uses basic search for standard and advanced for deep.
Task-specific domain/time filters apply to that task's original queries; new gap
queries stay broad to avoid trapping refinement inside an inadequate source set.

Page allocation leaves capacity for subsequent rounds. Failed page attempts consume
the global cap; other candidates can still fill the current round. The HTTP client
advertises only the compression codecs supported by its bounded decoder. Candidate ranking considers
primary sources, relevance, date and independence. This is a model assessment of
search results, not a credibility guarantee. Search snippets are never used as
report evidence. Fetched HTML retains Markdown headings and tables; web chunks
carry section headings into continuation passages. Exact normalized content and
repeated excerpts are deduplicated; detecting all paraphrased reprints is not
promised. Analysis must not treat reprints as independent confirmation.

The writer receives the plan, evidence analysis, dated provenance, gaps and
passages. It produces a cited summary, scope/method, findings by subquestion,
comparisons/conflicts when present, and conclusions with limitations. Deep reports
aim for 1500–3000 words when supported; length is not a completion criterion.

## Report contract

Reports retain the legacy `title`, `summary`, `sections`, `limitations`, `status`
and `sources` fields. `format_version` defaults to 1 when old data is read.
Version 2 adds:

- `summary_blocks` (a brief overview), and `blocks` on each section;
- section `task_ids`, linking dedicated analytical sections to planned tasks;
- server-derived `body_gaps`, naming tasks without verified body analysis;
- block `kind` (`fact`, `inference`, `context`, `table`), `text`, `citations`,
  `columns` and `rows` (empty arrays for paragraphs);
- source `title`, `domain`, `published_at`, `retrieved_at`, `query`, `excerpt`;
- `verification`: `not_checked`, `passed`, or `incomplete`.

Source metadata comes from server records, not the report-writing model. Unknown
dates stay null. Paragraphs and tables carry citations to the existing scoped
`E*` references. Context blocks may contain only framing/method, not uncited
factual assertions. Methodology-only sections can therefore have no citations;
their context blocks still undergo support checking. The verifier checks every block, including the summary and all
table cells, against its cited passages, and flags planned tasks missing from
the report even when their evidence was retrieved. One repair and one recheck are permitted.
Known unsupported blocks remaining after checking are removed. Unavailable checks
preserve the draft with `partial` and an explicit limitation.

Each planned task requires its own cited analytical body section. Summary facts
and scope/method paragraphs do not count as body coverage. Missing sections are
completed with separate task-specific generation calls when budget permits.
Up to five synthesis calls are reserved inside each profile's existing total model
cap, including bounded malformed-output retries. Existing analytical sections are
preserved, and a repair that removes them is rejected. Missing sections are
shown explicitly as gaps; a model support check cannot override this structural
requirement. The UI also flags older summary-only reports without changing the
stored history.

Only an unrepaired report with complete task coverage and passing support checks
can become `completed`. A repaired report remains conservatively `partial`, since
repair may have removed answers. Passing checks remain a model assessment.
The UI shows a contents list, tables, inference labels, source metadata and
expandable exact cited excerpts. Version 1 reports use the existing renderer.
No-source and deterministic fallback reports may still use version 1.

## Upgrade and rollback

Run the normal `alembic upgrade head` before starting the new API. Migration
`da73e1b94026` follows `a4d9c2f78106`, adding `depth` and `applied_limits` to
`research_runs`; existing rows receive `standard` and `{}` (historical limits are
unknown). No report backfill or Qdrant rebuild is required. Existing PDFs remain
readable through their stored evidence. New runs fetch/index web content with
the new extraction. Web digests include the chunking version, so fetching an
existing source again refreshes fragments produced by the previous table strategy.
The embedding dimensions and point-id formula do not change.
Checkpoint state is version 2; old checkpoints are not resumed (runs remain
request-scoped). Stored domain reports remain readable independently of checkpoints.

Rollback the application only after exporting newly generated v2 reports, because
the older strict report parser cannot read the added v2 fields. Migration downgrade
removes the two run metadata columns and does not transform report JSON. The new
API itself can read both report versions.

See [quality evaluation](evaluation/research-quality/README.md) for the 30-case
capture and blinded review procedure. The existing frozen offline gate is unchanged.
