# AI booking assistant (Python + LangChain + MCP)

```
Browser ─► React (frontend/) ─/api─► api_server.py ─┐
You ─► agent.py ─MCP─► booking_server.py / travel_server.py ─┤
                                                         auth.py · db.py · travel/service.py
                                                              │                 │
                                          database.py ──► PostgreSQL     cache.py ──► Redis
                                          (SQLite locally)               (memory locally)
                                                                                │
                                                       Duffel / partner / mock supplier APIs
```

Sign-in is password + MFA everywhere; the servers take identity only from the session. Bookings
and cancellations in the chat pause for your approval.

| Environment | Database | Cache | How it runs |
|---|---|---|---|
| Local quick start | SQLite file | in-process | `python ...` |
| Local full stack | PostgreSQL 16 | Redis 7 | `docker compose up --build` |
| Kubernetes dev | PostgreSQL (in cluster) | Redis (in cluster) | `kubectl apply -k k8s/overlays/dev` |
| Staging / production | Managed PostgreSQL | Managed Redis | GitHub Actions CD → `k8s/overlays/{staging,production}` |

## Files

| File | Role |
|---|---|
| `auth.py` | Accounts, scrypt passwords, TOTP MFA, recovery codes, lockout, sessions, roles, audit log |
| `mcp_auth.py` | Authorization for MCP tools: resolves the caller from the session; HTTP bearer-token verifier |
| `signin.py` | Interactive sign-up / sign-in / MFA setup in the terminal |
| `auth_cli.py` | Account commands: register, login (token for HTTP clients), MFA, roles, unlock |
| `database.py` | SQLite/PostgreSQL access: pooled connections, transactions with locks, versioned migrations |
| `cache.py` | Redis cache (supplier searches, geocoding) and shared rate limiter, with in-memory fallback |
| `migrate.py` | Apply migrations / show status / purge expired offers (used by Kubernetes) |
| `db.py` | Appointment data layer and business rules (hours, overlaps, validation) |
| `booking_server.py` | MCP server: appointments (+ `admin_list_bookings`) |
| `agent.py` | LangChain agent: sign-in, MCP client, memory, human approval, chat loop |
| `travel_server.py` | MCP server: search/book flights, hotels, cars, trains; find/cancel trips (+ `admin_list_trips`) |
| `travel/service.py` | Travel validation and booking rules (dates, IATA codes, passengers, ownership) |
| `travel/store.py` | Stores search offers server-side (the LLM only sees an `offer_id`) and confirmed trips |
| `travel/duffel.py` | Duffel API integration for flights and hotel stays |
| `travel/partner.py` | Generic REST adapter for a car-hire / rail partner API |
| `travel/mock.py` | Offline sandbox inventory for all four, used by default |
| `test_auth.py` | Auth unit tests: RFC 6238 vectors, replay, recovery codes, lockout, sessions, roles |
| `test_server.py`, `test_travel.py` | Server tests over MCP as different users, including authorization checks |
| `test_duffel.py` | Checks the Duffel request/response mapping against a fake HTTP transport |
| `test_platform.py` | Migrations, concurrent double-booking, transactions, cache and rate limiter |
| `run_tests.py` | Runs every suite in its own process (SQLite, or PostgreSQL + Redis with `TEST_*_URL`) |
| `Dockerfile`, `docker-compose.yml` | Image (UI + API, non-root) and the local PostgreSQL + Redis stack |
| `k8s/` | Kustomize base + dev/staging/production overlays ([k8s/README.md](k8s/README.md)) |
| `.github/` | CI, CD, reusable deploy, Dependabot, PR template, CODEOWNERS ([CONTRIBUTING.md](CONTRIBUTING.md)) |

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your API key in .env

python run_tests.py                # 1. all test suites (throwaway SQLite databases)
python agent.py                    # 2. sign up / sign in, then chat
```

### Full stack with PostgreSQL and Redis (Docker)

```bash
docker compose up --build          # http://localhost:8080 (APP_PORT=8088 if 8080 is taken)
docker compose exec api python auth_cli.py set-role you@example.com admin
docker compose run --rm migrate python migrate.py --status
docker compose down                # add -v to delete the database volume
```

Run the tests against PostgreSQL and Redis (CI does this on every pull request):

```bash
docker run -d --rm --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test -p 55432:5432 postgres:16-alpine
docker run -d --rm --name redis -p 56379:6379 redis:7-alpine
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/booking_test TEST_REDIS_URL=redis://localhost:56379/0 python run_tests.py
```

Each suite gets its own PostgreSQL schema and Redis key prefix, removed afterwards.

### What Redis is used for

| Use | Why | TTL |
|---|---|---|
| Supplier search results | Duffel and partner APIs are slow and rate-limited; identical searches reuse the answer. Every user still gets their own offer ids | `SEARCH_CACHE_TTL` (300 s) |
| Geocoding (hotel city → coordinates) | Rarely changes | 30 days |
| Login rate limit | Must be shared by all API replicas | 5 min window |

Sessions are deliberately **not** cached, so logout and revocation take effect at once. If Redis
is down the app keeps working: cache misses, and rate limits fall back to per-process counters.

## CI/CD and deployment

- **Git workflow** (branches, commits, PRs, releases, hotfixes, GitHub settings): [CONTRIBUTING.md](CONTRIBUTING.md)
- **CI** (`.github/workflows/ci.yml`, every PR): ruff lint, all tests on SQLite *and* PostgreSQL + Redis,
  frontend build + `npm audit`, Docker build + container smoke test + Trivy scan, Kubernetes manifest validation.
- **CD** (`.github/workflows/cd.yml`): push to `main` → CI → image pushed to GHCR (SBOM + provenance) →
  deploy to **staging** by digest; tag `vX.Y.Z` → same flow → **production** after approval.
  Failed rollouts or smoke tests roll back automatically.
- **Kubernetes**: [k8s/README.md](k8s/README.md)

Try: "I'd like a haircut next Tuesday afternoon", then "What bookings do I have?", then "Cancel it".

## Web app (React)

```
Browser ──► frontend/ (React + Vite) ──/api──► api_server.py (FastAPI) ──► auth.py + travel/service.py
```

The booking bar has tabs for Flights, Hotels, Cars, Trains and My trips. Admins also get an
**Admin** tab with every user and every booking. Each search form has date pickers, location
suggestions and, where it applies, a time range:

| Tab | Dates and times |
|---|---|
| Flights | Depart/return dates; departure time range (Morning / Afternoon / Evening or custom) |
| Hotels | Check-in and check-out dates |
| Cars | Pick-up and drop-off date and time; optional different drop-off location |
| Trains | Date; departure time range |

Run it in development (Node 18+):

```bash
uvicorn api_server:app --port 8080          # terminal 1: API
cd frontend && npm install && npm run dev   # terminal 2: open http://localhost:5173
```

For production, `cd frontend && npm run build`, then `uvicorn api_server:app --port 8080`
serves the built app and the API from one origin. Put it behind HTTPS and set
`COOKIE_SECURE=true`. API docs are at `/api/docs`.

Web security: the session is an httpOnly `SameSite=Strict` cookie that JavaScript can't read.
State-changing calls need the `X-Requested-With: booking-web` header (CSRF). Sign-in POSTs are
rate-limited per IP. Responses carry a strict Content-Security-Policy. Sign-in is password →
short-lived single-use challenge → MFA code; a password alone can never re-pair MFA. The UI
hides the Admin tab from customers, and the server independently returns `403` for `/api/admin/*`.

```bash
python test_api.py      # REST API: sign-up + MFA, CSRF, time ranges, bookings, roles, logout
```

## Authentication, authorization and MFA

**Sign-in.** `agent.py` asks you to sign in or create an account. Every account sets up an
authenticator app (Google or Microsoft Authenticator, 1Password, Authy...) on first sign-in;
after that, each sign-in needs the 6-digit code. You also get 8 one-time recovery codes in case
you lose your phone. Install `qrcode` to get a scannable QR code; otherwise type the setup key.

| Control | How |
|---|---|
| Passwords | scrypt (N=2^15, r=8, p=1), per-user salt, 12-128 characters, constant-time compare |
| MFA | TOTP (RFC 6238, 30 s, 6 digits, ±1 step drift); each code works once; secrets Fernet-encrypted at rest |
| Recovery codes | 8 one-time codes, stored as SHA-256 hashes |
| Brute force | 5 failed password or MFA attempts lock the account for 15 minutes |
| Enumeration | Same error and similar timing for an unknown email and a wrong password |
| Sessions | Random 256-bit tokens stored hashed; expire after `SESSION_HOURS` (8); revoked on quit, password change and MFA reset |
| Audit | Sign-ins, failures, lockouts, MFA and role changes are written to the `auth_events` table |

**Authorization.** Tools never take an email or customer name as an argument. The servers look
up the caller from the session on every call, so neither the user nor a prompt injection can make
the model act for another account.

| Who | Can |
|---|---|
| Not signed in | Nothing: every tool returns "You need to sign in first" |
| `customer` | Search; book for their own account; see and cancel only their own bookings and trips; book only offers from their own searches |
| `admin` | Everything a customer can, plus `admin_list_bookings`, `admin_list_trips`, and cancelling anyone's booking. Admins always need MFA, even with `REQUIRE_MFA=false` |

```bash
python auth_cli.py set-role you@example.com admin   # make an admin
python auth_cli.py reset-mfa user@example.com       # user lost phone and recovery codes
python auth_cli.py unlock user@example.com          # clear a lockout
python auth_cli.py recovery-codes                   # new set of recovery codes
python auth_cli.py change-password
```

## Use the servers from other MCP clients

Over HTTP, every request needs a session token:

```bash
python auth_cli.py login            # password + MFA, prints a bs_... token
python booking_server.py --http     # http://localhost:8000/mcp
python travel_server.py --http      # http://localhost:8001/mcp
```

Configure your MCP client to send `Authorization: Bearer <token>`. Requests without a valid
token get `401`. Revoke a token with `python auth_cli.py logout <token>`. On a real domain,
set `PUBLIC_BASE_URL` and serve over HTTPS only.

For stdio clients (Claude Desktop, IDEs), set `BOOKING_SESSION_TOKEN=<token>` in the server's
`env`. The server then acts as that user until the token expires.

## Travel booking

Every travel booking is two steps: `search_*` returns offers with an `offer_id`, then `book_*`
books one. Prices and supplier ids stay in the database, so the model can't change them. An
offer can only be booked once, and only by the user whose search produced it.

Pick an API per kind in `.env`:

| Kind | `mock` (default) | Real API |
|---|---|---|
| Flight | ✓ | `FLIGHT_PROVIDER=duffel` + `DUFFEL_ACCESS_TOKEN` |
| Hotel | ✓ | `HOTEL_PROVIDER=duffel` (enable Duffel Stays on your account) |
| Car | ✓ | `CAR_PROVIDER=partner` + `CAR_API_URL` / `CAR_API_KEY` |
| Train | ✓ | `TRAIN_PROVIDER=partner` + `TRAIN_API_URL` / `TRAIN_API_KEY` |

Use a `duffel_test_` token while developing: it books the "Duffel Airways" sandbox and pays
from a fake balance. A `duffel_live_` token makes real, paid bookings from your Duffel balance.

Car-hire and rail booking APIs (Booking.com Demand API, Trainline, Rail Europe, SilverRail...)
are only available under a partner contract and each has its own format, so `partner.py` defines
a small JSON contract to put in front of whichever one you sign with. You can also subclass
`TravelProvider` in `travel/base.py` and register it in `travel/registry.py`.

Try: "Find me a return flight London to Lisbon on the 3rd for 2 adults", "and a hotel for the week",
"I'll need a car from the airport too", then "What trips do I have?".

## Before production

- Verify email addresses at sign-up (send a link or code) before allowing bookings, and add
  self-service password reset by email. Neither is implemented yet.
- For a web or multi-service deployment, consider an identity provider (Auth0, Entra ID,
  Cognito, Keycloak) with OAuth 2.1 / OIDC: verify its JWTs in `mcp_auth.SessionTokenVerifier`
  and let it own passwords and MFA, including passkeys/WebAuthn.
- Load `MFA_ENCRYPTION_KEY` from a secrets manager instead of the generated `.mfa.key` file.
- Replace `InMemorySaver` with a persistent checkpointer (Postgres or SQLite).
- Add time zones, staff/resources and reminder emails.
- Set up PostgreSQL backups/PITR (managed services provide this) and monitoring (Prometheus metrics,
  log shipping, alerts on `/readyz` failures and 5xx rates).
- Add tests that run the agent against scripted conversations.
- Travel: take payment from the customer (e.g. Duffel Payments or Stripe) before booking instead
  of paying from your Duffel balance, show fare/cancellation rules, and reconcile any booking
  that the supplier confirmed but `store.save_booking` failed to record.
