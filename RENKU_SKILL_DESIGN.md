# Renku Agent Skill Design

This document records the initial design decisions for a portable Agent Skill that helps an agent use the Renku platform, with the main production instance at `https://renkulab.io` and testing on `https://dev.renku.ch`.

## Goal

Create a workflow-first skill for managing Renku projects and project assets, including data connectors, code repositories, session launchers, environments/builds, interactive sessions, and non-interactive jobs.

The skill should let the agent carry out higher-level Renku workflows rather than merely expose raw API endpoints. A thin API/helper layer should still exist underneath for composability and as an escape hatch.

## Scope

Version 1 should be **workflow-first** with reusable API primitives underneath.

The skill should support workflows such as:

- Authenticate to a Renku instance.
- Inspect account and platform state.
- Create and inspect projects.
- Add project assets such as data connectors, code repositories, environments, and session launchers.
- Launch, inspect, log, and stop sessions.
- Launch non-interactive jobs via the sessions API.
- Diagnose access, permission, and launch issues.

A generic API escape hatch should be included for unsupported or newly added Renku endpoints.

Example shape:

```bash
python scripts/renku_agent.py api GET /projects
python scripts/renku_agent.py api POST /projects --body payload.json
```

## Authentication

Default authentication should use OAuth device-code flow.

Design decisions:

- Default instance: `https://renkulab.io`
- Test/staging instance: `https://dev.renku.ch`
- OAuth client ID: `renku-cli`
- Client type: public client, no client secret
- Endpoint discovery: helper should discover the relevant OIDC/Keycloak/device endpoints automatically where possible
- Credential storage: user-level config file

```text
~/.config/pi-renku-skill/credentials.json
```

- Credential file permissions should be restricted, e.g. `0600`.
- Multiple instances/accounts should be supported, keyed by base URL.
- `RENKU_BASE_URL` should override the base URL.
- `RENKU_ACCESS_TOKEN` and/or `RENKU_TOKEN` should be supported as environment-token fallback for automation.

Planned auth commands:

```bash
python scripts/renku_agent.py auth login
python scripts/renku_agent.py auth status
python scripts/renku_agent.py auth logout
```

## Implementation Style

Use a portable Agent Skill first, with a Python helper script as the core implementation.

Rationale:

- Keeps the skill portable to other agents such as Claude Code.
- Avoids making pi-specific extensions the core dependency.
- Python is common in data science/Renku environments.
- The helper can use only the Python standard library initially for portability.
- A pi extension can be added later as a nicer wrapper around the Python helper.

Proposed structure:

```text
renku-skill/
├── SKILL.md
├── scripts/
│   └── renku_agent.py
└── references/
    ├── api-overview.md
    ├── auth.md
    ├── testing.md
    └── workflows.md
```

The helper should be an adapter/workflow orchestrator, not a permanent competitor to the official Renku CLI.

There is an official CLI at:

https://github.com/swissdatasciencecenter/renku-cli

Official CLI releases are published at:

https://github.com/swissdatasciencecenter/renku-cli/releases

Design principle:

> Prefer the official `renku-cli` when it supports a workflow reliably; otherwise use the bundled helper. Keep the bundled helper as a compatibility adapter so it can delegate to official CLI commands later.

The skill/helper should try to use `renku-cli` where appropriate. If it is not available locally, the skill may offer to download the latest suitable binary from the GitHub releases page and cache it under the skill/config directory. Downloading or executing a binary should require explicit user approval unless the user has configured an opt-in setting.

Suggested CLI acquisition behavior:

1. Check for an existing `rnk` binary on `PATH` first. The official `renku-cli` typically installs as the `rnk` command-line tool.
2. Optionally also check for `renku` or `renku-cli` aliases/wrappers on `PATH`.
3. Check a skill-managed cache directory, e.g. `~/.config/pi-renku-skill/bin/`.
4. If missing, inspect GitHub releases and offer to download the latest binary matching the current OS/architecture.
5. Verify basic executability with a version/help command such as `rnk --version` or `rnk --help`.
6. Prefer invoking the official CLI for supported operations; fall back to direct API calls for unsupported workflows or when the CLI fails in a known unsupported way.

The Python helper remains the stable interface used by the skill, so the underlying implementation can switch between official CLI delegation and direct API calls without changing agent workflows.

## Optional pi Extension

A pi extension may be useful later for better UX:

- Custom Renku tools callable directly by the LLM.
- `/renku-login` or similar commands.
- Interactive prompts and confirmations.
- Better rendering of Renku project/session results.
- Guardrails for destructive operations.

However, the extension should not be the core implementation because portability matters. It should wrap the portable Python CLI/helper.

## Safety Model

Classify operations as read-only, ordinary writes, destructive, sensitive, or compute/cost-related.

### Read-only operations

No confirmation required.

Examples:

- List/get projects.
- List namespaces.
- List resource pools/classes.
- List/get data connectors.
- List/get launchers/environments/sessions.
- Get logs.
- Search.
- Check permissions.

### Ordinary create/update workflows

Proceed once the user's intent is clear and required fields have been gathered.

Examples:

- Create project.
- Create data connector.
- Link connector to project.
- Create launcher.
- Update project metadata.
- Start a normal session when resource/cost is clear.

The agent should summarize what it is about to do when helpful.

### Explicit confirmation required

Always require explicit user confirmation for:

- Deleting projects.
- Deleting data connectors.
- Unlinking data connectors.
- Deleting session launchers.
- Stopping/deleting sessions.
- Removing users from projects/groups.
- Adding users to projects/groups.
- Changing users' project/group roles.
- Deleting or overwriting secrets.
- Destructive environment/build-related operations.

For confirmation, include the exact resource name/id.

### Sensitive operations

Secrets and credentials must be handled carefully.

Rules:

- Never print secret values.
- Redact secrets in logs, output, errors, dry-run payloads, and saved artifacts.
- For v1, collect connector secrets by prompting interactively.
- Do not read local `.env` or credential files unless the user explicitly asks.
- Confirm before overwriting or deleting existing secrets.

### Compute/cost operations

Launching sessions, builds, deposits, and non-interactive jobs may consume resources.

Require confirmation when:

- Resource class is expensive, GPU-enabled, or unknown.
- The operation may run for a long time.
- The command could mutate external data.

Otherwise, proceed once intent is clear.

CLI flags should include:

```bash
--dry-run
--yes
--json
--quiet
```

The agent should only use `--yes` after obtaining user confirmation.

## P0 Workflows

### Account/platform

- Auth login/status/logout.
- Show current user.
- List namespaces.
- List resource pools/classes.
- `doctor` command for setup/API/auth checks.

### Projects

- Search/list projects.
- Get project details.
- Create project.

### Data connectors

- Search/list data connectors.
- Get data connector details.
- Create data connector.
- Link/unlink existing connectors to projects. Unlink requires confirmation.

### Code repositories

- Connect/link external Git repositories as project-level assets.
- Repositories are expected to be included in `POST /projects` and/or modified via `PATCH /projects/{project_id}`.
- No separate public/private workflow is needed.
- Renku integrations determine repository permissions.
- Repository credentials are proxied automatically into sessions.
- The skill should not handle raw Git tokens for repositories.

Possible command shape:

```bash
python scripts/renku_agent.py project create --repository <url>[#ref]
python scripts/renku_agent.py project repo list --project <project-id>
python scripts/renku_agent.py project repo add --project <project-id> --url <repo-url> --ref <branch-or-tag>
python scripts/renku_agent.py project repo remove --project <project-id> --repository <repo-id-or-url>
```

### Session launchers/environments/builds

- List/create/update/delete session launchers.
- List/create/update/delete environments.
- Trigger/check image builds.
- Fetch build logs.

### Sessions and non-interactive jobs

- Launch sessions.
- List sessions.
- Get session status.
- Get session logs.
- Stop/delete sessions.
- Produce/open session URL.

Renku non-interactive jobs are launched through the sessions API:

- Endpoint: `POST /sessions`
- Field: `session_type`
- `session_type: non-interactive` launches a Kubernetes Job rather than an interactive StatefulSet.

The CLI should support both:

```bash
python scripts/renku_agent.py session launch ...
python scripts/renku_agent.py session launch --type non-interactive ...
python scripts/renku_agent.py job run ...   # convenience alias
```

For jobs, capture and report:

- project
- launcher/environment
- command/entrypoint if applicable
- resource class
- status
- logs
- final state

## Data Connector P0 Types

Support these connector workflows in P0:

### DOI / repository datasets

- DOI-based connectors.
- Zenodo.
- Dataverse.
- Global data connectors are just DOI data connectors.

### S3 / S3-compatible

- Create S3/S3-compatible data connectors.
- Prompt for required secrets interactively.
- Validate/test connection if supported by API.

### Polybox and SWITCHdrive

Polybox and SWITCHdrive should be modeled as first-class connector workflows in the skill, not only as generic WebDAV.

They have specialized Renku UX but are transformed under the hood to WebDAV/rclone-style configuration.

Support both access modes:

- Personal configuration: username plus password/token.
- Shared/public folder configuration: public link plus optional password.

The implementation should discover and use Renku's schema where possible, but expose convenient aliases/workflows:

```bash
python scripts/renku_agent.py connector create polybox --access personal ...
python scripts/renku_agent.py connector create polybox --access shared ...
python scripts/renku_agent.py connector create switchdrive --access personal ...
python scripts/renku_agent.py connector create switchdrive --access shared ...
```

Implementation notes from research:

- Polybox/SWITCHdrive are transformed to WebDAV internally.
- Shared Polybox uses a public WebDAV URL pattern.
- A vendor detail may be important for correct behavior when editing files/mounting.

### Secrets

For v1, secrets should be gathered via prompt only.

Later automation can add env/file secret inputs if needed.

## Output Format

Default output should be concise and human-readable.

Additional modes:

- `--json`: full machine-readable output.
- `--quiet`: only IDs/URLs where useful.

Errors should include:

- HTTP status.
- Renku error body/message.
- Redacted tokens/secrets.

## Testing

Use `https://dev.renku.ch` for testing, especially write tests.

Production docs/examples default to `https://renkulab.io`.

### Doctor command

Add a P0 `doctor` command:

```bash
RENKU_BASE_URL=https://dev.renku.ch python scripts/renku_agent.py doctor
```

It should check:

- Python version.
- Base URL.
- API reachability.
- OpenAPI availability.
- OIDC/device-flow discovery.
- Auth status.
- Current user if authenticated.
- Namespaces.
- Resource pools/classes.
- Optional project permissions if a project is supplied.

### Test layers

1. Offline tests/checks:
   - CLI parsing.
   - Auth discovery logic with mocked OIDC responses.
   - Request payload construction.
   - Redaction.
   - Credential store permissions.

2. Live read-only smoke tests:
   - Against `https://dev.renku.ch` or `https://renkulab.io`.
   - Auth login.
   - `GET /user`.
   - List namespaces.
   - List projects/data connectors.
   - List resource pools/classes.

3. Live write tests:
   - Use `https://dev.renku.ch`.
   - Must be explicitly enabled and/or confirmed.
   - Never run write tests against `renkulab.io` unless explicitly requested.

Suggested temporary resource naming:

```text
pi-renku-skill-test-YYYYMMDD-HHMMSS
```

## Relevant API Areas

Primary API documentation:

- Swagger UI: https://renkulab.io/swagger
- OpenAPI JSON: https://renkulab.io/api/data/spec.json

Relevant endpoint groups observed:

- `/user`, `/users`
- `/namespaces`
- `/resource_pools`, `/classes`
- `/projects`
- `/projects/{project_id}/members`
- `/projects/{project_id}/permissions`
- `/projects/{project_id}/data_connector_links`
- `/data_connectors`
- `/data_connectors/{data_connector_id}/project_links`
- `/data_connectors/{data_connector_id}/secrets`
- `/session_launchers`
- `/projects/{project_id}/session_launchers`
- `/environments`
- `/builds`
- `/sessions`
- `/sessions/{session_id}/logs`
- `/oauth2/providers`, `/oauth2/connections`
- `/search/query`
- `/storage_schema`, `/storage_schema/validate`, `/storage_schema/test_connection`

Exact request/response schemas should be read from the OpenAPI document during implementation.

## Open Questions / Implementation Checks

- Exact OIDC/device-flow discovery path for `renkulab.io` and `dev.renku.ch`.
- Exact project schema fields for repository assets in `POST /projects` and `PATCH /projects/{project_id}`.
- Exact data connector payloads for DOI, S3, Polybox, and SWITCHdrive.
- Exact session launcher/environment/build payloads.
- Exact `POST /sessions` payload for interactive and `session_type: non-interactive` sessions.
- Whether official `renku-cli` supports any P0 workflows well enough to delegate.
