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

The helper is dependency-free Python stdlib. It checks for the official Renku CLI as `rnk` and reports it in `doctor`; workflow commands currently use the Data Services API directly and are designed so they can delegate to `rnk` later.

## Authentication

Default auth uses OAuth device flow with public client `renku-cli`.

```bash
python3 scripts/renku_agent.py auth login
python3 scripts/renku_agent.py auth status
python3 scripts/renku_agent.py auth logout
```

Credentials are stored under `~/.config/pi-renku-skill/credentials.json` with restricted permissions. Environment token fallback is supported:

```bash
RENKU_ACCESS_TOKEN=... python3 scripts/renku_agent.py user
# or RENKU_TOKEN=...
```

## Safety Rules

- Read-only operations may be run freely.
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
python3 scripts/renku_agent.py connector create polybox --name "Polybox" --access personal --target-path /data
python3 scripts/renku_agent.py connector create switchdrive --name "Shared SWITCHdrive" --access shared --url <public-link> --target-path /data
```

For exact payload control, use `--body FILE` or `--payload JSON`.

### Launchers / Environments / Builds

Launchers and environments are schema-rich, so v1 create/patch commands accept JSON bodies.

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
```

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
python3 scripts/renku_agent.py session delete <session-id>
```

Confirm before deleting/stopping sessions unless the user has already explicitly approved.

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
