# 0.2.0 Alpha candidate acceptance

**Decision pending.** The MVP has a separate [release record](checklist.md). Use this protocol for one private deployment of the candidate. Record observations and approvals in the release PR or a restricted pilot record linked from it. Keep credentials, questions, excerpts and private account identifiers out of the release record.

## Local evidence collected 2026-11-06

The candidate working tree based on `3568684` passed 125 PostgreSQL/Qdrant integration tests, Ruff, mypy, Web lint and type checks, the offline baseline comparison, version checks and packaged smoke with both 0.2.0 images. Smoke verified loopback-only publishing, cancellation, restart persistence and log redaction. An abrupt API shutdown left a stale running row.

A separate drill created a synthetic v1 report and a Qdrant point at the MVP schema head. Both stopped volumes were archived and restored into fresh volumes under a different Compose project. After upgrading to Alpha, both regular accounts received 404 for the quarantined run. After explicit adoption and restart the owner read the v1 report through Web, the second account received 404 and the restored vector retained the expected scope. This drill used synthetic data. Actual pilot participants, external reachability and reviewer decisions remain pending.

## Candidate and evidence

Record the tested Git SHA, UTC timestamp, CI URL, image IDs, Alembic head, operator and reviewer. On any candidate change, identify affected observations and rerun CI on the exact SHA proposed for the tag.

| Check | Evidence required before GO |
|---|---|
| Packaging | Run `python3 scripts/check_release_version.py --tag v0.2.0` and `python3 scripts/check_release_compose.py`. Record full CI on the candidate, including Ruff, mypy, API and Web tests, offline baseline diff, PostgreSQL/Qdrant integration, both image builds and packaged smoke. Push the candidate SHA to a `codex/release-candidate/**` branch or dispatch CI with `full=true` to exercise all paths. |
| Private access | Record Web's loopback binding. From a client outside the approved access path, attempt the deployed web entry point and registration route. Record that access was denied at the boundary. From an admitted client, use the private tunnel and register a regular account. |
| Provider budget | Before a paid run, record owner, currency, approved cumulative ceiling, allowed run count, and who will stop the pilot. Check provider usage between runs. Until approval, use the stub and a paid-call budget of zero. Deep allows 600 seconds, 40 logical model calls and 12 searches per run. SDK retries and variable token volumes affect actual spend. |
| Upgrade and recovery | Follow [operations](operations.md#backups) on an isolated copy. Record the MVP SHA/schema, a known report and evidence ID, paired cold backups, and restored PostgreSQL and Qdrant reads. Upgrade to `da73e1b94026`, verify quarantine, adopt one scope, check owner access and another account’s 404, restart and reopen the v1 report. Verify fresh installation separately. Export v2 reports before a rollback to the paired MVP backup. |

## Invited pilot

Use two regular accounts A and B and a separate administrator. Record observed result, UTC time, SHA and an evidence reference for each row. Use synthetic documents for privacy checks and a disposable stack for failure injection.

| Phase | Steps | Reviewer decision |
|---|---|---|
| 8 | A and B sign in and run concurrently. Attach a text PDF as A. As B check history, the direct run ID, evidence search and upload using A’s scope. Verify 404 and absence of A’s content. Check Stop, logout and session revocation after password change or recovery. | Pending |
| 9 | An invited participant independently registers, runs research, attaches a PDF, reads citations and reopens a report after refresh. Record Stop and transport disconnect separately. Exercise insufficient evidence, failed and stale-running rows. Seed at least 21 owned runs and use Older runs. Check account switch and mobile keyboard focus. | Pending |
| 10 | Anonymous and regular users receive 401 and 403 respectively from admin APIs. As admin inspect compatible and incompatible evaluation runs, usage and diagnostics. Verify unknown cost, absence of synthetic private text and 404 on another account’s run. | Pending |

## Decision and release

The maintainer records GO or HOLD with date, reviewer, evidence links and outstanding risks. GO requires all three acceptance decisions, the external boundary check, approved pilot spend, restore evidence and full green CI. After GO, verify the tested SHA is reachable from master, create the annotated `v0.2.0` tag there, verify the tag workflow and publish the [changelog entry](../../CHANGELOG.md) as release notes.

Report offline regression and paid pilot observations separately. A claim of improved research quality requires the [blinded benchmark](../evaluation/research-quality/README.md), including all 10 holdouts, at least seven candidate wins and preserved supported-claim share.
