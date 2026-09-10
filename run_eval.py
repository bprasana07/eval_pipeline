"""
The runner.

    load cases -> run agent N times -> evaluate each -> average -> gate

Exit code 0 = PASS, 1 = FAIL. That exit code is the whole point: it's what
lets CI block a deploy.

Why N runs and not one: a real agent is non-deterministic even at
temperature 0. One run of a case is a sample, not a measurement. Gating on a
single sample gives you a flaky gate, and a flaky gate gets overridden and
then switched off.

Usage:
    python run_eval.py                  RUNS_PER_CASE from .env (default 1)
    python run_eval.py --runs 3         3 runs per case, gate on the mean
    python run_eval.py --judge          add the LLM judge
    python run_eval.py --verbose        per-case detail
"""

import argparse
import json
import sys
from collections import defaultdict

import config

# Which agent runs is set by USE_REAL_AGENT in .env.
if config.USE_REAL_AGENT:
    from agent_real import AGENT_VERSION, run_agent
else:
    from agent import AGENT_VERSION, run_agent

from evaluators import HARD_RULES, RULE_EVALUATORS, llm_judge


# The quality gate lives in config.py so that changes to it are reviewable.
GATE = config.GATE


def evaluate_once(case, use_judge):
    """One execution of one case. Returns (trace, results, judge_skipped)."""
    trace = run_agent(case)
    results = [evaluator(case, trace) for evaluator in RULE_EVALUATORS]

    # Short-circuit: if a hard rule already failed, don't pay for the judge.
    hard_failure = any(r["score"] < 1.0 and r["name"] in HARD_RULES for r in results)

    if use_judge:
        if hard_failure:
            # A case too broken to grade scores zero, not "not applicable" -
            # otherwise the worst cases drop out of the average and a failing
            # agent looks better than it is.
            results.extend(
                {"name": m, "score": 0.0, "reason": "hard rule failed; not graded"}
                for m in config.JUDGE_METRICS
            )
        else:
            results.extend(llm_judge(case, trace))

    return trace, results, hard_failure


def evaluate_case(case, use_judge, runs):
    """Run one case `runs` times and reduce to per-metric means."""
    attempts = [evaluate_once(case, use_judge) for _ in range(runs)]

    by_metric = defaultdict(list)
    worst = {}
    for _, results, _ in attempts:
        for r in results:
            by_metric[r["name"]].append(r["score"])
            # Keep the reason from the LOWEST-scoring run: a mean of 0.67 is
            # explained by the run that failed, not the two that passed.
            if r["name"] not in worst or r["score"] < worst[r["name"]][0]:
                worst[r["name"]] = (r["score"], r["reason"])

    scores = {m: sum(v) / len(v) for m, v in by_metric.items()}
    spread = {m: (min(v), max(v)) for m, v in by_metric.items()}
    unstable = {m for m, (lo, hi) in spread.items() if hi - lo > 1e-9}

    return {
        "case": case,
        "scores": scores,
        "spread": spread,
        "unstable": unstable,
        "reasons": {m: r for m, (_, r) in worst.items()},
        "per_run": dict(by_metric),
        "judge_skipped": any(skipped for _, _, skipped in attempts),
    }


def aggregate(per_case):
    """Average each metric across cases (each case already averaged its runs)."""
    buckets = defaultdict(list)
    for c in per_case:
        for metric, score in c["scores"].items():
            buckets[metric].append(score)

    metrics = {m: sum(v) / len(v) for m, v in buckets.items()}
    metrics["overall"] = sum(metrics.values()) / len(metrics) if metrics else 0.0
    coverage = {m: len(v) for m, v in buckets.items()}
    return metrics, coverage


def flagged(metric, score):
    """Is this score bad enough to call out on the case line?"""
    return score < GATE.get(metric, config.CASE_FLAG_THRESHOLD)


def check_gate(metrics, per_case):
    """
    Two independent checks:

      1. AGGREGATE - is the system good on average?
      2. PER CASE  - is any single case unacceptable?

    Both must pass. Without the second, four healthy cases can average a
    broken one past the threshold and the gate goes green on a real defect.
    """
    breaches = [
        ("aggregate", m, metrics[m], t) for m, t in GATE.items()
        if m in metrics and metrics[m] < t
    ]

    for metric, floor in config.CASE_FLOOR.items():
        for c in per_case:
            score = c["scores"].get(metric)
            if score is not None and score < floor:
                breaches.append((c["case"]["case_id"], metric, score, floor))

    return (len(breaches) == 0), breaches


def main():
    parser = argparse.ArgumentParser()
    # Defaults come from .env; the flags are a per-run override.
    parser.add_argument("--runs", type=int, default=config.RUNS_PER_CASE,
                        help="executions per case; gate on the mean")
    parser.add_argument("--judge", action="store_true", default=config.USE_JUDGE,
                        help="enable the LLM judge (default from USE_JUDGE in .env)")
    parser.add_argument("--no-judge", dest="judge", action="store_false",
                        help="force the judge off for this run")
    parser.add_argument("--verbose", action="store_true", default=config.VERBOSE,
                        help="show per-case detail (default from VERBOSE in .env)")
    parser.add_argument("--quiet", dest="verbose", action="store_false",
                        help="summary only")
    args = parser.parse_args()

    # Fail fast: check credentials before doing any work.
    if config.USE_REAL_AGENT or args.judge:
        config.require_azure()

    with open("testcases.json") as f:
        cases = json.load(f)

    per_case = [evaluate_case(c, args.judge, args.runs) for c in cases]

    runs_note = f" x {args.runs} runs" if args.runs > 1 else ""
    print(f"\nAgent {AGENT_VERSION} - {len(cases)} cases{runs_note}"
          f"{' - judge enabled' if args.judge else ' - rules only'}\n")

    # --- per-case lines ----------------------------------------------------
    any_unstable = False
    for c in per_case:
        bad = [m for m, s in c["scores"].items() if flagged(m, s)]
        if c["unstable"]:
            any_unstable = True

        mark = "FAIL" if bad else ("VARY" if c["unstable"] else "ok  ")
        print(f"  {mark}  {c['case']['case_id']}  {c['case']['input'][:50]}")

        for m in bad:
            print(f"          {m}: {c['reasons'][m]}")

        # A metric that moved between runs is worth naming even when the mean
        # still passes. It is the early warning for a flaky gate.
        for m in sorted(c["unstable"]):
            runs_str = ", ".join(f"{v:.2f}" for v in c["per_run"][m])
            print(f"          {m} varied across runs: {runs_str}")

        if c["judge_skipped"]:
            print("          judge skipped on at least one run (hard rule failed)")

        if args.verbose:
            for m in sorted(c["scores"]):
                lo, hi = c["spread"][m]
                rng = "" if m not in c["unstable"] else f"  [{lo:.2f}-{hi:.2f}]"
                print(f"          . {m:<18} {c['scores'][m]:.2f}{rng}  {c['reasons'][m]}")

    # --- summary -----------------------------------------------------------
    metrics, coverage = aggregate(per_case)
    n_cases = len(cases)

    print("\n  " + "-" * 56)
    for name in sorted(metrics):
        if name == "overall":
            continue
        note = "" if coverage[name] == n_cases else f"   (n={coverage[name]}/{n_cases})"
        shaky = "  unstable" if any(name in c["unstable"] for c in per_case) else ""
        print(f"  {name:<22} {metrics[name]:.2f}{note}{shaky}")
    print("  " + "-" * 56)
    print(f"  {'overall':<22} {metrics['overall']:.2f}")

    # --- gate --------------------------------------------------------------
    passed, breaches = check_gate(metrics, per_case)
    print()
    for scope, metric, actual, threshold in breaches:
        label = "GATE BREACH " if scope == "aggregate" else f"CASE FLOOR  "
        where = "" if scope == "aggregate" else f" [{scope}]"
        print(f"  {label} {metric}{where} {actual:.2f} < {threshold:.2f}")

    if args.runs == 1:
        print("  NOTE: single run. This verdict is one sample, not a measurement.")
        print("        Use --runs 3 for a gate that decides a deploy.")
    elif any_unstable:
        print("  NOTE: some metrics moved between runs. The mean decided this")
        print("        verdict; a single-run gate could have gone either way.")

    print(f"\n  RESULT: {'PASS - deploy' if passed else 'FAIL - deploy blocked'}\n")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()