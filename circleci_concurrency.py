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
# Workflow statuses we care about (in progress)
ACTIVE_WORKFLOW_STATUSES = {"running", "on_hold", "created"}


def get_token() -> str:
    token = os.environ.get("CIRCLE_TOKEN") or os.environ.get("CIRCLE_CI_TOKEN")
    if not token:
        print("Error: Set CIRCLE_TOKEN (or CIRCLE_CI_TOKEN) with your CircleCI API token.", file=sys.stderr)
        print("Create one at: https://app.circleci.com/settings/user/tokens", file=sys.stderr)
        sys.exit(1)
    return token


def request(
    token: str,
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    headers = {"Circle-Token": token, "Accept": "application/json"}
    resp = requests.request(method, url, headers=headers, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_pipelines(token: str, org_slug: str, page_token: Optional[str] = None) -> dict[str, Any]:
    params: dict[str, Any] = {"org-slug": org_slug}
    if page_token:
        params["page-token"] = page_token
    return request(token, "GET", "/pipeline", params=params)


def list_workflows_for_pipeline(token: str, pipeline_id: str, page_token: Optional[str] = None) -> dict[str, Any]:
    params = {}
    if page_token:
        params["page-token"] = page_token
    return request(token, "GET", f"/pipeline/{pipeline_id}/workflow", params=params or None)


def list_jobs_for_workflow(token: str, workflow_id: str, page_token: Optional[str] = None) -> dict[str, Any]:
    params = {}
    if page_token:
        params["page-token"] = page_token
    return request(token, "GET", f"/workflow/{workflow_id}/job", params=params or None)

# You can increase the maximum number of recent pipelines to check, but this will increase the number of API calls made. Which may lead to rate limits being hit.
def collect_all_pipelines(token: str, org_slug: str, max_pipelines: int = 100) -> list[dict[str, Any]]:
    pipelines: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while len(pipelines) < max_pipelines:
        data = list_pipelines(token, org_slug, page_token)
        items = data.get("items") or []
        pipelines.extend(items)
        page_token = data.get("next_page_token")
        if not page_token or not items:
            break
    return pipelines[:max_pipelines]


def collect_workflows_for_pipeline(token: str, pipeline_id: str) -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        data = list_workflows_for_pipeline(token, pipeline_id, page_token)
        items = data.get("items") or []
        workflows.extend(items)
        page_token = data.get("next_page_token")
        if not page_token or not items:
            break
    return workflows


def collect_jobs_for_workflow(token: str, workflow_id: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        data = list_jobs_for_workflow(token, workflow_id, page_token)
        items = data.get("items") or []
        jobs.extend(items)
        page_token = data.get("next_page_token")
        if not page_token or not items:
            break
    return jobs

# You can increase the maximum number of recent pipelines to check, but this will increase the number of API calls made. Which may lead to rate limits being hit.
def get_concurrency_usage(token: str, org_slug: str, max_pipelines: int = 50) -> dict[str, Any]:
    """
    Compute current concurrency usage for the organization by scanning recent
    pipelines and counting running/queued jobs.
    """
    pipelines = collect_all_pipelines(token, org_slug, max_pipelines=max_pipelines)
    running_jobs: list[dict[str, Any]] = []
    queued_jobs: list[dict[str, Any]] = []
    workflows_checked = 0
    pipelines_with_activity = 0

    for pipeline in pipelines:
        pipeline_id = pipeline.get("id")
        project_slug = pipeline.get("project_slug", "")
        if not pipeline_id:
            continue
        try:
            workflows = collect_workflows_for_pipeline(token, pipeline_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                continue
            raise
        active = [w for w in workflows if (w.get("status") or "").lower() in ACTIVE_WORKFLOW_STATUSES]
        if not active:
            continue
        pipelines_with_activity += 1
        for wf in active:
            workflows_checked += 1
            wf_id = wf.get("id")
            wf_name = wf.get("name", "?")
            if not wf_id:
                continue
            try:
                jobs = collect_jobs_for_workflow(token, wf_id)
            except requests.HTTPError:
                continue
            for job in jobs:
                status = (job.get("status") or "").lower()
                if status in RUNNING_STATUSES:
                    running_jobs.append({
                        "job_id": job.get("id"),
                        "job_number": job.get("job_number"),
                        "name": job.get("name"),
                        "project_slug": project_slug,
                        "workflow_name": wf_name,
                        "status": status,
                    })
                elif status in QUEUED_STATUSES:
                    queued_jobs.append({
                        "job_id": job.get("id"),
                        "job_number": job.get("job_number"),
                        "name": job.get("name"),
                        "project_slug": project_slug,
                        "workflow_name": wf_name,
                        "status": status,
                    })

    running_count = len(running_jobs)
    queued_count = len(queued_jobs)
    total_usage = running_count + queued_count

    return {
        "org_slug": org_slug,
        "pipelines_scanned": len(pipelines),
        "pipelines_with_active_workflows": pipelines_with_activity,
        "workflows_checked": workflows_checked,
        "running_jobs": running_jobs,
        "queued_jobs": queued_jobs,
        "running_count": running_count,
        "queued_count": queued_count,
        "total_concurrency_usage": total_usage,
    }


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
