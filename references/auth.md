# Renku Auth

The helper uses OAuth device flow by default.

- Default base URL: `https://renkulab.io`
- Override: `RENKU_BASE_URL=https://dev.renku.ch`
- Client ID: `renku-cli`
- Public client, no client secret
- Credentials: `~/.config/pi-renku-skill/credentials.json`

Login:

```bash
python3 scripts/renku_agent.py auth login
```

The helper discovers OIDC at:

```text
<base>/auth/realms/Renku/.well-known/openid-configuration
```

and uses the `device_authorization_endpoint` and `token_endpoint` from that discovery document.

Environment token fallback:

```bash
RENKU_ACCESS_TOKEN=... python3 scripts/renku_agent.py user
RENKU_TOKEN=... python3 scripts/renku_agent.py user
```

Token values are redacted from output. If a refresh token is stored, the helper refreshes access tokens when they are near expiry.
