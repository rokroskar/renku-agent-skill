# Renku Agent Skill

A portable Agent Skill for using the [Renku](https://renkulab.io) platform from AI coding agents.

The skill is workflow-first: it helps agents authenticate, inspect account/platform state, create and manage projects, connect data/code assets, create session launchers, launch interactive sessions, and run non-interactive jobs.

After installation, users interact with the skill by asking their coding agent for Renku outcomes in plain language. The skill instructions tell the agent how to use Renku CLI/API support safely behind the scenes.

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

## Using the skill

Once installed, start a new agent session and ask for the Renku task you want. For example:

```text
Log me in to the Renku instance at renkulab.io
```

```text
Create a private Renku project from this GitHub repository and set up a JupyterLab launcher:
https://github.com/rokroskar/renku-demo-air-quality-analysis
```

```text
Convert the launcher into one that can run as a job and submit the job
```

```text
Add this dataset as a data connector, link it to the project, and rerun the notebook job: <URL>
```

```text
Show me the status and logs for my latest non-interactive Renku job.
```

The agent should handle login, project creation, data connectors, launchers, sessions, and jobs by following the skill instructions. If a device-login approval is needed, the agent will show you the URL/code and wait for you to approve it.

By default, the skill targets `https://renkulab.io`. Ask explicitly for another deployment, for example `https://dev.renku.ch`, when needed.

## Installation

### Pi Agent

From a git repository:

```bash
pi install git:github.com/rokroskar/renku-agent-skill
```

Or from a local checkout:

```bash
pi install /path/to/renku-agent-skill
```

Then reload pi or start a new session. The skill should be available as:

```text
/skill:renku
```

### Claude Code

Claude Code discovers skills from a directory containing a `SKILL.md` file. The directory name becomes the `/renku` slash command.

**Global install** (available in all Claude Code sessions):

```bash
git clone https://github.com/rokroskar/renku-agent-skill.git ~/.claude/renku-agent-skill
mkdir -p ~/.claude/skills
ln -s ~/.claude/renku-agent-skill/skills/renku ~/.claude/skills/renku
```

**Project-local install** (this project only):

```bash
git clone https://github.com/rokroskar/renku-agent-skill.git .agent-skills/renku-agent-skill
mkdir -p .claude/skills
ln -s ../../.agent-skills/renku-agent-skill/skills/renku .claude/skills/renku
```

**Updating to a new version:**

```bash
# Global:
git -C ~/.claude/renku-agent-skill pull

# Project-local:
git -C .agent-skills/renku-agent-skill pull
```

Claude Code auto-discovers changes without restarting. Invoke with `/renku`, or just describe a Renku task in plain language — the skill's description is enough for Claude to load it automatically.

The skill uses `${CLAUDE_SKILL_DIR}` to locate its helper script, so it works correctly regardless of the current working directory.

### MCP server (Claude Code / Claude Desktop)

The `mcp/` directory contains a FastMCP server that exposes every Renku operation as a typed tool. This is an alternative to the skill for MCP-compatible clients; typed schemas prevent whole classes of argument errors that the text-based skill cannot catch.

**Install [uv](https://docs.astral.sh/uv/)** (manages the `fastmcp` dependency automatically — no pip install needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Authenticate once** (shared credential store with the skill):

```bash
python3 skills/renku/scripts/renku_agent.py auth login
```

**Register in Claude Code** using `claude mcp add` (stores in `~/.claude.json`, available in all sessions):

```bash
claude mcp add renku -- uv run /path/to/renku-agent-skill/mcp/server.py
```

For a non-default deployment, or to pass connector credentials (they stay in local config, never in conversations):

```bash
claude mcp add renku \
  -e RENKU_BASE_URL=https://dev.renku.ch \
  -e RENKU_S3_ACCESS_KEY_ID=... \
  -e RENKU_S3_SECRET_ACCESS_KEY=... \
  -e RENKU_CONNECTOR_PASSWORD=... \
  -- uv run /path/to/renku-agent-skill/mcp/server.py
```

**Update:**

```bash
git -C /path/to/renku-agent-skill pull
```

The server restarts automatically when Claude Code reloads its MCP config.

### Codex CLI

Codex uses `AGENTS.md` files for persistent instructions. Clone the package somewhere stable, then add a short instruction file that tells Codex where the skill lives.

Global setup:

```bash
git clone https://github.com/rokroskar/renku-agent-skill.git ~/.codex/renku-agent-skill
cat >> ~/.codex/AGENTS.md <<'EOF'

## Renku Agent Skill
When asked to work with Renku/RenkuLab projects, data connectors, launchers, sessions, or jobs, use the Agent Skill at:
~/.codex/renku-agent-skill/skills/renku/SKILL.md
Read that SKILL.md first, then follow the skill instructions and references.
EOF
```

Project-local setup:

```bash
git clone https://github.com/rokroskar/renku-agent-skill.git .agent-skills/renku-agent-skill
cat >> AGENTS.md <<'EOF'

## Renku Agent Skill
When asked to work with Renku/RenkuLab projects, data connectors, launchers, sessions, or jobs, use the Agent Skill at:
.agent-skills/renku-agent-skill/skills/renku/SKILL.md
Read that SKILL.md first, then follow the skill instructions and references.
EOF
```

Start a new Codex session after adding or changing `AGENTS.md`.

### OpenCode

opencode also reads project rules from `AGENTS.md`. Clone the package into your project and reference the skill from `AGENTS.md`:

```bash
git clone https://github.com/rokroskar/renku-agent-skill.git .agent-skills/renku-agent-skill
cat >> AGENTS.md <<'EOF'

## Renku Agent Skill
When asked to work with Renku/RenkuLab projects, data connectors, launchers, sessions, or jobs, use the Agent Skill at:
.agent-skills/renku-agent-skill/skills/renku/SKILL.md
Read that SKILL.md first, then follow the skill instructions and references.
EOF
```

If you prefer opencode-specific rules, put the same block in `.opencode/AGENTS.md` instead.

### Install in other Agent Skills-compatible agents

Use the skill directory directly:

```text
skills/renku/
```

It contains a standard `SKILL.md` plus supporting files and references.

## Tutorial: prompting an agent through a Renku workflow

This section shows how to ask an agent using this skill to carry out a complete Renku workflow. It uses:

- code repository: <https://github.com/rokroskar/renku-demo-air-quality-analysis>
- public Polybox data source: <https://polybox.ethz.ch/index.php/s/6EsHI6MF83mg52o>

The point is not to type low-level commands yourself. Prompt the agent with goals and let it inspect the repository and Renku state before taking action.

### 1. Log in

Prompt:

```text
Log me in to the Renku instance at dev.renku.ch.
```

Expected agent behavior:

- Target the requested Renku deployment (`https://dev.renku.ch`).
- Start a device-login flow.
- Show you the device URL/code.
- After you approve and say `done`, complete login and verify account status.
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
- Watch the image build until it succeeds; the agent should report concise log progress so you can see whether Renku is cloning the repository, installing dependencies, exporting the image, or pushing the final image.
- Give you the Renku project/session iframe URL when launching an interactive session, not just the raw backend session URL.

### 3. Run notebooks as a non-interactive job

Prompt:

```text
Look in the repo for Jupyter notebooks. I want to run them in batch mode.
Convert the successful build-from-code launcher into an external-image job launcher,
use the CNB lifecycle launcher, and run the notebooks as a non-interactive Renku job.
```

Expected agent behavior:

- If the user asks for non-default compute such as a GPU/A100, inspect available resource pools, choose an accessible matching GPU class, and report the selected resource class before launch.
- Get the successful build image from the launcher environment.
- Patch or create a launcher using:
  - `environment_image_source: image`
  - `container_image: <successful-build-image>`
  - `command: ["/cnb/lifecycle/launcher"]`
  - `args` that execute the notebooks, e.g. with `jupyter nbconvert --execute`
- Start the non-interactive job.
- Wait for the job using the skill's built-in workflow instead of ad-hoc polling.
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

Validate the package:

```bash
npm run check
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
