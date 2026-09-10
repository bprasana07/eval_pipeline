"""
Measure how consistently the agent behaves.

capture.py shows you 25 lines and leaves you to spot the pattern. This turns
those lines into a number, so "did the prompt fix help?" has an answer rather
than an impression.

    python -B variance.py traces-20260909-130819.json
    python -B variance.py before.json after.json      compare two captures

Consistency = (runs taking the most common trajectory) / (total runs).
1.00 means every run did the same thing. 0.60 means 2 runs in 5 diverged.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces"


def load(name):
    path = TRACE_DIR / name
    if not path.exists():
        available = sorted(p.name for p in TRACE_DIR.glob("*.json")) if TRACE_DIR.exists() else []
        raise SystemExit(
            f"\n  Not found: {name}\n"
            + ("  Available: " + ", ".join(available) if available
               else "  Run  python -B capture.py  first.") + "\n"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def trajectory(trace):
    """The ordered tool sequence, as a comparable string."""
    calls = [s["name"] for s in trace["steps"] if s["type"] == "tool_call"]
    return " > ".join(calls) if calls else "(no tools)"


def analyse(payload):
    """Returns {case_id: {"consistency": float, "variants": Counter, "runs": int}}"""
    by_case = {}
    for trace in payload["traces"]:
        by_case.setdefault(trace["case_id"], []).append(trajectory(trace))

    out = {}
    for case_id, paths in sorted(by_case.items()):
        counts = Counter(paths)
        out[case_id] = {
            "consistency": counts.most_common(1)[0][1] / len(paths),
            "variants": counts,
            "runs": len(paths),
        }
    return out


def report(name, payload, results):
    print(f"\n  {name}   {payload['agent_version']}   "
          f"{payload['runs_per_case']} runs per case\n")

    for case_id, r in results.items():
        flag = "" if r["consistency"] == 1.0 else "   UNSTABLE"
        print(f"  {case_id}   consistency {r['consistency']:.2f}"
              f"   {len(r['variants'])} distinct path(s){flag}")
        for path, n in r["variants"].most_common():
            print(f"      {n}/{r['runs']}  {path}")

    overall = sum(r["consistency"] for r in results.values()) / len(results)
    unstable = [c for c, r in results.items() if r["consistency"] < 1.0]
    print(f"\n  overall consistency  {overall:.2f}")
    print(f"  unstable cases       {', '.join(unstable) if unstable else 'none'}")
    return overall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="one or two capture filenames")
    args = parser.parse_args()

    if len(args.files) == 1:
        payload = load(args.files[0])
        report("CAPTURE", payload, analyse(payload))
        print()
        return

    before_p, after_p = load(args.files[0]), load(args.files[1])
    before_r, after_r = analyse(before_p), analyse(after_p)

    b_overall = report("BEFORE", before_p, before_r)
    a_overall = report("AFTER", after_p, after_r)

    print("\n  " + "=" * 52)
    print(f"  {'case':<10} {'before':>8} {'after':>8}   change")
    print("  " + "-" * 52)
    for case_id in sorted(set(before_r) | set(after_r)):
        b = before_r.get(case_id, {}).get("consistency")
        a = after_r.get(case_id, {}).get("consistency")
        if b is None or a is None:
            continue
        arrow = "same" if abs(a - b) < 1e-9 else ("BETTER" if a > b else "WORSE")
        print(f"  {case_id:<10} {b:>8.2f} {a:>8.2f}   {arrow}")
    print("  " + "-" * 52)
    delta = a_overall - b_overall
    print(f"  {'overall':<10} {b_overall:>8.2f} {a_overall:>8.2f}   "
          f"{delta:+.2f}\n")

    if delta > 0.05:
        print("  The prompt change improved consistency.\n")
    elif delta < -0.05:
        print("  The prompt change made it WORSE. Revert and try again.\n")
    else:
        print("  No meaningful change. The prompt was not the cause,\n"
              "  or the sample is too small to tell.\n")


if __name__ == "__main__":
    main()