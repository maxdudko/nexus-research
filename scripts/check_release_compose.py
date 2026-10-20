#!/usr/bin/env python3
"""Static checks for compose.release.yml. Needs Docker Compose, not a running stack.

    python3 scripts/check_release_compose.py

The template is rendered with a throwaway password and sentinel provider keys, so
no real secret is read. Exit status is 0 when every check passes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "compose.release.yml"
TEMPLATE = ROOT / ".env.release.example"

TEST_PASSWORD = "check0release0compose0password00"
SENTINELS = {
    "NEXUS_OPENAI_API_KEY": "sk-check-openai-sentinel",
    "NEXUS_TAVILY_API_KEY": "tvly-check-tavily-sentinel",
}
DATA_ONLY = {"postgres", "qdrant", "init"}
LOOPBACK = "127.0.0.1"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def compose_config(env_file: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE_FILE),
            *extra,
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def render_template(values: dict[str, str]) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    for name, value in values.items():
        text, count = re.subn(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
        if count != 1:
            raise SystemExit(f"{TEMPLATE.name}: expected one {name}= line")
    return text


def depends_on(service: dict[str, object]) -> dict[str, str]:
    deps = service.get("depends_on") or {}
    assert isinstance(deps, dict)
    return {name: spec["condition"] for name, spec in deps.items()}


def check_template() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    check(
        re.search(r"^POSTGRES_PASSWORD=$", text, re.M) is not None,
        "template leaves POSTGRES_PASSWORD blank",
    )
    for name in SENTINELS:
        check(
            re.search(rf"^{name}=$", text, re.M) is not None,
            f"template leaves {name} blank",
        )
    check(
        re.search(r"^\s*NEXT_PUBLIC_", text, re.M) is None,
        "template defines no NEXT_PUBLIC_ variable",
    )


def check_missing_password(tmp: Path) -> None:
    env = tmp / "blank.env"
    env.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    result = compose_config(env)
    check(
        result.returncode != 0 and "POSTGRES_PASSWORD" in result.stderr,
        "config fails with a POSTGRES_PASSWORD message when the password is blank",
    )


def check_services(config: dict[str, object]) -> None:
    services = config["services"]
    assert isinstance(services, dict)
    check(
        set(services) == {"postgres", "qdrant", "init", "api", "web"},
        "default profile runs postgres, qdrant, init, api and web",
    )

    for name, service in services.items():
        ports = service.get("ports") or []
        if name == "web":
            check(len(ports) == 1, "web publishes exactly one port")
            check(
                all(port.get("host_ip") == LOOPBACK for port in ports),
                "web port binds 127.0.0.1",
            )
        else:
            check(not ports, f"{name} publishes no host port")
        check("env_file" not in service, f"{name} uses no env_file")

    for name in DATA_ONLY:
        networks = set(services[name].get("networks") or {})
        check(networks == {"data"}, f"{name} is only on the internal data network")
    check(
        set(services["api"].get("networks") or {}) == {"data", "edge"},
        "api is on data and edge",
    )
    check(
        set(services["web"].get("networks") or {}) == {"edge"},
        "web is only on edge",
    )

    check(
        depends_on(services["init"]) == {"postgres": "service_healthy"},
        "init waits for healthy postgres",
    )
    check(
        depends_on(services["api"])
        == {
            "init": "service_completed_successfully",
            "postgres": "service_healthy",
            "qdrant": "service_healthy",
        },
        "api waits for successful init and healthy data services",
    )
    check(
        depends_on(services["web"]) == {"api": "service_healthy"},
        "web waits for healthy api",
    )
    check(services["init"].get("restart") == "no", "init does not restart")
    for name in ("postgres", "qdrant", "api", "web"):
        check("healthcheck" in services[name], f"{name} has a healthcheck")

    for name, service in services.items():
        environment = service.get("environment") or {}
        values = {str(value) for value in environment.values() if value is not None}
        for key, sentinel in SENTINELS.items():
            present = any(sentinel in value for value in values)
            if name == "api":
                check(environment.get(key) == sentinel, f"api receives {key}")
            else:
                check(not present, f"{name} does not receive {key}")
        if name != "postgres":
            check(
                not any(key.startswith("POSTGRES_") for key in environment),
                f"{name} receives no POSTGRES_* variable",
            )

    api_env = services["api"]["environment"]
    check(api_env.get("NEXUS_ENVIRONMENT") == "production", "api runs in production")
    check(
        api_env.get("NEXUS_QDRANT_URL") == "http://qdrant:6333"
        and "@postgres:5432/" in str(api_env.get("NEXUS_DATABASE_URL")),
        "api uses Compose service DNS for postgres and qdrant",
    )
    check(
        services["web"]["environment"] == {"API_PROXY_TARGET": "http://api:8000"},
        "web receives only API_PROXY_TARGET=http://api:8000",
    )

    networks = config["networks"]
    check(networks["data"].get("internal") is True, "data network is internal")
    volumes = {
        volume["source"]
        for name in ("postgres", "qdrant")
        for volume in services[name].get("volumes", [])
        if volume.get("type") == "volume"
    }
    check(
        volumes == {"postgres_data", "qdrant_data"},
        "postgres and qdrant use named volumes",
    )


def check_observability_profile(env: Path) -> None:
    result = compose_config(env, "--profile", "observability")
    check(result.returncode == 0, "observability profile renders")
    if result.returncode != 0:
        return
    jaeger = json.loads(result.stdout)["services"].get("jaeger", {})
    ports = jaeger.get("ports") or []
    check(
        bool(ports) and all(port.get("host_ip") == LOOPBACK for port in ports),
        "jaeger UI binds 127.0.0.1 only",
    )


def main() -> int:
    check_template()
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        check_missing_password(tmp)
        env = tmp / "check.env"
        env.write_text(
            render_template({"POSTGRES_PASSWORD": TEST_PASSWORD, **SENTINELS}),
            encoding="utf-8",
        )
        result = compose_config(env)
        check(result.returncode == 0, "compose.release.yml renders with a test env")
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1
        check_services(json.loads(result.stdout))
        check_observability_profile(env)

    if failures:
        print(f"\n{len(failures)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall release compose checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
