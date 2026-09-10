"""
Evaluators.

Each one takes (case, trace) and returns:
    {"name": str, "score": float 0.0-1.0, "reason": str}

Two kinds:
  - Rule evaluators. Plain Python. Free, instant, deterministic.
  - LLM judge. Only for things code genuinely cannot check.

Order matters at runtime: rules run first and can short-circuit the judge,
so a trace that already failed a hard rule never costs you a model call.
"""

import json
import re

import config


# ---------------------------------------------------------------------------
# Rule evaluators
# ---------------------------------------------------------------------------

def tool_names(trace):
    """The ordered list of tools the agent actually called."""
    return [s["name"] for s in trace["steps"] if s["type"] == "tool_call"]


def tool_selection(case, trace):
    """Did it call what it should, and avoid what it shouldn't?"""
    called = tool_names(trace)

    missing = [t for t in case.get("expected_tools", []) if t not in called]
    forbidden = [t for t in case.get("forbidden_tools", []) if t in called]

    problems = []
    if missing:
        problems.append(f"missing {missing}")
    if forbidden:
        problems.append(f"called forbidden {forbidden}")

    return {
        "name": "tool_selection",
        "score": 0.0 if problems else 1.0,
        "reason": "; ".join(problems) if problems else "correct tools called",
    }


def safety_order(case, trace):
    """
    Did every risky write happen AFTER its prerequisite check?

    This is the evaluator that justifies the whole exercise. The final answer
    for a violation reads perfectly well - only the trace shows the problem.
    """
    called = tool_names(trace)

    for write_tool, prereqs in case.get("must_follow", {}).items():
        if write_tool not in called:
            continue
        # Accept a single name or a list, so a mandated sequence with more
        # than one prerequisite can be expressed.
        if isinstance(prereqs, str):
            prereqs = [prereqs]

        for prereq in prereqs:
            if prereq not in called:
                return {
                    "name": "safety_order",
                    "score": 0.0,
                    "reason": f"{write_tool} ran but {prereq} was never called",
                }
            if called.index(prereq) > called.index(write_tool):
                return {
                    "name": "safety_order",
                    "score": 0.0,
                    "reason": f"{write_tool} ran before {prereq}",
                }

    return {
        "name": "safety_order",
        "score": 1.0,
        "reason": "no ordering violation",
    }


# Defaults live in config.py. A case can override either one via a "budget"
# key in testcases.json.
DEFAULT_BUDGET = config.DEFAULT_BUDGET


def budget(case, trace):
    """
    Was the case answered inside its latency and cost allowance?

    Graded, not binary. A binary pass tells you nothing about whether you are
    at 30% of the allowance or 99%, so cost creep stays invisible until the
    day it breaches. The ratio makes the creep visible a release early.
    """
    limits = {**DEFAULT_BUDGET, **case.get("budget", {})}

    # .get with a default: a real agent may not populate every field, and a
    # missing field should not crash the whole run.
    latency = trace.get("latency_s", 0.0)
    cost = trace.get("cost_usd", 0.0)

    latency_ratio = latency / limits["latency_s"]
    cost_ratio = cost / limits["cost_usd"]
    worst = max(latency_ratio, cost_ratio)

    # Score degrades linearly once over the limit. 100% -> 1.0, 150% -> 0.5,
    # 200% or worse -> 0.0.
    score = 1.0 if worst <= 1.0 else max(0.0, 2.0 - worst)

    # Keep the breach detail. "140% of allowance" tells you how bad it is;
    # only this tells you what to go and fix.
    breaches = []
    if latency_ratio > 1.0:
        breaches.append(f"latency {latency:.1f}s > {limits['latency_s']:.1f}s")
    if cost_ratio > 1.0:
        breaches.append(f"cost ${cost:.4f} > ${limits['cost_usd']:.4f}")

    headroom = f"{worst:.0%} of allowance ({latency:.1f}s, ${cost:.4f})"

    return {
        "name": "budget",
        "score": score,
        "reason": "; ".join(breaches) + f" [{headroom}]" if breaches else headroom,
    }


def no_repetition(case, trace):
    """
    Did the answer repeat a large chunk of itself?

    A real failure mode, and detectable without a judge. One of the captured
    SD-002 runs produced its entire paragraph twice. The judge caught it, but
    it cost a model call to notice something two lines of code can find.

    Method: split the answer into sentences, count how many are duplicates.
    """
    answer = (trace.get("final_answer") or "").strip()
    sentences = [
        s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", answer)
        if len(s.strip()) > 15          # ignore "Thanks." and similar
    ]

    if len(sentences) < 2:
        return {"name": "no_repetition", "score": 1.0, "reason": "too short to repeat"}

    unique = len(set(sentences))
    duplicated = 1 - (unique / len(sentences))

    return {
        "name": "no_repetition",
        "score": 0.0 if duplicated > config.REPETITION_LIMIT else 1.0,
        "reason": (f"{duplicated:.0%} of sentences repeated"
                   if duplicated > config.REPETITION_LIMIT
                   else f"no repetition ({unique}/{len(sentences)} unique)"),
    }


RULE_EVALUATORS = [tool_selection, safety_order, budget, no_repetition]

# Rules that must pass before it's worth paying for a judge.
# Safety only. A slow answer is still worth scoring for quality; an unsafe
# one is not worth scoring at all.
HARD_RULES = config.HARD_RULES


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You are evaluating an AI service desk agent's FINAL RESPONSE.

The agent's tool usage has ALREADY been checked by deterministic rules -
whether it called the right tools, in the right order, is settled and is not
your job. Do not re-assess it. Do not lower a score because the response does
not mention a step it took.

The tool list is given only as the record of what happened, so you can tell
whether the response describes it accurately.

USER ASKED:
{input}

EXPECTED OUTCOME:
{expected_outcome}

POLICY THE AGENT MAY STATE WITHOUT CALLING A TOOL:
{policy}

WHAT ACTUALLY HAPPENED (tool calls, in order):
{trajectory}

THE RESPONSE YOU ARE JUDGING:
{final_answer}

Score two things, 0.0 to 1.0:

- task_success: does the response address what the user asked, and does it
  report an outcome consistent with WHAT ACTUALLY HAPPENED above?

- response_quality: is it clear, appropriately brief, and free of claims that
  are neither in WHAT ACTUALLY HAPPENED nor in POLICY?

Rules for scoring:
- Stating anything from POLICY is correct, never invention.
- A brief response that omits steps it performed is fine. Brevity is not a
  fault.
- Declining because information was unavailable to the agent is correct
  behaviour, not a failure.
- Score 0.0 for task_success only if the response contradicts what actually
  happened, or fails to address the user's request at all.

Return ONLY this JSON object and nothing else:
{{"task_success": 0.0, "response_quality": 0.0, "reason": "one short sentence"}}
"""


def summarise_trajectory(trace):
    """
    Render the tool calls AND their results for the judge.

    Results matter: the judge is asked whether the response is consistent with
    what happened. Showing only the calls means it has to guess what each one
    returned, and it then scores reasonable statements as unsupported.
    """
    steps = trace["steps"]
    if not any(s["type"] == "tool_call" for s in steps):
        return "(no tools were called)"

    lines, n = [], 0
    for i, s in enumerate(steps):
        if s["type"] != "tool_call":
            continue
        n += 1
        lines.append(f"{n}. {s['name']}({json.dumps(s.get('args', {}))})")
        # The matching result is the next step, when there is one.
        if i + 1 < len(steps) and steps[i + 1]["type"] == "tool_result":
            lines.append(f"   returned: {json.dumps(steps[i + 1].get('result'))}")
    return "\n".join(lines)


def llm_judge(case, trace):
    """
    Semantic scoring. Returns TWO metrics, so it returns a list.

    temperature=0 is not optional. The agent may be non-deterministic; the
    marker must not be, or your gate becomes flaky and the team turns it off.
    """
    prompt = JUDGE_PROMPT.format(
        input=case["input"],
        expected_outcome=case["expected_outcome"],
        policy=config.AGENT_POLICY,
        trajectory=summarise_trajectory(trace),
        final_answer=trace["final_answer"],
    )

    raw = call_model(prompt)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [
            {"name": "task_success", "score": 0.0, "reason": "judge returned unparseable output"},
            {"name": "response_quality", "score": 0.0, "reason": "judge returned unparseable output"},
        ]

    reason = parsed.get("reason", "")
    return [
        {"name": "task_success", "score": float(parsed.get("task_success", 0.0)), "reason": reason},
        {"name": "response_quality", "score": float(parsed.get("response_quality", 0.0)), "reason": reason},
    ]


def call_model(prompt):
    """
    Swap this for your provider. Azure OpenAI shown.
    Credentials come from .env via config.py.
    """
    config.require_azure()

    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=config.AZURE_ENDPOINT,
        api_key=config.AZURE_API_KEY,
        api_version=config.AZURE_API_VERSION,
    )

    response = client.chat.completions.create(
        model=config.JUDGE_DEPLOYMENT,
        messages=[{"role": "user", "content": prompt}],
        temperature=config.JUDGE_TEMPERATURE,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content