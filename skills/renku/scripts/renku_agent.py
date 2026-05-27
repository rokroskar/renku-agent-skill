#!/usr/bin/env python3
"""Portable Renku agent helper.

Workflow-oriented CLI for Agent Skills. Uses the Renku Data Services API and,
where possible, can delegate to the official Renku CLI (`rnk`) in future.
This first implementation is dependency-free (Python stdlib only).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = "https://renkulab.io"
CLIENT_ID = "renku-cli"
def find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".pi").is_dir():
            return p
        parts = p.parts
        if ".pi" in parts:
            idx = parts.index(".pi")
            if idx > 0:
                return Path(*parts[:idx])
    return None


def choose_config_dir() -> Path:
    if os.environ.get("RENKU_SKILL_CONFIG_DIR"):
        return Path(os.environ["RENKU_SKILL_CONFIG_DIR"]).expanduser()
    preferred = Path.home() / ".config" / "renku-agent-skill"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("")
        probe.unlink(missing_ok=True)
        return preferred
    except Exception:
        root = find_project_root()
        if root:
            return root / ".pi" / "renku-config"
        return Path.cwd() / ".renku-config"


CONFIG_DIR = choose_config_dir()
CREDS_FILE = CONFIG_DIR / "credentials.json"
STATE_DIR = CONFIG_DIR / "state"
REDACT_KEYS = {"access_token", "refresh_token", "id_token", "token", "password", "secret", "client_secret", "pass"}


class RenkuError(Exception):
    pass


def base_url() -> str:
    return os.environ.get("RENKU_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_base(base: Optional[str] = None) -> str:
    return (base or base_url()).rstrip("/") + "/api/data"


def sensitive_key(key: str) -> bool:
    k = key.lower()
    if k in {"env_token", "token_type", "expires_in", "refresh_expires_in"}:
        return False
    return k in REDACT_KEYS or k.endswith("_token") or k.endswith("_secret") or "password" in k


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if sensitive_key(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def print_out(data: Any, args: argparse.Namespace, summary: Optional[str] = None) -> None:
    if getattr(args, "json", False):
        print(json.dumps(redact(data), indent=2, sort_keys=True))
    elif getattr(args, "quiet", False):
        if isinstance(data, dict):
            print(data.get("url") or data.get("id") or data.get("name") or json.dumps(redact(data)))
        else:
            print(json.dumps(redact(data)))
    else:
        if summary:
            print(summary)
        else:
            print(json.dumps(redact(data), indent=2, sort_keys=True))


def load_creds() -> dict[str, Any]:
    if not CREDS_FILE.exists():
        return {"instances": {}}
    try:
        return json.loads(CREDS_FILE.read_text())
    except Exception as e:
        raise RenkuError(f"Cannot read credentials file {CREDS_FILE}: {e}")


def save_creds(creds: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CREDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(creds, indent=2, sort_keys=True))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CREDS_FILE)


def discover_oidc(base: Optional[str] = None) -> dict[str, Any]:
    b = (base or base_url()).rstrip("/")
    candidates = [
        f"{b}/auth/realms/Renku/.well-known/openid-configuration",
        f"{b}/.well-known/openid-configuration",
    ]
    last = None
    for url in candidates:
        try:
            return http_json("GET", url, auth=False, absolute=True)
        except Exception as e:
            last = e
    raise RenkuError(f"Could not discover OIDC configuration for {b}: {last}")


def form_post(url: str, data: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        raise RenkuError(f"HTTP {e.code} from {url}: {redact(detail)}")


def token_from_env() -> Optional[str]:
    return os.environ.get("RENKU_ACCESS_TOKEN") or os.environ.get("RENKU_TOKEN") or os.environ.get("RENKU_CLI_ACCESS_TOKEN")


def rnk_token_paths() -> list[Path]:
    paths: list[Path] = []
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Application Support" / "io.renku.sdsc.renku-cli" / "token.json")
    xdg = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    paths.append(xdg / "io.renku.sdsc.renku-cli" / "token.json")
    if os.environ.get("APPDATA"):
        paths.append(Path(os.environ["APPDATA"]) / "io.renku.sdsc.renku-cli" / "token.json")
    return paths


def load_rnk_token(base: Optional[str] = None) -> Optional[dict[str, Any]]:
    expected_issuer = (base or base_url()).rstrip("/") + "/auth/realms/Renku"
    for path in rnk_token_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            response = data.get("response") or data
            access = response.get("access_token")
            if not access:
                continue
            # Do a lightweight issuer check by decoding the JWT payload without verification.
            try:
                import base64
                payload_part = access.split(".")[1]
                payload_part += "=" * (-len(payload_part) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_part.encode()))
                if payload.get("iss") != expected_issuer:
                    continue
                if payload.get("exp") and time.time() > int(payload["exp"]) - 60:
                    continue
            except Exception:
                pass
            return {"tokens": response, "path": str(path)}
        except Exception:
            continue
    return None


def get_instance_creds(base: Optional[str] = None) -> Optional[dict[str, Any]]:
    creds = load_creds()
    return creds.get("instances", {}).get((base or base_url()).rstrip("/"))


def set_instance_creds(base: str, token_payload: dict[str, Any], oidc: Optional[dict[str, Any]] = None) -> None:
    creds = load_creds()
    creds.setdefault("instances", {})[base.rstrip("/")] = {
        "tokens": token_payload,
        "oidc": oidc or {},
        "saved_at": int(time.time()),
    }
    save_creds(creds)


def auth_header(base: Optional[str] = None) -> dict[str, str]:
    env = token_from_env()
    if env:
        return {"Authorization": f"Bearer {env}"}
    rnk_tok = load_rnk_token(base)
    if rnk_tok:
        return {"Authorization": f"Bearer {rnk_tok['tokens']['access_token']}"}
    inst = get_instance_creds(base)
    if not inst:
        raise RenkuError("Not authenticated. Run: python scripts/renku_agent.py auth login")
    tokens = inst.get("tokens", {})
    # Refresh if near expiry and possible.
    saved = inst.get("saved_at", 0)
    expires = tokens.get("expires_in") or 0
    if tokens.get("refresh_token") and expires and time.time() > saved + int(expires) - 60:
        try:
            oidc = inst.get("oidc") or discover_oidc(base)
            refreshed = form_post(oidc["token_endpoint"], {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": tokens["refresh_token"],
            })
            # Preserve refresh token if provider omits a new one.
            if "refresh_token" not in refreshed:
                refreshed["refresh_token"] = tokens["refresh_token"]
            set_instance_creds((base or base_url()).rstrip("/"), refreshed, oidc)
            tokens = refreshed
        except Exception as e:
            raise RenkuError(f"Token refresh failed; run auth login again. {e}")
    access = tokens.get("access_token")
    if not access:
        raise RenkuError("Stored credentials do not contain an access token. Run auth login again.")
    return {"Authorization": f"Bearer {access}"}


def admin_check_enabled() -> bool:
    return os.environ.get("RENKU_ALLOW_ADMIN", "").lower() not in {"1", "true", "yes"}


_admin_check_cache: Optional[bool] = None


def assert_not_admin() -> None:
    global _admin_check_cache
    if not admin_check_enabled():
        return
    if _admin_check_cache is False:
        return
    try:
        user = http_json("GET", "/user", auth=True, skip_admin_check=True)
        if isinstance(user, dict) and user.get("is_admin") is True:
            _admin_check_cache = True
            raise RenkuError("Refusing to operate on Renku while authenticated as an admin user. Run auth logout and log in with a non-admin account.")
        _admin_check_cache = False
    except RenkuError:
        raise
    except Exception:
        return


def http_json(method: str, path_or_url: str, body: Any = None, auth: bool = True, absolute: bool = False, query: Optional[dict[str, Any]] = None, skip_admin_check: bool = False, headers_extra: Optional[dict[str, str]] = None, return_headers: bool = False) -> Any:
    if absolute or path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = api_base() + (path_or_url if path_or_url.startswith("/") else "/" + path_or_url)
    if query:
        q = {k: v for k, v in query.items() if v is not None}
        if q:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q, doseq=True)
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if auth:
        if not skip_admin_check and not (method.upper() == "GET" and path_or_url == "/user"):
            assert_not_admin()
        headers.update(auth_header())
    if headers_extra:
        headers.update(headers_extra)
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            data = {"status": resp.status} if not raw else json.loads(raw)
            if return_headers:
                return data, dict(resp.headers)
            return data
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        raise RenkuError(f"HTTP {e.code} {method.upper()} {url}: {json.dumps(redact(detail), default=str)}")
    except urllib.error.URLError as e:
        raise RenkuError(f"Request failed {method.upper()} {url}: {e}")


def read_body_arg(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if getattr(args, "body", None):
            if args.body == "-":
                return json.load(sys.stdin)
            return json.loads(Path(args.body).read_text())
        if getattr(args, "payload", None):
            return json.loads(args.payload)
    except (OSError, ValueError) as e:
        raise RenkuError(f"Failed to read JSON body: {e}")
    raise RenkuError("A JSON body is required via --body FILE, --body -, or --payload JSON")


def confirm(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "yes", False):
        return
    if not sys.stdin.isatty():
        raise RenkuError(f"Confirmation required but stdin is not a TTY; pass --yes to proceed: {message}")
    ans = input(f"{message}\nType 'yes' to continue: ")
    if ans != "yes":
        raise RenkuError("Cancelled")


def try_rnk() -> Optional[str]:
    for exe in ("rnk", "renku", "renku-cli"):
        p = shutil.which(exe)
        if p:
            return p
    cached = CONFIG_DIR / "bin" / "rnk"
    if cached.exists() and os.access(cached, os.X_OK):
        return str(cached)
    return None


def auth_state_path(base: str) -> Path:
    safe = base.replace("https://", "").replace("http://", "").replace("/", "_")
    return STATE_DIR / f"device-{safe}.json"


def start_device_login(args: argparse.Namespace, b: str, oidc: dict[str, Any]) -> dict[str, Any]:
    device_ep = oidc.get("device_authorization_endpoint")
    if not device_ep:
        raise RenkuError("OIDC configuration does not include device_authorization_endpoint")
    init = form_post(device_ep, {"client_id": CLIENT_ID, "scope": args.scope})
    state = {"base_url": b, "oidc": oidc, "device": init, "created_at": int(time.time())}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = auth_state_path(b)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return state


def finish_device_login(state: dict[str, Any]) -> bool:
    b = state["base_url"]
    oidc = state["oidc"]
    init = state["device"]
    tok = form_post(oidc["token_endpoint"], {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": CLIENT_ID,
        "device_code": init["device_code"],
    })
    set_instance_creds(b, tok, oidc)
    try:
        auth_state_path(b).unlink(missing_ok=True)
    except Exception:
        pass
    print(f"Authenticated to {b}; credentials saved in {CREDS_FILE}")
    return True


def print_device_instructions(state: dict[str, Any]) -> None:
    init = state["device"]
    path = auth_state_path(state["base_url"])
    print("Open this URL and enter the code to authorize Renku access:")
    print(init.get("verification_uri_complete") or init.get("verification_uri"))
    if init.get("user_code"):
        print(f"Code: {init['user_code']}")
    print(f"Then run: python3 scripts/renku_agent.py auth complete --state {path}")


def cmd_auth_login(args: argparse.Namespace) -> None:
    b = base_url()
    if args.method in ("auto", "rnk") and not args.no_rnk:
        exe = try_rnk()
        if exe:
            cmd = [exe, "--renku-url", b, "login"]
            print(f"Using official Renku CLI for login: {' '.join(cmd)}")
            rc = subprocess.call(cmd)
            if rc == 0:
                rnk_tok = load_rnk_token(b)
                if rnk_tok:
                    try:
                        set_instance_creds(b, rnk_tok["tokens"], discover_oidc(b))
                        print(f"Imported rnk credentials from {rnk_tok['path']} for direct API use.")
                    except Exception as e:
                        print(f"Warning: rnk login succeeded but importing credentials failed: {e}", file=sys.stderr)
                print(f"Authenticated to {b} with rnk")
                return
            if args.method == "rnk":
                raise RenkuError(f"rnk login failed with exit code {rc}")
            print("rnk login failed; falling back to built-in device flow", file=sys.stderr)
        elif args.method == "rnk":
            raise RenkuError("rnk was not found on PATH or in the skill cache")
    oidc = discover_oidc(b)
    state = start_device_login(args, b, oidc)
    print_device_instructions(state)
    if args.user_code_only:
        return
    interval = int(state["device"].get("interval", 5))
    deadline = time.time() + int(state["device"].get("expires_in", 600))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            finish_device_login(state)
            return
        except RenkuError as e:
            msg = str(e)
            if "authorization_pending" in msg:
                continue
            if "slow_down" in msg:
                interval += 5
                continue
            raise
    raise RenkuError("Device authorization timed out; run auth complete after approving the code")


def cmd_auth_complete(args: argparse.Namespace) -> None:
    b = base_url()
    path = Path(args.state) if args.state else auth_state_path(b)
    if not path.exists():
        raise RenkuError(f"No pending auth state found at {path}. Run auth login --user-code-only first.")
    state = json.loads(path.read_text())
    finish_device_login(state)


def cmd_auth_status(args: argparse.Namespace) -> None:
    out = {"base_url": base_url(), "env_token": bool(token_from_env()), "stored": bool(get_instance_creds())}
    try:
        out["user"] = http_json("GET", "/user")
        print_out(out, args, f"Authenticated to {out['base_url']} as {out['user'].get('email') or out['user'].get('username') or out['user'].get('id')}")
    except Exception as e:
        out["error"] = str(e)
        print_out(out, args, f"Not authenticated to {out['base_url']}: {e}")


def cmd_auth_logout(args: argparse.Namespace) -> None:
    b = base_url()
    creds = load_creds()
    existed = creds.get("instances", {}).pop(b, None) is not None
    save_creds(creds)
    print(f"Removed credentials for {b}" if existed else f"No stored credentials for {b}")


def cmd_doctor(args: argparse.Namespace) -> None:
    checks: dict[str, Any] = {"base_url": base_url(), "python": sys.version.split()[0], "platform": platform.platform(), "rnk": try_rnk()}
    for name, path, auth in [
        ("platform_config", "/platform/config", False),
        ("openapi", base_url() + "/api/data/spec.json", False),
    ]:
        try:
            checks[name] = "ok" if http_json("GET", path, auth=auth, absolute=path.startswith("http")) is not None else "empty"
        except Exception as e:
            checks[name] = f"failed: {e}"
    try:
        oidc = discover_oidc()
        checks["oidc"] = {"issuer": oidc.get("issuer"), "device_flow": bool(oidc.get("device_authorization_endpoint"))}
    except Exception as e:
        checks["oidc"] = f"failed: {e}"
    try:
        checks["user"] = http_json("GET", "/user")
        checks["namespaces"] = http_json("GET", "/namespaces")
        checks["resource_pools"] = http_json("GET", "/resource_pools")
    except Exception as e:
        checks["auth_api"] = f"failed: {e}"
    print_out(checks, args)


def cmd_api(args: argparse.Namespace) -> None:
    body = read_body_arg(args) if args.method.upper() in {"POST", "PUT", "PATCH"} and (args.body or args.payload) else None
    if args.dry_run:
        print_out({"method": args.method.upper(), "path": args.path, "body": body}, args)
        return
    data = http_json(args.method, args.path, body=body, auth=not args.no_auth)
    print_out(data, args)


def cmd_user(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/user"), args)


def cmd_namespaces(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/namespaces"), args)


def cmd_pools(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/resource_pools"), args)


def cmd_classes(args: argparse.Namespace) -> None:
    if args.pool:
        data = http_json("GET", f"/resource_pools/{args.pool}/classes")
    else:
        pools = http_json("GET", "/resource_pools")
        data = []
        for p in pools if isinstance(pools, list) else pools.get("items", []):
            pid = p.get("id")
            if pid is not None:
                try:
                    data.append({"resource_pool": pid, "classes": http_json("GET", f"/resource_pools/{pid}/classes")})
                except Exception as e:
                    data.append({"resource_pool": pid, "error": str(e)})
    print_out(data, args)


def cmd_search(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/search/query", query={"q": args.query, "type": getattr(args, "type", None), "page": getattr(args, "page", None), "per_page": getattr(args, "per_page", None)}), args)


def cmd_project_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/projects", query={"name": args.search, "page": args.page, "per_page": args.per_page}), args)


def _project_api_path(ident: str) -> str:
    if "/" in ident:
        ns, slug = ident.split("/", 1)
        return f"/namespaces/{urllib.parse.quote(ns)}/projects/{urllib.parse.quote(slug)}"
    return f"/projects/{urllib.parse.quote(ident)}"


def _project_fetch(ident: str) -> tuple[dict[str, Any], str, str]:
    """Return (project_data, project_id, etag). Raises RenkuError if ETag is missing."""
    proj, resp_headers = http_json("GET", _project_api_path(ident), return_headers=True)
    proj_id = proj.get("id") or ident
    etag = resp_headers.get("ETag") or resp_headers.get("etag")
    if not etag:
        raise RenkuError(f"Project GET response did not include an ETag header; cannot PATCH safely")
    return proj, proj_id, etag


def cmd_project_get(args: argparse.Namespace) -> None:
    print_out(http_json("GET", _project_api_path(args.project)), args)


def project_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.body or args.payload:
        return read_body_arg(args)
    body: dict[str, Any] = {"name": args.name, "namespace": args.namespace}
    for k in ("slug", "visibility", "description", "documentation", "secrets_mount_directory"):
        v = getattr(args, k, None)
        if v is not None:
            body[k] = v
    if args.keyword:
        body["keywords"] = args.keyword
    if args.repository:
        body["repositories"] = args.repository
    return body


def cmd_project_create(args: argparse.Namespace) -> None:
    body = project_payload_from_args(args)
    if args.dry_run:
        print_out({"POST": "/projects", "body": body}, args); return
    data = http_json("POST", "/projects", body)
    print_out(data, args, f"Created project {data.get('namespace')}/{data.get('slug')} ({data.get('id')})")


def cmd_project_documentation_get(args: argparse.Namespace) -> None:
    data = http_json("GET", _project_api_path(args.project))
    content = data.get("documentation") or ""
    print_out({"content": content, "length": len(content)}, args)


def cmd_project_documentation_set(args: argparse.Namespace) -> None:
    if getattr(args, "file", None):
        try:
            content = Path(args.file).read_text()
        except OSError as e:
            raise RenkuError(f"Cannot read documentation file: {e}")
    elif getattr(args, "content", None) is not None:
        content = args.content
    else:
        raise RenkuError("Provide documentation via --content TEXT or --file PATH")
    if len(content) > 5000:
        raise RenkuError(f"Project documentation is limited to 5000 characters; got {len(content)}. Provide a shorter summary and link to longer docs.")
    if args.dry_run:
        print_out({"PATCH": "/projects/<id>", "body": {"documentation": content}, "length": len(content)}, args); return
    proj, proj_id, etag = _project_fetch(args.project)
    data = http_json("PATCH", f"/projects/{proj_id}", body={"documentation": content}, headers_extra={"If-Match": etag})
    print_out(data, args, f"Updated documentation for project {args.project}")


def cmd_project_repo_list(args: argparse.Namespace) -> None:
    proj = http_json("GET", _project_api_path(args.project))
    print_out(proj.get("repositories", []), args)


def cmd_project_repo_add(args: argparse.Namespace) -> None:
    proj, proj_id, etag = _project_fetch(args.project)
    repos = list(proj.get("repositories") or [])
    url = args.url + (f"#{args.ref}" if getattr(args, "ref", None) else "")
    if url not in repos:
        repos.append(url)
    body = {"repositories": repos}
    if args.dry_run:
        print_out({"PATCH": f"/projects/{proj_id}", "body": body}, args); return
    data = http_json("PATCH", f"/projects/{proj_id}", body, headers_extra={"If-Match": etag})
    print_out(data, args, f"Updated repositories for project {args.project}")


def cmd_project_repo_remove(args: argparse.Namespace) -> None:
    confirm(args, f"Remove repository {args.repository} from project {args.project}?")
    proj, proj_id, etag = _project_fetch(args.project)
    target = args.repository.split("#")[0]
    repos = [r for r in (proj.get("repositories") or []) if r.split("#")[0] != target]
    body = {"repositories": repos}
    if args.dry_run:
        print_out({"PATCH": f"/projects/{proj_id}", "body": body}, args); return
    data = http_json("PATCH", f"/projects/{proj_id}", body, headers_extra={"If-Match": etag})
    print_out(data, args, f"Removed repository from project {args.project}")


def cmd_project_members_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", f"/projects/{args.project}/members"), args)


def cmd_project_members_add(args: argparse.Namespace) -> None:
    confirm(args, f"Add/update user {args.user} as {args.role} in project {args.project}?")
    body = {"id": args.user, "role": args.role}
    if args.dry_run:
        print_out({"POST": f"/projects/{args.project}/members", "body": body}, args); return
    print_out(http_json("POST", f"/projects/{args.project}/members", body), args)


def cmd_project_members_remove(args: argparse.Namespace) -> None:
    confirm(args, f"Remove user {args.user} from project {args.project}?")
    if args.dry_run:
        print_out({"DELETE": f"/projects/{args.project}/members/{args.user}"}, args); return
    print_out(http_json("DELETE", f"/projects/{args.project}/members/{args.user}"), args, "Member removed")


def _prompt_input(prompt: str, env_var: str, arg_val: Optional[str] = None) -> str:
    if arg_val is not None:
        return arg_val
    env = os.environ.get(env_var)
    if env is not None:
        return env
    if not sys.stdin.isatty():
        raise RenkuError(f"Input required but stdin is not a TTY; set {env_var} or use --body/--payload")
    return input(prompt)


def _prompt_secret(prompt: str, env_var: str, arg_val: Optional[str] = None) -> str:
    if arg_val is not None:
        return arg_val
    env = os.environ.get(env_var)
    if env is not None:
        return env
    if not sys.stdin.isatty():
        raise RenkuError(f"Secret required but stdin is not a TTY; set {env_var} or use --body/--payload")
    return getpass.getpass(prompt)


def connector_storage(args: argparse.Namespace) -> dict[str, Any]:
    target = args.target_path
    if args.kind in ("doi", "zenodo", "dataverse"):
        doi_or_url = args.doi or args.url
        if not doi_or_url:
            raise RenkuError(f"connector create {args.kind} requires --doi <DOI> or --url <URL>")
        provider = args.provider
        if not provider and args.kind in ("zenodo", "dataverse"):
            provider = args.kind
        cfg = {"type": "doi", "doi": doi_or_url}
        if provider:
            cfg["provider"] = provider
        return {"configuration": cfg, "source_path": args.source_path or "/", "readonly": True}
    if args.kind == "s3":
        access_key = _prompt_input("S3 access key id: ", "RENKU_S3_ACCESS_KEY_ID", getattr(args, "access_key_id", None))
        secret_key = _prompt_secret("S3 secret access key: ", "RENKU_S3_SECRET_ACCESS_KEY", getattr(args, "secret_access_key", None))
        cfg = {"type": "s3", "provider": args.provider or "Other", "endpoint": args.endpoint or "", "access_key_id": access_key, "secret_access_key": secret_key}
        return {"configuration": cfg, "source_path": args.source_path or args.bucket or "/", "target_path": target, "readonly": args.readonly}
    if args.kind in ("polybox", "switchdrive"):
        cfg = {"type": "switchDrive" if args.kind == "switchdrive" else "polybox", "provider": args.access}
        if args.access == "shared":
            cfg["public_link"] = _prompt_input("Public/share link: ", "RENKU_CONNECTOR_URL", args.url)
            pw = getattr(args, "password", None) or os.environ.get("RENKU_CONNECTOR_PASSWORD") or (
                getpass.getpass("Share password (leave empty if none): ") if sys.stdin.isatty() else ""
            )
            if pw:
                cfg["pass"] = pw
        else:
            cfg["user"] = _prompt_input("Username: ", "RENKU_CONNECTOR_USERNAME", args.username)
            cfg["pass"] = _prompt_secret("Password/token: ", "RENKU_CONNECTOR_PASSWORD", getattr(args, "password", None))
            if args.url:
                cfg["url"] = args.url
        return {"configuration": cfg, "source_path": args.source_path or "/", "target_path": target, "readonly": args.readonly}
    if args.kind == "webdav":
        url = _prompt_input("WebDAV URL: ", "RENKU_CONNECTOR_URL", args.url)
        user = _prompt_input("Username: ", "RENKU_CONNECTOR_USERNAME", args.username)
        pw = _prompt_secret("Password/token: ", "RENKU_CONNECTOR_PASSWORD", getattr(args, "password", None))
        cfg = {"type": "webdav", "url": url, "user": user, "pass": pw}
        return {"configuration": cfg, "source_path": args.source_path or "/", "target_path": target, "readonly": args.readonly}
    raise RenkuError(f"Unsupported connector kind {args.kind}")


def cmd_connector_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/data_connectors", query={"name": args.search, "page": args.page, "per_page": args.per_page}), args)


def cmd_connector_get(args: argparse.Namespace) -> None:
    print_out(http_json("GET", f"/data_connectors/{args.connector}"), args)


def cmd_connector_create(args: argparse.Namespace) -> None:
    if args.body or args.payload:
        body = read_body_arg(args)
    else:
        is_doi_connector = getattr(args, "kind", None) in ("doi", "zenodo", "dataverse")
        if is_doi_connector:
            if getattr(args, "namespace", None):
                print("Warning: --namespace is ignored for DOI/Zenodo/Dataverse connectors; they are always created as global connectors.", file=sys.stderr)
            args.global_connector = True
            # The global DOI endpoint derives name/slug/visibility/target_path from DOI metadata.
            # It rejects project-owned fields such as name and namespace.
            body = {"storage": connector_storage(args)}
        else:
            body = {"name": args.name, "storage": connector_storage(args)}
            if args.namespace: body["namespace"] = args.namespace
            if args.slug: body["slug"] = args.slug
            if args.visibility: body["visibility"] = args.visibility
            if args.description: body["description"] = args.description
    path = "/data_connectors/global" if args.global_connector else "/data_connectors"
    if args.dry_run:
        print_out({"POST": path, "body": redact(body)}, args); return
    data = http_json("POST", path, body)
    storage = data.get("storage") or {}
    target = storage.get("target_path")
    summary = f"Created data connector {data.get('name')} ({data.get('id')})"
    if target:
        summary += f" — target_path: {target!r} (mount at /home/renku/work/{target})"
    print_out(data, args, summary)


def cmd_connector_link(args: argparse.Namespace) -> None:
    body = {"project_id": args.project}
    if args.dry_run:
        print_out({"POST": f"/data_connectors/{args.connector}/project_links", "body": body}, args); return
    data = http_json("POST", f"/data_connectors/{args.connector}/project_links", body)
    print_out(data, args, f"Linked connector {args.connector} to project {args.project}")


def cmd_connector_unlink(args: argparse.Namespace) -> None:
    confirm(args, f"Unlink data connector link {args.link} from connector {args.connector}?")
    if args.dry_run:
        print_out({"DELETE": f"/data_connectors/{args.connector}/project_links/{args.link}"}, args); return
    data = http_json("DELETE", f"/data_connectors/{args.connector}/project_links/{args.link}")
    print_out(data, args, "Unlinked data connector")


def cmd_connector_delete(args: argparse.Namespace) -> None:
    try:
        connector = http_json("GET", f"/data_connectors/{args.connector}")
        name = connector.get("name") or args.connector
        namespace = connector.get("namespace")
        visibility = connector.get("visibility")
        storage = connector.get("storage") or {}
        target = storage.get("target_path")
        detail = f"Delete data connector {name} ({args.connector})"
        if namespace:
            detail += f" from namespace {namespace}"
        if visibility:
            detail += f", visibility={visibility}"
        if target:
            detail += f", target_path={target}"
        detail += "? This deletes project-owned connectors; use connector unlink for non-owned linked/global connectors."
    except RenkuError:
        detail = f"Delete data connector {args.connector}? This deletes project-owned connectors; use connector unlink for non-owned linked/global connectors."
    confirm(args, detail)
    if args.dry_run:
        print_out({"DELETE": f"/data_connectors/{args.connector}"}, args); return
    data = http_json("DELETE", f"/data_connectors/{args.connector}")
    print_out(data, args, "Deleted data connector")


def simple_crud(resource: str, path: str):
    def list_cmd(args): print_out(http_json("GET", path, query={"page": getattr(args, "page", None), "per_page": getattr(args, "per_page", None)}), args)
    def get_cmd(args): print_out(http_json("GET", f"{path}/{getattr(args, resource)}"), args)
    def create_cmd(args):
        body = read_body_arg(args)
        if args.dry_run: print_out({"POST": path, "body": body}, args); return
        print_out(http_json("POST", path, body), args)
    def patch_cmd(args):
        body = read_body_arg(args)
        if args.dry_run: print_out({"PATCH": f"{path}/{getattr(args, resource)}", "body": body}, args); return
        print_out(http_json("PATCH", f"{path}/{getattr(args, resource)}", body), args)
    def delete_cmd(args):
        confirm(args, f"Delete {resource} {getattr(args, resource)}?")
        if args.dry_run: print_out({"DELETE": f"{path}/{getattr(args, resource)}"}, args); return
        print_out(http_json("DELETE", f"{path}/{getattr(args, resource)}"), args)
    return list_cmd, get_cmd, create_cmd, patch_cmd, delete_cmd


def cmd_launcher_project_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", f"/projects/{args.project}/session_launchers"), args)


def add_iframe_url(session: dict[str, Any]) -> dict[str, Any]:
    try:
        project_id = session.get("project_id")
        name = session.get("name") or session.get("id")
        if project_id and name:
            project = http_json("GET", f"/projects/{project_id}")
            ns = project.get("namespace")
            slug = project.get("slug")
            if ns and slug:
                session = dict(session)
                session["iframe_url"] = f"{base_url()}/p/{urllib.parse.quote(str(ns))}/{urllib.parse.quote(str(slug))}/sessions/show/{urllib.parse.quote(str(name))}"
    except Exception:
        pass
    return session


def extract_build_progress(logs: Any, lines: int = 8) -> str:
    if isinstance(logs, dict):
        # Prefer the actual build step, then include other steps if needed.
        ordered = []
        for key in ("step-build-and-push", "step-source-default", "prepare"):
            if key in logs:
                ordered.append(str(logs[key]))
        for key, value in logs.items():
            if key not in {"step-build-and-push", "step-source-default", "prepare"}:
                ordered.append(str(value))
        text = "\n".join(ordered)
    else:
        text = str(logs)
    useful = []
    noisy_prefixes = ("Downloading ", "Collecting ")
    for ln in text.splitlines():
        s = ln.strip()
        if not s or any(s.startswith(p) for p in noisy_prefixes):
            continue
        useful.append(s)
    return "\n".join(useful[-lines:])


def cmd_build_wait(args: argparse.Namespace) -> None:
    terminal = {"succeeded", "failed", "error"}
    success = {"succeeded"}
    end = time.time() + args.timeout
    last_status = None
    build: dict[str, Any] = {}
    while time.time() < end:
        build = http_json("GET", f"/builds/{args.build}")
        status = build.get("status", "unknown")
        if not args.json and (args.verbose or status != last_status):
            print(f"build {args.build}: {status}")
        if args.logs:
            try:
                tail = extract_build_progress(http_json("GET", f"/builds/{args.build}/logs"), args.log_lines)
                if tail and not args.json:
                    print(tail)
            except Exception as e:
                if args.verbose and not args.json:
                    print(f"build logs unavailable: {e}")
        if status in terminal:
            print_out(build, args)
            if status not in success:
                raise RenkuError(f"Build {args.build} ended with status: {status}")
            return
        last_status = status
        time.sleep(args.interval)
    raise RenkuError(f"Timed out waiting for build {args.build}")


def cmd_session_launch(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"launcher_id": args.launcher, "session_type": "interactive"}
    if args.disk_storage is not None: body["disk_storage"] = args.disk_storage
    if args.resource_class_id is not None: body["resource_class_id"] = args.resource_class_id
    if args.dry_run:
        print_out({"POST": "/sessions", "body": body}, args); return
    data = add_iframe_url(http_json("POST", "/sessions", body))
    print_out(data, args, f"Launched session {data.get('name') or data.get('id') or ''}")


def cmd_job_run(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"launcher_id": args.launcher, "session_type": "non-interactive"}
    if args.disk_storage is not None: body["disk_storage"] = args.disk_storage
    if args.resource_class_id is not None: body["resource_class_id"] = args.resource_class_id
    if args.dry_run:
        print_out({"POST": "/sessions", "body": body}, args); return
    data = add_iframe_url(http_json("POST", "/sessions", body))
    print_out(data, args, f"Launched non-interactive job {data.get('name') or data.get('id') or ''}")


def cmd_session_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/sessions", query={"session_type": args.type, "page": getattr(args, "page", None), "per_page": getattr(args, "per_page", None)}), args)


def cmd_session_get(args: argparse.Namespace) -> None:
    print_out(add_iframe_url(http_json("GET", f"/sessions/{args.session}")), args)


def cmd_session_logs(args: argparse.Namespace) -> None:
    print_out(http_json("GET", f"/sessions/{args.session}/logs"), args)


def cmd_session_delete(args: argparse.Namespace) -> None:
    confirm(args, f"Stop/delete session {args.session}?")
    if args.dry_run:
        print_out({"DELETE": f"/sessions/{args.session}"}, args); return
    print_out(http_json("DELETE", f"/sessions/{args.session}"), args, "Session deleted")


def extract_log_tail(logs: Any, lines: int = 5) -> str:
    if isinstance(logs, dict):
        text = "\n".join(str(v) for v in logs.values())
    else:
        text = str(logs)
    useful = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(useful[-lines:])


def cmd_session_wait(args: argparse.Namespace) -> None:
    if getattr(args, "wait_for_job", False):
        terminal = {"succeeded", "completed", "finished", "failed", "error", "stopped"}
        success = {"succeeded", "completed", "finished"}
    else:
        terminal = {"running", "succeeded", "completed", "finished", "failed", "error", "stopped"}
        success = {"running", "succeeded", "completed", "finished"}
    end = time.time() + args.timeout
    last_state = None
    session: dict[str, Any] = {}
    while time.time() < end:
        session = add_iframe_url(http_json("GET", f"/sessions/{args.session}"))
        status = session.get("status") or {}
        state = status.get("state") or session.get("state") or "unknown"
        ready = f"{status.get('ready_containers', '?')}/{status.get('total_containers', '?')}"
        if not args.json and (args.verbose or state != last_state):
            print(f"session {args.session}: {state} containers={ready}")
        if args.logs:
            try:
                tail = extract_log_tail(http_json("GET", f"/sessions/{args.session}/logs"), args.log_lines)
                if tail and not args.json:
                    print(tail)
            except Exception as e:
                if args.verbose and not args.json:
                    print(f"logs unavailable: {e}")
        if state in terminal:
            print_out(session, args)
            if state not in success:
                raise RenkuError(f"Session/job {args.session} ended with state: {state}")
            return
        last_state = state
        time.sleep(args.interval)
    raise RenkuError(f"Timed out waiting for session/job {args.session}")


def add_common(p):
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")


def normalize_argv(argv):
    """Allow global flags before or after subcommands for agent convenience."""
    if argv is None:
        argv = sys.argv[1:]
    global_flags = {"--json", "--quiet", "--dry-run", "--yes"}
    front, rest = [], []
    for a in argv:
        (front if a in global_flags else rest).append(a)
    return front + rest


def main(argv=None) -> int:
    argv = normalize_argv(argv)
    root = argparse.ArgumentParser(description="Renku Agent Skill helper")
    add_common(root)
    sub = root.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auth"); s = p.add_subparsers(dest="auth_cmd", required=True)
    q=s.add_parser("login"); q.add_argument("--scope", default="openid profile email offline_access"); q.add_argument("--method", choices=["auto","rnk","device"], default="auto", help="auto prefers official rnk login, then falls back to built-in device flow"); q.add_argument("--user-code-only", action="store_true", help="print device URL/code and save state, but do not poll"); q.add_argument("--no-rnk", action="store_true", help="skip official rnk login even in auto mode"); q.set_defaults(func=cmd_auth_login)
    q=s.add_parser("complete"); q.add_argument("--state", help="path to pending device-flow state file"); q.set_defaults(func=cmd_auth_complete)
    q=s.add_parser("status"); q.set_defaults(func=cmd_auth_status)
    q=s.add_parser("logout"); q.set_defaults(func=cmd_auth_logout)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    q=sub.add_parser("api"); q.add_argument("method"); q.add_argument("path"); q.add_argument("--body"); q.add_argument("--payload"); q.add_argument("--no-auth", action="store_true"); q.set_defaults(func=cmd_api)
    sub.add_parser("user").set_defaults(func=cmd_user)
    sub.add_parser("namespaces").set_defaults(func=cmd_namespaces)
    sub.add_parser("resource-pools").set_defaults(func=cmd_pools)
    q=sub.add_parser("resource-classes"); q.add_argument("--pool"); q.set_defaults(func=cmd_classes)
    q=sub.add_parser("search"); q.add_argument("query"); q.add_argument("--type"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_search)

    p=sub.add_parser("project"); sp=p.add_subparsers(dest="project_cmd", required=True)
    q=sp.add_parser("list"); q.add_argument("--search"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_project_list)
    q=sp.add_parser("get"); q.add_argument("project"); q.set_defaults(func=cmd_project_get)
    q=sp.add_parser("create"); q.add_argument("--name", required=False); q.add_argument("--namespace"); q.add_argument("--slug"); q.add_argument("--visibility"); q.add_argument("--description"); q.add_argument("--documentation"); q.add_argument("--keyword", action="append"); q.add_argument("--repository", action="append"); q.add_argument("--secrets-mount-directory"); q.add_argument("--body"); q.add_argument("--payload"); q.set_defaults(func=cmd_project_create)
    dp=sp.add_parser("documentation"); dsp=dp.add_subparsers(dest="documentation_cmd", required=True)
    q=dsp.add_parser("get"); q.add_argument("project"); q.set_defaults(func=cmd_project_documentation_get)
    q=dsp.add_parser("set"); q.add_argument("project"); q.add_argument("--content"); q.add_argument("--file"); q.set_defaults(func=cmd_project_documentation_set)
    rp=sp.add_parser("repo"); rsp=rp.add_subparsers(dest="repo_cmd", required=True)
    q=rsp.add_parser("list"); q.add_argument("--project", required=True); q.set_defaults(func=cmd_project_repo_list)
    q=rsp.add_parser("add"); q.add_argument("--project", required=True); q.add_argument("--url", required=True); q.add_argument("--ref"); q.set_defaults(func=cmd_project_repo_add)
    q=rsp.add_parser("remove"); q.add_argument("--project", required=True); q.add_argument("--repository", required=True); q.set_defaults(func=cmd_project_repo_remove)
    mp=sp.add_parser("members"); msp=mp.add_subparsers(dest="members_cmd", required=True)
    q=msp.add_parser("list"); q.add_argument("--project", required=True); q.set_defaults(func=cmd_project_members_list)
    q=msp.add_parser("add"); q.add_argument("--project", required=True); q.add_argument("--user", required=True); q.add_argument("--role", required=True, choices=["owner","editor","viewer"]); q.set_defaults(func=cmd_project_members_add)
    q=msp.add_parser("remove"); q.add_argument("--project", required=True); q.add_argument("--user", required=True); q.set_defaults(func=cmd_project_members_remove)

    p=sub.add_parser("connector"); sp=p.add_subparsers(dest="connector_cmd", required=True)
    q=sp.add_parser("list"); q.add_argument("--search"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_connector_list)
    q=sp.add_parser("get"); q.add_argument("connector"); q.set_defaults(func=cmd_connector_get)
    q=sp.add_parser("create"); q.add_argument("kind", choices=["doi","zenodo","dataverse","s3","polybox","switchdrive","webdav"]); q.add_argument("--name"); q.add_argument("--namespace"); q.add_argument("--slug"); q.add_argument("--visibility"); q.add_argument("--description"); q.add_argument("--target-path", default="data"); q.add_argument("--source-path"); q.add_argument("--doi"); q.add_argument("--url"); q.add_argument("--global", dest="global_connector", action="store_true"); q.add_argument("--bucket"); q.add_argument("--endpoint"); q.add_argument("--provider"); q.add_argument("--readonly", action="store_true", default=True); q.add_argument("--no-readonly", dest="readonly", action="store_false"); q.add_argument("--access", choices=["personal","shared"], default="personal"); q.add_argument("--username"); q.add_argument("--password", help="Connector password/token (or set RENKU_CONNECTOR_PASSWORD)"); q.add_argument("--access-key-id", help="S3 access key ID (or set RENKU_S3_ACCESS_KEY_ID)"); q.add_argument("--secret-access-key", help="S3 secret access key (or set RENKU_S3_SECRET_ACCESS_KEY)"); q.add_argument("--body"); q.add_argument("--payload"); q.set_defaults(func=cmd_connector_create)
    q=sp.add_parser("link"); q.add_argument("--connector", required=True); q.add_argument("--project", required=True); q.set_defaults(func=cmd_connector_link)
    q=sp.add_parser("unlink"); q.add_argument("--connector", required=True); q.add_argument("--link", required=True); q.set_defaults(func=cmd_connector_unlink)
    q=sp.add_parser("delete"); q.add_argument("connector"); q.set_defaults(func=cmd_connector_delete)

    for name,path,key in [("launcher","/session_launchers","launcher"),("environment","/environments","environment")]:
        p=sub.add_parser(name); sp=p.add_subparsers(dest=f"{name}_cmd", required=True)
        funcs=simple_crud(key,path)
        q=sp.add_parser("list"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=funcs[0])
        q=sp.add_parser("get"); q.add_argument(key); q.set_defaults(func=funcs[1])
        q=sp.add_parser("create"); q.add_argument("--body", required=True); q.add_argument("--payload"); q.set_defaults(func=funcs[2])
        q=sp.add_parser("patch"); q.add_argument(key); q.add_argument("--body", required=True); q.add_argument("--payload"); q.set_defaults(func=funcs[3])
        q=sp.add_parser("delete"); q.add_argument(key); q.set_defaults(func=funcs[4])
        if name == "launcher":
            q=sp.add_parser("project-list"); q.add_argument("--project", required=True); q.set_defaults(func=cmd_launcher_project_list)

    p=sub.add_parser("build"); sp=p.add_subparsers(dest="build_cmd", required=True)
    q=sp.add_parser("list"); q.add_argument("--environment", required=True); q.set_defaults(func=lambda a: print_out(http_json("GET", f"/environments/{a.environment}/builds"), a))
    q=sp.add_parser("start"); q.add_argument("--environment", required=True); q.set_defaults(func=lambda a: print_out(http_json("POST", f"/environments/{a.environment}/builds"), a))
    q=sp.add_parser("get"); q.add_argument("build"); q.set_defaults(func=lambda a: print_out(http_json("GET", f"/builds/{a.build}"), a))
    q=sp.add_parser("logs"); q.add_argument("build"); q.set_defaults(func=lambda a: print_out(http_json("GET", f"/builds/{a.build}/logs"), a))
    q=sp.add_parser("wait"); q.add_argument("build"); q.add_argument("--timeout", type=int, default=1800); q.add_argument("--interval", type=int, default=15); q.add_argument("--logs", action="store_true", default=True); q.add_argument("--no-logs", dest="logs", action="store_false"); q.add_argument("--log-lines", type=int, default=10); q.add_argument("--verbose", action="store_true"); q.set_defaults(func=cmd_build_wait)

    p=sub.add_parser("session"); sp=p.add_subparsers(dest="session_cmd", required=True)
    q=sp.add_parser("launch"); q.add_argument("--launcher", required=True); q.add_argument("--disk-storage", type=int); q.add_argument("--resource-class-id", type=int); q.set_defaults(func=cmd_session_launch)
    q=sp.add_parser("list"); q.add_argument("--type", choices=["interactive","non-interactive"], default="interactive"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_session_list)
    q=sp.add_parser("get"); q.add_argument("session"); q.set_defaults(func=cmd_session_get)
    q=sp.add_parser("logs"); q.add_argument("session"); q.set_defaults(func=cmd_session_logs)
    q=sp.add_parser("delete"); q.add_argument("session"); q.set_defaults(func=cmd_session_delete)
    q=sp.add_parser("wait"); q.add_argument("session"); q.add_argument("--timeout", type=int, default=900); q.add_argument("--interval", type=int, default=10); q.add_argument("--logs", action="store_true"); q.add_argument("--log-lines", type=int, default=5); q.add_argument("--verbose", action="store_true"); q.set_defaults(func=cmd_session_wait)

    p=sub.add_parser("job"); sp=p.add_subparsers(dest="job_cmd", required=True)
    q=sp.add_parser("run"); q.add_argument("--launcher", required=True); q.add_argument("--disk-storage", type=int); q.add_argument("--resource-class-id", type=int); q.set_defaults(func=cmd_job_run)
    q=sp.add_parser("list"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(type="non-interactive", func=cmd_session_list)
    q=sp.add_parser("logs"); q.add_argument("session"); q.set_defaults(func=cmd_session_logs)
    q=sp.add_parser("stop"); q.add_argument("session"); q.set_defaults(func=cmd_session_delete)
    q=sp.add_parser("wait"); q.add_argument("session"); q.add_argument("--timeout", type=int, default=900); q.add_argument("--interval", type=int, default=10); q.add_argument("--logs", action="store_true", default=True); q.add_argument("--log-lines", type=int, default=8); q.add_argument("--verbose", action="store_true"); q.set_defaults(func=cmd_session_wait, wait_for_job=True)

    args = root.parse_args(argv)
    try:
        # Validate required unless JSON body supplied.
        if getattr(args, "cmd", None)=="project" and getattr(args,"project_cmd",None)=="create" and not (args.body or args.payload) and not (args.name and args.namespace):
            raise RenkuError("project create needs --name and --namespace, or --body/--payload")
        if getattr(args, "cmd", None)=="connector" and getattr(args,"connector_cmd",None)=="create" and not (args.body or args.payload) and not args.name and getattr(args, "kind", None) not in ("doi", "zenodo", "dataverse"):
            raise RenkuError("connector create needs --name for non-DOI connectors, or --body/--payload")
        args.func(args)
        return 0
    except RenkuError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
