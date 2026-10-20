"""Accuracy test: run every demo test order through the pipeline and compare with its answer key.

    make eval                        all 20 demo orders
    make eval ARGS="--only 05"       one order (and the ones before it) with full detail
    make eval ARGS="--no-cache"      fresh AI calls instead of saved answers

Works on a fresh copy of the demo client in a temporary folder, so it never
touches real data. AI answers are kept in data/ai_cache/ so runs repeat exactly.
Fails (exit code 1) if any order is confidently wrong or reaches the wrong status.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.config import get_settings


@dataclass
class OrderResult:
    order: str
    expected_status: str
    actual_status: str | None
    exact: bool
    confidently_wrong: bool
    differences: list[str] = field(default_factory=list)
    missing_flags: list[str] = field(default_factory=list)
    hard_cases: list[str] = field(default_factory=list)
    ai_cost_gbp: str | None = None
    ai_tokens: int = 0
    processing_ms: int | None = None
    detail: dict | None = None


def _same_number(a, b) -> bool:
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    return Decimal(str(a)) == Decimal(str(b))


def compare(ours: dict, answer: dict) -> list[str]:
    """Differences between our standard order and the answer key, in plain words. Empty means exactly right."""
    diffs = []
    our_customer = (ours.get("customer") or {}).get("account_code")
    their_customer = (answer.get("customer") or {}).get("account_code")
    if our_customer != their_customer:
        diffs.append(f"customer {our_customer} (expected {their_customer})")
    if (ours.get("po_number") or None) != (answer.get("po_number") or None):
        diffs.append(f"PO {ours.get('po_number')} (expected {answer.get('po_number')})")
    if (ours.get("requested_delivery_date") or None) != (answer.get("requested_delivery_date") or None):
        diffs.append(f"delivery {ours.get('requested_delivery_date')} "
                     f"(expected {answer.get('requested_delivery_date')})")
    our_lines, their_lines = ours.get("lines") or [], answer.get("lines") or []
    if len(our_lines) != len(their_lines):
        diffs.append(f"{len(our_lines)} lines (expected {len(their_lines)})")
    for mine, theirs in zip(our_lines, their_lines):
        n = theirs["line_no"]
        if mine.get("product_code") != theirs.get("product_code"):
            diffs.append(f"line {n} product {mine.get('product_code')} (expected {theirs.get('product_code')})")
        if not _same_number(mine.get("qty"), theirs.get("qty")) or (mine.get("unit") or None) != (theirs.get("unit")
                                                                                                    or None):
            diffs.append(f"line {n} qty {mine.get('qty')} {mine.get('unit')} "
                         f"(expected {theirs.get('qty')} {theirs.get('unit')})")
        if not _same_number(mine.get("unit_price"), theirs.get("unit_price")):
            diffs.append(f"line {n} price {mine.get('unit_price')} (expected {theirs.get('unit_price')})")
    return diffs


def flag_codes(ours: dict) -> set[str]:
    codes = {f.get("code") for f in ours.get("flags") or []}
    for line in ours.get("lines") or []:
        codes |= {f.get("code") for f in line.get("flags") or []}
    return codes


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo_ai_answers"


def run_demo(cache_dir: Path | None, only: str | None, ai=None) -> list[OrderResult]:
    """Process the demo orders in a temporary data folder and score each one.

    ai, when given, answers every AI call (the tests pass one that replays saved answers).
    """
    from sqlmodel import Session

    from app import ai_client, db
    from app.orders import service
    from app.orders.standard import standard_order
    from pipeline.ai import AnthropicAI
    from seed.demo_data import load_demo_client, write_demo_files

    settings = get_settings()
    work = Path(tempfile.mkdtemp(prefix="orderdesk-eval-"))
    saved = os.environ.get("DATA_DIR"), ai_client.factory, service.run_later
    ai = ai or AnthropicAI(settings.anthropic_api_key, settings.anthropic_model, cache_dir=cache_dir)
    try:
        os.environ["DATA_DIR"] = str(work / "data")
        get_settings.cache_clear()
        db.reset_engine()
        ai_client.factory = lambda: ai
        service.run_later = lambda fn, *args: fn(*args)  # one order at a time, oldest first

        files = write_demo_files(work / "seed")
        answers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(files["answers"].glob("*.json"))]
        answers.sort(key=lambda a: a["received_at"])
        if only:
            match = [a for a in answers if a["order"].startswith(only)]
            if not match:
                raise SystemExit(f"No demo order starting with '{only}'.")
            answers = answers[:answers.index(match[0]) + 1]  # earlier orders too, for the duplicate check

        results = []
        with Session(db.get_engine()) as session:
            client = load_demo_client(session, files)
            for answer in answers:
                path = files["orders"] / answer["file"]
                received = datetime.fromisoformat(answer["received_at"].replace("Z", "+00:00"))
                orders, _ = service.receive_files(session, client, [(path.name, path.read_bytes())],
                                                  sender_email=answer["sender_email"], received_at=received)
                order = orders[0]
                session.expire_all()
                session.refresh(order)
                ours = standard_order(session, order)
                ours["status"] = order.initial_status
                diffs = compare(ours, answer)
                actual = order.initial_status
                results.append(OrderResult(
                    order=answer["order"],
                    expected_status=answer["expected_initial_status"],
                    actual_status=actual,
                    exact=not diffs,
                    confidently_wrong=actual == "ready" and bool(diffs),
                    differences=diffs,
                    missing_flags=sorted(set(answer.get("expected_flag_codes") or []) - flag_codes(ours)),
                    hard_cases=answer.get("hard_cases") or [],
                    ai_cost_gbp=str(order.ai_cost_gbp) if order.ai_cost_gbp is not None else None,
                    ai_tokens=order.ai_input_tokens + order.ai_output_tokens,
                    processing_ms=order.processing_ms,
                    detail={"ours": ours, "expected": answer} if only else None,
                ))
        return results
    finally:
        db.reset_engine()
        data_dir, ai_client.factory, service.run_later = saved
        if data_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = data_dir
        get_settings.cache_clear()
        shutil.rmtree(work, ignore_errors=True)


def summarise(results: list[OrderResult]) -> dict:
    n = len(results) or 1
    costs = [Decimal(r.ai_cost_gbp) for r in results if r.ai_cost_gbp is not None]
    times = [r.processing_ms for r in results if r.processing_ms is not None]
    return {
        "orders": len(results),
        "first_pass_exact": sum(r.exact for r in results),
        "first_pass_exact_pct": round(100 * sum(r.exact for r in results) / n, 1),
        "confidently_wrong": sum(r.confidently_wrong for r in results),
        "flagged": sum(r.actual_status in ("check", "problem") for r in results),
        "flagged_pct": round(100 * sum(r.actual_status in ("check", "problem") for r in results) / n, 1),
        "wrong_status": sum(r.actual_status != r.expected_status for r in results),
        "avg_ai_cost_gbp": str((sum(costs) / len(costs)).quantize(Decimal("0.0001"))) if costs else None,
        "avg_ai_tokens": round(sum(r.ai_tokens for r in results) / n),
        "avg_processing_ms": round(sum(times) / len(times)) if times else None,
    }


def print_table(results: list[OrderResult]) -> None:
    print(f"{'Order':30} {'Expected':9} {'Actual':9} {'Exact':6} Differences")
    print("-" * 110)
    for r in results:
        mark = "yes" if r.exact else "no"
        status = r.actual_status or "-"
        if r.actual_status != r.expected_status:
            status += " !"
        notes = "; ".join(r.differences)
        if r.missing_flags:
            notes += ("; " if notes else "") + "flags not raised: " + ", ".join(r.missing_flags)
        print(f"{r.order:30} {r.expected_status:9} {status:9} {mark:6} {notes}")


def print_summary(summary: dict, previous: dict | None) -> None:
    def delta(key: str) -> str:
        if not previous or previous.get(key) is None or summary.get(key) is None:
            return ""
        try:
            change = float(summary[key]) - float(previous[key])
        except (TypeError, ValueError):
            return ""
        return f"  ({change:+g} on last run)" if change else "  (same as last run)"

    print()
    print(f"First pass exact:   {summary['first_pass_exact_pct']}% ({summary['first_pass_exact']} of "
          f"{summary['orders']}){delta('first_pass_exact_pct')}")
    print(f"Confidently wrong:  {summary['confidently_wrong']}{delta('confidently_wrong')}")
    print(f"Flagged for review: {summary['flagged_pct']}% ({summary['flagged']}){delta('flagged_pct')}")
    print(f"Wrong status:       {summary['wrong_status']}{delta('wrong_status')}")
    cost = f"£{summary['avg_ai_cost_gbp']}" if summary["avg_ai_cost_gbp"] else "not known (set the AI prices in .env)"
    print(f"Average AI cost:    {cost} per order, {summary['avg_ai_tokens']} tokens")
    if summary["avg_processing_ms"] is not None:
        print(f"Average time:       {summary['avg_processing_ms'] / 1000:.1f}s per order")


def save_test_answers(paths: list[Path]) -> None:
    """Keep exactly the AI answers this run used, so tests can replay the demo run without the AI."""
    if FIXTURES_DIR.exists():
        shutil.rmtree(FIXTURES_DIR)
    FIXTURES_DIR.mkdir(parents=True)
    for path in paths:
        shutil.copyfile(path, FIXTURES_DIR / path.name)
    print(f"Saved {len(set(paths))} AI answers to {FIXTURES_DIR}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Order Desk accuracy test")
    parser.add_argument("--client", help="compare a real trial client against its staff entries")
    parser.add_argument("--no-cache", action="store_true", help="make fresh AI calls")
    parser.add_argument("--only", help="run one demo order (by its number or name) with full detail")
    parser.add_argument("--save-test-answers", action="store_true",
                        help="copy this run's AI answers into tests/fixtures for the offline pipeline test")
    args = parser.parse_args(argv)

    if args.client:
        print("Comparing a real client needs its uploaded staff entries, which arrive in Milestone 7.")
        return 2

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("No AI key is set. Add ANTHROPIC_API_KEY to the .env file first.")
        return 2
    results_dir = settings.data_dir / "eval"
    cache_dir = None if args.no_cache else settings.ai_cache_dir

    from pipeline.ai import AnthropicAI

    ai = AnthropicAI(settings.anthropic_api_key, settings.anthropic_model, cache_dir=cache_dir)
    results = run_demo(cache_dir, args.only, ai=ai)
    summary = summarise(results)
    print_table(results)
    if args.save_test_answers:
        if args.only or args.no_cache:
            print("Saving test answers needs a full run with the cache on.")
        else:
            save_test_answers(ai.used_cache_files)

    if args.only:
        last = results[-1]
        print()
        print(json.dumps(last.detail["ours"], indent=2, default=str))
        print_summary(summarise([last]), None)
        return 1 if (last.confidently_wrong or last.actual_status != last.expected_status) else 0

    previous = None
    if results_dir.is_dir():
        runs = sorted(results_dir.glob("*.json"))
        if runs:
            previous = json.loads(runs[-1].read_text(encoding="utf-8")).get("summary")
    print_summary(summary, previous)

    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (results_dir / f"{stamp}.json").write_text(json.dumps({
        "run_at": stamp, "model": settings.anthropic_model, "cached": not args.no_cache,
        "summary": summary, "orders": [asdict(r) for r in results],
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {results_dir / (stamp + '.json')}")

    failed = []
    if summary["confidently_wrong"]:
        failed.append(f"{summary['confidently_wrong']} confidently wrong (must be 0)")
    if summary["wrong_status"]:
        failed.append(f"{summary['wrong_status']} at the wrong status")
    if failed:
        print("\nFAILED: " + " and ".join(failed) + ".")
        return 1
    print("\nPASSED: no confidently wrong orders, every hard case at its expected status.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
