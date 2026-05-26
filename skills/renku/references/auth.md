# Renku Auth

The helper prefers the official Renku CLI (`rnk login`) by default, then falls back to built-in OAuth device flow.

- Default base URL: `https://renkulab.io`
- Override: `RENKU_BASE_URL=https://dev.renku.ch`
- Client ID: `renku-cli`
- Public client, no client secret
- Helper credentials: `~/.config/renku-agent-skill/credentials.json`
- Official CLI command: `rnk`

Login:

```bash
python3 scripts/renku_agent.py auth login                  # auto: prefer rnk
python3 scripts/renku_agent.py auth login --method rnk     # require rnk
python3 scripts/renku_agent.py auth login --method device  # built-in device flow
```

When `rnk login` succeeds, the helper attempts to import the official CLI token for direct API calls. It also reads a valid `rnk` token directly when available.

The built-in fallback discovers OIDC at:

```text
<base>/auth/realms/Renku/.well-known/openid-configuration
```

and uses the `device_authorization_endpoint` and `token_endpoint` from that discovery document.

Environment token fallback:

```bash
RENKU_ACCESS_TOKEN=... python3 scripts/renku_agent.py user
RENKU_TOKEN=... python3 scripts/renku_agent.py user
RENKU_CLI_ACCESS_TOKEN=... python3 scripts/renku_agent.py user
```

Token values are redacted from output. If a refresh token is stored in the helper store, the helper refreshes access tokens when they are near expiry.
