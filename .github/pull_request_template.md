## What and why

<!-- One or two sentences. Link the issue: Closes #123 -->

## How it was tested

- [ ] `python run_tests.py` passes (SQLite)
- [ ] Ran against PostgreSQL + Redis if the change touches data or caching (`docker compose up`)
- [ ] UI checked in the browser if the frontend changed

## Checklist

- [ ] Schema changes are a **new** entry in `database.MIGRATIONS` (never edit a shipped one) and are backward compatible with the running version
- [ ] No secrets, keys or real customer data in the diff
- [ ] Docs / `.env.example` / `k8s/base/config.env` updated for new settings
