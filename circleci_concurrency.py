"""
CircleCI Concurrency Usage Checker — CLI entry point.

Determines current concurrency usage by fetching pipelines for an organization,
then counting running and queued jobs across all in-progress workflows.
"""

import os
import sys
from typing import Optional

import requests

from utils import get_concurrency_usage, get_runner_concurrency_usage, get_token

CLI_FLAGS = frozenset(
    {
        "--verbose",
        "-v",
        "--runners",
        "--runners-only",
        "-r",
    }
)


def _parse_org_slug() -> Optional[str]:
    """Resolve org slug from env or first non-flag argv."""
    env_slug = os.environ.get("CIRCLE_ORG_SLUG", "").strip()
    if env_slug:
        return env_slug
    for arg in sys.argv[1:]:
        if arg not in CLI_FLAGS:
            return arg
    return None


def _print_runner_section(result: dict) -> None:
    print("Self-hosted Runner concurrency:")
    print(f"  Running on runners:  {result['runner_running_count']}")
    print(f"  Queued for runners:  {result['runner_queued_count']}")
    print(f"  Total (runner jobs): {result['runner_total_concurrency_usage']}")
    if result.get("by_resource_class"):
        print("  By resource_class:")
        for rc, counts in sorted(result["by_resource_class"].items()):
            r, q = counts["running"], counts["queued"]
            if r or q:
                print(f"    {rc}: running={r}, queued={q}")
    print()


def main() -> None:
    org_slug = _parse_org_slug()
    if not org_slug:
        print("Usage: python circleci_concurrency.py <org-slug> [options]", file=sys.stderr)
        print("Example: python circleci_concurrency.py gh/MyOrg", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print("  -v, --verbose       List each running/queued job", file=sys.stderr)
        print("  --runners, -r       Include concurrency for self-hosted Runner jobs", file=sys.stderr)
        print("  --runners-only      Only show Runner concurrency (skip org-wide totals)", file=sys.stderr)
        print("Or set CIRCLE_ORG_SLUG environment variable.", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    show_runners = "--runners" in sys.argv or "-r" in sys.argv
    runners_only = "--runners-only" in sys.argv

    if runners_only:
        show_runners = True

    try:
        if not runners_only:
            result = get_concurrency_usage(token, org_slug)
        runner_result = None
        if show_runners:
            runner_result = get_runner_concurrency_usage(token, org_slug)
    except requests.HTTPError as e:
        print(f"CircleCI API error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"Response: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)

    if not runners_only:
        print(f"Organization: {result['org_slug']}")
        print(f"Pipelines scanned: {result['pipelines_scanned']} (with active workflows: {result['pipelines_with_active_workflows']})")
        print()
        print("Current concurrency usage (all executors):")
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

    if show_runners and runner_result is not None:
        if not runners_only:
            print()
        print(f"Organization: {runner_result['org_slug']}")
        _print_runner_section(runner_result)
        if verbose and (
            runner_result["runner_running_jobs"] or runner_result["runner_queued_jobs"]
        ):
            print("Runner jobs (running):")
            for j in runner_result["runner_running_jobs"]:
                rc = j.get("resource_class") or "?"
                print(
                    f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                    f"(#{j.get('job_number', '?')}) [{rc}]"
                )
            if runner_result["runner_queued_jobs"]:
                print("Runner jobs (queued):")
                for j in runner_result["runner_queued_jobs"]:
                    rc = j.get("resource_class") or "?"
                    print(
                        f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                        f"[#{j.get('job_number', '?')}] ({j['status']}) [{rc}]"
                    )


if __name__ == "__main__":
    main()
