"""Re-check the API facts the project depends on. About 12 requests.

Writes reports/api_probe.json and exits 1 if any fact changed, so the daily
job can alert when CMS changes page caps, paging behaviour or releases.
"""

from __future__ import annotations

import json
import sys

from ingest.common import ROOT, http_client, load_config
from ingest.sync_open_payments import discover, parse_release

NPPES_API = "https://npiregistry.cms.hhs.gov/api/"
RATE_HEADERS = ("ratelimit", "x-ratelimit", "retry-after")


def rate_headers(r) -> dict:
    return {k: v for k, v in r.headers.items() if any(h in k.lower() for h in RATE_HEADERS)}


def main() -> int:
    cfg = load_config()
    op = cfg["open_payments"]
    facts: dict = {}
    failures: list[str] = []

    with http_client(cfg) as c:
        ds = discover(cfg, c, ["general"], [max(op["program_years"])])[0]
        q = f"{op['datastore_url']}/{ds.identifier}/0"

        ok = c.get(
            q,
            params={
                "limit": op["expected"]["max_page_size"],
                "schema": "false",
                "count": "false",
                "properties[]": "record_id",
            },
        )
        over = c.get(q, params={"limit": op["expected"]["max_page_size"] + 1, "schema": "false"})
        facts["open_payments"] = {
            "probed_dataset": ds.title,
            "release": ds.release,
            "modified": ds.modified,
            "page_size_ok": ok.status_code == 200 and len(ok.json()["results"]) == 500,
            "page_size_over_rejected": over.status_code == 400,
            "rate_limit_headers": rate_headers(ok),
            "cache_control": ok.headers.get("cache-control"),
        }
        if not facts["open_payments"]["page_size_ok"] or not over.status_code == 400:
            failures.append("Open Payments max page size is no longer 500")

        items = c.get(op["metastore_url"]).json()
        releases = sorted(
            {
                parse_release(d["downloadURL"])[1]
                for i in items
                for d in i.get("distribution", [])
                if "/PGYR" in d.get("downloadURL", "")
            }
        )
        facts["open_payments"]["releases_in_catalog"] = releases

        base = {"version": "2.1", "last_name": "smith"}
        r201 = c.get(NPPES_API, params={**base, "limit": 201})
        cap = r201.json().get("result_count")
        first = {}
        for skip in (999, 1000, 1001):
            r = c.get(NPPES_API, params={**base, "limit": 2, "skip": skip})
            first[skip] = [x["number"] for x in r.json().get("results", [])]
        clamped = first[1000] == first[1001]
        facts["nppes_api"] = {
            "limit_201_returns": cap,
            "skip_1000_eq_skip_1001": clamped,
            "first_npis_by_skip": first,
            "rate_limit_headers": rate_headers(r201),
        }
        if cap != cfg["nppes"]["expected"]["api_max_limit"]:
            failures.append(f"NPPES max limit changed: limit=201 returned {cap}")
        if not clamped:
            failures.append("NPPES skip no longer clamps at 1000")

    facts["failures"] = failures
    out = ROOT / "reports" / "api_probe.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(facts, indent=2, default=str) + "\n")
    print(json.dumps(facts, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
