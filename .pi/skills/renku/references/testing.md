# Testing

Use `https://dev.renku.ch` for write tests.

```bash
export RENKU_BASE_URL=https://dev.renku.ch
python3 scripts/renku_agent.py doctor
python3 scripts/renku_agent.py auth login
python3 scripts/renku_agent.py doctor --json
```

Read-only smoke checks:

```bash
python3 scripts/renku_agent.py user
python3 scripts/renku_agent.py namespaces
python3 scripts/renku_agent.py resource-pools
python3 scripts/renku_agent.py project list
python3 scripts/renku_agent.py connector list
```

Write tests must be explicitly requested/confirmed. Suggested temporary resource prefix:

```text
pi-renku-skill-test-YYYYMMDD-HHMMSS
```

Never run write tests against `https://renkulab.io` unless the user explicitly asks.

Offline checks:

```bash
python3 -m py_compile scripts/renku_agent.py
python3 scripts/renku_agent.py --help
python3 scripts/renku_agent.py doctor --json
```
