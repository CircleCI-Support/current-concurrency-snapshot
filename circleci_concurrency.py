"""
CircleCI Concurrency Usage Checker — CLI entry point.

Determines current concurrency usage by fetching pipelines for an organization,
then counting running and queued jobs across all in-progress workflows.
"""

import os
import sys
from typing import Optional

import requests

from utils import get_concurrency_usage, get_token


def _parse_org_slug() -> Optional[str]:
    """Resolve org slug from env or first non-flag argv."""
    env_slug = os.environ.get("CIRCLE_ORG_SLUG", "").strip()
    if env_slug:
        return env_slug
    for arg in sys.argv[1:]:
        if arg not in ("--verbose", "-v"):
            return arg
    return None


def main() -> None:
    org_slug = _parse_org_slug()
    if not org_slug:
        print("Usage: python circleci_concurrency.py <org-slug>", file=sys.stderr)
        print("Example: python circleci_concurrency.py gh/MyOrg", file=sys.stderr)
        print("Or set CIRCLE_ORG_SLUG environment variable.", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    try:
        result = get_concurrency_usage(token, org_slug)
    except requests.HTTPError as e:
        print(f"CircleCI API error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"Response: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)

    print(f"Organization: {result['org_slug']}")
    print(f"Pipelines scanned: {result['pipelines_scanned']} (with active workflows: {result['pipelines_with_active_workflows']})")
    print()
    print("Current concurrency usage:")
    print(f"  Running jobs:  {result['running_count']}")
    print(f"  Queued jobs:  {result['queued_count']}")
    print(f"  Total in use: {result['total_concurrency_usage']}")
    print()

    if verbose and (result["running_jobs"] or result["queued_jobs"]):
        print("Running jobs:")
        for j in result["running_jobs"]:
            print(f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} (#{j.get('job_number', '?')})")
        if result["queued_jobs"]:
            print("Queued jobs:")
            for j in result["queued_jobs"]:
                print(f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} [#{j.get('job_number', '?')}] ({j['status']})")


if __name__ == "__main__":
    main()
