"""
Label captured traces by hand.

You are producing the ground truth the judge gets measured against. Label
what you actually see, not what you hope the agent did - if you soften a
label because you know it will make the numbers look better, the whole
exercise is pointless.

    python -B label.py traces-20260909-101500.json

Controls:  p = pass    f = fail    s = skip    q = save and quit

Progress is saved after every answer, so you can stop and resume.
Output: labels/<trace-file>.labels.json
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces"
LABEL_DIR = Path(__file__).parent / "labels"


def render(trace, case, index, total, done):
    print("\n" + "=" * 78)
    print(f"  {trace['trace_id']}          {index} of {total}   "
          f"({done} labelled so far)")
    print("=" * 78)

    print("\n  USER ASKED")
    for line in textwrap.wrap(trace["input"], 72):
        print(f"    {line}")

    print("\n  EXPECTED OUTCOME")
    for line in textwrap.wrap(case["expected_outcome"], 72):
        print(f"    {line}")

    print("\n  WHAT THE AGENT DID")
    calls = [s for s in trace["steps"] if s["type"] == "tool_call"]
    if not calls:
        print("    (no tools were called)")
    for i, c in enumerate(calls, 1):
        print(f"    {i}. {c['name']}({json.dumps(c.get('args', {}))})")

    print("\n  FINAL ANSWER")
    for line in textwrap.wrap(trace["final_answer"], 72):
        print(f"    {line}")

    print("\n" + "-" * 78)
    print("  Did the agent achieve the expected outcome?   [p]ass  [f]ail  [s]kip  [q]uit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_file", help="filename inside traces/")
    args = parser.parse_args()

    trace_path = TRACE_DIR / args.trace_file
    if not trace_path.exists():
        available = sorted(p.name for p in TRACE_DIR.glob("*.json")) if TRACE_DIR.exists() else []
        raise SystemExit(
            f"\n  Not found: {trace_path}\n"
            + ("  Available: " + ", ".join(available) if available
               else "  Run  python -B capture.py  first.") + "\n"
        )

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    traces = payload["traces"]

    with open("testcases.json") as f:
        cases = {c["case_id"]: c for c in json.load(f)}

    LABEL_DIR.mkdir(exist_ok=True)
    label_path = LABEL_DIR / f"{trace_path.stem}.labels.json"
    labels = {}
    if label_path.exists():
        labels = json.loads(label_path.read_text(encoding="utf-8"))
        print(f"\n  Resuming: {len(labels)} already labelled.")

    total = len(traces)
    for i, trace in enumerate(traces, 1):
        if trace["trace_id"] in labels:
            continue

        render(trace, cases[trace["case_id"]], i, total, len(labels))

        while True:
            try:
                key = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                key = "q"
            if key in {"p", "f", "s", "q"}:
                break
            print("  Enter p, f, s or q.")

        if key == "q":
            break
        if key == "s":
            continue

        labels[trace["trace_id"]] = 1 if key == "p" else 0
        label_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")

    passes = sum(labels.values())
    print(f"\n  {len(labels)} of {total} labelled  "
          f"({passes} pass, {len(labels) - passes} fail)")
    print(f"  saved to {label_path.relative_to(Path.cwd())}")
    print(f"\n  next:  python -B validate_judge.py {args.trace_file}\n")


if __name__ == "__main__":
    sys.exit(main())
