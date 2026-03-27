"""
CircleCI Concurrency Usage Checker — CLI entry point.

Determines current concurrency usage by fetching pipelines for an organization,
then counting running and queued jobs across all in-progress workflows.
"""

import os
import sys
from typing import Optional

import requests

from utils import (
    get_concurrency_usage,
    get_executor_breakdown_concurrency_usage,
    get_token,
)

# How many recent org pipelines to scan (higher = more API calls, better coverage, but can lead to rate limiting).
MAX_PIPELINES_TO_SCAN = 50

CLI_FLAGS = frozenset(
    {
        "--verbose",
        "-v",
        "--runners",
        "--runners-only",
        "-r",
        "--by-project",
        "--cloud",
        "--cloud-only",
        "-c",
    }
)

# Flags that take a value (skip this token and the next when resolving org slug)
CLI_VALUE_FLAGS = ("--project",)


def _iter_positionals() -> list[str]:
    """Argv tokens that are not known flags or flag values."""
    out: list[str] = []
    i = 1
    n = len(sys.argv)
    while i < n:
        a = sys.argv[i]
        if a in CLI_VALUE_FLAGS:
            i += 2
            continue
        if a in CLI_FLAGS:
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def _parse_org_slug() -> Optional[str]:
    """Resolve org slug from env or first positional argv."""
    env_slug = os.environ.get("CIRCLE_ORG_SLUG", "").strip()
    if env_slug:
        return env_slug
    positionals = _iter_positionals()
    return positionals[0] if positionals else None


def _parse_project_filter() -> Optional[str]:
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip() or None
    env_p = os.environ.get("CIRCLE_PROJECT_SLUG", "").strip()
    return env_p or None


def _print_by_project_section(result: dict, title: str = "By project") -> None:
    by_p = result.get("by_project") or {}
    rows = [(slug, c["running"], c["queued"], c["total"]) for slug, c in by_p.items() if c["total"] > 0]
    rows.sort(key=lambda r: (-r[3], r[0]))
    print(title + ":")
    if not rows:
        print("  (no running or queued jobs in scanned pipelines)")
    else:
        for slug, r, q, t in rows:
            print(f"  {slug}  running={r}  queued={q}  total={t}")
    print()


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


def _print_cloud_section(result: dict) -> None:
    print("Hosted concurrency (not self-hosted Runner):")
    print(f"  Running (hosted):  {result['cloud_running_count']}")
    print(f"  Queued (hosted):  {result['cloud_queued_count']}")
    print(f"  Total (hosted jobs): {result['cloud_total_concurrency_usage']}")
    if result.get("cloud_by_resource_class"):
        print("  By resource_class:")
        for rc, counts in sorted(result["cloud_by_resource_class"].items()):
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
        print("  --cloud, -c         Include hosted (non-runner) concurrency from job details", file=sys.stderr)
        print("  --cloud-only        Only show hosted concurrency (skip org-wide totals)", file=sys.stderr)
        print("  --project SLUG      Only include pipelines for this project (e.g. gh/Org/repo)", file=sys.stderr)
        print("  --by-project        List concurrency per project (from scanned pipelines)", file=sys.stderr)
        print("Or set CIRCLE_ORG_SLUG (and optionally CIRCLE_PROJECT_SLUG) environment variables.", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    show_runners = "--runners" in sys.argv or "-r" in sys.argv
    show_cloud = "--cloud" in sys.argv or "-c" in sys.argv
    runners_only = "--runners-only" in sys.argv
    cloud_only = "--cloud-only" in sys.argv
    by_project = "--by-project" in sys.argv
    project_filter = _parse_project_filter()

    if runners_only:
        show_runners = True
    if cloud_only:
        show_cloud = True

    skip_main = runners_only or cloud_only
    need_executor_breakdown = show_runners or show_cloud

    try:
        result = None
        if not skip_main:
            result = get_concurrency_usage(
                token,
                org_slug,
                max_pipelines=MAX_PIPELINES_TO_SCAN,
                project_slug_filter=project_filter,
            )
        breakdown = None
        if need_executor_breakdown:
            breakdown = get_executor_breakdown_concurrency_usage(
                token,
                org_slug,
                max_pipelines=MAX_PIPELINES_TO_SCAN,
                project_slug_filter=project_filter,
            )
    except requests.HTTPError as e:
        print(f"CircleCI API error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"Response: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)

    if not skip_main and result is not None:
        print(f"Organization: {result['org_slug']}")
        if project_filter:
            print(f"Project filter: {project_filter}")
        print(f"Pipelines scanned: {result['pipelines_scanned']} (with active workflows: {result['pipelines_with_active_workflows']})")
        print()
        print("Current concurrency usage (all executors):")
        print(f"  Running jobs:  {result['running_count']}")
        print(f"  Queued jobs:  {result['queued_count']}")
        print(f"  Total in use: {result['total_concurrency_usage']}")
        print()

        if by_project:
            _print_by_project_section(result)

        if verbose and (result["running_jobs"] or result["queued_jobs"]):
            print("Running jobs:")
            for j in result["running_jobs"]:
                print(f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} (#{j.get('job_number', '?')})")
            if result["queued_jobs"]:
                print("Queued jobs:")
                for j in result["queued_jobs"]:
                    print(f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} [#{j.get('job_number', '?')}] ({j['status']})")

    if need_executor_breakdown and breakdown is not None:
        if not skip_main:
            print()
        print(f"Organization: {breakdown['org_slug']}")
        if project_filter:
            print(f"Project filter: {project_filter}")

        if show_runners:
            _print_runner_section(breakdown)
            if by_project:
                _print_by_project_section(breakdown, title="Runner jobs by project")
            if verbose and (
                breakdown["runner_running_jobs"] or breakdown["runner_queued_jobs"]
            ):
                print("Runner jobs (running):")
                for j in breakdown["runner_running_jobs"]:
                    rc = j.get("resource_class") or "?"
                    print(
                        f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                        f"(#{j.get('job_number', '?')}) [{rc}]"
                    )
                if breakdown["runner_queued_jobs"]:
                    print("Runner jobs (queued):")
                    for j in breakdown["runner_queued_jobs"]:
                        rc = j.get("resource_class") or "?"
                        print(
                            f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                            f"[#{j.get('job_number', '?')}] ({j['status']}) [{rc}]"
                        )

        if show_cloud:
            if show_runners:
                print()
            _print_cloud_section(breakdown)
            if by_project:
                _print_by_project_section(
                    {"by_project": breakdown["cloud_by_project"]},
                    title="Hosted jobs by project",
                )
            if verbose and (
                breakdown["cloud_running_jobs"] or breakdown["cloud_queued_jobs"]
            ):
                print("Hosted jobs (running):")
                for j in breakdown["cloud_running_jobs"]:
                    rc = j.get("resource_class") or "(unset)"
                    print(
                        f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                        f"(#{j.get('job_number', '?')}) [{rc}]"
                    )
                if breakdown["cloud_queued_jobs"]:
                    print("Hosted jobs (queued):")
                    for j in breakdown["cloud_queued_jobs"]:
                        rc = j.get("resource_class") or "(unset)"
                        print(
                            f"  - {j['project_slug']} | {j['workflow_name']} | {j['name']} "
                            f"[#{j.get('job_number', '?')}] ({j['status']}) [{rc}]"
                        )


if __name__ == "__main__":
    main()
