# Contributing and git workflow

We use **GitHub Flow with release tags**: `main` is always deployable, all work happens on
short-lived branches merged through pull requests, and releases are tags on `main`.

```
feature/xyz ──PR──► main ──(auto)──► staging          tag vX.Y.Z ──(approve)──► production
   ▲ CI on PR         │ CD: CI again, build image,       │ CD: same image digest flow
   └ review           │ deploy by digest                 └ GitHub "production" environment
```

## Day to day

```bash
git switch main && git pull
git switch -c feat/hotel-filters          # feat/, fix/, chore/, docs/, refactor/
# ...code, then:
python run_tests.py                       # all suites, SQLite
docker compose up --build                 # optional: full stack on PostgreSQL + Redis
git commit -m "feat(hotels): filter by star rating"
git push -u origin feat/hotel-filters     # open a pull request
```

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`; `!` or `BREAKING CHANGE:` for breaking changes.
- **Pull requests** are small (aim for < 400 changed lines), fill in the template, and need
  green CI plus one approving review. Merge with **squash**, so `main` has one commit per PR.
- **Hooks**: `pip install -r requirements-dev.txt && pre-commit install` runs ruff, YAML checks,
  private-key and secret detection (gitleaks) before each commit, and blocks direct commits to `main`.

## Releases

1. Make sure `main` is green and verified on staging.
2. Tag it with [SemVer](https://semver.org/): `git tag -a v1.4.0 -m "v1.4.0" && git push origin v1.4.0`.
3. CD builds `ghcr.io/<owner>/booking-assistant:1.4.0` and waits for approval on the
   **production** environment, then deploys it and smoke-tests it; failed rollouts are undone automatically.

**Hotfix:** branch `fix/...` from `main`, PR, merge, tag a patch version (`v1.4.1`). We don't keep
long-lived release branches; if an old version needs a patch, branch from its tag (`release/1.3`).

**Rollback:** re-deploy the previous tag (re-run its CD workflow), or immediately with
`kubectl -n booking-prod rollout undo deployment/booking-api`.

## Database changes

Migrations live in `database.MIGRATIONS` and run before each rollout (init container).
Because old and new pods run side by side during a rolling update, make changes in
**expand → migrate → contract** steps: add columns/tables first (nullable or with defaults),
ship code that uses them, and only drop old columns in a later release. Never edit a
migration that has shipped; add a new one.

## One-time GitHub setup (repository settings)

| Setting | Value |
|---|---|
| Branch protection / ruleset on `main` | Require PR, 1 approval, Code Owner review, dismiss stale approvals; require status checks `lint`, `test (sqlite)`, `test (postgres)`, `frontend`, `docker`, `kubernetes`; require linear history; block force pushes and deletion |
| Tag ruleset `v*` | Only maintainers may create; block deletion and updates |
| Merge button | Allow squash merge only; auto-delete head branches |
| Environments | `staging` (no reviewers) and `production` (required reviewers, deployment branches/tags: `v*`). Add secrets to each: `KUBE_CONFIG`, `DATABASE_URL`, `REDIS_URL`, `MFA_ENCRYPTION_KEY`, optional `DUFFEL_ACCESS_TOKEN` |
| Code security | Enable Dependabot alerts, secret scanning and push protection |
| Packages | After the first image push, make `booking-assistant` private or public as needed and give the cluster pull access (imagePullSecret) if private |
