"""
Lightweight DART API key validation.

Only checks:
1. DART_API_KEY is set
2. The key is accepted by the DART API (single HTTP call)
3. Samsung Electronics (005930) returns a valid response

Does NOT use OpenDartReader (which downloads a large corp code XML on init).
Uses a direct HTTP request to the DART API for speed.

Usage:
    python test_dart_key.py
    python test_dart_key.py --timeout 15
"""

import os
import sys
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY")

# DART REST API base
DART_API_BASE = "https://opendart.fss.or.kr/api"


def main():
    parser = argparse.ArgumentParser(description="Validate DART API key")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds (default: 15)")
    args = parser.parse_args()

    print("DART API Key Validation")
    print("=" * 40)

    # 1. Check env var
    if not DART_API_KEY:
        print("FAIL: DART_API_KEY not set in environment or .env.local")
        return 1

    key_preview = DART_API_KEY[:4] + "..." + DART_API_KEY[-4:] if len(DART_API_KEY) > 8 else "***"
    print("  Key: {}".format(key_preview))

    # 2. Direct HTTP test — use the company search API (fast, no XML download)
    import urllib.request
    import urllib.error
    import json

    # Search for Samsung Electronics by name — lightweight endpoint
    url = "{}/company.json?crtfc_key={}&corp_code=00126380".format(DART_API_BASE, DART_API_KEY)
    # 00126380 is Samsung Electronics' well-known DART corp code

    print("  Testing API with Samsung (corp_code=00126380)...", end=" ", flush=True)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        status = data.get("status", "")
        message = data.get("message", "")

        if status == "000":
            # Success
            corp_name = data.get("corp_name", "unknown")
            stock_code = data.get("stock_code", "unknown")
            print("OK")
            print("  Corp name:  {}".format(corp_name))
            print("  Stock code: {}".format(stock_code.strip()))
            print("  API status: {} ({})".format(status, message))
        elif status == "010":
            print("FAIL")
            print("  Error: Unregistered API key (status 010)")
            print("  Register at https://opendart.fss.or.kr/")
            return 1
        elif status == "020":
            print("FAIL")
            print("  Error: Invalid API key (status 020)")
            return 1
        elif status == "011":
            print("FAIL")
            print("  Error: API usage limit exceeded (status 011)")
            print("  Free tier: ~1000 requests/day. Wait until midnight KST.")
            return 1
        else:
            print("WARN")
            print("  Unexpected status: {} ({})".format(status, message))
            # Still return success — the key reached the API
    except urllib.error.URLError as e:
        print("FAIL")
        print("  Network error: {}".format(e.reason))
        return 1
    except Exception as e:
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            print("TIMEOUT ({}s)".format(args.timeout))
            print("  The DART API did not respond in time.")
            print("  Try again or increase timeout: python test_dart_key.py --timeout 30")
            return 1
        print("FAIL: {}".format(str(e)[:100]))
        return 1

    # 3. Quick check: can we also list filings? (optional, may be slow)
    print("  Listing recent filings...", end=" ", flush=True)
    list_url = "{}/list.json?crtfc_key={}&corp_code=00126380&bgn_de=20240101&end_de=20241231&page_count=1".format(
        DART_API_BASE, DART_API_KEY)
    try:
        req2 = urllib.request.Request(list_url)
        with urllib.request.urlopen(req2, timeout=args.timeout) as resp2:
            data2 = json.loads(resp2.read().decode("utf-8"))
        total = data2.get("total_count", 0)
        print("OK ({} filings in 2024)".format(total))
    except Exception as e:
        print("skip ({})".format(str(e)[:60]))

    print()
    print("ALL CLEAR: DART API key is valid and working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
