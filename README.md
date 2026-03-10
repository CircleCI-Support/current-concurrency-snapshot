# CircleCI Concurrency Usage

A small CLI that reports **current concurrency usage** for a CircleCI organization by counting running and queued jobs across recent pipelines.

## What it does

- Calls the CircleCI API v2 to list pipelines for your org
- For each pipeline, fetches workflows and keeps only those that are in progress (`running`, `on_hold`, `created`)
- For each in-progress workflow, fetches jobs and counts:
  - **Running** jobs (actively using concurrency)
  - **Queued** jobs (e.g. `pending`, `on_hold`, `blocked`)
- Prints a summary: running count, queued count, and total concurrency in use

**PLEASE NOTE:**

1. **This will retrieve an estimate based on the in-flight jobs and is not meant to be a definitive. Some jobs may complete shortly after running the CLI so the data will not be accurate.**
2. **We advise ONLY using this CLI when you believe concurrency Maximums are being hit. Exccessive usage can lead to abuse flagging on your org/project or rate limiting being imposedI**



## Setup

1. **Create a CircleCI API token**
   - Go to [CircleCI User Settings → API Tokens](https://app.circleci.com/settings/user/tokens)
   - Create a token with at least read access to your organization

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your token**
   ```bash
   export CIRCLE_TOKEN="your-token-here"
   ```

## Usage

```bash
# Usage: pass your org slug (VCS/org-name)
python circleci_concurrency.py gh/YourOrg
# Or for Bitbucket: bb/YourOrg
# For GitLab / GitHub App use circleci as vcs and org ID: circleci/your-org-id
```

**With environment variable:**
```bash
export CIRCLE_ORG_SLUG=gh/YourOrg
python circleci_concurrency.py
```

**Verbose (list each running/queued job):**
```bash
python circleci_concurrency.py gh/YourOrg --verbose
# or
python circleci_concurrency.py gh/YourOrg -v
```

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

## Notes

- Concurrency in CircleCI is the number of jobs that can run at once (e.g. 30 on the free plan). This tool reports how many of those slots are currently in use (running) or waiting (queued).
- The script only looks at recent pipelines returned by the “list pipelines” API (up to 50 by default). If you have many projects, very old in-progress runs might not be included.
- Your API token must have access to the organization you query.
