"""
Is the judge itself stable?

You have measured the agent's variance. The judge is also an LLM at
temperature 0, so everything that makes the agent non-deterministic applies
to it equally. A judge that scores the same trace 1.00 and then 0.00 cannot
gate a deploy, no matter how good its rubric reads.

This scores ONE frozen trace N times and reports the spread.

    python -B judge_stability.py traces-20260910-134347.json SD-001
    python -B judge_stability.py traces-20260910-134347.json SD-001 --n 7

Because the trace is identical every time, any variation you see is the
judge, not the agent.
"""

import argparse
import json
import statistics
from pathlib import Path

import config
from evaluators import llm_judge

TRACE_DIR = Path(__file__).parent / "traces"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_file")
    parser.add_argument("case_id", help="e.g. SD-001")
    parser.add_argument("--n", type=int, default=5, help="judge runs (default 5)")
    parser.add_argument("--run", type=int, default=1,
                        help="which captured run to use (default 1)")
    args = parser.parse_args()

    config.require_azure()

    path = TRACE_DIR / args.trace_file
    if not path.exists():
        available = sorted(p.name for p in TRACE_DIR.glob("*.json")) if TRACE_DIR.exists() else []
        raise SystemExit(f"\n  Not found: {args.trace_file}\n"
                         + ("  Available: " + ", ".join(available) if available else "") + "\n")

    payload = json.loads(path.read_text(encoding="utf-8"))
    trace_id = f"{args.case_id}-r{args.run}"
    trace = next((t for t in payload["traces"] if t["trace_id"] == trace_id), None)
    if trace is None:
        raise SystemExit(f"\n  No trace {trace_id} in {args.trace_file}\n")

    with open("testcases.json") as f:
        case = next(c for c in json.load(f) if c["case_id"] == args.case_id)

    print(f"\n  Scoring {trace_id} {args.n} times with the SAME judge prompt.")
    print(f"  The trace never changes, so any spread below is the judge.\n")
    print(f"  ANSWER BEING JUDGED")
    print(f"    {trace['final_answer']}\n")
    print(f"  {'run':<5} {'task_success':>13} {'response_quality':>18}   reason")
    print("  " + "-" * 74)

    scores = {"task_success": [], "response_quality": []}
    reasons = []

    for i in range(1, args.n + 1):
        results = llm_judge(case, trace)
        ts = next(r for r in results if r["name"] == "task_success")["score"]
        rq = next(r for r in results if r["name"] == "response_quality")["score"]
        reason = next(r for r in results if r["name"] == "task_success")["reason"]
        scores["task_success"].append(ts)
        scores["response_quality"].append(rq)
        reasons.append(reason)
        print(f"  {i:<5} {ts:>13.2f} {rq:>18.2f}   {reason[:38]}")

    print("\n  " + "-" * 74)
    for metric, values in scores.items():
        lo, hi = min(values), max(values)
        sd = statistics.pstdev(values)
        flag = "" if hi - lo < 1e-9 else "   UNSTABLE"
        print(f"  {metric:<18} mean {statistics.fmean(values):.2f}   "
              f"range {lo:.2f}-{hi:.2f}   sd {sd:.3f}{flag}")

    ts_range = max(scores["task_success"]) - min(scores["task_success"])
    print()
    if ts_range < 0.05:
        print("  The judge is stable on this trace. Disagreements with your labels")
        print("  are about the rubric's content, not its consistency.\n")
    elif ts_range < 0.30:
        print("  Mild variation. Tolerable if you gate on a mean of several runs.\n")
    else:
        print("  The judge is NOT stable. It gives materially different verdicts on")
        print("  identical input. Fix this before gating on it:")
        print("    - tighten the rubric so less is left to interpretation")
        print("    - remove criteria your deterministic rules already cover")
        print("    - or score each trace N times and use the median\n")


if __name__ == "__main__":
    main()