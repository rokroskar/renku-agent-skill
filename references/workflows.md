# Renku Skill Workflows

## Principle

Use workflow commands first; fall back to `api` when the Renku API has changed or a P0 command is missing.

Run commands from the skill directory:

```bash
python3 scripts/renku_agent.py <command>
```

Global flags may be placed before or after subcommands:

```bash
--json      machine-readable output
--quiet     print only the key id/url where useful
--dry-run   show intended request without sending writes
--yes       skip confirmation only after user approved
```

## Project with repository

```bash
python3 scripts/renku_agent.py project create \
  --name "Example" \
  --namespace <namespace> \
  --visibility private \
  --repository https://github.com/org/repo.git
```

Repositories are strings in the project's `repositories` array. Add/remove by fetching project, changing the list, and PATCHing it.

## Data connector then link to project

```bash
python3 scripts/renku_agent.py connector create doi --name "Dataset" --doi "10.xxxx/..." --target-path /data --global
python3 scripts/renku_agent.py connector link --connector <connector-id> --project <project-id>
```

For S3/Polybox/SWITCHdrive the helper prompts for secrets and redacts them from output.

## Launcher from JSON

Minimal launcher body shape from OpenAPI:

```json
{
  "project_id": "01...",
  "name": "JupyterLab",
  "environment": { "id": "01..." },
  "resource_class_id": 1
}
```

Then:

```bash
python3 scripts/renku_agent.py launcher create --body launcher.json
```

## Non-interactive job

```bash
python3 scripts/renku_agent.py job run --launcher <launcher-id>
python3 scripts/renku_agent.py job list
python3 scripts/renku_agent.py session logs <session-id>
```

This wraps `POST /sessions` with `session_type: non-interactive`.
