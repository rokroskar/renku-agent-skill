# Renku API Overview

Primary API docs:

- Swagger UI: https://renkulab.io/swagger
- OpenAPI JSON: https://renkulab.io/api/data/spec.json

The helper targets the Data Services API under:

```text
<base>/api/data
```

Important endpoint groups:

- Account/platform: `/user`, `/namespaces`, `/resource_pools`, `/resource_pools/{id}/classes`, `/platform/config`
- Projects: `/projects`, `/projects/{project_id}`, `/namespaces/{namespace}/projects/{slug}`
- Project members/permissions: `/projects/{project_id}/members`, `/projects/{project_id}/permissions`
- Data connectors: `/data_connectors`, `/data_connectors/global`, `/data_connectors/{id}`
- Connector links/secrets: `/data_connectors/{id}/project_links`, `/data_connectors/{id}/secrets`
- Launchers: `/session_launchers`, `/projects/{project_id}/session_launchers`
- Environments/builds: `/environments`, `/environments/{environment_id}/builds`, `/builds/{build_id}`, `/builds/{build_id}/logs`
- Sessions/jobs: `/sessions`, `/sessions/{session_id}`, `/sessions/{session_id}/logs`
- OAuth integrations: `/oauth2/providers`, `/oauth2/connections`
- Generic search: `/search/query`
- Storage schemas: `/storage_schema`, `/storage_schema/validate`, `/storage_schema/test_connection`

Non-interactive jobs use `POST /sessions` with:

```json
{ "launcher_id": "...", "session_type": "non-interactive" }
```

Interactive sessions use `session_type: interactive` or omit the field.
