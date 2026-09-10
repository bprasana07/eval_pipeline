"""Print the full trace for one or more cases.  python -B show_trace.py <file> SD-003"""
import json, sys, textwrap
from pathlib import Path

path = Path("traces") / sys.argv[1]
wanted = sys.argv[2:] or None

for t in json.loads(path.read_text(encoding="utf-8"))["traces"]:
    if wanted and t["case_id"] not in wanted:
        continue
    print("\n" + "=" * 74)
    print(f"  {t['trace_id']}")
    print("=" * 74)
    for s in t["steps"]:
        if s["type"] == "tool_call":
            print(f"  -> {s['name']}({json.dumps(s.get('args', {}))})")
        else:
            print(f"     {json.dumps(s.get('result'))}")
    print("\n  ANSWER")
    for line in textwrap.wrap(t["final_answer"], 68):
        print(f"    {line}")