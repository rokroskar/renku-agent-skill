#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=2.0"]
# ///
"""
Renku MCP server.

Exposes Renku platform operations as typed MCP tools.
Authenticate with the official Renku CLI:

    rnk login

The server reads rnk's token file automatically. Claude Code starts this
server automatically via the MCP config (see README).
uv handles the fastmcp dependency — no manual pip install needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from fastmcp import FastMCP
except ImportError:
    print("fastmcp not installed. Run: pip install fastmcp", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# HTTP / auth layer  (stdlib only, same pattern as renku_agent.py)
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return os.environ.get("RENKU_BASE_URL", "https://renkulab.io").rstrip("/")


def _creds_candidates() -> list[Path]:
    candidates = []
    if d := os.environ.get("RENKU_CONFIG_DIR"):
        candidates.append(Path(d) / "credentials.json")
    home = Path.home()
    candidates += [
        home / ".config" / "renku-agent-skill" / "credentials.json",
        home / ".pi" / "renku-config" / "credentials.json",
    ]
    return candidates


def _rnk_token_paths() -> list[Path]:
    """Paths where the official rnk CLI stores its token file."""
    home = Path.home()
    paths = []
    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / "io.renku.sdsc.renku-cli" / "token.json")
    xdg = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    paths.append(xdg / "io.renku.sdsc.renku-cli" / "token.json")
    if appdata := os.environ.get("APPDATA"):
        paths.append(Path(appdata) / "io.renku.sdsc.renku-cli" / "token.json")
    return paths


def _load_rnk_token() -> str | None:
    """Read an access token from the rnk CLI token file, validating issuer and expiry."""
    import base64
    expected_issuer = _base_url() + "/auth/realms/Renku"
    for path in _rnk_token_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            response = data.get("response") or data
            access = response.get("access_token")
            if not access:
                continue
            try:
                payload_part = access.split(".")[1]
                payload_part += "=" * (-len(payload_part) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_part.encode()))
                if payload.get("iss") != expected_issuer:
                    continue
                if payload.get("exp") and time.time() > int(payload["exp"]) - 60:
                    continue
            except Exception:
                pass  # accept token even if JWT decode fails
            return access
        except Exception:
            continue
    return None


def _token() -> str:
    for var in ("RENKU_ACCESS_TOKEN", "RENKU_TOKEN", "RENKU_CLI_ACCESS_TOKEN"):
        if t := os.environ.get(var):
            return t
    checked: list[str] = []
    for f in _creds_candidates():
        checked.append(str(f))
        if f.exists():
            try:
                entry = json.loads(f.read_text()).get(_base_url(), {})
                if t := entry.get("access_token") or entry.get("token"):
                    return t
            except Exception:
                pass
    if t := _load_rnk_token():
        return t
    rnk_paths = [str(p) for p in _rnk_token_paths()]
    raise RuntimeError(
        f"Not authenticated for {_base_url()}.\n"
        f"Run: rnk login\n"
        f"Credentials searched in: {', '.join(checked)}\n"
        f"rnk token paths searched: {', '.join(rnk_paths)}\n"
        f"Or set RENKU_ACCESS_TOKEN in the MCP server environment config."
    )


def _api(
    method: str,
    path: str,
    body: Any = None,
    *,
    query: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    return_headers: bool = False,
) -> Any:
    url = f"{_base_url()}/api/data{path}"
    if query:
        p = {k: str(v) for k, v in query.items() if v is not None}
        if p:
            url += "?" + urllib.parse.urlencode(p)
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            if return_headers:
                return result, dict(resp.headers)
            return result
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}")


def _project_path(ident: str) -> str:
    if "/" in ident:
        ns, slug = ident.split("/", 1)
        return f"/namespaces/{urllib.parse.quote(ns)}/projects/{urllib.parse.quote(slug)}"
    return f"/projects/{urllib.parse.quote(ident)}"


def _launcher_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Add a concise handoff block to a launcher response."""
    env = data.get("environment") or {}
    data["_handoff"] = {
        "launcher_id": data.get("id"),
        "environment_id": env.get("id"),
        "resource_class_id": data.get("resource_class_id"),
        "container_image": env.get("container_image"),
        "port": env.get("port"),
        "command": env.get("command"),
        "args": env.get("args"),
    }
    return data


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "renku",
    instructions=(
        "Tools for the Renku data science platform (renkulab.io or custom deployment). "
        "Set RENKU_BASE_URL to target a non-default deployment. "
        "Authenticate once before using these tools: run 'rnk login' in the terminal.\n\n"
        "Safety rules:\n"
        "- If auth_status shows is_admin=true, do not perform any operation. "
        "Ask the user to log out and log back in as a non-admin.\n"
        "- Always call resource_classes() before creating a launcher or running a job. "
        "Pick a matching=true class appropriate for the task and pass its id.\n"
        "- Confirm with the user before deleting connectors, launchers, or running sessions.\n"
        "- Credentials for S3/Polybox are read from server-side environment variables — "
        "never ask the user to pass them as tool parameters."
    ),
)


# -- Auth / platform ---------------------------------------------------------


@mcp.tool()
def auth_status() -> dict:
    """Return current authentication status, user info, and target deployment URL.
    Always check this first; refuse all operations if is_admin is true.
    If not authenticated, the response includes the paths that were searched
    so the user knows where to look or which env var to set."""
    try:
        user = _api("GET", "/user")
        return {"authenticated": True, "base_url": _base_url(), "user": user, "is_admin": user.get("is_admin", False)}
    except RuntimeError as e:
        return {
            "authenticated": False,
            "base_url": _base_url(),
            "error": str(e),
            "credentials_searched": [str(f) for f in _creds_candidates()],
            "hint": "Run 'rnk login' in the terminal, or set RENKU_ACCESS_TOKEN in the MCP server env config.",
        }


@mcp.tool()
def resource_classes() -> list[dict]:
    """List available compute resource classes.
    Always call this before creating a launcher or running a session/job.
    Use classes where matching=true. Pick the smallest class with enough cpu/memory
    for the task; for GPU work look for gpu > 0."""
    pools = _api("GET", "/resource_pools")
    classes: list[dict] = []
    for pool in pools if isinstance(pools, list) else pools.get("resource_pools", []):
        for cls in pool.get("classes", []):
            cls = dict(cls)
            cls["pool_name"] = pool.get("name")
            classes.append(cls)
    return classes


@mcp.tool()
def namespaces() -> list[dict]:
    """List namespaces accessible to the current user (user namespace + groups)."""
    return _api("GET", "/namespaces")


# -- Projects ----------------------------------------------------------------


@mcp.tool()
def project_list() -> list[dict]:
    """List Renku projects for the current user."""
    return _api("GET", "/projects")


@mcp.tool()
def project_get(project: str) -> dict:
    """Get a Renku project by ID or namespace/slug (e.g. 'myuser/my-project')."""
    return _api("GET", _project_path(project))


@mcp.tool()
def project_create(
    name: str,
    namespace: str,
    visibility: str = "private",
    description: str = "",
    repository_url: str = "",
) -> dict:
    """Create a new Renku project.

    Args:
        name: Human-readable project name.
        namespace: User or group namespace slug (from namespaces()).
        visibility: 'private' or 'public'.
        description: Optional description.
        repository_url: Optional Git repository URL to attach.
    """
    body: dict[str, Any] = {"name": name, "namespace": namespace, "visibility": visibility}
    if description:
        body["description"] = description
    if repository_url:
        body["repositories"] = [repository_url]
    return _api("POST", "/projects", body)


@mcp.tool()
def project_repo_add(project: str, repository_url: str) -> dict:
    """Add a Git repository URL to a project's repositories list.

    Args:
        project: Project ID or namespace/slug.
        repository_url: Git URL to add.
    """
    proj, resp_headers = _api("GET", _project_path(project), return_headers=True)
    etag = resp_headers.get("ETag") or resp_headers.get("etag") or proj.get("etag")
    if not etag:
        raise RuntimeError("Could not get project ETag — cannot PATCH safely")
    repos = list(proj.get("repositories") or [])
    if repository_url not in repos:
        repos.append(repository_url)
    return _api(
        "PATCH",
        f"/projects/{proj['id']}",
        {"repositories": repos},
        extra_headers={"If-Match": etag},
    )


# -- Data connectors ---------------------------------------------------------


@mcp.tool()
def connector_list(search: str = "") -> list[dict]:
    """List data connectors visible to the current user."""
    return _api("GET", "/data_connectors", query={"name": search or None})


@mcp.tool()
def connector_get(connector_id: str) -> dict:
    """Get a data connector by ID."""
    return _api("GET", f"/data_connectors/{connector_id}")


@mcp.tool()
def connector_create_doi(doi: str, target_path: str = "") -> dict:
    """Create a global DOI/Zenodo/Dataverse data connector.

    target_path is set by Renku from the DOI metadata — do not guess it.
    Read _mount_path from the returned connector to know where data will appear
    in sessions (/home/renku/work/<target_path>/).

    Args:
        doi: DOI string e.g. '10.5281/zenodo.1234567'.
        target_path: Override mount path (leave empty; Renku derives it from metadata).
    """
    storage: dict[str, Any] = {
        "configuration": {"type": "doi", "doi": doi},
        "source_path": "/",
        "readonly": True,
    }
    if target_path:
        storage["target_path"] = target_path
    data = _api("POST", "/data_connectors/global", {"storage": storage})
    t = (data.get("storage") or {}).get("target_path")
    if t:
        data["_mount_path"] = f"/home/renku/work/{t}"
    return data


@mcp.tool()
def connector_create_s3(
    name: str,
    namespace: str,
    bucket: str,
    target_path: str,
    endpoint: str = "",
    provider: str = "Other",
    readonly: bool = True,
) -> dict:
    """Create an S3/S3-compatible data connector.

    Credentials are read from RENKU_S3_ACCESS_KEY_ID and RENKU_S3_SECRET_ACCESS_KEY
    environment variables in the MCP server process — never pass them as parameters.

    Args:
        name: Connector display name.
        namespace: Namespace slug (namespace/project-slug or user namespace).
        bucket: S3 bucket name.
        target_path: Relative mount path in sessions (e.g. 'data', not '/data').
        endpoint: S3 endpoint URL (empty for AWS S3).
        provider: S3 provider name (default 'Other').
        readonly: Whether the connector is read-only.
    """
    access_key = os.environ.get("RENKU_S3_ACCESS_KEY_ID")
    secret_key = os.environ.get("RENKU_S3_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise RuntimeError(
            "S3 credentials missing. Set RENKU_S3_ACCESS_KEY_ID and "
            "RENKU_S3_SECRET_ACCESS_KEY in the MCP server environment config."
        )
    body = {
        "name": name,
        "namespace": namespace,
        "storage": {
            "configuration": {
                "type": "s3",
                "provider": provider,
                "endpoint": endpoint,
                "access_key_id": access_key,
                "secret_access_key": secret_key,
            },
            "source_path": f"/{bucket}",
            "target_path": target_path,
            "readonly": readonly,
        },
    }
    return _api("POST", "/data_connectors", body)


@mcp.tool()
def connector_create_polybox(
    name: str,
    namespace: str,
    public_link: str,
    target_path: str,
    visibility: str = "public",
    readonly: bool = True,
    kind: str = "polybox",
) -> dict:
    """Create a Polybox or SWITCHdrive shared-link data connector.

    Password (if required) is read from RENKU_CONNECTOR_PASSWORD in the MCP server
    environment — never pass it as a parameter.

    Args:
        name: Connector display name.
        namespace: Namespace/project-slug.
        public_link: Polybox/SWITCHdrive share link URL.
        target_path: Relative mount path (e.g. 'data', not '/data').
        visibility: 'public' or 'private'.
        readonly: Whether the connector is read-only.
        kind: 'polybox' or 'switchdrive'.
    """
    cfg: dict[str, Any] = {
        "type": "switchDrive" if kind == "switchdrive" else "polybox",
        "provider": "shared",
        "public_link": public_link,
    }
    if pw := os.environ.get("RENKU_CONNECTOR_PASSWORD"):
        cfg["pass"] = pw
    return _api(
        "POST",
        "/data_connectors",
        {
            "name": name,
            "namespace": namespace,
            "visibility": visibility,
            "storage": {
                "configuration": cfg,
                "source_path": "/",
                "target_path": target_path,
                "readonly": readonly,
            },
        },
    )


@mcp.tool()
def connector_link(connector_id: str, project_id: str) -> dict:
    """Link a data connector to a project."""
    return _api("POST", f"/data_connectors/{connector_id}/project_links", {"project_id": project_id})


@mcp.tool()
def connector_unlink(connector_id: str, link_id: str) -> str:
    """Unlink a non-owned data connector from a project. Use connector_delete for owned connectors."""
    _api("DELETE", f"/data_connectors/{connector_id}/project_links/{link_id}")
    return f"Unlinked connector {connector_id} (link {link_id})"


@mcp.tool()
def connector_delete(connector_id: str) -> str:
    """Delete a project-owned data connector. Use connector_unlink for non-owned linked connectors."""
    _api("DELETE", f"/data_connectors/{connector_id}")
    return f"Deleted connector {connector_id}"


# -- Launchers ---------------------------------------------------------------


@mcp.tool()
def launcher_list() -> list[dict]:
    """List session launchers."""
    return _api("GET", "/session_launchers")


@mcp.tool()
def launcher_project_list(project_id: str) -> list[dict]:
    """List session launchers for a specific project."""
    return _api("GET", f"/projects/{project_id}/session_launchers")


@mcp.tool()
def launcher_get(launcher_id: str) -> dict:
    """Get a session launcher by ID."""
    return _api("GET", f"/session_launchers/{launcher_id}")


@mcp.tool()
def launcher_create(
    project_id: str,
    name: str,
    resource_class_id: int,
    environment: dict,
    description: str = "",
) -> dict:
    """Create a session launcher.

    Always call resource_classes() first and pass an appropriate resource_class_id.

    The environment dict must include environment_image_source: 'build' or 'image'.
    For 'build': repository, builder_variant, frontend_variant (required);
      repository_revision, context_dir, platforms (optional). Do NOT include 'name'.
    For 'image': name, environment_kind='CUSTOM', container_image,
      working_directory='/home/renku/work', mount_directory='/home/renku/work',
      command=['/cnb/lifecycle/launcher'], args, port, uid, gid.

    Args:
        project_id: Project ID.
        name: Launcher name.
        resource_class_id: Compute resource class ID (from resource_classes()).
        environment: Environment definition dict.
        description: Optional launcher description.
    """
    # 'name' is required for image-source environments but rejected by the
    # BuildParametersPost schema used for build-source environments.
    if environment.get("environment_image_source") != "build" and "name" not in environment:
        environment = {"name": name, **environment}
    body: dict[str, Any] = {
        "project_id": project_id,
        "name": name,
        "resource_class_id": resource_class_id,
        "environment": environment,
    }
    if description:
        body["description"] = description
    return _launcher_summary(_api("POST", "/session_launchers", body))


@mcp.tool()
def launcher_patch(
    launcher_id: str,
    resource_class_id: int | None = None,
    name: str | None = None,
    environment: dict | None = None,
) -> dict:
    """Patch a session launcher. Omit any field to leave it unchanged.

    Args:
        launcher_id: Launcher ID.
        resource_class_id: New resource class ID.
        name: New launcher name.
        environment: Partial or full environment dict to replace.
    """
    body: dict[str, Any] = {}
    if resource_class_id is not None:
        body["resource_class_id"] = resource_class_id
    if name is not None:
        body["name"] = name
    if environment is not None:
        body["environment"] = environment
    if not body:
        raise RuntimeError("launcher_patch: provide at least one field to update")
    return _launcher_summary(_api("PATCH", f"/session_launchers/{launcher_id}", body))


# -- Sessions ----------------------------------------------------------------


@mcp.tool()
def session_launch(
    launcher_id: str,
    resource_class_id: int | None = None,
    disk_storage: int | None = None,
) -> dict:
    """Launch an interactive session from a launcher.

    Args:
        launcher_id: Launcher ID.
        resource_class_id: Override resource class for this launch only.
        disk_storage: Override disk storage in GB.
    """
    body: dict[str, Any] = {"launcher_id": launcher_id, "session_type": "interactive"}
    if resource_class_id is not None:
        body["resource_class_id"] = resource_class_id
    if disk_storage is not None:
        body["disk_storage"] = disk_storage
    return _api("POST", "/sessions", body)


@mcp.tool()
def session_list(session_type: str = "interactive") -> list[dict]:
    """List sessions. session_type: 'interactive' or 'non-interactive'."""
    return _api("GET", "/sessions", query={"session_type": session_type})


@mcp.tool()
def session_get(session_id: str) -> dict:
    """Get session status and details."""
    return _api("GET", f"/sessions/{session_id}")


@mcp.tool()
def session_logs(session_id: str) -> dict:
    """Get logs for a session. Returns a dict of container_name -> log_text.
    The 'amalthea-session' container holds the main application logs."""
    return _api("GET", f"/sessions/{session_id}/logs")


@mcp.tool()
def session_delete(session_id: str) -> str:
    """Stop and delete a session."""
    _api("DELETE", f"/sessions/{session_id}")
    return f"Deleted session {session_id}"


@mcp.tool()
def session_delete_if_failed(session_id: str) -> str:
    """Delete a session only if it is in a failed/error/stopped state. Safe no-op otherwise.
    Use this before relaunching from the same launcher to avoid session name conflicts."""
    session = _api("GET", f"/sessions/{session_id}")
    status = session.get("status") or {}
    state = status.get("state") or session.get("state") or "unknown"
    if state not in {"failed", "error", "stopped"}:
        return f"Session {session_id} is in state '{state}' — not deleted."
    _api("DELETE", f"/sessions/{session_id}")
    return f"Deleted session {session_id} (was {state})"


@mcp.tool()
def session_wait(session_id: str, timeout: int = 900, interval: int = 10) -> dict:
    """Wait for an interactive session to reach 'running' state.
    Returns final state dict; includes logs on failure.

    Args:
        session_id: Session name or ID.
        timeout: Maximum wait time in seconds.
        interval: Poll interval in seconds.
    """
    terminal = {"running", "succeeded", "failed", "error", "stopped"}
    success = {"running", "succeeded"}
    end = time.time() + timeout
    while time.time() < end:
        session = _api("GET", f"/sessions/{session_id}")
        status = session.get("status") or {}
        state = status.get("state") or session.get("state") or "unknown"
        if state in terminal:
            result: dict[str, Any] = {"state": state, "session": session}
            if state not in success:
                try:
                    result["logs"] = _api("GET", f"/sessions/{session_id}/logs")
                except Exception:
                    pass
            return result
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for session {session_id}")


# -- Jobs --------------------------------------------------------------------


@mcp.tool()
def job_run(
    launcher_id: str,
    resource_class_id: int | None = None,
    disk_storage: int | None = None,
) -> dict:
    """Launch a non-interactive job from a launcher.

    Args:
        launcher_id: Launcher ID.
        resource_class_id: Override resource class for this run only.
        disk_storage: Override disk storage in GB.
    """
    body: dict[str, Any] = {"launcher_id": launcher_id, "session_type": "non-interactive"}
    if resource_class_id is not None:
        body["resource_class_id"] = resource_class_id
    if disk_storage is not None:
        body["disk_storage"] = disk_storage
    return _api("POST", "/sessions", body)


@mcp.tool()
def job_list() -> list[dict]:
    """List non-interactive job sessions."""
    return _api("GET", "/sessions", query={"session_type": "non-interactive"})


@mcp.tool()
def job_wait(session_id: str, timeout: int = 1800, interval: int = 15) -> dict:
    """Wait for a non-interactive job to reach a terminal state.
    Returns final state dict; includes all container logs on failure
    with amalthea-session (main app container) first.

    Args:
        session_id: Session name or ID.
        timeout: Maximum wait time in seconds (default 1800).
        interval: Poll interval in seconds.
    """
    terminal = {"succeeded", "completed", "finished", "failed", "error", "stopped"}
    success = {"succeeded", "completed", "finished"}
    end = time.time() + timeout
    while time.time() < end:
        session = _api("GET", f"/sessions/{session_id}")
        status = session.get("status") or {}
        state = status.get("state") or session.get("state") or "unknown"
        if state in terminal:
            result: dict[str, Any] = {"state": state, "session": session}
            if state not in success:
                try:
                    logs = _api("GET", f"/sessions/{session_id}/logs")
                    if isinstance(logs, dict):
                        ordered = dict(
                            sorted(logs.items(), key=lambda kv: (kv[0] != "amalthea-session", kv[0]))
                        )
                        result["logs"] = ordered
                    else:
                        result["logs"] = logs
                except Exception:
                    pass
            return result
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for job {session_id}")


# -- Builds ------------------------------------------------------------------


@mcp.tool()
def build_list(environment_id: str) -> list[dict]:
    """List builds for an environment."""
    return _api("GET", f"/environments/{environment_id}/builds")


@mcp.tool()
def build_get(build_id: str) -> dict:
    """Get build status."""
    return _api("GET", f"/builds/{build_id}")


@mcp.tool()
def build_logs(build_id: str) -> Any:
    """Get build logs."""
    return _api("GET", f"/builds/{build_id}/logs")


@mcp.tool()
def build_wait(build_id: str, timeout: int = 1800, interval: int = 15) -> dict:
    """Wait for a build-from-code image build to complete.
    Returns final build state; includes logs on failure.

    Args:
        build_id: Build ID (from build_list()).
        timeout: Maximum wait time in seconds.
        interval: Poll interval in seconds.
    """
    terminal = {"succeeded", "failed", "error"}
    end = time.time() + timeout
    while time.time() < end:
        build = _api("GET", f"/builds/{build_id}")
        state = build.get("status", "unknown")
        if state in terminal:
            result: dict[str, Any] = {"state": state, "build": build}
            if state != "succeeded":
                try:
                    result["logs"] = _api("GET", f"/builds/{build_id}/logs")
                except Exception:
                    pass
            return result
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for build {build_id}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
