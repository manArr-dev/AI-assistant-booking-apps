"""
Tests for the platform layer: database migrations, concurrency, cache and rate limiting.
Runs on SQLite + in-memory cache by default; on PostgreSQL/Redis when TEST_DATABASE_URL /
TEST_REDIS_URL are set (as in CI).

    python test_platform.py
"""

import test_helpers  # noqa: F401, I001  (must come first: isolates env + database)

# isort: split
import threading
from datetime import date, timedelta

import cache
import database
import db
from travel import service


def test_migrations_are_idempotent():
    database._migrated.clear()
    assert database.migrate() == [], "already applied by test_helpers"
    with database.connect() as conn:
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [m[0] for m in database.MIGRATIONS]


def test_concurrent_bookings_cannot_double_book():
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    results, barrier = [], threading.Barrier(8)

    def attempt(i):
        barrier.wait()
        results.append(db.create_booking("haircut", d.isoformat(), "15:00", f"User {i}", f"u{i}@example.com"))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r["ok"]]
    assert len(winners) == 1, results
    assert all("taken" in r["error"] for r in results if not r["ok"])


def test_transaction_rolls_back():
    try:
        with database.transaction() as conn:
            conn.execute("INSERT INTO services (id, name, duration_min, price) VALUES (?, ?, ?, ?)",
                         ("tmp", "Temp", 10, 1.0))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with database.connect() as conn:
        assert conn.execute("SELECT id FROM services WHERE id = ?", ("tmp",)).fetchone() is None


def test_cache_roundtrip_and_ttl_zero():
    calls = []

    def compute():
        calls.append(1)
        return {"value": len(calls)}

    assert cache.cached("t", 60, ("a",), compute) == {"value": 1}
    assert cache.cached("t", 60, ("a",), compute) == {"value": 1}, "second call is a hit"
    assert cache.cached("t", 60, ("b",), compute) == {"value": 2}, "different key misses"
    assert cache.cached("t", 0, ("a",), compute) == {"value": 3}, "ttl 0 disables caching"


def test_search_results_are_cached_but_offer_ids_are_per_user():
    from travel.registry import get_provider

    provider = get_provider("flight")
    calls = []
    original = provider.search
    provider.search = lambda q: (calls.append(q), original(q))[1]
    try:
        day = (date.today() + timedelta(days=30)).isoformat()
        a = service.search_flights("LHR", "CDG", day, user_id=1)
        b = service.search_flights("LHR", "CDG", day, user_id=2)
    finally:
        provider.search = original
    assert len(calls) == 1, "second identical search should come from the cache"
    assert [o["total_price"] for o in a["offers"]] == [o["total_price"] for o in b["offers"]]
    assert not {o["offer_id"] for o in a["offers"]} & {o["offer_id"] for o in b["offers"]}


def test_rate_limit():
    assert not any(cache.hit_rate_limit("unit", 3, 60) for _ in range(3))
    assert cache.hit_rate_limit("unit", 3, 60)


def test_health_pings():
    assert database.ping()
    assert cache.ping() in (None, True)


if __name__ == "__main__":
    engine = "postgresql" if database.is_postgres() else "sqlite"
    redis = "redis" if cache.redis_url() else "memory cache"
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"\nAll {len(tests)} platform checks passed ({engine}, {redis}).")
