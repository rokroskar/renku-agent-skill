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
    preferred = Path.home() / ".config" / "pi-renku-skill"
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
REDACT_KEYS = {"access_token", "refresh_token", "id_token", "token", "password", "secret", "client_secret"}


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


def assert_not_admin() -> None:
    if not admin_check_enabled():
        return
    try:
        user = http_json("GET", "/user", auth=True, skip_admin_check=True)
        if isinstance(user, dict) and user.get("is_admin") is True:
            raise RenkuError("Refusing to operate on Renku while authenticated as an admin user. Run auth logout and log in with a non-admin account.")
    except RenkuError:
        raise
    except Exception:
        # If user status cannot be determined, continue and let the original request surface auth/API errors.
        return


def http_json(method: str, path_or_url: str, body: Any = None, auth: bool = True, absolute: bool = False, query: Optional[dict[str, Any]] = None, skip_admin_check: bool = False) -> Any:
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
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            if not raw:
                return {"status": resp.status}
            return json.loads(raw)
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
    if getattr(args, "body", None):
        if args.body == "-":
            return json.load(sys.stdin)
        return json.loads(Path(args.body).read_text())
    if getattr(args, "payload", None):
        return json.loads(args.payload)
    raise RenkuError("A JSON body is required via --body FILE, --body -, or --payload JSON")


def confirm(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "yes", False):
        return
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


def access_token_for_subprocess() -> Optional[str]:
    env = token_from_env()
    if env:
        return env
    rnk_tok = load_rnk_token()
    if rnk_tok:
        return rnk_tok["tokens"].get("access_token")
    inst = get_instance_creds()
    if inst:
        return (inst.get("tokens") or {}).get("access_token")
    return None


def run_rnk(args: list[str]) -> tuple[int, str, str]:
    exe = try_rnk()
    if not exe:
        return 127, "", "rnk not found"
    assert_not_admin()
    env = os.environ.copy()
    env["RENKU_CLI_RENKU_URL"] = base_url()
    token = access_token_for_subprocess()
    if token:
        env["RENKU_CLI_ACCESS_TOKEN"] = token
    cmd = [exe, "--renku-url", base_url(), "--format", "json", *args]
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {"output": text.strip()}


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


def cmd_project_list(args: argparse.Namespace) -> None:
    print_out(http_json("GET", "/projects", query={"name": args.search, "page": args.page, "per_page": args.per_page}), args)


def cmd_project_get(args: argparse.Namespace) -> None:
    ident = args.project
    if "/" in ident and not ident.startswith("01"):
        ns, slug = ident.split("/", 1)
        path = f"/namespaces/{urllib.parse.quote(ns)}/projects/{urllib.parse.quote(slug)}"
    else:
        path = f"/projects/{urllib.parse.quote(ident)}"
    print_out(http_json("GET", path), args)


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


def cmd_project_repo_list(args: argparse.Namespace) -> None:
    proj = http_json("GET", f"/projects/{args.project}")
    print_out(proj.get("repositories", []), args)


def cmd_project_repo_add(args: argparse.Namespace) -> None:
    proj = http_json("GET", f"/projects/{args.project}")
    repos = list(proj.get("repositories") or [])
    if args.url not in repos:
        repos.append(args.url)
    body = {"repositories": repos}
    if args.dry_run:
        print_out({"PATCH": f"/projects/{args.project}", "body": body}, args); return
    data = http_json("PATCH", f"/projects/{args.project}", body)
    print_out(data, args, f"Updated repositories for project {args.project}")


def cmd_project_repo_remove(args: argparse.Namespace) -> None:
    confirm(args, f"Remove repository {args.repository} from project {args.project}?")
    proj = http_json("GET", f"/projects/{args.project}")
    repos = [r for r in (proj.get("repositories") or []) if r != args.repository]
    body = {"repositories": repos}
    if args.dry_run:
        print_out({"PATCH": f"/projects/{args.project}", "body": body}, args); return
    data = http_json("PATCH", f"/projects/{args.project}", body)
    print_out(data, args, f"Removed repository from project {args.project}")


def connector_storage(args: argparse.Namespace) -> dict[str, Any]:
    target = args.target_path
    if args.kind in ("doi", "zenodo", "dataverse"):
        return {"storage_url": args.doi or args.url, "target_path": target, "readonly": True}
    if args.kind == "s3":
        cfg = {"type": "s3", "provider": args.provider or "Other", "endpoint": args.endpoint or "", "access_key_id": input("S3 access key id: "), "secret_access_key": getpass.getpass("S3 secret access key: ")}
        return {"configuration": cfg, "source_path": args.source_path or args.bucket or "/", "target_path": target, "readonly": args.readonly}
    if args.kind in ("polybox", "switchdrive"):
        cfg = {"type": args.kind, "provider": args.access}
        if args.access == "shared":
            cfg["url"] = args.url or input("Public/share link: ")
            pw = getpass.getpass("Share password (leave empty if none): ")
            if pw: cfg["pass"] = pw
        else:
            cfg["user"] = args.username or input("Username: ")
            cfg["pass"] = getpass.getpass("Password/token: ")
            if args.url: cfg["url"] = args.url
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
        body = {"name": args.name, "storage": connector_storage(args)}
        if args.namespace: body["namespace"] = args.namespace
        if args.slug: body["slug"] = args.slug
        if args.visibility: body["visibility"] = args.visibility
        if args.description: body["description"] = args.description
    path = "/data_connectors/global" if args.global_connector else "/data_connectors"
    if args.dry_run:
        print_out({"POST": path, "body": redact(body)}, args); return
    data = http_json("POST", path, body)
    print_out(data, args, f"Created data connector {data.get('name')} ({data.get('id')})")


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


def simple_crud(resource: str, path: str):
    def list_cmd(args): print_out(http_json("GET", path), args)
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


def cmd_session_launch(args: argparse.Namespace) -> None:
    use_rnk = getattr(args, "backend", "api") in {"auto", "rnk"} and getattr(args, "type", "interactive") == "non-interactive" and args.disk_storage is None and args.resource_class_id is None
    if use_rnk:
        if args.dry_run:
            print_out({"rnk": ["job", "start", args.launcher]}, args); return
        rc, out, err = run_rnk(["job", "start", args.launcher])
        if rc == 0:
            data = maybe_json(out)
            if isinstance(data, dict):
                data = add_iframe_url(data)
            print_out(data, args, f"Started job with rnk from launcher {args.launcher}")
            return
        if getattr(args, "backend", "api") == "rnk":
            raise RenkuError(f"rnk job start failed: {err.strip() or out.strip()}")
        if not args.json:
            print(f"rnk job start failed; falling back to API: {err.strip() or out.strip()}", file=sys.stderr)
    body = {"launcher_id": args.launcher}
    if args.type: body["session_type"] = args.type
    if args.disk_storage is not None: body["disk_storage"] = args.disk_storage
    if args.resource_class_id is not None: body["resource_class_id"] = args.resource_class_id
    if args.dry_run:
        print_out({"POST": "/sessions", "body": body}, args); return
    data = add_iframe_url(http_json("POST", "/sessions", body))
    print_out(data, args, f"Launched {body.get('session_type','interactive')} session {data.get('name') or data.get('id') or ''}")


def cmd_session_list(args: argparse.Namespace) -> None:
    use_rnk = getattr(args, "backend", "api") in {"auto", "rnk"} and getattr(args, "type", "interactive") == "non-interactive"
    if use_rnk:
        rc, out, err = run_rnk(["job", "list"])
        if rc == 0:
            print_out(maybe_json(out), args)
            return
        if getattr(args, "backend", "api") == "rnk":
            raise RenkuError(f"rnk job list failed: {err.strip() or out.strip()}")
        if not args.json:
            print(f"rnk job list failed; falling back to API: {err.strip() or out.strip()}", file=sys.stderr)
    print_out(http_json("GET", "/sessions", query={"session_type": args.type}), args)


def cmd_session_get(args: argparse.Namespace) -> None:
    print_out(add_iframe_url(http_json("GET", f"/sessions/{args.session}")), args)


def cmd_session_logs(args: argparse.Namespace) -> None:
    use_rnk = getattr(args, "backend", "api") in {"auto", "rnk"} and getattr(args, "job", False)
    if use_rnk:
        rc, out, err = run_rnk(["job", "logs", args.session])
        if rc == 0:
            print_out(maybe_json(out), args)
            return
        if getattr(args, "backend", "api") == "rnk":
            raise RenkuError(f"rnk job logs failed: {err.strip() or out.strip()}")
        if not args.json:
            print(f"rnk job logs failed; falling back to API: {err.strip() or out.strip()}", file=sys.stderr)
    print_out(http_json("GET", f"/sessions/{args.session}/logs"), args)


def cmd_session_delete(args: argparse.Namespace) -> None:
    confirm(args, f"Stop/delete session {args.session}?")
    use_rnk = getattr(args, "backend", "api") in {"auto", "rnk"} and getattr(args, "job", False)
    if use_rnk:
        if args.dry_run:
            print_out({"rnk": ["job", "stop", args.session]}, args); return
        rc, out, err = run_rnk(["job", "stop", args.session])
        if rc == 0:
            print_out(maybe_json(out) if out.strip() else {"status": "stopped", "job": args.session}, args)
            return
        if getattr(args, "backend", "api") == "rnk":
            raise RenkuError(f"rnk job stop failed: {err.strip() or out.strip()}")
        if not args.json:
            print(f"rnk job stop failed; falling back to API delete: {err.strip() or out.strip()}", file=sys.stderr)
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
                return
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

    p=sub.add_parser("project"); sp=p.add_subparsers(dest="project_cmd", required=True)
    q=sp.add_parser("list"); q.add_argument("--search"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_project_list)
    q=sp.add_parser("get"); q.add_argument("project"); q.set_defaults(func=cmd_project_get)
    q=sp.add_parser("create"); q.add_argument("--name", required=False); q.add_argument("--namespace"); q.add_argument("--slug"); q.add_argument("--visibility"); q.add_argument("--description"); q.add_argument("--documentation"); q.add_argument("--keyword", action="append"); q.add_argument("--repository", action="append"); q.add_argument("--secrets-mount-directory"); q.add_argument("--body"); q.add_argument("--payload"); q.set_defaults(func=cmd_project_create)
    rp=sp.add_parser("repo"); rsp=rp.add_subparsers(dest="repo_cmd", required=True)
    q=rsp.add_parser("list"); q.add_argument("--project", required=True); q.set_defaults(func=cmd_project_repo_list)
    q=rsp.add_parser("add"); q.add_argument("--project", required=True); q.add_argument("--url", required=True); q.set_defaults(func=cmd_project_repo_add)
    q=rsp.add_parser("remove"); q.add_argument("--project", required=True); q.add_argument("--repository", required=True); q.set_defaults(func=cmd_project_repo_remove)

    p=sub.add_parser("connector"); sp=p.add_subparsers(dest="connector_cmd", required=True)
    q=sp.add_parser("list"); q.add_argument("--search"); q.add_argument("--page", type=int); q.add_argument("--per-page", type=int); q.set_defaults(func=cmd_connector_list)
    q=sp.add_parser("get"); q.add_argument("connector"); q.set_defaults(func=cmd_connector_get)
    q=sp.add_parser("create"); q.add_argument("kind", choices=["doi","zenodo","dataverse","s3","polybox","switchdrive"]); q.add_argument("--name"); q.add_argument("--namespace"); q.add_argument("--slug"); q.add_argument("--visibility"); q.add_argument("--description"); q.add_argument("--target-path", default="/data"); q.add_argument("--source-path"); q.add_argument("--doi"); q.add_argument("--url"); q.add_argument("--global", dest="global_connector", action="store_true"); q.add_argument("--bucket"); q.add_argument("--endpoint"); q.add_argument("--provider"); q.add_argument("--readonly", action="store_true", default=True); q.add_argument("--access", choices=["personal","shared"], default="personal"); q.add_argument("--username"); q.add_argument("--body"); q.add_argument("--payload"); q.set_defaults(func=cmd_connector_create)
    q=sp.add_parser("link"); q.add_argument("--connector", required=True); q.add_argument("--project", required=True); q.set_defaults(func=cmd_connector_link)
    q=sp.add_parser("unlink"); q.add_argument("--connector", required=True); q.add_argument("--link", required=True); q.set_defaults(func=cmd_connector_unlink)

    for name,path,key in [("launcher","/session_launchers","launcher"),("environment","/environments","environment")]:
        p=sub.add_parser(name); sp=p.add_subparsers(dest=f"{name}_cmd", required=True)
        funcs=simple_crud(key,path)
        sp.add_parser("list").set_defaults(func=funcs[0])
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

    p=sub.add_parser("session"); sp=p.add_subparsers(dest="session_cmd", required=True)
    q=sp.add_parser("launch"); q.add_argument("--launcher", required=True); q.add_argument("--type", choices=["interactive","non-interactive"], default="interactive"); q.add_argument("--disk-storage", type=int); q.add_argument("--resource-class-id", type=int); q.set_defaults(func=cmd_session_launch)
    q=sp.add_parser("list"); q.add_argument("--type", choices=["interactive","non-interactive"], default="interactive"); q.set_defaults(func=cmd_session_list)
    q=sp.add_parser("get"); q.add_argument("session"); q.set_defaults(func=cmd_session_get)
    q=sp.add_parser("logs"); q.add_argument("session"); q.set_defaults(func=cmd_session_logs)
    q=sp.add_parser("delete"); q.add_argument("session"); q.set_defaults(func=cmd_session_delete)
    q=sp.add_parser("wait"); q.add_argument("session"); q.add_argument("--timeout", type=int, default=900); q.add_argument("--interval", type=int, default=10); q.add_argument("--logs", action="store_true"); q.add_argument("--log-lines", type=int, default=5); q.add_argument("--verbose", action="store_true"); q.set_defaults(func=cmd_session_wait)

    p=sub.add_parser("job"); sp=p.add_subparsers(dest="job_cmd", required=True)
    q=sp.add_parser("run"); q.add_argument("--launcher", required=True); q.add_argument("--disk-storage", type=int); q.add_argument("--resource-class-id", type=int); q.add_argument("--backend", choices=["auto","api","rnk"], default="auto", help="auto tries rnk for simple job starts, then falls back to API"); q.set_defaults(type="non-interactive", func=cmd_session_launch)
    q=sp.add_parser("list"); q.add_argument("--backend", choices=["auto","api","rnk"], default="auto"); q.set_defaults(type="non-interactive", func=cmd_session_list)
    q=sp.add_parser("logs"); q.add_argument("session"); q.add_argument("--backend", choices=["auto","api","rnk"], default="auto"); q.set_defaults(func=cmd_session_logs, job=True)
    q=sp.add_parser("stop"); q.add_argument("session"); q.add_argument("--backend", choices=["auto","api","rnk"], default="auto"); q.set_defaults(func=cmd_session_delete, job=True)
    q=sp.add_parser("wait"); q.add_argument("session"); q.add_argument("--timeout", type=int, default=900); q.add_argument("--interval", type=int, default=10); q.add_argument("--logs", action="store_true", default=True); q.add_argument("--log-lines", type=int, default=8); q.add_argument("--verbose", action="store_true"); q.set_defaults(func=cmd_session_wait, wait_for_job=True)

    args = root.parse_args(argv)
    try:
        # Validate required unless JSON body supplied.
        if getattr(args, "cmd", None)=="project" and getattr(args,"project_cmd",None)=="create" and not (args.body or args.payload) and not (args.name and args.namespace):
            raise RenkuError("project create needs --name and --namespace, or --body/--payload")
        if getattr(args, "cmd", None)=="connector" and getattr(args,"connector_cmd",None)=="create" and not (args.body or args.payload) and not args.name:
            raise RenkuError("connector create needs --name, or --body/--payload")
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
