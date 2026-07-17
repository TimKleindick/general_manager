# Pull Request Database Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add merge-blocking PostgreSQL and MariaDB functional-suite checks to every pull request while retaining the full six-job backend matrix before releases.

**Architecture:** Extract one backend/Python execution into a reusable GitHub Actions workflow. Call it from a two-job pull-request matrix fixed to Python 3.14 and a six-job release matrix covering Python 3.12-3.14, then add the two observed PR check contexts to the existing default-branch ruleset without changing any other rule.

**Tech Stack:** GitHub Actions reusable workflows, YAML, pytest, PyYAML workflow contract tests, Ruff, mypy, GitHub CLI and repository rulesets REST API.

---

## File Map

- Create `.github/workflows/database-backend-tests.yml`: execute one database/Python functional-suite combination.
- Modify `.github/workflows/quality.yml`: replace the inline release-only database job with separate PR and release callers of the reusable workflow.
- Modify `tests/unit/test_release_workflows.py`: contract-test the reusable runner and both caller matrices.
- Update GitHub repository ruleset `5527054`: require the two emitted PR database contexts after they have passed on PR #408.
- Update PR #408 description: describe the two required PR gates and retained six-job release coverage.

### Task 1: Extract the reusable backend runner

**Files:**
- Create: `.github/workflows/database-backend-tests.yml`
- Modify: `tests/unit/test_release_workflows.py`

- [ ] **Step 1: Add the reusable-workflow contract test**

Add this test after `test_quality_test_job_preserves_supported_matrix_and_test_services`:

```python
def test_database_backend_runner_preserves_services_and_test_contract() -> None:
    workflow = load_workflow("database-backend-tests.yml")

    assert workflow["on"] == {
        "workflow_call": {
            "inputs": {
                "python-version": {"required": "true", "type": "string"},
                "database-label": {"required": "true", "type": "string"},
                "database-selector": {"required": "true", "type": "string"},
                "database-image": {"required": "true", "type": "string"},
                "database-port": {"required": "true", "type": "number"},
                "database-user": {"required": "true", "type": "string"},
                "database-driver": {"required": "true", "type": "string"},
                "database-health-command": {
                    "required": "true",
                    "type": "string",
                },
            }
        }
    }
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["backend-test"]
    assert job["name"] == "Backend test"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["services"]["database"] == {
        "image": "${{ inputs.database-image }}",
        "ports": [
            "${{ inputs.database-port }}:${{ inputs.database-port }}",
        ],
        "env": {
            "POSTGRES_DB": "general_manager",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "general_manager",
            "MARIADB_DATABASE": "general_manager",
            "MARIADB_ROOT_PASSWORD": "general_manager",
        },
        "options": (
            '--health-cmd="${{ inputs.database-health-command }}" '
            "--health-interval=5s --health-timeout=5s --health-retries=20"
        ),
    }
    assert job["services"]["meilisearch"] == {
        "image": "getmeili/meilisearch:v1.30.0",
        "ports": ["7700:7700"],
        "env": {"MEILI_NO_ANALYTICS": "true"},
        "options": (
            '--health-cmd="wget -qO- http://127.0.0.1:7700/health" '
            "--health-interval=5s --health-timeout=5s --health-retries=10"
        ),
    }
    assert action_step(job, "actions/checkout@v4")["with"] == {
        "persist-credentials": "false"
    }
    assert action_step(job, "actions/setup-python@v5")["with"] == {
        "python-version": "${{ inputs.python-version }}"
    }

    commands = run_commands(job)
    assert 'pip install -e ".[file-upload-image]"' in commands
    assert "pip install pytest pytest-django meilisearch==0.40.0" in commands
    assert 'pip install "${{ inputs.database-driver }}"' in commands
    assert 'python -m pytest -m "not perf"' in commands

    test_step = next(
        step
        for step in job["steps"]
        if step.get("name")
        == "Run ${{ inputs.database-label }} backend test suite"
    )
    assert test_step["env"] == {
        "GENERAL_MANAGER_TEST_DATABASE": "${{ inputs.database-selector }}",
        "GENERAL_MANAGER_TEST_DATABASE_NAME": "general_manager",
        "GENERAL_MANAGER_TEST_SECONDARY_DATABASE_NAME": "general_manager_secondary",
        "GENERAL_MANAGER_TEST_DATABASE_USER": "${{ inputs.database-user }}",
        "GENERAL_MANAGER_TEST_DATABASE_PASSWORD": "general_manager",
        "GENERAL_MANAGER_TEST_DATABASE_HOST": "127.0.0.1",
        "GENERAL_MANAGER_TEST_DATABASE_PORT": "${{ inputs.database-port }}",
        "MEILISEARCH_URL": "http://127.0.0.1:7700",
    }
```

- [ ] **Step 2: Run the contract test and confirm RED**

Run:

```bash
PYTHONPATH=src python -m pytest tests/unit/test_release_workflows.py::test_database_backend_runner_preserves_services_and_test_contract -q
```

Expected: FAIL because `.github/workflows/database-backend-tests.yml` does not exist.

- [ ] **Step 3: Create the reusable workflow**

Create `.github/workflows/database-backend-tests.yml` with:

```yaml
name: Database Backend Tests

on:
  workflow_call:
    inputs:
      python-version:
        required: true
        type: string
      database-label:
        required: true
        type: string
      database-selector:
        required: true
        type: string
      database-image:
        required: true
        type: string
      database-port:
        required: true
        type: number
      database-user:
        required: true
        type: string
      database-driver:
        required: true
        type: string
      database-health-command:
        required: true
        type: string

permissions:
  contents: read

jobs:
  backend-test:
    name: Backend test
    runs-on: ubuntu-latest
    services:
      database:
        image: ${{ inputs.database-image }}
        ports:
          - "${{ inputs.database-port }}:${{ inputs.database-port }}"
        env:
          POSTGRES_DB: general_manager
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: general_manager
          MARIADB_DATABASE: general_manager
          MARIADB_ROOT_PASSWORD: general_manager
        options: >-
          --health-cmd="${{ inputs.database-health-command }}"
          --health-interval=5s
          --health-timeout=5s
          --health-retries=20
      meilisearch:
        image: getmeili/meilisearch:v1.30.0
        ports:
          - 7700:7700
        env:
          MEILI_NO_ANALYTICS: "true"
        options: >-
          --health-cmd="wget -qO- http://127.0.0.1:7700/health"
          --health-interval=5s
          --health-timeout=5s
          --health-retries=10

    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          persist-credentials: false

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ inputs.python-version }}

      - name: Install package, test dependencies, and database driver
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[file-upload-image]"
          pip install pytest pytest-django meilisearch==0.40.0
          pip install "${{ inputs.database-driver }}"

      - name: Run ${{ inputs.database-label }} backend test suite
        env:
          GENERAL_MANAGER_TEST_DATABASE: ${{ inputs.database-selector }}
          GENERAL_MANAGER_TEST_DATABASE_NAME: general_manager
          GENERAL_MANAGER_TEST_SECONDARY_DATABASE_NAME: general_manager_secondary
          GENERAL_MANAGER_TEST_DATABASE_USER: ${{ inputs.database-user }}
          GENERAL_MANAGER_TEST_DATABASE_PASSWORD: general_manager
          GENERAL_MANAGER_TEST_DATABASE_HOST: 127.0.0.1
          GENERAL_MANAGER_TEST_DATABASE_PORT: ${{ inputs.database-port }}
          MEILISEARCH_URL: http://127.0.0.1:7700
        run: python -m pytest -m "not perf"
```

- [ ] **Step 4: Run the focused contract test and confirm GREEN**

Run the Step 2 command again.

Expected: `1 passed`.

- [ ] **Step 5: Run formatting and diff checks**

Run:

```bash
ruff check tests/unit/test_release_workflows.py
ruff format --check tests/unit/test_release_workflows.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Commit the reusable runner**

```bash
git add .github/workflows/database-backend-tests.yml tests/unit/test_release_workflows.py
git commit -m "ci: extract reusable database backend tests"
```

### Task 2: Add the two-job PR gate and preserve the release matrix

**Files:**
- Modify: `.github/workflows/quality.yml`
- Modify: `tests/unit/test_release_workflows.py`

- [ ] **Step 1: Replace the inline-job contract with caller contracts**

In `test_quality_workflow_has_reusable_least_privilege_triggers`, change the
expected job set to:

```python
    assert set(workflow["jobs"]) == {
        "test",
        "database-pr-gates",
        "database-release",
        "lint-and-mypy",
        "docs",
    }
```

Replace `test_quality_release_database_job_covers_full_supported_matrix` with:

```python
DATABASE_MATRIX = [
    {
        "name": "PostgreSQL 18",
        "selector": "postgresql",
        "image": "postgres:18",
        "port": "5432",
        "user": "postgres",
        "driver": "psycopg[binary]>=3.3,<4",
        "health-command": "pg_isready -U postgres -d general_manager",
    },
    {
        "name": "MariaDB 11.8 LTS",
        "selector": "mariadb",
        "image": "mariadb:11.8",
        "port": "3306",
        "user": "root",
        "driver": "mysqlclient>=2.2,<3",
        "health-command": "healthcheck.sh --connect --innodb_initialized",
    },
]


def assert_database_workflow_inputs(job: Mapping[str, Any]) -> None:
    assert job["uses"] == "./.github/workflows/database-backend-tests.yml"
    assert job["with"] == {
        "python-version": "${{ matrix.python-version }}",
        "database-label": "${{ matrix.database.name }}",
        "database-selector": "${{ matrix.database.selector }}",
        "database-image": "${{ matrix.database.image }}",
        "database-port": "${{ matrix.database.port }}",
        "database-user": "${{ matrix.database.user }}",
        "database-driver": "${{ matrix.database.driver }}",
        "database-health-command": "${{ matrix.database.health-command }}",
    }


def test_quality_pr_database_gate_uses_two_python_314_jobs() -> None:
    job = load_workflow("quality.yml")["jobs"]["database-pr-gates"]

    assert job["name"] == (
        "🗄️ PR ${{ matrix.database.name }} / Python ${{ matrix.python-version }}"
    )
    assert job["if"] == "${{ github.event_name == 'pull_request' }}"
    assert job["strategy"] == {
        "fail-fast": "false",
        "matrix": {
            "python-version": ["3.14"],
            "database": DATABASE_MATRIX,
        },
    }
    assert_database_workflow_inputs(job)


def test_quality_release_database_gate_keeps_full_supported_matrix() -> None:
    job = load_workflow("quality.yml")["jobs"]["database-release"]

    assert job["name"] == (
        "🗄️ Release ${{ matrix.database.name }} / Python "
        "${{ matrix.python-version }}"
    )
    assert job["if"] == "${{ github.event_name == 'push' }}"
    assert job["strategy"] == {
        "fail-fast": "false",
        "matrix": {
            "python-version": ["3.12", "3.13", "3.14"],
            "database": DATABASE_MATRIX,
        },
    }
    assert_database_workflow_inputs(job)
```

Move `DATABASE_MATRIX` immediately after the existing helper functions so it
is defined before both tests.

- [ ] **Step 2: Run the caller contract tests and confirm RED**

Run:

```bash
PYTHONPATH=src python -m pytest \
  tests/unit/test_release_workflows.py::test_quality_workflow_has_reusable_least_privilege_triggers \
  tests/unit/test_release_workflows.py::test_quality_pr_database_gate_uses_two_python_314_jobs \
  tests/unit/test_release_workflows.py::test_quality_release_database_gate_keeps_full_supported_matrix \
  -q
```

Expected: failures because `quality.yml` still contains `database-backends`
and has no PR or release reusable-workflow callers.

- [ ] **Step 3: Replace the inline database job with two callers**

Delete the complete `database-backends` job from `quality.yml`. Insert these
jobs at the same location:

```yaml
  database-pr-gates:
    name: 🗄️ PR ${{ matrix.database.name }} / Python ${{ matrix.python-version }}
    if: ${{ github.event_name == 'pull_request' }}
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.14"]
        database:
          - name: PostgreSQL 18
            selector: postgresql
            image: postgres:18
            port: 5432
            user: postgres
            driver: psycopg[binary]>=3.3,<4
            health-command: pg_isready -U postgres -d general_manager
          - name: MariaDB 11.8 LTS
            selector: mariadb
            image: mariadb:11.8
            port: 3306
            user: root
            driver: mysqlclient>=2.2,<3
            health-command: healthcheck.sh --connect --innodb_initialized
    uses: ./.github/workflows/database-backend-tests.yml
    with:
      python-version: ${{ matrix.python-version }}
      database-label: ${{ matrix.database.name }}
      database-selector: ${{ matrix.database.selector }}
      database-image: ${{ matrix.database.image }}
      database-port: ${{ matrix.database.port }}
      database-user: ${{ matrix.database.user }}
      database-driver: ${{ matrix.database.driver }}
      database-health-command: ${{ matrix.database.health-command }}

  database-release:
    name: 🗄️ Release ${{ matrix.database.name }} / Python ${{ matrix.python-version }}
    if: ${{ github.event_name == 'push' }}
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13", "3.14"]
        database:
          - name: PostgreSQL 18
            selector: postgresql
            image: postgres:18
            port: 5432
            user: postgres
            driver: psycopg[binary]>=3.3,<4
            health-command: pg_isready -U postgres -d general_manager
          - name: MariaDB 11.8 LTS
            selector: mariadb
            image: mariadb:11.8
            port: 3306
            user: root
            driver: mysqlclient>=2.2,<3
            health-command: healthcheck.sh --connect --innodb_initialized
    uses: ./.github/workflows/database-backend-tests.yml
    with:
      python-version: ${{ matrix.python-version }}
      database-label: ${{ matrix.database.name }}
      database-selector: ${{ matrix.database.selector }}
      database-image: ${{ matrix.database.image }}
      database-port: ${{ matrix.database.port }}
      database-user: ${{ matrix.database.user }}
      database-driver: ${{ matrix.database.driver }}
      database-health-command: ${{ matrix.database.health-command }}
```

- [ ] **Step 4: Run all workflow contract tests and confirm GREEN**

Run:

```bash
PYTHONPATH=src python -m pytest tests/unit/test_release_workflows.py -q
```

Expected: all tests in the module pass.

- [ ] **Step 5: Run static checks for the changed files**

```bash
ruff check tests/unit/test_release_workflows.py
ruff format --check tests/unit/test_release_workflows.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Commit the callers**

```bash
git add .github/workflows/quality.yml tests/unit/test_release_workflows.py
git commit -m "ci: gate pull requests on database backends"
```

### Task 3: Verify locally and publish the workflow changes

**Files:**
- Verify only; no additional repository files expected.

- [ ] **Step 1: Run the focused workflow suite**

```bash
PYTHONPATH=src python -m pytest tests/unit/test_release_workflows.py -q
```

Expected: all workflow contract tests pass.

- [ ] **Step 2: Run the complete local quality gate**

Run these commands:

```bash
PYTHONPATH=src python -m pytest -m "not perf" -q
ruff check --config pyproject.toml src tests scripts
ruff format --config pyproject.toml --check .
PYTHONPATH=src mypy --strict
```

Expected: all commands pass. Record the exact pytest counts for the PR body.

- [ ] **Step 3: Push the branch**

```bash
git push origin tk/database-backend-ci
```

Expected: the remote branch advances to the two workflow commits and the
already committed design/plan documentation.

- [ ] **Step 4: Wait for the two PR backend checks**

```bash
gh pr checks 408 --watch --interval 10
```

Expected: the two new backend checks and all existing PR checks pass. The
release caller is skipped on the pull-request event.

- [ ] **Step 5: Verify the exact emitted PR check names**

```bash
gh pr checks 408 --json name,state,link \
  --jq '.[] | select(.name | startswith("🗄️ PR "))'
```

Expected exactly two successful checks named:

```text
🗄️ PR PostgreSQL 18 / Python 3.14 / Backend test
🗄️ PR MariaDB 11.8 LTS / Python 3.14 / Backend test
```

If the names differ, do not update the ruleset. Adjust the caller or called
job names and their contract tests until GitHub emits these exact contexts.

### Task 4: Add the observed PR checks to the active ruleset

**Files:**
- Create temporarily: `/private/tmp/general-manager-ruleset-5527054.json`
- Modify external state: GitHub repository ruleset `5527054`

- [ ] **Step 1: Re-read the live ruleset before mutation**

```bash
gh api repos/TimKleindick/general_manager/rulesets/5527054 \
  --jq '{name,enforcement,conditions,rules,bypass_actors}'
```

Expected: active ruleset `Don't push directly to main`, default-branch
condition, DeployKey bypass, deletion/code-scanning/status/non-fast-forward/PR
rules, and these existing required contexts:

```text
🧪 Run Tests (3.12)
🧪 Run Tests (3.13)
lint-and-mypy
🧪 Run Tests (3.14)
```

Stop if any unrelated rule differs from this plan; preserve the live state
rather than overwriting an unreviewed concurrent change.

- [ ] **Step 2: Build the exact ruleset update payload**

Create `/private/tmp/general-manager-ruleset-5527054.json` with:

```json
{
  "name": "Don't push directly to main",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    {
      "actor_id": null,
      "actor_type": "DeployKey",
      "bypass_mode": "always"
    }
  ],
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": ["~DEFAULT_BRANCH"]
    }
  },
  "rules": [
    {"type": "deletion"},
    {
      "type": "code_scanning",
      "parameters": {
        "code_scanning_tools": [
          {
            "alerts_threshold": "errors",
            "security_alerts_threshold": "high_or_higher",
            "tool": "CodeQL"
          }
        ]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "do_not_enforce_on_create": true,
        "required_status_checks": [
          {"context": "🧪 Run Tests (3.12)", "integration_id": 15368},
          {"context": "🧪 Run Tests (3.13)", "integration_id": 15368},
          {"context": "lint-and-mypy", "integration_id": 15368},
          {"context": "🧪 Run Tests (3.14)", "integration_id": 15368},
          {
            "context": "🗄️ PR PostgreSQL 18 / Python 3.14 / Backend test",
            "integration_id": 15368
          },
          {
            "context": "🗄️ PR MariaDB 11.8 LTS / Python 3.14 / Backend test",
            "integration_id": 15368
          }
        ],
        "strict_required_status_checks_policy": true
      }
    },
    {"type": "non_fast_forward"},
    {
      "type": "pull_request",
      "parameters": {
        "allowed_merge_methods": ["rebase"],
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 1,
        "required_review_thread_resolution": true,
        "required_reviewers": []
      }
    }
  ]
}
```

- [ ] **Step 3: Update the ruleset through the REST API**

```bash
gh api --method PUT \
  repos/TimKleindick/general_manager/rulesets/5527054 \
  --input /private/tmp/general-manager-ruleset-5527054.json \
  --jq '{id,name,enforcement}'
```

Expected: ruleset ID `5527054`, the unchanged name, and `active`
enforcement.

- [ ] **Step 4: Verify no rule changed except the required-context list**

```bash
gh api repos/TimKleindick/general_manager/rulesets/5527054 \
  --jq '{name,enforcement,conditions,rules,bypass_actors}'
```

Expected: identical output to Step 1 except the required-status-check list now
also contains the two exact PR database contexts with GitHub Actions
integration ID `15368`.

- [ ] **Step 5: Verify PR #408 satisfies both required database contexts**

```bash
gh pr checks 408 --required
```

Expected: both database contexts are listed and passing alongside the existing
required checks. The PR remains a draft and still requires review; this step
does not change its draft state or review policy.

### Task 5: Update the PR description and perform the final handoff

**Files:**
- Modify external state: PR #408 description.

- [ ] **Step 1: Update the PR's CI-policy wording**

Replace the PR description's `Release behavior` section with:

```markdown
## Pull-request and release gates

Every pull request runs the complete non-performance suite against PostgreSQL
18 and MariaDB 11.8 LTS on Python 3.14. Both emitted checks are required by the
active default-branch ruleset.

The push-triggered release workflow retains the full six-job backend matrix:
both databases across Python 3.12, 3.13, and 3.14. Release artifacts cannot be
published unless all six jobs pass. Manual Quality dispatches do not run the
server-database jobs.
```

Also replace the validation bullets with the exact local counts from Task 3
and links to the successful PostgreSQL and MariaDB PR checks. Apply the body
through the REST endpoint used previously:

```bash
gh api --method PATCH \
  repos/TimKleindick/general_manager/pulls/408 \
  -F body=@/private/tmp/general-manager-pr-408.md \
  --jq '{number,html_url,draft,state}'
```

Expected: PR #408 remains open and draft.

- [ ] **Step 2: Verify repository and PR state**

```bash
git status --short --branch
gh pr checks 408 --required
gh api repos/TimKleindick/general_manager/pulls/408 \
  --jq '{title,html_url,draft,state}'
```

Expected:

- the worktree is clean and synchronized with
  `origin/tk/database-backend-ci`;
- every required check, including both PR database gates, passes; and
- PR #408 is open and draft with its existing title.

No temporary branch is needed for this rollout because the pull-request event
itself now exercises the two intended backend jobs.
