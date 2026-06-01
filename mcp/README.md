# Renku MCP Server

A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes Renku platform operations as typed MCP tools. Use this as an alternative to (or alongside) the SKILL.md-based skill for MCP-compatible clients such as Claude Code and Claude Desktop.

## Why MCP vs the skill

The skill works in any agent (Pi, Codex, OpenCode, Claude Code) but relies on the agent reading and following prose instructions. The MCP server exposes the same operations as typed tool schemas, which:

- Enforce required fields at call time (e.g. `environment.name`, `resource_class_id`) rather than discovering them as 422 errors from the API.
- Keep credentials in the server process environment — they never appear as tool parameters in the conversation.
- Return structured data rather than text output, which is easier for models to inspect and chain.

## Prerequisites

[uv](https://docs.astral.sh/uv/) must be installed. `uv` reads the dependency declaration embedded in `server.py` and manages the virtualenv automatically — no manual `pip install` needed.

```bash
# Install uv if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Python 3.11+ required (uv will download it if needed).

## Authentication

The MCP server reads the same credential file as the CLI helper. Authenticate once with the CLI:

```bash
python3 skills/renku/scripts/renku_agent.py auth login
```

Credentials are stored in `~/.config/renku-agent-skill/credentials.json` and reused by the server. You do not need to restart the server after logging in — it reads the file on each request.

Alternatively, pass a token directly via environment variable:

```bash
RENKU_ACCESS_TOKEN=<token> python3 mcp/server.py
```

## Running the server

Claude Code starts the server automatically — you don't run it manually. For testing:

```bash
uv run mcp/server.py
```

## Claude Code integration

Use the `claude mcp add` command — it writes to `~/.claude.json` which Claude Code always reads:

```bash
claude mcp add renku -- uv run /absolute/path/to/renku-agent-skill/mcp/server.py
```

For a non-default Renku deployment, pass `RENKU_BASE_URL` via `-e`:

```bash
claude mcp add renku -e RENKU_BASE_URL=https://dev.renku.ch -- uv run /absolute/path/to/renku-agent-skill/mcp/server.py
```

To verify it's registered:

```bash
claude mcp list
```

To remove it:

```bash
claude mcp remove renku
```

## Passing connector credentials safely

Credentials for S3, Polybox, and SWITCHdrive connectors are read from the MCP server's environment — they are never exposed as tool parameters and never appear in the conversation.

Set them in the `env` block of your MCP config:

```json
"env": {
  "RENKU_BASE_URL": "https://dev.renku.ch",
  "RENKU_S3_ACCESS_KEY_ID": "your-key-id",
  "RENKU_S3_SECRET_ACCESS_KEY": "your-secret-key",
  "RENKU_CONNECTOR_PASSWORD": "your-polybox-password"
}
```

| Variable | Used for |
|---|---|
| `RENKU_S3_ACCESS_KEY_ID` | S3 access key |
| `RENKU_S3_SECRET_ACCESS_KEY` | S3 secret key |
| `RENKU_CONNECTOR_USERNAME` | Polybox/SWITCHdrive/WebDAV personal username |
| `RENKU_CONNECTOR_PASSWORD` | Polybox/SWITCHdrive/WebDAV password |

## Available tools

| Tool | Description |
|---|---|
| `auth_status` | Check authentication and user info |
| `resource_classes` | List available compute classes (call before creating launchers) |
| `namespaces` | List accessible namespaces |
| `project_list` | List projects |
| `project_get` | Get a project by ID or namespace/slug |
| `project_create` | Create a project |
| `project_repo_add` | Add a Git repository to a project |
| `connector_list` | List data connectors |
| `connector_get` | Get a connector by ID |
| `connector_create_doi` | Create a global DOI/Zenodo connector |
| `connector_create_s3` | Create an S3 connector (credentials from env) |
| `connector_create_polybox` | Create a Polybox/SWITCHdrive connector (password from env) |
| `connector_link` | Link a connector to a project |
| `connector_unlink` | Unlink a non-owned connector from a project |
| `connector_delete` | Delete a project-owned connector |
| `launcher_list` | List launchers |
| `launcher_project_list` | List launchers for a project |
| `launcher_get` | Get a launcher by ID |
| `launcher_create` | Create a launcher (resource_class_id required) |
| `launcher_patch` | Patch a launcher |
| `session_launch` | Launch an interactive session |
| `session_list` | List sessions |
| `session_get` | Get session status |
| `session_logs` | Get session logs (all containers) |
| `session_delete` | Stop and delete a session |
| `session_delete_if_failed` | Delete only if failed/error/stopped — safe no-op otherwise |
| `session_wait` | Wait for a session to reach running state |
| `job_run` | Launch a non-interactive job |
| `job_list` | List non-interactive jobs |
| `job_wait` | Wait for a job to complete (returns logs on failure) |
| `build_list` | List builds for an environment |
| `build_get` | Get build status |
| `build_logs` | Get build logs |
| `build_wait` | Wait for a build to complete |

## Updating

```bash
git -C /path/to/renku-agent-skill pull
```

Claude Code picks up changes automatically when it restarts the MCP server process.
