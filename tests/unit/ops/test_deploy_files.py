from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SECRET_KEY_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD)", re.IGNORECASE)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))


def _services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return compose["services"]


def test_has_worker_and_dashboard(compose: dict[str, Any]) -> None:
    assert {"worker", "dashboard"} <= set(_services(compose))


def test_every_published_port_is_loopback_only(compose: dict[str, Any]) -> None:
    published = 0
    for name, svc in _services(compose).items():
        for port in svc.get("ports", []):
            published += 1
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"
    assert published >= 1


def test_no_privileged_or_host_network(compose: dict[str, Any]) -> None:
    for name, svc in _services(compose).items():
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") not in {"host"}, name
        assert svc.get("pid") != "host", name
        assert "NET_ADMIN" not in svc.get("cap_add", []), name


def test_env_file_is_dotenv_and_no_secrets_in_environment(compose: dict[str, Any]) -> None:
    for name, svc in _services(compose).items():
        assert svc["env_file"] in (".env", [".env"]), name
        env = svc.get("environment", {})
        pairs = env.items() if isinstance(env, dict) else (e.split("=", 1) for e in env)
        for key, value in pairs:
            assert not SECRET_KEY_RE.search(str(key)), f"{name}: {key} looks like a secret"
            assert "${" not in str(value) or "?" in str(value), f"{name}: {key} interpolates"


def test_both_services_mount_the_data_volume(compose: dict[str, Any]) -> None:
    assert "guard-data" in compose["volumes"]
    for name, svc in _services(compose).items():
        assert any(str(v).startswith("guard-data:/data") for v in svc["volumes"]), name
        assert svc["environment"]["GUARD_DATA_DIR"] == "/data", name


def test_services_restart_have_healthchecks_and_limits(compose: dict[str, Any]) -> None:
    for name, svc in _services(compose).items():
        assert svc["restart"] == "unless-stopped", name
        assert "healthcheck" in svc, name
        assert svc.get("mem_limit"), name
        assert svc.get("read_only") is True, name


def test_worker_defaults_run_the_scheduler() -> None:
    text = (ROOT / "Dockerfile").read_text("utf-8")
    assert re.search(r'^CMD \["python", "-m", "guard\.worker"\]', text, re.MULTILINE)


def test_dockerignore_excludes_secrets_and_data() -> None:
    lines = {
        ln.strip()
        for ln in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    for required in (".env", ".env.*", "!.env.example", ".git", "data/", "*.enc.yaml", ".venv"):
        assert required in lines, required
    assert any(ln.startswith("msci") for ln in lines)


def test_dockerfile_copies_no_secrets_and_runs_non_root() -> None:
    lines = (ROOT / "Dockerfile").read_text("utf-8").splitlines()
    for ln in lines:
        if ln.strip().upper().startswith(("COPY", "ADD")):
            assert ".env" not in ln and "enc.yaml" not in ln and "msci" not in ln.lower(), ln
            assert not re.search(r"(^|\s)\.(\s|$)", ln.split("--from")[0]) or "--from" in ln, ln
        assert not re.match(r"\s*(ENV|ARG)\s+\S*(KEY|SECRET|TOKEN|PASSWORD)", ln, re.IGNORECASE), ln
    users = [ln.split()[1] for ln in lines if ln.strip().upper().startswith("USER ")]
    assert users, "no USER instruction"
    assert users[-1] not in {"root", "0"}
    assert "--locked" in (ROOT / "Dockerfile").read_text("utf-8")
    assert "--extra dashboard" in (ROOT / "Dockerfile").read_text("utf-8")


def test_no_tracked_file_matches_secret_patterns() -> None:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = [
        f
        for f in out
        if (name := Path(f).name) == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith(".enc.yaml")
        or (name.lower().startswith("msci") and name.endswith((".csv", ".xlsx")))
        or name.endswith((".pem", ".key"))
        or f.startswith("data/")
    ]
    assert bad == []


def test_ops_scripts_are_valid_bash_and_executable() -> None:
    for script in ("portscan.sh", "restore_drill.sh"):
        path = ROOT / "ops" / script
        assert path.stat().st_mode & 0o100, f"{script} is not executable"
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_runbooks_mark_commands_unverified() -> None:
    for doc in ("ops/deploy.md", "docs/runbook.md", "docs/paper-trading.md"):
        assert (ROOT / doc).exists(), doc
    assert "unverified" in (ROOT / "ops/deploy.md").read_text("utf-8").lower()
    assert "unverified" in (ROOT / "docs/runbook.md").read_text("utf-8").lower()
