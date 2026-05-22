# Pi Renku Skill

A portable Agent Skill for using the [Renku](https://renkulab.io) platform from AI coding agents.

The skill is workflow-first: it helps agents authenticate, inspect account/platform state, create and manage projects, connect data/code assets, create session launchers, launch interactive sessions, and run non-interactive jobs.

`rnk` usage note: this package uses the official Renku CLI for authentication (`rnk login`) and token import/reuse. Job commands support `--backend auto|api|rnk`: in `auto` mode the helper tries `rnk job start/list/logs/stop` for simple job operations and falls back to the Renku Data Services API if needed. Other workflows use the API directly. The helper keeps API-based job commands for richer agent behavior such as waiting for terminal states, removing failed job sessions before rerun, enriching session/project URLs, and supporting workflows not yet covered by `rnk`.

## Features

- OAuth/device-flow login for Renku deployments.
- Uses the official `rnk` CLI for login/token reuse when available, with built-in fallback.
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

## Tutorial: prompting an agent through a Renku workflow

This section shows how to ask an agent using this skill to carry out a complete Renku workflow. It uses:

- code repository: <https://github.com/rokroskar/renku-demo-air-quality-analysis>
- public Polybox data source: <https://polybox.ethz.ch/index.php/s/6EsHI6MF83mg52o>

The point is not to type low-level commands yourself. Prompt the agent with goals, let it inspect the repo/API state, and let it use the helper commands.

### 1. Log in

Prompt:

```text
Log me in to the Renku instance at dev.renku.ch.
```

Expected agent behavior:

- Set `RENKU_BASE_URL=https://dev.renku.ch`.
- Use `auth login --method device --user-code-only`.
- Show you the device URL/code.
- After you approve and say `done`, run `auth complete` and `auth status`.
- Refuse to continue if the authenticated user has `is_admin: true`.

### 2. Create a project from a GitHub repository

Prompt:

```text
I have this repo and I would like to base a Renku project on it:
https://github.com/rokroskar/renku-demo-air-quality-analysis

Please inspect the repo and suggest the project setup.
```

Expected agent behavior:

- Inspect the repository structure.
- Notice `requirements.txt` and notebooks.
- Recommend a Renku project with the repository attached.
- Recommend a build-from-code session launcher with:
  - `builder_variant: python`
  - `frontend_variant: jupyterlab`

Then prompt:

```text
Create the project as private in my user namespace, and create the JupyterLab build-from-code launcher.
```

Expected agent behavior:

- Create a project with the repo in `repositories`.
- Create a build-from-code launcher.
- Watch the image build until it succeeds.
- Give you the Renku project/session iframe URL when launching an interactive session, not just the raw backend session URL.

### 3. Run notebooks as a non-interactive job

Prompt:

```text
Look in the repo for Jupyter notebooks. I want to run them in batch mode.
Convert the successful build-from-code launcher into an external-image job launcher,
use the CNB lifecycle launcher, and run the notebooks as a non-interactive Renku job.
```

Expected agent behavior:

- Get the successful build image from the launcher environment.
- Patch or create a launcher using:
  - `environment_image_source: image`
  - `container_image: <successful-build-image>`
  - `command: ["/cnb/lifecycle/launcher"]`
  - `args` that execute the notebooks, e.g. with `jupyter nbconvert --execute`
- Run it with `job run`.
- Use `job wait` instead of writing ad-hoc polling loops.
- If a previous failed job session exists, remove it before rerunning.

In this demo, the first run failed because the notebook expected data at:

```text
/home/renku/work/zurich-air-quality-data
```

### 4. Add a data connector for the input data

Prompt:

```text
Add a data connector for the data from here:
https://polybox.ethz.ch/index.php/s/6EsHI6MF83mg52o
```

Expected agent behavior:

- Create a shared Polybox data connector.
- Use `public_link`, not a generic `url` field.
- Use a relative target path, not an absolute one:

```text
zurich-air-quality-data
```

- Link the connector to the project.
- Rerun the job after removing the failed job session.

### 5. Optionally write outputs to external storage

If you add a writable data connector such as writable Polybox/SWITCHdrive, Dropbox/Google Drive, or S3, you can ask the agent to route executed notebooks or other outputs there.

Prompt:

```text
I added a writable data connector called output-data.
Make sure the rendered/executed notebooks go there, then rerun the job.
```

Expected agent behavior:

- Verify the connector exists, is linked, and is writable.
- Confirm its `target_path`, e.g. `output-data`.
- Patch the job launcher so `nbconvert` writes rendered notebooks under something like:

```text
/home/renku/work/output-data/executed-notebooks/
```

- Rerun the job and wait for `succeeded`.

In the demo, the rendered notebook was written to:

```text
/home/renku/work/output-data/executed-notebooks/notebooks/exploratory_analysis.ipynb
```

### Useful follow-up prompts

```text
Show me the logs from the last job.
```

```text
Stop/delete the running session.
```

```text
Create a separate job launcher instead of modifying my interactive launcher.
```

```text
Make the notebook execution timeout longer and rerun the job.
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

MIT
