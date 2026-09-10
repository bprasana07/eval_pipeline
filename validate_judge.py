"""
Measure the judge against your labels.

Reports raw agreement and Cohen's kappa on the same frozen traces you
labelled by hand.

    python -B validate_judge.py traces-20260909-101500.json

Why kappa and not just agreement:

  If 90% of your cases pass, a judge that says PASS to everything scores 90%
  agreement while being useless. Kappa subtracts the agreement you would
  expect from chance alone, so that judge scores 0.

    kappa = (observed - expected) / (1 - expected)

  Rough reading:  > 0.80 strong    0.60-0.80 usable    < 0.60 rewrite the rubric
"""

import argparse
import json
from pathlib import Path

import config
from evaluators import llm_judge

TRACE_DIR = Path(__file__).parent / "traces"
LABEL_DIR = Path(__file__).parent / "labels"

# The judge emits a continuous score; your label is binary. This is the
# threshold that converts one to the other - deliberately the same number the
# quality gate uses, so validation measures the decision you actually ship.
PASS_AT = config.GATE.get("task_success", 0.90)


def kappa(pairs):
    """Cohen's kappa for two binary raters. pairs = [(human, judge), ...]"""
    n = len(pairs)
    if n == 0:
        return 0.0, 0.0

    agree = sum(1 for h, j in pairs if h == j)
    po = agree / n

    h1 = sum(h for h, _ in pairs) / n          # human pass rate
    j1 = sum(j for _, j in pairs) / n          # judge pass rate
    pe = h1 * j1 + (1 - h1) * (1 - j1)         # agreement expected by chance

    if pe >= 1.0:
        return po, 1.0 if po >= 1.0 else 0.0
    return po, (po - pe) / (1 - pe)


def verdict(k):
    if k >= 0.80:
        return "STRONG   - safe to gate on"
    if k >= 0.60:
        return "USABLE   - gate on it, keep watching"
    if k >= 0.40:
        return "WEAK     - rewrite the rubric before gating"
    return "UNUSABLE - the judge is close to guessing"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_file")
    args = parser.parse_args()

    config.require_azure()

    trace_path = TRACE_DIR / args.trace_file
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    traces = {t["trace_id"]: t for t in payload["traces"]}

    label_path = LABEL_DIR / f"{trace_path.stem}.labels.json"
    if not label_path.exists():
        raise SystemExit(f"\n  No labels yet. Run:  python -B label.py {args.trace_file}\n")
    labels = json.loads(label_path.read_text(encoding="utf-8"))

    with open("testcases.json") as f:
        cases = {c["case_id"]: c for c in json.load(f)}

    print(f"\n  Scoring {len(labels)} labelled traces with the judge "
          f"(pass threshold {PASS_AT})\n")
    print(f"  {'trace':<14} {'you':<7} {'judge':<7} {'score':<7} agree")
    print("  " + "-" * 52)

    pairs, disagreements = [], []

    for trace_id, human in labels.items():
        trace = traces[trace_id]
        case = cases[trace["case_id"]]

        results = llm_judge(case, trace)
        ts = next(r for r in results if r["name"] == "task_success")
        judge = 1 if ts["score"] >= PASS_AT else 0

        pairs.append((human, judge))
        mark = "yes" if human == judge else "NO"
        if human != judge:
            disagreements.append((trace_id, human, judge, ts["score"], ts["reason"]))

        print(f"  {trace_id:<14} {'pass' if human else 'fail':<7} "
              f"{'pass' if judge else 'fail':<7} {ts['score']:<7.2f} {mark}")

    po, k = kappa(pairs)

    print("\n  " + "-" * 52)
    print(f"  agreement      {po:.0%}  ({sum(1 for h, j in pairs if h == j)} of {len(pairs)})")
    print(f"  Cohen's kappa  {k:.2f}")
    print(f"  verdict        {verdict(k)}")

    human_rate = sum(h for h, _ in pairs) / len(pairs)
    if human_rate > 0.85 or human_rate < 0.15:
        print(f"\n  CAUTION: {human_rate:.0%} of your labels are one class. Kappa is")
        print("           unstable on unbalanced sets - add cases the agent fails.")

    if disagreements:
        print(f"\n  {len(disagreements)} disagreement(s) - read these before changing anything:\n")
        for tid, h, j, score, reason in disagreements:
            print(f"    {tid}   you said {'pass' if h else 'fail'}, "
                  f"judge said {'pass' if j else 'fail'} ({score:.2f})")
            print(f"      judge's reasoning: {reason}")
        print("\n    For each one, decide which is wrong:")
        print("      the AGENT   - rules and judge agree it is bad   -> fix the prompt")
        print("      the JUDGE   - rules and judge contradict        -> fix the rubric")
        print("      the TEST    - the expectation was impossible    -> fix the case")
    else:
        print("\n  No disagreements. With a small sample that is weak evidence -")
        print("  add cases the agent genuinely struggles with before trusting it.")

    print()


if __name__ == "__main__":
    main()
