#!/usr/bin/env python3
"""Paid research capture and blinded human comparison; separate from offline gates."""

import argparse
import hashlib
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "docs/evaluation/research-quality/questions.json"


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def usage_for(path: Path | None, run_id: str | None) -> dict:
    if path is not None and run_id is not None:
        for row in reversed(read_rows(path)):
            if row.get("run_id") == run_id and row.get("operation") == "usage_summary":
                return {
                    key: row.get(key)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "embedding_tokens",
                        "dropped",
                    )
                }
    return {
        "input_tokens": None,
        "output_tokens": None,
        "embedding_tokens": None,
        "dropped": None,
    }


def report_metrics(report: dict | None) -> dict | None:
    if report is None:
        return None
    if report.get("format_version") == 2:
        blocks = [
            *report.get("summary_blocks", []),
            *(
                b
                for section in report.get("sections", [])
                for b in section.get("blocks", [])
            ),
        ]
        text = " ".join(
            b.get("text", "")
            + " "
            + " ".join(cell for row in b.get("rows", []) for cell in row)
            for b in blocks
        )
        factual = [b for b in blocks if b.get("kind") != "context"]
        cited_share = (
            sum(bool(b.get("citations")) for b in factual) / len(factual)
            if factual
            else None
        )
    else:
        text = (
            report.get("summary", "")
            + " "
            + " ".join(s.get("content", "") for s in report.get("sections", []))
        )
        cited_share = None  # Section citations cannot measure claim-level coverage.
    sources = report.get("sources", [])
    domains = {urlsplit(s["url"]).hostname for s in sources if s.get("url")}
    return {
        "word_count": len(text.split()),
        "source_count": len({s["source_id"] for s in sources}),
        "domain_count": len(domains),
        "cited_block_share": cited_share,
        "model_verification": report.get("verification", "not_checked"),
    }


def stream_capture(
    client: httpx.Client, payload: dict
) -> tuple[dict | None, dict | None, str | None]:
    """Use SSE so deep runs survive the Web proxy's response-header timeout."""
    run_id = None
    completed = False
    event = ""
    with client.stream("POST", "/api/v1/research/stream", json=payload) as response:
        if not response.is_success:
            response.read()
            return (
                None,
                {
                    "http_status": response.status_code,
                    "code": response.json().get("error", {}).get("code", "unknown"),
                },
                None,
            )
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                run_id = data.get("run_id") or run_id
                if event == "research.completed":
                    completed = True
                    break
                if event in {"research.failed", "research.interrupted"}:
                    return None, {"code": data.get("code", "unknown")}, run_id
    if not completed or run_id is None:
        return None, {"code": "stream_disconnected"}, run_id
    detail = client.get(f"/api/v1/research/runs/{run_id}")
    detail.raise_for_status()
    body = detail.json()
    run = body["run"]
    return (
        {
            "run_id": run_id,
            "research_id": run["research_id"],
            "status": run["status"],
            "report": body["report"],
            "warnings": run["warnings"],
            "usage": run["usage"],
            "depth": run.get("depth"),
            "applied_limits": run.get("applied_limits"),
        },
        None,
        run_id,
    )


def capture(args: argparse.Namespace) -> None:
    if not args.allow_paid:
        raise SystemExit(
            "capture requires --allow-paid; research incurs provider charges"
        )
    email = os.environ.get("NEXUS_BENCH_EMAIL")
    password = os.environ.get("NEXUS_BENCH_PASSWORD")
    if not email or not password:
        raise SystemExit(
            "Set NEXUS_BENCH_EMAIL and NEXUS_BENCH_PASSWORD for an existing account"
        )
    dataset_bytes = args.dataset.read_bytes()
    dataset = json.loads(dataset_bytes)
    cases = [
        c for c in dataset["cases"] if args.split == "all" or c["split"] == args.split
    ]
    if args.limit is not None:
        cases = cases[: args.limit]
    with httpx.Client(
        base_url=args.base_url,
        timeout=620,
        headers={"Origin": args.base_url.rstrip("/")},
    ) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        login.raise_for_status()
        client.headers["x-csrf-token"] = client.cookies.get("nexus_csrf", "")
        # Exclusive creation prevents accidental replacement of the baseline.
        with args.output.open("x") as output:
            for case in cases:
                payload = {"question": case["question"]}
                if not args.legacy:
                    payload["depth"] = args.depth
                started_at = datetime.now(UTC).isoformat()
                start = time.monotonic()
                response_body = None
                error = None
                run_id = None
                try:
                    response_body, error, run_id = stream_capture(client, payload)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    error = {"type": type(exc).__name__}
                row = {
                    "case_id": case["id"],
                    "split": case["split"],
                    "question": case["question"],
                    "criteria": case["criteria"],
                    "dataset_version": dataset["version"],
                    "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                    "revision": args.revision,
                    "label": args.label,
                    "depth": args.depth,
                    "started_at": started_at,
                    "duration_seconds": round(time.monotonic() - start, 3),
                    "result": response_body,
                    "error": error,
                    "automatic_metrics": report_metrics(
                        response_body.get("report") if response_body else None
                    ),
                    "tokens": usage_for(
                        args.usage_log,
                        run_id,
                    ),
                }
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                print(f"{case['id']}: {'error' if error else response_body['status']}")


def blind(args: argparse.Namespace) -> None:
    left = {row["case_id"]: row for row in read_rows(args.baseline)}
    right = {row["case_id"]: row for row in read_rows(args.candidate)}
    if left.keys() != right.keys():
        raise SystemExit("Both artifacts must contain the same cases")
    rng = random.Random(args.seed)
    reviews, key = [], {}
    for case_id, baseline in left.items():
        candidate = right[case_id]
        if (baseline["question"], baseline["dataset_sha256"]) != (
            candidate["question"],
            candidate["dataset_sha256"],
        ):
            raise SystemExit("Dataset/question mismatch")
        swapped = rng.choice([False, True])
        pair = [baseline, candidate] if not swapped else [candidate, baseline]

        def anonymous(row: dict) -> dict:
            result = row.get("result") or {}
            report = result.get("report")
            # Show content and evidence; remove version/status to reduce identity cues.
            if report:
                report = {
                    k: v
                    for k, v in report.items()
                    if k not in {"format_version", "verification", "status"}
                }
            return {"report": report, "error": row["error"]}

        reviews.append(
            {
                "case_id": case_id,
                "split": baseline["split"],
                "question": baseline["question"],
                "criteria": baseline["criteria"],
                "A": anonymous(pair[0]),
                "B": anonymous(pair[1]),
                "winner": None,
                "rationale": "",
                "A_supported_claims": None,
                "A_total_claims": None,
                "B_supported_claims": None,
                "B_total_claims": None,
                "A_scores": {
                    k: None
                    for k in ["completeness", "source_quality", "analysis", "conflicts"]
                },
                "B_scores": {
                    k: None
                    for k in ["completeness", "source_quality", "analysis", "conflicts"]
                },
            }
        )
        key[case_id] = "A" if swapped else "B"
    args.output.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n")
    args.key.write_text(json.dumps(key, indent=2) + "\n")


def score(args: argparse.Namespace) -> None:
    reviews = json.loads(args.reviews.read_text())
    key = json.loads(args.key.read_text())
    holdout = [r for r in reviews if r["split"] == "holdout"]
    wins = 0
    candidate_supported = candidate_total = baseline_supported = baseline_total = 0
    complete = len(holdout) == 10 and len({r["case_id"] for r in holdout}) == 10
    for row in holdout:
        candidate = key[row["case_id"]]
        baseline = "B" if candidate == "A" else "A"
        complete &= row["winner"] in {"A", "B", "tie"} and bool(
            row["rationale"].strip()
        )
        wins += row["winner"] == candidate
        counts = [
            row[f"{side}_{kind}_claims"]
            for side in [candidate, baseline]
            for kind in ["supported", "total"]
        ]
        if not all(type(n) is int and n >= 0 for n in counts):
            complete = False
            continue
        cs, ct, bs, bt = counts
        if cs > ct or bs > bt:
            complete = False
            continue
        candidate_supported += cs
        candidate_total += ct
        baseline_supported += bs
        baseline_total += bt
    candidate_rate = candidate_supported / candidate_total if candidate_total else None
    baseline_rate = baseline_supported / baseline_total if baseline_total else None
    complete = complete and candidate_rate is not None and baseline_rate is not None
    passed = complete and wins >= 7 and candidate_rate >= baseline_rate
    print(
        json.dumps(
            {
                "complete": bool(complete),
                "holdout_cases": len(holdout),
                "candidate_wins": wins,
                "candidate_support_rate": candidate_rate,
                "baseline_support_rate": baseline_rate,
                "accepted": bool(passed),
            },
            indent=2,
        )
    )
    if not passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("capture")
    run.add_argument("--base-url", default="http://127.0.0.1:3000")
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--label", required=True)
    run.add_argument(
        "--revision",
        required=True,
        help="Deployed source revision, not the harness revision",
    )
    run.add_argument("--depth", choices=["standard", "deep"], default="standard")
    run.add_argument(
        "--split", choices=["development", "holdout", "all"], default="development"
    )
    run.add_argument("--limit", type=int)
    run.add_argument(
        "--legacy", action="store_true", help="Omit depth for the pre-change API"
    )
    run.add_argument(
        "--usage-log",
        type=Path,
        help="API JSONL log for observed tokens; unknown stays null",
    )
    run.add_argument("--allow-paid", action="store_true")
    run.add_argument("--output", type=Path, required=True)
    run.set_defaults(func=capture)
    review = sub.add_parser("blind")
    review.add_argument("--baseline", type=Path, required=True)
    review.add_argument("--candidate", type=Path, required=True)
    review.add_argument("--seed", type=int, default=6106)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--key", type=Path, required=True)
    review.set_defaults(func=blind)
    evaluate = sub.add_parser("score")
    evaluate.add_argument("--reviews", type=Path, required=True)
    evaluate.add_argument("--key", type=Path, required=True)
    evaluate.set_defaults(func=score)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
