---
name: renku
description: Manage Renku/RenkuLab projects and assets. Use for Renku authentication, account/platform inspection, creating/listing projects, linking code repositories, managing data connectors, session launchers, environments/builds, interactive sessions, and non-interactive jobs on renkulab.io or another Renku deployment.
---

# Renku

Use this skill when the user asks to work with the Renku platform, especially `https://renkulab.io` or `https://dev.renku.ch`.

The skill is workflow-first. Prefer high-level commands in `scripts/renku_agent.py`; use the generic API escape hatch only when no workflow command exists.

## Helper

From the skill directory:

```bash
python3 scripts/renku_agent.py --help
```

Default instance is `https://renkulab.io`. Override with:

```bash
export RENKU_BASE_URL=https://dev.renku.ch
```

The helper is dependency-free Python stdlib. It checks for the official Renku CLI as `rnk` and reports it in `doctor`. `rnk` is used for authentication (`rnk login`) and token import/reuse. Job commands (`job run/list/logs/stop`) support `--backend auto|api|rnk`; `auto` tries `rnk job start/list/logs/stop` for simple job operations and falls back to the API if `rnk` fails or does not support the requested options. Project, connector, launcher, and session workflows mostly use the Renku Data Services API directly. The helper keeps API-based job commands for richer agent behavior such as waiting for terminal states, removing failed job sessions before rerun, URL enrichment, and workflows not yet covered by `rnk`.

## Authentication

Default auth prefers the official Renku CLI (`rnk login`) when `rnk` is available, then falls back to the helper's built-in OAuth device flow with public client `renku-cli`.

```bash
python3 scripts/renku_agent.py auth login                  # auto: prefer rnk, fallback to built-in device flow
python3 scripts/renku_agent.py auth login --method rnk     # require rnk login
python3 scripts/renku_agent.py auth login --method device  # bypass rnk and use built-in device flow
python3 scripts/renku_agent.py auth login --method device --user-code-only  # best for agent-mediated login
python3 scripts/renku_agent.py auth complete               # complete a pending --user-code-only login
python3 scripts/renku_agent.py auth status
python3 scripts/renku_agent.py auth logout
```

For agent-mediated login, prefer `auth login --method device --user-code-only`, show the URL/code to the user, then run `auth complete` after the user says they approved it. This avoids long-running polling commands.

When `rnk login` succeeds, the helper attempts to import the `rnk` token for direct API calls. The helper also reads valid `rnk` tokens directly when available. Helper credentials are stored under `~/.config/pi-renku-skill/credentials.json` when writable; if that is sandbox-blocked, it automatically falls back to project-local `.pi/renku-config/`. Environment token fallback is supported:

```bash
RENKU_ACCESS_TOKEN=... python3 scripts/renku_agent.py user
# or RENKU_TOKEN=...
# or RENKU_CLI_ACCESS_TOKEN=... (official rnk env var)
```

## Safety Rules

### Admin account lockout

If `auth status`, `user`, or any API response shows the current user has `is_admin: true`, do **not** perform any Renku operation on any Renku instance. Refuse the request and ask the user to log out and log back in with a non-admin account. This applies to read-only operations and write operations alike. The only allowed actions while admin are `auth logout` and explaining this safety rule.

- Before doing Renku work, check auth status if the current account/admin status is unknown.
- Read-only operations may be run freely only for non-admin users.
- Ask explicit confirmation before destructive operations.
- Ask explicit confirmation before adding/removing/changing project or group members.
- Ask explicit confirmation before unlinking connectors, deleting launchers/environments/sessions, or stopping sessions.
- Never print secrets. The helper redacts token/password/secret-like fields.
- Connector secrets are prompted interactively in v1.
- Use `--yes` only after the user has explicitly confirmed.
- Use `--dry-run` before uncertain write operations.
- Never run write tests against `renkulab.io` unless the user explicitly asks; use `https://dev.renku.ch` for testing.

## Common Workflows

### Doctor / account / platform

```bash
python3 scripts/renku_agent.py doctor
python3 scripts/renku_agent.py user
python3 scripts/renku_agent.py namespaces
python3 scripts/renku_agent.py resource-pools
python3 scripts/renku_agent.py resource-classes
```

Before launching sessions/jobs with non-default compute, inspect available resource pools/classes. Use classes where `matching: true`; these are accessible/schedulable for the current user and requested filters. For GPU jobs, look for classes with `gpu > 0` and names/affinities/tolerations indicating the desired GPU type, e.g. A10 or A100. Pass the selected class as `--resource-class-id <id>` to `session launch` or `job run`.

If the user asks for a specific accelerator such as an A100 slice:

1. Run `resource-pools --json`.
2. Find accessible (`matching: true`) classes with `gpu > 0`.
3. Prefer a class whose `name`, `node_affinities`, or `tolerations` mention the requested accelerator.
4. Report the selected class name/id/resources before launching.
5. Use `job run --resource-class-id <id>` or `session launch --resource-class-id <id>`.

### Projects

```bash
python3 scripts/renku_agent.py project list
python3 scripts/renku_agent.py project get <project-id-or-namespace/slug>
python3 scripts/renku_agent.py project create --name "My Project" --namespace <namespace> --visibility private
```

Add repositories as project-level assets:

```bash
python3 scripts/renku_agent.py project repo list --project <project-id>
python3 scripts/renku_agent.py project repo add --project <project-id> --url https://github.com/org/repo.git
python3 scripts/renku_agent.py project repo remove --project <project-id> --repository https://github.com/org/repo.git
```

Repository permissions and credentials are handled by Renku integrations; do not ask the user for raw Git tokens unless they explicitly request a lower-level workaround.

### Data Connectors

```bash
python3 scripts/renku_agent.py connector list
python3 scripts/renku_agent.py connector get <connector-id>
python3 scripts/renku_agent.py connector link --connector <connector-id> --project <project-id>
python3 scripts/renku_agent.py connector unlink --connector <connector-id> --link <link-id>
```

Create supported P0 connector types:

```bash
# DOI / Zenodo / Dataverse global connector
python3 scripts/renku_agent.py connector create doi --name "Dataset" --doi "10.xxxx/..." --target-path /data --global

# S3/S3-compatible; prompts for secrets
python3 scripts/renku_agent.py connector create s3 --name "S3 Data" --bucket my-bucket --endpoint https://s3.example.org --target-path /data

# Polybox / SWITCHdrive personal or shared; prompts for secrets when needed
# target paths are relative to the session working directory: use data, not /data
python3 scripts/renku_agent.py connector create polybox --name "Polybox" --access personal --target-path data
python3 scripts/renku_agent.py connector create switchdrive --name "Shared SWITCHdrive" --access shared --url <public-link> --target-path data
```

For exact payload control, use `--body FILE` or `--payload JSON`.

### Launchers / Environments / Builds

Renku supports two main kinds of session launcher environments:

1. **Existing image / global environment**: the launcher references an existing environment by ID or defines a container image directly.
2. **Build from code**: the launcher asks Renku to build an environment image from a project code repository. Use this when a repository contains dependency files such as `requirements.txt`, `environment.yml`, `pyproject.toml`, `DESCRIPTION`, `renv.lock`, or a Docker/build configuration. For Python repositories with notebooks, prefer `builder_variant: "python"` and `frontend_variant: "jupyterlab"`.

When the user gives a Git repository with Python dependency files, offer this workflow:

1. Create a Renku project with the repository in the project's `repositories` list.
2. Create a session launcher with build-from-code using the same repository.
3. Use the JupyterLab frontend for notebook/Python repos.
4. Optionally start a build/session after the launcher is created.

Build-from-code launcher body example:

```json
{
  "project_id": "<project-id>",
  "name": "JupyterLab from repository",
  "description": "Builds a JupyterLab environment from the linked repository.",
  "environment": {
    "environment_image_source": "build",
    "repository": "https://github.com/org/repo.git",
    "builder_variant": "python",
    "frontend_variant": "jupyterlab",
    "repository_revision": "main",
    "context_dir": ".",
    "platforms": ["linux/amd64"]
  }
}
```

Known build variants/frontends:

- Python builder: `builder_variant: "python"`
- Python-compatible frontends: `jupyterlab`, `vscodium`, `ttyd`
- R builder, when available: `builder_variant: "r"`, usually with `frontend_variant: "rstudio"`

Launcher and environment commands:

```bash
python3 scripts/renku_agent.py launcher list
python3 scripts/renku_agent.py launcher project-list --project <project-id>
python3 scripts/renku_agent.py launcher get <launcher-id>
python3 scripts/renku_agent.py launcher create --body launcher.json
python3 scripts/renku_agent.py launcher patch <launcher-id> --body patch.json
python3 scripts/renku_agent.py launcher delete <launcher-id>

python3 scripts/renku_agent.py environment list
python3 scripts/renku_agent.py environment get <environment-id>
python3 scripts/renku_agent.py environment create --body environment.json

python3 scripts/renku_agent.py build list --environment <environment-id>
python3 scripts/renku_agent.py build start --environment <environment-id>
python3 scripts/renku_agent.py build get <build-id>
python3 scripts/renku_agent.py build logs <build-id>
python3 scripts/renku_agent.py build wait <build-id>
```

Use `build wait <build-id>` instead of writing ad-hoc polling loops. It polls the build status, prints concise build-log progress by default, and exits when the build reaches `succeeded`, `failed`, or `error`. This is preferred when users are waiting for build-from-code images because it reports what is happening, e.g. repository clone, dependency resolution, package installation, image export/push.

### Sessions and Non-interactive Jobs

Interactive sessions and non-interactive jobs both use `POST /sessions`. Jobs set `session_type` to `non-interactive`.

```bash
python3 scripts/renku_agent.py session launch --launcher <launcher-id>
python3 scripts/renku_agent.py session launch --launcher <launcher-id> --type non-interactive
python3 scripts/renku_agent.py job run --launcher <launcher-id>

python3 scripts/renku_agent.py session list
python3 scripts/renku_agent.py job list
python3 scripts/renku_agent.py session get <session-id>
python3 scripts/renku_agent.py session logs <session-id>
python3 scripts/renku_agent.py session wait <session-id> --logs
python3 scripts/renku_agent.py job wait <job-session-id>
python3 scripts/renku_agent.py session delete <session-id>
```

When presenting a URL to the user, prefer the Renku project iframe URL rather than the raw backend session URL. If the project namespace/slug and session name are known, construct:

```text
<base>/p/<namespace>/<project-slug>/sessions/show/<session-name>
```

Example:

```text
https://dev.renku.ch/p/rokroskar/renku-demo-air-quality-analysis/sessions/show/rokroskar-e9d5c7d09604
```

The raw session URL returned by the API, e.g. `<base>/sessions/<session-name>/lab/`, is useful for diagnostics but may not be the best URL to give users.

Use `job wait <job-session-id>` or `session wait <session-id> --logs` instead of writing ad-hoc polling loops. These commands poll status, optionally show log tails, and stop at terminal states.

To convert a successful build-from-code launcher into a non-interactive job launcher:

1. Get the launcher and use the built `environment.container_image`.
2. Patch the environment to `environment_image_source: "image"` and `environment_kind: "CUSTOM"`.
3. Set `command` to `["/cnb/lifecycle/launcher"]` to initialize the CNB launch environment.
4. Set `args` to the batch command. For notebook execution, prefer `python -c` invoking `jupyter nbconvert` over complex shell quoting.
5. Run with `job run --launcher <launcher-id>`.

If preserving the original interactive launcher matters, create a separate launcher instead of patching it in place. See `references/workflows.md` for a full build-from-code-to-job patch example.

Before rerunning a non-interactive job from the same launcher/project, check for an existing failed/stopped job session with the same session name. Remove failed job sessions before starting a new one, otherwise Renku may reuse or conflict with the previous failed session. Deleting failed/stopped job sessions is allowed when the user explicitly asks to rerun the job; mention what is being removed.

Confirm before deleting/stopping running interactive sessions unless the user has already explicitly approved.

### Generic API Escape Hatch

```bash
python3 scripts/renku_agent.py api GET /projects
python3 scripts/renku_agent.py api POST /projects --body project.json
python3 scripts/renku_agent.py api PATCH /projects/<id> --payload '{"description":"new"}'
```

## References

- Design notes: `RENKU_SKILL_DESIGN.md`
- Workflow reference: `references/workflows.md`
- Auth reference: `references/auth.md`
- Testing reference: `references/testing.md`
- API overview: `references/api-overview.md`
