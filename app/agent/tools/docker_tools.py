import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agent.runtime_profile import get_runtime_profile
from app.agent.tools.security import register_tool

MAX_LOG_CHARS = 12000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_docker(args: List[str], project_dir: str, timeout: int = 8) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            ["docker", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        return {
            "ok": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "error": "" if proc.returncode == 0 else (proc.stderr or proc.stdout or "docker command failed"),
    }


def _json_result(ok: bool, **extra: Any) -> str:
    payload = {
        "queried_at": _now(),
        "ok": ok,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _parse_ps_json_lines(stdout: str) -> List[Dict[str, Any]]:
    stripped = stdout.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            pass

    services: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            services.append(item)
    return services


@register_tool(
    name="docker_compose_ps",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
)
def docker_compose_ps(project_dir: str = "") -> str:
    """Run read-only docker compose ps and return structured service/container status."""
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)

    result = _run_docker(["compose", "ps", "--format", "json"], selected_project_dir, timeout=8)
    if result.get("ok"):
        rows = _parse_ps_json_lines(str(result.get("stdout") or ""))
        return _json_result(
            True,
            project_dir=selected_project_dir,
            services=rows,
            raw_stdout=str(result.get("stdout") or "")[:MAX_LOG_CHARS],
        )

    fallback = _run_docker(["compose", "ps"], selected_project_dir, timeout=8)
    return _json_result(
        bool(fallback.get("ok")),
        project_dir=selected_project_dir,
        services=[],
        raw_stdout=str(fallback.get("stdout") or "")[:MAX_LOG_CHARS],
        stderr=str(fallback.get("stderr") or result.get("stderr") or "")[:4000],
        error=str(fallback.get("error") or result.get("error") or ""),
        returncode=fallback.get("returncode"),
    )


@register_tool(
    name="docker_inspect_container",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
)
def docker_inspect_container(container_name: str = "") -> str:
    """Run read-only docker inspect for one container and summarize state, health, ports, and mounts."""
    profile = get_runtime_profile()
    selected_container = container_name or profile.app.container_name
    selected_project_dir = str(profile.compose_project_dir)
    result = _run_docker(["inspect", selected_container], selected_project_dir, timeout=8)

    if not result.get("ok"):
        return _json_result(
            False,
            container_name=selected_container,
            error=str(result.get("error") or ""),
            stderr=str(result.get("stderr") or "")[:4000],
            returncode=result.get("returncode"),
        )

    try:
        parsed = json.loads(str(result.get("stdout") or "[]"))
    except Exception as e:
        return _json_result(False, container_name=selected_container, error_type="InvalidJson", error=str(e))

    item = parsed[0] if isinstance(parsed, list) and parsed else {}
    state = item.get("State") if isinstance(item, dict) else {}
    network_settings = item.get("NetworkSettings") if isinstance(item, dict) else {}
    host_config = item.get("HostConfig") if isinstance(item, dict) else {}

    health = None
    if isinstance(state, dict):
        health_obj = state.get("Health")
        if isinstance(health_obj, dict):
            health = health_obj.get("Status")

    return _json_result(
        True,
        container_name=selected_container,
        state={
            "status": state.get("Status") if isinstance(state, dict) else None,
            "running": state.get("Running") if isinstance(state, dict) else None,
            "restarting": state.get("Restarting") if isinstance(state, dict) else None,
            "exit_code": state.get("ExitCode") if isinstance(state, dict) else None,
            "health": health,
            "started_at": state.get("StartedAt") if isinstance(state, dict) else None,
            "finished_at": state.get("FinishedAt") if isinstance(state, dict) else None,
        },
        restart_count=item.get("RestartCount") if isinstance(item, dict) else None,
        ports=network_settings.get("Ports") if isinstance(network_settings, dict) else {},
        binds=host_config.get("Binds") if isinstance(host_config, dict) else [],
    )


@register_tool(
    name="docker_compose_logs",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
)
def docker_compose_logs(service: str = "", tail: int = 200, project_dir: str = "") -> str:
    """Read recent docker compose logs for one service. This is read-only."""
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)
    selected_service = service or profile.app.service_name
    tail = max(10, min(int(tail), 1000))
    result = _run_docker(
        ["compose", "logs", "--tail", str(tail), selected_service],
        selected_project_dir,
        timeout=12,
    )
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    lines = stdout.splitlines()
    error_lines = [line for line in lines if "ERROR" in line.upper() or "WARN" in line.upper()]
    return _json_result(
        bool(result.get("ok")),
        project_dir=selected_project_dir,
        service=selected_service,
        tail=tail,
        line_count=len(lines),
        recent_error_lines=error_lines[-30:],
        logs=stdout[-MAX_LOG_CHARS:],
        stderr=stderr[-4000:],
        error=str(result.get("error") or ""),
        returncode=result.get("returncode"),
    )


@register_tool(
    name="docker_service_status_summary",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"],
)
def docker_service_status_summary(project_dir: str = "") -> str:
    """Summarize newbee-mall compose services with read-only Docker commands."""
    profile = get_runtime_profile()
    selected_project_dir = project_dir or str(profile.compose_project_dir)
    ps_payload = json.loads(docker_compose_ps(selected_project_dir))

    containers = [
        profile.app.container_name,
        profile.mysql.container_name,
        "prometheus",
        "alertmanager",
        "grafana",
    ]
    inspect_summaries = []
    for container in containers:
        inspect_summaries.append(json.loads(docker_inspect_container(container)))

    return _json_result(
        bool(ps_payload.get("ok")),
        project_dir=selected_project_dir,
        compose=ps_payload,
        containers=inspect_summaries,
    )
