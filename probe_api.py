"""
Find a working API surface for your deployment.

Your deployment listing succeeded, so host, key and deployment name are all
correct. That leaves two variables:

  1. api-version        older versions 404 on newer models
  2. endpoint shape     GPT-5 family deployments often expose only the
                        Responses API (/openai/responses), not the older
                        chat/completions path

This script tries every combination and reports which ones answer. It sends
a two-token request, so the whole run costs a fraction of a penny.

Run:  python -B probe_api.py
"""

import json
import urllib.error
import urllib.request

import config

VERSIONS = [
    config.AZURE_API_VERSION,   # whatever you have now, tested first
    "2025-04-01-preview",
    "2025-01-01-preview",
    "2024-12-01-preview",
    "2024-10-21",
    "preview",
]


def post(url, payload, key):
    """Returns (status, short body). Never raises."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)[:200]


def verdict(status):
    if status == 200:
        return "WORKS"
    if status == 400:
        # The path exists - the request body was wrong. Still a win.
        return "path ok, body rejected"
    if status == 404:
        return "not found"
    if status == 401:
        return "auth"
    return f"http {status}"


def main():
    endpoint = config.AZURE_ENDPOINT.rstrip("/")
    key = config.AZURE_API_KEY
    dep = config.AZURE_DEPLOYMENT

    seen = []
    for version in dict.fromkeys(VERSIONS):   # de-duplicate, keep order
        seen.append(("chat", version))
        seen.append(("responses", version))

    print(f"\nProbing {dep} on {endpoint}\n")
    print(f"  {'surface':<12} {'api-version':<22} {'result'}")
    print("  " + "-" * 60)

    working = []

    for surface, version in seen:
        if surface == "chat":
            url = f"{endpoint}/openai/deployments/{dep}/chat/completions?api-version={version}"
            payload = {
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 16,
            }
        else:
            url = f"{endpoint}/openai/responses?api-version={version}"
            payload = {"model": dep, "input": "hi", "max_output_tokens": 16}

        status, body = post(url, payload, key)
        label = verdict(status)
        print(f"  {surface:<12} {version:<22} {label}")

        if status == 200:
            working.append((surface, version))
        elif status == 400:
            print(f"               -> {body[:120]}")

    # --- what to do about it -----------------------------------------------
    print()
    if not working:
        print("  Nothing answered with 200.")
        print("  If some rows say 'path ok, body rejected', that surface exists and")
        print("  only the payload needs adjusting - paste the output and I'll fix it.")
        return 1

    chat_hits = [v for s, v in working if s == "chat"]
    resp_hits = [v for s, v in working if s == "responses"]

    if chat_hits:
        print(f"  Chat completions works. Set this in .env:\n")
        print(f"    AZURE_OPENAI_API_VERSION={chat_hits[0]}\n")
        print("  No code change needed - run_eval.py will work as-is.")
    else:
        print(f"  Chat completions is NOT available for this deployment.")
        print(f"  The Responses API works with api-version={resp_hits[0]}.\n")
        print("  Two options:")
        print("    a) deploy a gpt-4o-mini and point .env at it (fastest)")
        print("    b) switch agent_real.py to the Responses API (tell me and I'll do it)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())