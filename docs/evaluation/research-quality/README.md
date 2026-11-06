# Research quality evaluation

This suite measures live research separately from the frozen offline regression gate.
`questions.json` has 20 development cases and 10 held-out cases, with topic-specific
acceptance criteria. Do not tune prompts on the held-out answers.

## Current evidence

The pre-change source revision is `7c9a85f012bf8d0a20e7427deb8a032462746d24`.
No 30-case before/after result or human acceptance score is claimed by this change.
The dataset and capture/review tools are ready; an end-to-end paid smoke is a
separate technical check, not evidence of a quality improvement.

## Capture

Use an existing benchmark account on an isolated deployment of each revision.
The harness never deploys, migrates, registers accounts, or sends email. Set
`NEXUS_BENCH_EMAIL` and `NEXUS_BENCH_PASSWORD` in the environment. Credentials
are not written into artifacts. Keep artifacts private: they contain reports and
source excerpts. Use the same dataset, model and provider settings for both builds.
For exact token aggregation, set `NEXUS_AI_MAX_RETRIES=0` on both deployments;
otherwise unknown physical retry counts may leave totals unavailable.

From the repository root:

```bash
apps/api/.venv/bin/python scripts/research_benchmark.py capture \
  --base-url http://127.0.0.1:3000 --legacy \
  --label baseline-current --revision 7c9a85f012bf8d0a20e7427deb8a032462746d24 \
  --split all --allow-paid --usage-log /tmp/baseline-api.jsonl \
  --output /tmp/baseline-current.jsonl
```

`--legacy` omits the new `depth` request field for the old API. To distinguish
pipeline quality from larger limits, also capture the baseline with its existing
server settings set to 2 iterations, 6 searches, 20 model calls, 8 pages, 24 passages,
2000 excerpt characters, and 180 seconds. Label that artifact `baseline-matched`.
The old settings accept these values. Preserve the original baseline separately.
On the candidate deployment, omit `--legacy`, use `--depth standard`, and label the
artifact `candidate-standard`. Capture `--depth deep` separately as `candidate-deep`.
Always supply the deployed revision explicitly; the harness revision is not proof
of what the server runs. Also record a copy of the non-secret deployment settings.

Capture uses SSE and loads the committed report from history, so Deep mode
does not wait for response headers behind the Web proxy.
Capture is sequential and writes each case immediately. Each output must be a new
file. Failed requests remain cases with errors; do not drop them. `--limit 1` is
useful for a paid connectivity check. `--split development` is the default; use
`--split all` for the final 30-case comparison.

Artifacts retain question, criteria, dataset hash, revision, mode, full response,
source references/excerpts when the API provides them, failures, and elapsed time.
`--usage-log` reads the API's existing JSONL `usage_summary` records by run id.
Automatic diagnostics include word count, unique sources/domains, cited-block
share for v2, and the model verification status. These are not entailment scores.
Unknown token counts remain null, including when SDK retries prevent exact totals.
No usage log means token counts are unavailable, not zero. Baseline v1 reports do
not embed excerpts; archive that deployment's evidence/checkpoint storage with the
capture if later claim-level review needs exact original passages.

## Blinded review and acceptance

```bash
apps/api/.venv/bin/python scripts/research_benchmark.py blind \
  --baseline /tmp/baseline-matched.jsonl --candidate /tmp/candidate-standard.jsonl \
  --output /tmp/review.json --key /tmp/review-key.json
```

Give reviewers only `review.json`. It randomizes A/B and removes version/status
labels; differences in report structure may still reveal implementation features.
Keep the mapping file away from reviewers until scoring is complete. Repeat for
baseline-current versus candidate-deep as a separate comparison.

For each pair, fill `winner` with `A`, `B`, or `tie`, explain the choice in
`rationale`, score completeness, source quality, depth of analysis, and treatment
of conflicts from 1 to 5. Count all independently checkable factual assertions,
including tables and summary, then the number supported by their cited passages.
Use the same counting rules for both versions. Citations that merely exist do not
count as support; reprints do not count as independent corroboration. Check primary
sources, applicable dates, units and comparability. Review numeric and conflicting
claims manually. Failed cases remain in the comparison.

```bash
apps/api/.venv/bin/python scripts/research_benchmark.py score \
  --reviews /tmp/review.json --key /tmp/review-key.json
```

Acceptance requires all 10 holdout reviews, at least 7 candidate wins, and no
reduction in supported-claim share. Missing/invalid reviews cannot pass. Record
standard and deep results separately; a slower deep run is not an equal-budget
comparison. Report actual time and observed tokens alongside quality judgments.
The model's own verification is an automated diagnostic, not an independent judge.

## Technical smoke

With local PostgreSQL/Qdrant and valid provider keys in `apps/api/.env`:

```bash
cd apps/api
NEXUS_RUN_LIVE_AI=1 .venv/bin/python -m pytest \
  tests/integration/test_quality_live.py -m live -q
```

This uses the guarded `nexus_test` database and a temporary Qdrant collection.
It makes paid calls, cleans up storage, and leaves the report and observed
operations at `/tmp/nexus-quality-live-smoke.json`. Do not run it concurrently
with other integration tests: they share the disposable database name.
