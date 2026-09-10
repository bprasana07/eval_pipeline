"""
Azure connection check.

A 404 from Azure OpenAI means the host was reached but the path did not exist.
The path is built from your endpoint, your deployment name, and the api-version:

  {endpoint}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version={VER}

So a 404 is almost always one of three things, and this script tells you which.

Run:  python -B check_azure.py
"""

import json
import sys
import urllib.error
import urllib.request

import config


def mask(value):
    if not value:
        return "(empty)"
    return value[:4] + "..." + value[-4:] if len(value) > 12 else "(set)"


def get(url, key):
    req = urllib.request.Request(url, headers={"api-key": key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    endpoint = config.AZURE_ENDPOINT.rstrip("/")
    key = config.AZURE_API_KEY
    deployment = config.AZURE_DEPLOYMENT
    version = config.AZURE_API_VERSION

    print("\n--- what config.py loaded from .env ---")
    print(f"  endpoint    {endpoint or '(empty)'}")
    print(f"  api key     {mask(key)}")
    print(f"  deployment  {deployment or '(empty)'}")
    print(f"  api version {version}")

    print("\n--- URL the SDK builds ---")
    print(f"  {endpoint}/openai/deployments/{deployment}/chat/completions"
          f"?api-version={version}")

    # Check 1: endpoint shape
    print("\n--- endpoint check ---")
    problems = []
    if not endpoint.startswith("https://"):
        problems.append("should start with https://")
    if "/openai" in endpoint:
        problems.append("should NOT include /openai - the SDK adds it")
    if endpoint.endswith("/"):
        problems.append("trailing slash (harmless, but tidy it)")
    if "services.ai.azure.com" in endpoint:
        problems.append(
            "this is a Foundry project endpoint. For the OpenAI SDK you usually "
            "want the https://<resource>.openai.azure.com form instead"
        )
    if problems:
        for p in problems:
            print(f"  PROBLEM  {p}")
    else:
        print("  looks fine")

    # Check 2: list the deployments that actually exist
    print("\n--- deployments that actually exist on this resource ---")
    try:
        data = get(f"{endpoint}/openai/deployments?api-version=2023-03-15-preview", key)
        names = [d.get("id") or d.get("name") for d in data.get("data", [])]

        if not names:
            print("  none found - nothing is deployed on this resource")
        for name in names:
            marker = "  <-- your DEPLOYMENT setting" if name == deployment else ""
            print(f"  {name}{marker}")

        print()
        if deployment in names:
            print(f"  OK: '{deployment}' exists, and the key is valid.")
            print(f"      If chat/completions still 404s, it is the api-version or the")
            print(f"      endpoint shape. Run:  python -B probe_api.py")
        else:
            print(f"  CAUSE FOUND: '{deployment}' is not deployed here.")
            print(f"  Set AZURE_OPENAI_DEPLOYMENT in .env to one of the names above.")
            print(f"  Note: it must be the DEPLOYMENT name you chose, not the model name.")

    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code} listing deployments")
        if exc.code == 401:
            print("  CAUSE: the api key is wrong, or belongs to a different resource.")
        elif exc.code == 404:
            print("  CAUSE: the endpoint host is wrong. Check the resource name.")
        else:
            print(f"  {exc.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as exc:
        print(f"  cannot reach the host: {exc.reason}")
        print("  Check the resource name in the endpoint, and any proxy or TLS "
              "interception on this machine.")

    print()


if __name__ == "__main__":
    sys.exit(main())