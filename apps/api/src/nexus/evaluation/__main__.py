"""Command line for the offline suite and the opted-in live judge."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from nexus.core.config import get_settings
from nexus.evaluation.datasets import DatasetError
from nexus.evaluation.grounding import valid_case_count
from nexus.evaluation.runner import (
    load_or_raise,
    offline_failed,
    run_live,
    run_offline,
    write_report,
)
from nexus.observability.pricing import CHAT_PRICES, PRICING_VERSION


def main(argv: list[str] | None = None) -> None:
    """Exit 1 on fixture or provenance failure. Exit 2 when live is refused."""
    raise SystemExit(run_cli(argv))


def run_cli(argv: list[str] | None = None) -> int:
    """Score the fixtures. The live suite calls OpenAI only after the opt-in."""
    args = _parser().parse_args(argv)
    if args.max_cases < 0:
        print("max-cases must be zero or greater.", file=sys.stderr)
        return 2
    try:
        dataset = load_or_raise(args.fixtures)
    except DatasetError as exc:
        print(str(exc), file=sys.stderr)
        _write_error(args.output, args.suite)
        return 1
    offline = run_offline(dataset)
    if offline_failed(offline):
        write_report(args.output, offline)
        print("Fixture provenance checks failed.", file=sys.stderr)
        return 1
    if args.suite == "offline":
        write_report(args.output, offline)
        return 0
    if os.environ.get("NEXUS_RUN_LIVE_AI") != "1":
        print("Refusing the live judge. Set NEXUS_RUN_LIVE_AI=1.", file=sys.stderr)
        return 2
    calls = valid_case_count(dataset)
    if calls > args.max_cases:
        print(
            "Refusing the live judge because the case count exceeds the cap.",
            file=sys.stderr,
        )
        return 1
    settings = get_settings()
    print(live_cost_warning(settings.openai_model, calls), file=sys.stderr)
    if settings.openai_api_key is None:
        print(
            "Refusing the live judge because no OpenAI API key is configured.",
            file=sys.stderr,
        )
        return 2
    report = asyncio.run(run_live(dataset, settings))
    write_report(args.output, report)
    return 0


def live_cost_warning(model: str, calls: int) -> str:
    """Print published rates. Token totals are unknown until calls return."""
    row = CHAT_PRICES.get(model)
    if row is None:
        rates = "This model has no row in the price table, so no estimate is available."
    else:
        rates = (
            f"Published rates (pricing version {PRICING_VERSION}): "
            f"${row[0]} / 1M input tokens, ${row[1]} / 1M output tokens. "
            "These rates are estimates, not an invoice."
        )
    return (
        f"Live grounding judge will call {model} up to {calls} times. "
        f"{rates} Token use is unknown until each call returns."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score the versioned evaluation fixtures."
    )
    parser.add_argument("--suite", choices=("offline", "live"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=None)
    parser.add_argument("--max-cases", type=int, default=12)
    return parser


def _write_error(path: Path, suite: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"error_code":"dataset","suite":"{suite}"}}\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
