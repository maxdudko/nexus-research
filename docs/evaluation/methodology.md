# Evaluation methodology

Dataset version `2026-10-19.1`. Retrieval cutoff `K = 5`. The offline command does not call a model, an embedding API, or the network.

```bash
cd apps/api
uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json
```

The command exits 0 when the fixture schema, citation provenance, and grounding structure match the authored expectations. Retrieval scores are written into the artifact. They are not a pass/fail threshold. A later change can decide whether those measured numbers become a CI gate.

## Identity mapping

Gold files use stable semantic ids such as `web-limits#c0`. A frozen rank list is already an ordered list of those ids.

Production point ids are different. `evidence_point_id` is a UUID5 of `v2|{source_id}|{content_hash}|{claim_token}|{chunk_index}`. A new digest or claim token changes that UUID. The labels `E1..En` are assigned to one ranking and change when scores change. Gold files do not contain those point UUIDs.

The optional local adapter is the only path that builds `chunk UUID -> semantic id`. It uses the fixture claim token `33333333-3333-4333-8333-333333333333` and `evidence_point_id`, then scores the semantic ids. That check confirms the mapping. It does not measure embedding quality.

## Retrieval metrics

Each case keeps the first occurrence of a semantic id. A later duplicate does not add another hit. Frozen lists stay in their authored order. When the adapter has scores, equal scores sort by semantic id, and the first score seen for an id is the one that is kept.

- `Recall@K` is the number of relevant ids in the top K divided by the number of gold ids. An empty gold set is null.
- `Precision@K` uses K as the denominator. A list shorter than K does not shrink the denominator. An empty gold set is null.
- `MRR@K` is `1/rank` of the first relevant hit whose rank is at most K, otherwise 0. An empty gold set is null.

Each defined metric is rounded to 6 decimal places. The reported aggregate is the macro-average of those rounded values, rounded to 6 decimal places again. Cases with an empty gold set are counted as undefined and left out of the average.

## Citations and grounding

`citation_valid` means the `E*` labels are unique, match the case's source list, are canonical evidence ids, and resolve to chunks in that case's research scope. A web row has a URL and no page. A PDF row has a page and no URL. A foreign `research_id` fails the check.

`support_label` is stored beside that result. A citation can be valid while the passage does not support the claim. The checker does not decide entailment.

The grounding validator accepts only the cited excerpts, cuts each excerpt at 500 characters, and rejects an unknown citation or a passage from another research scope. It does not label the claim. The offline suite checks that this structural result matches `expect_structurally_valid`.

## Optional judge

The live judge is a separate object. It uses temperature 0 and `max_retries` 0, and it builds its own chat client. It does not call `build_chat_model`, so planner and synthesis settings stay unchanged. Importing `nexus.evaluation` does not construct a client. The judge has no tools and does not write a report.

`python -m nexus.evaluation --suite live` refuses to run unless `NEXUS_RUN_LIVE_AI=1`. It prints the published rates from the existing price table, refuses when the number of judge calls would exceed `--max-cases` (default 12), and records model, prompt version, dataset version, temperature, timestamp, and token provenance. Missing usage stays null. Claim text, excerpts, and rationale are not logged.

`calibration.py` builds a confusion matrix and an agreement rate from gold labels versus judge labels. Both figures are estimates on this authored set. The judge labels are `supported`, `unsupported`, `insufficient_context`, and `contradictory`. The uncertain fixture case is gold-labeled `insufficient_context`.
