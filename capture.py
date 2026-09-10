"""
Capture traces to disk.

The point of this file is methodological, not technical.

A real agent is non-deterministic. If you label a trace today and the judge
scores a freshly-generated trace tomorrow, you are comparing two different
executions - and any disagreement tells you nothing about the judge, because
you were not looking at the same thing.

So: capture once, save to disk, then have BOTH the human and the judge score
that identical frozen set.

    python -B capture.py            3 runs per case (default)
    python -B capture.py --runs 5

Output: traces/traces-<timestamp>.json
"""

import argparse
import json
import time
from pathlib import Path

import config

if config.USE_REAL_AGENT:
    from agent_real import AGENT_VERSION, run_agent
else:
    from agent import AGENT_VERSION, run_agent

TRACE_DIR = Path(__file__).parent / "traces"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3,
                        help="how many times to run each case (default 3)")
    parser.add_argument("--out", default=None, help="output filename")
    args = parser.parse_args()

    if config.USE_REAL_AGENT:
        config.require_azure()

    with open("testcases.json") as f:
        cases = json.load(f)

    TRACE_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = TRACE_DIR / (args.out or f"traces-{stamp}.json")

    records = []
    total = len(cases) * args.runs
    n = 0

    print(f"\nCapturing {total} traces "
          f"({len(cases)} cases x {args.runs} runs) with {AGENT_VERSION}\n")

    for run in range(1, args.runs + 1):
        for case in cases:
            n += 1
            trace = run_agent(case)
            # trace_id is what the human label and the judge score both key on.
            trace["trace_id"] = f"{case['case_id']}-r{run}"
            trace["run"] = run
            records.append(trace)
            tools = [s["name"] for s in trace["steps"] if s["type"] == "tool_call"]
            print(f"  [{n:>3}/{total}] {trace['trace_id']:<12} "
                  f"{trace['latency_s']:>5.1f}s  ${trace['cost_usd']:.4f}  "
                  f"{', '.join(tools) or '(no tools)'}")

    payload = {
        "captured_at": stamp,
        "agent_version": AGENT_VERSION,
        "runs_per_case": args.runs,
        "traces": records,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\n  saved {len(records)} traces to {out_path.relative_to(Path.cwd())}")
    print(f"\n  next:  python -B label.py {out_path.name}\n")

    if not config.USE_REAL_AGENT:
        print("  NOTE: the scripted agent is deterministic, so every run of a case")
        print("        is identical. Set USE_REAL_AGENT=1 for traces that vary.\n")


if __name__ == "__main__":
    main()
