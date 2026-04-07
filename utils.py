"""
CircleCI API helpers and concurrency aggregation logic.
"""

import os
import sys
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import quote

import requests

BASE_URL = "https://circleci.com/api/v2"

# Job statuses that consume or reserve concurrency.
# "on_hold" (e.g. approval) does not count toward CircleCI concurrency limits.
RUNNING_STATUSES = {"running"}
QUEUED_STATUSES = {"pending", "queued", "blocked"}

# Workflows we traverse for active jobs. Exclude "on_hold" (manual approval / paused).
ACTIVE_WORKFLOW_STATUSES = {"running", "created"}

# Self-hosted runners use resource_class "namespace/runner-name" (contains "/").
# Cloud resource classes use names like medium, arm.large, macos.m1.large.gen2 (no "/").


def get_token() -> str:
    token = os.environ.get("CIRCLE_TOKEN") or os.environ.get("CIRCLE_CI_TOKEN")
    if not token:
        print("Error: Set CIRCLE_TOKEN (or CIRCLE_CI_TOKEN) with your CircleCI API token.", file=sys.stderr)
        print("Create one at: https://app.circleci.com/settings/user/tokens", file=sys.stderr)
        sys.exit(1)
    return token


def api_request(
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
    return api_request(token, "GET", "/pipeline", params=params)


def list_workflows_for_pipeline(token: str, pipeline_id: str, page_token: Optional[str] = None) -> dict[str, Any]:
    params = {}
    if page_token:
        params["page-token"] = page_token
    return api_request(token, "GET", f"/pipeline/{pipeline_id}/workflow", params=params or None)


def list_jobs_for_workflow(token: str, workflow_id: str, page_token: Optional[str] = None) -> dict[str, Any]:
    params = {}
    if page_token:
        params["page-token"] = page_token
    return api_request(token, "GET", f"/workflow/{workflow_id}/job", params=params or None)


def is_self_hosted_runner_resource_class(resource_class: str) -> bool:
    """True if resource_class targets a self-hosted Runner (org/namespace format)."""
    rc = (resource_class or "").strip()
    return bool(rc) and "/" in rc


def get_job_details(token: str, project_slug: str, job_number: Any) -> Optional[dict[str, Any]]:
    """Fetch full job details (includes executor.resource_class). Returns None on failure."""
    if job_number is None:
        return None
    enc = quote(project_slug, safe="")
    try:
        return api_request(token, "GET", f"/project/{enc}/job/{job_number}")
    except requests.HTTPError:
        return None


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


def _project_matches_filter(project_slug: str, project_filter: Optional[str]) -> bool:
    if not project_filter:
        return True
    return project_slug.strip() == project_filter.strip()


def _build_by_project_counts(
    running_jobs: list[dict[str, Any]],
    queued_jobs: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    by_p: dict[str, dict[str, int]] = defaultdict(lambda: {"running": 0, "queued": 0, "total": 0})
    for j in running_jobs:
        ps = j.get("project_slug") or ""
        if not ps:
            continue
        by_p[ps]["running"] += 1
        by_p[ps]["total"] += 1
    for j in queued_jobs:
        ps = j.get("project_slug") or ""
        if not ps:
            continue
        by_p[ps]["queued"] += 1
        by_p[ps]["total"] += 1
    return dict(by_p)


def get_concurrency_usage(
    token: str,
    org_slug: str,
    max_pipelines: int = 50,
    project_slug_filter: Optional[str] = None,
) -> dict[str, Any]:
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
        if not _project_matches_filter(project_slug, project_slug_filter):
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
    by_project = _build_by_project_counts(running_jobs, queued_jobs)

    return {
        "org_slug": org_slug,
        "project_slug_filter": project_slug_filter,
        "pipelines_scanned": len(pipelines),
        "pipelines_with_active_workflows": pipelines_with_activity,
        "workflows_checked": workflows_checked,
        "running_jobs": running_jobs,
        "queued_jobs": queued_jobs,
        "running_count": running_count,
        "queued_count": queued_count,
        "total_concurrency_usage": total_usage,
        "by_project": by_project,
    }


def get_executor_breakdown_concurrency_usage(
    token: str,
    org_slug: str,
    max_pipelines: int = 50,
    project_slug_filter: Optional[str] = None,
) -> dict[str, Any]:
    """
    Classify running/queued jobs into self-hosted Runner vs hosted (non-runner) using
    job details (executor.resource_class). One job-details call per active job.
    """
    pipelines = collect_all_pipelines(token, org_slug, max_pipelines=max_pipelines)
    runner_running: list[dict[str, Any]] = []
    runner_queued: list[dict[str, Any]] = []
    cloud_running: list[dict[str, Any]] = []
    cloud_queued: list[dict[str, Any]] = []
    by_rc_runner: dict[str, dict[str, int]] = {}
    by_rc_cloud: dict[str, dict[str, int]] = {}

    def _bump(store: dict[str, dict[str, int]], rc_key: str, field: str) -> None:
        if rc_key not in store:
            store[rc_key] = {"running": 0, "queued": 0}
        store[rc_key][field] += 1

    for pipeline in pipelines:
        pipeline_id = pipeline.get("id")
        project_slug = pipeline.get("project_slug", "")
        if not pipeline_id or not project_slug:
            continue
        if not _project_matches_filter(project_slug, project_slug_filter):
            continue
        try:
            workflows = collect_workflows_for_pipeline(token, pipeline_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                continue
            raise
        active = [w for w in workflows if (w.get("status") or "").lower() in ACTIVE_WORKFLOW_STATUSES]
        for wf in active:
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
                if status not in RUNNING_STATUSES and status not in QUEUED_STATUSES:
                    continue
                jn = job.get("job_number")
                details = get_job_details(token, project_slug, jn)
                if not details:
                    continue
                executor = details.get("executor") or {}
                rc = (executor.get("resource_class") or "").strip()
                is_runner = is_self_hosted_runner_resource_class(rc)
                rc_bucket = rc if rc else "(unset)"
                base = {
                    "job_id": job.get("id"),
                    "job_number": jn,
                    "name": job.get("name"),
                    "project_slug": project_slug,
                    "workflow_name": wf_name,
                    "status": status,
                    "resource_class": rc,
                    "executor_type": executor.get("type"),
                }
                if is_runner:
                    if status in RUNNING_STATUSES:
                        runner_running.append(base)
                        _bump(by_rc_runner, rc, "running")
                    else:
                        runner_queued.append(base)
                        _bump(by_rc_runner, rc, "queued")
                else:
                    if status in RUNNING_STATUSES:
                        cloud_running.append(base)
                        _bump(by_rc_cloud, rc_bucket, "running")
                    else:
                        cloud_queued.append(base)
                        _bump(by_rc_cloud, rc_bucket, "queued")

    runner_by_project = _build_by_project_counts(runner_running, runner_queued)
    cloud_by_project = _build_by_project_counts(cloud_running, cloud_queued)

    return {
        "org_slug": org_slug,
        "project_slug_filter": project_slug_filter,
        "pipelines_scanned": len(pipelines),
        "runner_running_jobs": runner_running,
        "runner_queued_jobs": runner_queued,
        "runner_running_count": len(runner_running),
        "runner_queued_count": len(runner_queued),
        "runner_total_concurrency_usage": len(runner_running) + len(runner_queued),
        "by_resource_class": by_rc_runner,
        "by_project": runner_by_project,
        "cloud_running_jobs": cloud_running,
        "cloud_queued_jobs": cloud_queued,
        "cloud_running_count": len(cloud_running),
        "cloud_queued_count": len(cloud_queued),
        "cloud_total_concurrency_usage": len(cloud_running) + len(cloud_queued),
        "cloud_by_resource_class": by_rc_cloud,
        "cloud_by_project": cloud_by_project,
    }


def get_runner_concurrency_usage(
    token: str,
    org_slug: str,
    max_pipelines: int = 50,
    project_slug_filter: Optional[str] = None,
) -> dict[str, Any]:
    """Concurrency for self-hosted Runner jobs only (same data as breakdown, runner fields)."""
    b = get_executor_breakdown_concurrency_usage(
        token, org_slug, max_pipelines=max_pipelines, project_slug_filter=project_slug_filter
    )
    return {
        "org_slug": b["org_slug"],
        "project_slug_filter": b["project_slug_filter"],
        "pipelines_scanned": b["pipelines_scanned"],
        "runner_running_jobs": b["runner_running_jobs"],
        "runner_queued_jobs": b["runner_queued_jobs"],
        "runner_running_count": b["runner_running_count"],
        "runner_queued_count": b["runner_queued_count"],
        "runner_total_concurrency_usage": b["runner_total_concurrency_usage"],
        "by_resource_class": b["by_resource_class"],
        "by_project": b["by_project"],
    }


def get_cloud_concurrency_usage(
    token: str,
    org_slug: str,
    max_pipelines: int = 50,
    project_slug_filter: Optional[str] = None,
) -> dict[str, Any]:
    """Concurrency for hosted (non–self-hosted Runner) jobs from job details API."""
    b = get_executor_breakdown_concurrency_usage(
        token, org_slug, max_pipelines=max_pipelines, project_slug_filter=project_slug_filter
    )
    return {
        "org_slug": b["org_slug"],
        "project_slug_filter": b["project_slug_filter"],
        "pipelines_scanned": b["pipelines_scanned"],
        "cloud_running_jobs": b["cloud_running_jobs"],
        "cloud_queued_jobs": b["cloud_queued_jobs"],
        "cloud_running_count": b["cloud_running_count"],
        "cloud_queued_count": b["cloud_queued_count"],
        "cloud_total_concurrency_usage": b["cloud_total_concurrency_usage"],
        "by_resource_class": b["cloud_by_resource_class"],
        "by_project": b["cloud_by_project"],
    }
