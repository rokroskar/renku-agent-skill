# Renku Skill Workflows

## Principle

Admin safety lockout: if the current Renku user has `is_admin: true`, do not perform any Renku operation on any Renku instance. Only `auth logout` is allowed. Ask the user to log back in with a non-admin account.

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

## Choosing resource classes

Before launching sessions or jobs with non-default compute, inspect available resource pools/classes:

```bash
python3 scripts/renku_agent.py resource-pools --json
python3 scripts/renku_agent.py resource-classes --json
```

Use a class with `matching: true`. For GPU jobs, choose a class with `gpu > 0` and check its `name`, `node_affinities`, and `tolerations` for the requested accelerator type.

Example decision process for “use an A100 slice”:

1. List resource pools/classes.
2. Filter to `matching: true` and `gpu > 0`.
3. Prefer names like `A100`, `GPU - A100 20GB vRAM`, or affinities/tolerations mentioning A100/GPU partitioning.
4. Tell the user which class will be used, including id, CPU, memory, GPU, and storage limits.
5. Launch with:

```bash
python3 scripts/renku_agent.py job run --launcher <launcher-id> --resource-class-id <class-id>
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

## Project documentation

Project documentation is the `documentation` field on the project object. Updating it requires an `If-Match` header with the current project `etag`; use the helper rather than raw `api PATCH`:

```bash
python3 scripts/renku_agent.py project documentation get <project-id-or-namespace/slug>
python3 scripts/renku_agent.py project documentation set <project-id-or-namespace/slug> --file docs.md
```

The field is limited to 5000 characters. If a repository README is longer, write a concise Renku project summary and link to the repository README.

## Data connector then link to project

DOI / Zenodo / Dataverse connectors are global. Create them with the DOI helper, then link the returned connector id to a project:

```bash
python3 scripts/renku_agent.py connector create zenodo --doi "10.5281/zenodo.10058130"
python3 scripts/renku_agent.py connector link --connector <connector-id> --project <project-id>
```

Do not create DOI connectors by manually POSTing a payload with `namespace`; that creates a project-owned connector instead of a global DOI connector. The global DOI endpoint expects only `storage.configuration.{type,doi,provider}` plus storage fields and derives name/slug/target path from the DOI metadata.

The `target_path` (mount path) for DOI connectors is set by Renku from the DOI metadata — it is **not** `data/` or any other default. Always read `storage.target_path` from the creation response before referencing the data in a session or job. The helper prints it as `target_path: '<value>' (mount at /home/renku/work/<value>)`. If the connector already exists, run `connector get <id>` to retrieve it.

For S3/Polybox/SWITCHdrive the helper prompts for secrets and redacts them from output. Data connector `target_path` values are relative to the session working directory, so use `output` or `zurich-air-quality-data` rather than `/output` or `/zurich-air-quality-data`.

For shared Polybox/SWITCHdrive connectors, the API expects `configuration.public_link` rather than `configuration.url`. Use the helper flags; for example:

```bash
python3 scripts/renku_agent.py connector create polybox --name "Published results" --namespace <namespace/project-slug> --visibility public --access shared --url <public-link> --target-path results --readonly
python3 scripts/renku_agent.py connector create polybox --name "Job output storage" --namespace <namespace/project-slug> --visibility private --access shared --url <public-link> --password <password> --target-path output --no-readonly
```

To remove connectors, distinguish linked/global connectors from project-owned connectors:

```bash
# For a connector linked into a project from elsewhere:
python3 scripts/renku_agent.py connector unlink --connector <connector-id> --link <link-id>

# For a project-owned connector. This deletes the connector itself and requires confirmation:
python3 scripts/renku_agent.py connector delete <connector-id>
```

If unlinking fails with "link to the owner project cannot be removed", the connector is project-owned and should be deleted instead of unlinked.

## Session launcher types

Renku session launchers can use different environment sources.

### Existing/global environment

Use this when the user wants a prebuilt image/environment.

```json
{
  "project_id": "01...",
  "name": "JupyterLab",
  "environment": { "id": "01..." },
  "resource_class_id": 1
}
```

### Build from code

Use this when a linked Git repository contains dependency files or build hints. For Python notebook repos containing `requirements.txt`, `environment.yml`, `pyproject.toml`, or similar, offer to create a build-from-code launcher with JupyterLab.

```json
{
  "project_id": "01...",
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

Then pass the JSON inline with `--payload`. For a build-from-code launcher the payload fits on one line:

```bash
python3 scripts/renku_agent.py launcher create --payload '{"project_id":"01...","name":"JupyterLab from repository","description":"Builds from repo","environment":{"environment_image_source":"build","repository":"https://github.com/org/repo.git","builder_variant":"python","frontend_variant":"jupyterlab","repository_revision":"main","context_dir":".","platforms":["linux/amd64"]}}'
```

Build-from-code launcher creation starts an image build. Use `build wait` rather than writing custom polling loops:

```bash
python3 scripts/renku_agent.py build list --environment <environment-id>
python3 scripts/renku_agent.py build wait <build-id> --timeout 1800 --interval 15
```

`build wait` prints concise log progress by default so the user can see whether Renku is cloning the repository, resolving/installing dependencies, exporting the image, or pushing the final image.

For R projects, use `builder_variant: "r"` and `frontend_variant: "rstudio"` when available. Other Python frontends may include `vscodium` and `ttyd`.

## Recommended repo-to-Renku workflow

When the user provides a GitHub/GitLab repo and asks to base a Renku project on it:

1. Inspect the repository structure.
2. If Python dependency files are present, recommend build-from-code with `builder_variant=python` and `frontend_variant=jupyterlab`.
3. Create the project with the repo URL in `repositories`.
4. Create a build-from-code session launcher for that repo.
5. Optionally trigger a build or launch a session.

## Non-interactive job

```bash
python3 scripts/renku_agent.py job run --launcher <launcher-id>
python3 scripts/renku_agent.py job list
python3 scripts/renku_agent.py job wait <job-session-id>
python3 scripts/renku_agent.py session logs <session-id>
```

The session behaviour is determined by the `session_type` field in the POST /sessions body. `job run` sends `session_type: "non-interactive"`; `session launch` sends `session_type: "interactive"`.

Use `job wait` rather than writing custom polling loops. It polls status, prints concise log tails, and exits when the job reaches a terminal state:

```bash
python3 scripts/renku_agent.py job wait <job-session-id> --timeout 1800 --interval 10
```

Before rerunning a job, remove the previous failed/stopped job session from the same launcher/project. Renku may reuse or conflict with an existing failed job session name. If the user asks to rerun a failed job, it is acceptable to delete the failed job session first, while clearly stating which session is being removed.

## Convert a build-from-code launcher into a job launcher

A build-from-code launcher has an environment like:

```json
{
  "environment_image_source": "build",
  "build_parameters": {
    "repository": "https://github.com/org/repo",
    "builder_variant": "python",
    "frontend_variant": "jupyterlab"
  },
  "container_image": "harbor.../renku-build:..."
}
```

After the build succeeds, create a **separate** launcher for the job (preserving the original interactive one) or patch the existing launcher. The `session_type` (`interactive` vs `non-interactive`) is set at launch time in the POST /sessions body — there is no `launcher_type` field on the launcher.

1. Get the launcher and confirm the build succeeded.
2. Use the built `environment.container_image` as the fixed image.
3. Set `environment_image_source` to `image`, `environment_kind` to `CUSTOM`.
4. Set `command` to `["/cnb/lifecycle/launcher"]` so the CNB launch environment is initialized correctly.
5. Set `args` to the batch command.
6. Run with `job run --launcher <launcher-id>` — this sets `session_type: "non-interactive"` at launch time.

For notebook batch execution, prefer a Python `-c` script over complex shell quoting. Example launcher patch:

```json
{
  "name": "Run notebooks batch",
  "description": "Non-interactive launcher that executes notebooks and writes rendered notebooks to output-data.",
  "environment": {
    "name": "Run notebooks batch",
    "environment_image_source": "image",
    "environment_kind": "CUSTOM",
    "container_image": "<successful-build-image>",
    "default_url": "/",
    "working_directory": "/home/renku/work",
    "mount_directory": "/home/renku/work",
    "port": 8888,
    "command": ["/cnb/lifecycle/launcher"],
    "args": [
      "python",
      "-c",
      "import pathlib, subprocess; root=pathlib.Path('/home/renku/work'); repo=root/'<repo-dir>'; outroot=root/'output-data'/'executed-notebooks'; nbs=[p for p in repo.rglob('*.ipynb') if '.ipynb_checkpoints' not in p.parts]; print('Notebooks:', [str(p.relative_to(repo)) for p in nbs], flush=True); assert nbs, 'No notebooks found'; [(lambda nb, rel: ((outroot/rel.parent).mkdir(parents=True, exist_ok=True), print(f'Executing {nb} -> {outroot/rel}', flush=True), subprocess.run(['jupyter','nbconvert','--to','notebook','--execute',str(nb),'--output-dir',str(outroot/rel.parent),'--output',rel.name,'--ExecutePreprocessor.timeout=1200'], check=True)))(nb, nb.relative_to(repo)) for nb in nbs]"
    ]
  }
}
```

The Python `-c` argument is too long for an inline `--payload`. Write the patch to `.pi/tmp/` (gitignored) and remove it afterwards:

```bash
python3 scripts/renku_agent.py launcher patch <launcher-id> --body .pi/tmp/job-launcher-patch.json
rm .pi/tmp/job-launcher-patch.json
python3 scripts/renku_agent.py session delete <old-failed-job-session> --yes   # if rerunning a failed/stopped job
python3 scripts/renku_agent.py job run --launcher <launcher-id>
python3 scripts/renku_agent.py job wait <job-session-name> --timeout 1800 --interval 10
```

Notes:

- Do not use the raw API session URL for jobs; jobs usually return `url: None`.
- If writing outputs to a data connector, ensure the connector is linked to the project, writable, and has a relative `target_path` such as `output-data`.
- The job session name may be reused for the same launcher, so remove failed/stopped job sessions before rerunning.
- If the original interactive launcher should be preserved, create a separate launcher for the job instead of patching the interactive launcher in place.

## Session URLs

The sessions API returns a raw session URL like:

```text
<base>/sessions/<session-name>/lab/
```

For users, prefer the Renku project iframe URL when namespace and project slug are known:

```text
<base>/p/<namespace>/<project-slug>/sessions/show/<session-name>
```

Example:

```text
https://dev.renku.ch/p/rokroskar/renku-demo-air-quality-analysis/sessions/show/rokroskar-e9d5c7d09604
```
