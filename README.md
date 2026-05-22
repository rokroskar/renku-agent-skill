# Pi Renku Skill

A portable Agent Skill for using the [Renku](https://renkulab.io) platform from AI coding agents.

The skill is workflow-first: it helps agents authenticate, inspect account/platform state, create and manage projects, connect data/code assets, create session launchers, launch interactive sessions, and run non-interactive jobs.

## Features

- OAuth/device-flow login for Renku deployments.
- Uses the official `rnk` CLI for login when available, with built-in fallback.
- Project workflows: list/get/create projects and attach Git repositories.
- Data connector workflows: DOI/Zenodo/Dataverse, S3-compatible, Polybox, SWITCHdrive.
- Session launchers and environments, including build-from-code launchers.
- Convert successful build-from-code launchers into non-interactive job launchers.
- Launch, wait for, inspect, and stop sessions/jobs.
- Safety guardrails:
  - refuses all Renku operations when authenticated as an admin user;
  - requires confirmation for destructive operations;
  - redacts secrets/tokens;
  - reminds agents to remove failed job sessions before rerunning jobs.

## Install in pi

From a git repository:

```bash
pi install git:github.com/OWNER/pi-renku-skill
```

Or from a local checkout:

```bash
pi install /path/to/pi-renku-skill
```

Then reload pi or start a new session. The skill should be available as:

```text
/skill:renku
```

## Install in other Agent Skills-compatible agents

Use the skill directory directly:

```text
skills/renku/
```

It contains a standard `SKILL.md` plus helper scripts and references.

## Quick start

Set the Renku instance, then login:

```bash
export RENKU_BASE_URL=https://dev.renku.ch
cd skills/renku
python3 scripts/renku_agent.py auth login --method device --user-code-only
# open/approve the printed URL/code
python3 scripts/renku_agent.py auth complete
python3 scripts/renku_agent.py auth status
```

For production RenkuLab, omit `RENKU_BASE_URL` or set:

```bash
export RENKU_BASE_URL=https://renkulab.io
```

If the environment cannot write to `~/.config/pi-renku-skill`, the helper falls back to a local `.pi/renku-config` directory when used inside a pi project.

## Common commands

```bash
python3 scripts/renku_agent.py doctor
python3 scripts/renku_agent.py namespaces
python3 scripts/renku_agent.py project list
python3 scripts/renku_agent.py connector list
python3 scripts/renku_agent.py session list
```

Create a project with a repository:

```bash
python3 scripts/renku_agent.py project create \
  --name "Zurich Air Quality Analysis" \
  --namespace rokroskar \
  --slug renku-demo-air-quality-analysis \
  --visibility private \
  --repository https://github.com/rokroskar/renku-demo-air-quality-analysis
```

Create a build-from-code launcher by preparing a JSON body and running:

```bash
python3 scripts/renku_agent.py launcher create --body launcher.json
```

Run a non-interactive job and wait for completion:

```bash
python3 scripts/renku_agent.py job run --launcher <launcher-id>
python3 scripts/renku_agent.py job wait <job-session-name> --timeout 1800 --interval 10
```

## Development

Validate the helper:

```bash
npm run check
```

or directly:

```bash
python3 -m py_compile skills/renku/scripts/renku_agent.py
python3 skills/renku/scripts/renku_agent.py --help
```

## Repository layout

```text
skills/renku/
├── SKILL.md
├── scripts/
│   └── renku_agent.py
├── references/
│   ├── api-overview.md
│   ├── auth.md
│   ├── testing.md
│   └── workflows.md
└── RENKU_SKILL_DESIGN.md
```

The package manifest loads `skills/renku/`.

## Important safety note

The skill refuses to operate if the current Renku user is an admin (`is_admin: true`). Log out and log back in with a non-admin account before asking the agent to perform Renku operations.

## License

No open-source license has been selected yet. Add one before publishing broadly.
