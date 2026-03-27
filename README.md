# CircleCI Concurrency Usage

A small CLI that reports **current concurrency usage** for a CircleCI organization by counting running and queued jobs across recent pipelines.

***PLEASE NOTE: This script was made to assist customers but is NOT a maintained and supported CircleCI product offering. Use at your own discretion.***

**Layout:** `circleci_concurrency.py` is the CLI entry point; `utils.py` holds API calls and concurrency logic.

**Pipeline limit:** Edit `MAX_PIPELINES_TO_SCAN` at the top of `circleci_concurrency.py` to change how many recent pipelines are scanned (default `50`).

## What it does

- Calls the CircleCI API v2 to list pipelines for your org
- For each pipeline, fetches workflows and keeps only those that are in progress (`running`, `on_hold`, `created`)
- For each in-progress workflow, fetches jobs and counts:
  - **Running** jobs (actively using concurrency)
  - **Queued** jobs (e.g. `pending`, `on_hold`, `blocked`)
- Prints a summary: running count, queued count, and total concurrency in use

**Self-hosted Runners:** With `--runners` / `-r`, the tool calls the job-details API for each active job and reports jobs whose `resource_class` is a Runner (`namespace/runner-name`, contains `/`). Use `--runners-only` for only that section.

**Hosted (non-runner) jobs:** With `--cloud` / `-c`, the same job-details responses are split the other way: jobs that are **not** self-hosted Runner (CircleCI-hosted `resource_class` values like `medium`, `arm.large`, etc., or `(unset)` if missing). Use `--cloud-only` for only that section. If you pass both `-r` and `-c`, the tool makes **one** breakdown pass (not two).

## Setup

1. **Create a CircleCI API token**
   - Go to [CircleCI User Settings → API Tokens](https://app.circleci.com/settings/user/tokens)
   - Create a token with at least read access to your organization

2. **Install dependencies**
   ```bash
   python3 -m venv venv
   source venv/bin/activate 
   pip install -r requirements.txt
   ```

3. **Set your token**
   ```bash
   export CIRCLE_TOKEN="your-token-here"
   ```

## Usage

```bash
# Usage: pass your org slug (VCS/org-name)
python3 circleci_concurrency.py gh/YourOrg
# Or for Bitbucket: bb/YourOrg
# For GitLab / GitHub App use circleci as vcs and org ID: circleci/your-org-id
```

**With environment variable:**
```bash
export CIRCLE_ORG_SLUG=gh/YourOrg
python3 circleci_concurrency.py
```

**Verbose (list each running/queued job):**
```bash
python3 circleci_concurrency.py gh/YourOrg --verbose
# or
python3 circleci_concurrency.py gh/YourOrg -v
```

**Runner concurrency (self-hosted):**
```bash
python3 circleci_concurrency.py gh/YourOrg --runners
python3 circleci_concurrency.py gh/YourOrg -r -v          # include per-job lines + resource_class
python3 circleci_concurrency.py gh/YourOrg --runners-only # only Runner stats
```

**Hosted / cloud concurrency (not on self-hosted Runners):**
```bash
python3 circleci_concurrency.py gh/YourOrg --cloud
python3 circleci_concurrency.py gh/YourOrg -c -v
python3 circleci_concurrency.py gh/YourOrg --cloud-only
# Runner + hosted in one API pass:
python3 circleci_concurrency.py gh/YourOrg -r -c --by-project
```

**By project:**
```bash
# One repo only (full project slug: vcs/org/repo)
python3 circleci_concurrency.py gh/YourOrg --project gh/YourOrg/my-service

# Break down running/queued totals per project (from scanned pipelines)
python3 circleci_concurrency.py gh/YourOrg --by-project

# Combine with runners / hosted + verbose
python3 circleci_concurrency.py gh/YourOrg --by-project -r -v
python3 circleci_concurrency.py gh/YourOrg --by-project -c
```

You can set `CIRCLE_PROJECT_SLUG` instead of `--project` when using `CIRCLE_ORG_SLUG`.

**Alternative token env var:** `CIRCLE_CI_TOKEN` is also supported.

## Output example

```
Organization: gh/MyOrg
Pipelines scanned: 30 (with active workflows: 3)

Current concurrency usage:
  Running jobs:  5
  Queued jobs:  2
  Total in use: 7
```

With `--verbose` you also get a line per job (project, workflow name, job name, and number).

Runner mode adds a section like:

```
Self-hosted Runner concurrency:
  Running on runners:  2
  Queued for runners:  1
  Total (runner jobs): 3
  By resource_class:
    my-org/docker-large: running=2, queued=1
```

`--cloud` adds:

```
Hosted concurrency (not self-hosted Runner):
  Running (hosted):  4
  Queued (hosted):  1
  Total (hosted jobs): 5
  By resource_class:
    medium: running=3, queued=0
    arm.large: running=1, queued=1
```

## Important notes

- Concurrency in CircleCI is the number of jobs that can run at once (e.g. 30 on the free plan). This tool reports how many slots are in use (running) or waiting (queued).
- The script only scans recent pipelines (see `MAX_PIPELINES_TO_SCAN` in `circleci_concurrency.py`). Very old in-progress runs may be missing.
- Your API token must have access to the organization you query.
- Please only run the CLI when investigating concurrency; heavy or constant use may trigger rate limits.
- **Runner and hosted (`-r` / `-c`)** modes issue one job-details API call per running/queued job in active workflows—use sparingly. Using both flags together still uses a single pass over those jobs.
- Outputted counts are a snapshot; jobs may finish immediately after you run the CLI so the total is not always reliable
