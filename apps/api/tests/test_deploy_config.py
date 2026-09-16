"""The deployed start commands cannot drift from the runbook — a test reads both.

The reader's finding: no test read the Railway start command, so "migrations +
seed on every api boot" drifted silently from ``docs/deployment.md``. Now the
start commands live in the repo (``deploy/railway/*.json``, attached per service
as config-as-code), the runbook carries the same table, ``docker-compose.yml``
runs the same boot step, and this file holds the three to each other.

Every check is a pure function over texts and dicts, so each one is also run
against a deliberately broken copy: a start command that runs bare ``alembic
upgrade head``, a runbook row that disagrees with its file, a compose command that
still runs the unguarded upgrade.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
RAILWAY_DIR = os.path.join(REPO_ROOT, "deploy", "railway")
RUNBOOK = os.path.join(REPO_ROOT, "docs", "ops-runbook.md")
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.yml")

#: What the api must boot through (``garh_api.migrate``: the advisory lock, then the
#: once-only seed) and what it must never run bare.
BOOT_STEP = "python -m garh_api.migrate"
UNGUARDED = "alembic upgrade head"

WORKERS = ("solver", "render", "drawings")

#: The stage Railway builds — the LAST one in each Dockerfile. Listed so a stage
#: appended for local convenience cannot silently become the deployed image.
LAST_STAGE = {
    "apps/api/Dockerfile": "prod",
    "services/Dockerfile": "prod",
    "apps/web/Dockerfile": "prod-railway",
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_configs(directory: str = RAILWAY_DIR) -> dict[str, dict[str, Any]]:
    """``{service: parsed json}`` for every ``*.json`` under the directory."""
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            out[name[: -len(".json")]] = json.loads(_read(os.path.join(directory, name)))
    return out


def runbook_table(text: str) -> dict[str, tuple[str, str]]:
    """``{service: (config file, start command)}`` from the runbook's service table.

    Only rows whose first cell is one backticked service name; the managed row
    (``postgres``, ``redis``, ``minio``) and the header are skipped.
    """
    section = text[text.index("## Services and their start commands") :]
    section = section[: section.index("\n## ", 1)]
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        match = re.fullmatch(r"`([a-z-]+)`", cells[0])
        if match is None:
            continue
        rows[match.group(1)] = (cells[1].strip("`"), cells[2])
    return rows


def check_api(config: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    deploy = config.get("deploy", {})
    command = str(deploy.get("startCommand", ""))
    if not command.startswith(BOOT_STEP):
        problems.append("api startCommand must begin with %r, got %r" % (BOOT_STEP, command))
    if UNGUARDED in command:
        problems.append("api startCommand runs bare %r" % UNGUARDED)
    if "uvicorn garh_api.main:app" not in command:
        problems.append("api startCommand does not serve garh_api.main:app")
    if deploy.get("healthcheckPath") != "/healthz":
        problems.append("api healthcheckPath must be /healthz (routers/health.py)")
    if config.get("build", {}).get("dockerfilePath") != "apps/api/Dockerfile":
        problems.append("api must build apps/api/Dockerfile")
    return problems


def check_worker(name: str, config: dict[str, Any], *, repo_root: str = REPO_ROOT) -> list[str]:
    problems: list[str] = []
    expected = "python -m services.%s.worker" % name
    command = str(config.get("deploy", {}).get("startCommand", ""))
    if command != expected:
        problems.append("worker-%s startCommand must be %r, got %r" % (name, expected, command))
    if config.get("build", {}).get("dockerfilePath") != "services/Dockerfile":
        problems.append("worker-%s must build services/Dockerfile" % name)
    if not os.path.exists(os.path.join(repo_root, "services", name, "worker.py")):
        problems.append("services/%s/worker.py does not exist" % name)
    return problems


def check_backup(config: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    deploy = config.get("deploy", {})
    schedule = str(deploy.get("cronSchedule", "")).split()
    if len(schedule) != 5:
        problems.append("backup needs a five-field cronSchedule")
    elif schedule[2:] != ["*", "*", "*"] or not schedule[1].isdigit():
        problems.append("backup must run daily at a fixed hour, got %r" % " ".join(schedule))
    if "backup_db.sh backup-s3" not in str(deploy.get("startCommand", "")):
        problems.append("backup startCommand must run backup_db.sh backup-s3")
    if config.get("build", {}).get("dockerfilePath") != "deploy/backup/Dockerfile":
        problems.append("backup must build deploy/backup/Dockerfile")
    return problems


def check_runbook_matches(
    configs: dict[str, dict[str, Any]], table: dict[str, tuple[str, str]]
) -> list[str]:
    problems: list[str] = []
    for service, config in configs.items():
        if service not in table:
            problems.append("runbook table has no row for %s" % service)
            continue
        config_file, command_cell = table[service]
        if config_file != "deploy/railway/%s.json" % service:
            problems.append("runbook names %r for %s" % (config_file, service))
        start = config.get("deploy", {}).get("startCommand")
        if start is None:
            if "image `CMD`" not in command_cell:
                problems.append("%s has no startCommand; the runbook must say image CMD" % service)
        elif start not in command_cell:
            problems.append(
                "runbook start command for %s differs from %s.json:\n  %s\n  %s"
                % (service, service, command_cell, start)
            )
        cron = config.get("deploy", {}).get("cronSchedule")
        if cron is not None and cron not in command_cell:
            problems.append("runbook does not name %s's schedule %r" % (service, cron))
    for service in table:
        if service not in configs:
            problems.append("runbook row %s has no deploy/railway/%s.json" % (service, service))
    return problems


def check_compose(text: str) -> list[str]:
    problems: list[str] = []
    if BOOT_STEP not in text:
        problems.append("docker-compose.yml api command does not run %s" % BOOT_STEP)
    for line in text.splitlines():
        stripped = line.strip()
        if UNGUARDED in stripped and not stripped.startswith("#"):
            problems.append("docker-compose.yml runs bare %r: %s" % (UNGUARDED, stripped))
    return problems


def last_stage(dockerfile_text: str) -> str | None:
    stages = re.findall(r"^FROM\s+\S+\s+AS\s+([\w-]+)", dockerfile_text, flags=re.M)
    return stages[-1] if stages else None


# ---------------------------------------------------------------------------
# The real files
# ---------------------------------------------------------------------------


def test_every_service_has_a_config_and_the_api_boots_through_the_locked_step() -> None:
    configs = load_configs()
    assert set(configs) == {"api", "web", "backup", *("worker-%s" % w for w in WORKERS)}
    assert check_api(configs["api"]) == []
    for worker in WORKERS:
        assert check_worker(worker, configs["worker-%s" % worker]) == []
    assert check_backup(configs["backup"]) == []
    for config in configs.values():
        assert config["$schema"].startswith("https://railway.com/")


def test_the_runbook_table_matches_the_config_files() -> None:
    problems = check_runbook_matches(load_configs(), runbook_table(_read(RUNBOOK)))
    assert problems == [], "\n".join(problems)


def test_compose_runs_the_same_boot_step_and_never_the_bare_upgrade() -> None:
    assert check_compose(_read(COMPOSE)) == []


@pytest.mark.parametrize(("path", "stage"), sorted(LAST_STAGE.items()))
def test_railway_builds_the_intended_last_stage(path: str, stage: str) -> None:
    assert last_stage(_read(os.path.join(REPO_ROOT, path))) == stage, (
        "Railway builds the LAST stage of %s; it must stay %r" % (path, stage)
    )


def test_the_backup_readme_names_every_service() -> None:
    readme = _read(os.path.join(RAILWAY_DIR, "README.md"))
    for service in load_configs():
        assert "`%s.json`" % service in readme, service


# ---------------------------------------------------------------------------
# Negative controls — each checker fails on the drift it exists for
# ---------------------------------------------------------------------------


def test_the_checkers_refuse_the_drifts_they_exist_for() -> None:
    configs = load_configs()

    bare = json.loads(json.dumps(configs["api"]))
    bare["deploy"]["startCommand"] = (
        "alembic upgrade head && python -m garh_api.seed && exec uvicorn garh_api.main:app"
    )
    problems = check_api(bare)
    assert any("must begin with" in p for p in problems)
    assert any("bare" in p for p in problems)

    wrong_probe = json.loads(json.dumps(configs["api"]))
    wrong_probe["deploy"]["healthcheckPath"] = "/readyz"
    assert check_api(wrong_probe) == ["api healthcheckPath must be /healthz (routers/health.py)"]

    wrong_worker = json.loads(json.dumps(configs["worker-render"]))
    wrong_worker["deploy"]["startCommand"] = "python -m services.solver.worker"
    assert any("worker-render startCommand" in p for p in check_worker("render", wrong_worker))

    hourly = json.loads(json.dumps(configs["backup"]))
    hourly["deploy"]["cronSchedule"] = "0 * * * *"
    assert check_backup(hourly) == ["backup must run daily at a fixed hour, got '0 * * * *'"]

    stale_runbook = _read(RUNBOOK).replace("python -m garh_api.migrate --seed && exec", "exec")
    assert any(
        "runbook start command for api differs" in p
        for p in check_runbook_matches(configs, runbook_table(stale_runbook))
    )

    stale_compose = _read(COMPOSE).replace(
        "python -m garh_api.migrate --seed; fi", "alembic upgrade head; fi"
    )
    assert any("bare" in p for p in check_compose(stale_compose))
    assert check_compose("services: {}") == [
        "docker-compose.yml api command does not run python -m garh_api.migrate"
    ]

    assert last_stage("FROM a AS base\nFROM base AS prod\nFROM prod AS debug\n") == "debug"
