"""Small, durable SQLite store. Run one application worker/instance."""
import json
import math
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, created REAL, body TEXT);
                CREATE TABLE IF NOT EXISTS budgets (day TEXT, provider TEXT, used INTEGER,
                    PRIMARY KEY(day, provider));
                CREATE TABLE IF NOT EXISTS cooldowns (provider TEXT PRIMARY KEY, until_at REAL);
                CREATE TABLE IF NOT EXISTS provider_state (
                    provider TEXT PRIMARY KEY, next_request REAL DEFAULT 0, failures INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS payments (
                    fingerprint TEXT PRIMARY KEY, resource TEXT NOT NULL, state TEXT NOT NULL,
                    body TEXT NOT NULL, receipt TEXT, updated REAL NOT NULL, proof TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def cached(self, key, max_age):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row["created"] < max_age:
            return json.loads(row["body"])
        return None

    def cache(self, key, body):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(body)))

    def budget(self, provider, limit):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO budgets VALUES (?,?,0)", (day, provider))
            count = db.execute("SELECT used FROM budgets WHERE day=? AND provider=?", (day, provider)).fetchone()[0]
            if count >= limit:
                return False
            db.execute("UPDATE budgets SET used=used+1 WHERE day=? AND provider=?", (day, provider))
        return True

    def cooldown(self, provider, seconds=None):
        with self.connect() as db:
            if seconds is not None:
                db.execute("INSERT OR REPLACE INTO cooldowns VALUES (?,?)", (provider, time.time() + seconds))
            row = db.execute("SELECT until_at FROM cooldowns WHERE provider=?", (provider,)).fetchone()
        return bool(row and row[0] > time.time())

    def cooldown_remaining(self, provider):
        with self.connect() as db:
            row = db.execute("SELECT until_at FROM cooldowns WHERE provider=?", (provider,)).fetchone()
        return max(0, math.ceil(row[0] - time.time())) if row else 0

    def reserve_request(self, provider, interval):
        """Reserve one slot atomically. A rejected reservation consumes no slot."""
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO provider_state(provider) VALUES (?)", (provider,))
            next_at = db.execute("SELECT next_request FROM provider_state WHERE provider=?", (provider,)).fetchone()[0]
            if next_at > now:
                return next_at - now
            db.execute("UPDATE provider_state SET next_request=? WHERE provider=?", (now + interval, provider))
        return 0

    def provider_failure(self, provider, minimum=0):
        """Persist exponential backoff across assets and application restarts."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO provider_state(provider) VALUES (?)", (provider,))
            failures = db.execute("SELECT failures FROM provider_state WHERE provider=?", (provider,)).fetchone()[0] + 1
            delay = max(minimum, min(3600, 60 * 2 ** min(failures - 1, 6)))
            db.execute("UPDATE provider_state SET failures=? WHERE provider=?", (failures, provider))
            db.execute("INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET until_at=MAX(until_at,excluded.until_at)", (provider, time.time() + delay))
        return self.cooldown_remaining(provider)

    def provider_success(self, provider):
        with self.connect() as db:
            db.execute("UPDATE provider_state SET failures=0 WHERE provider=?", (provider,))
            db.execute("DELETE FROM cooldowns WHERE provider=?", (provider,))

    def payment(self, fingerprint):
        with self.connect() as db:
            row = db.execute("SELECT * FROM payments WHERE fingerprint=?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def claim(self, fingerprint, resource, body, proof):
        with self.connect() as db:
            result = db.execute("INSERT OR IGNORE INTO payments VALUES (?,?,?,?,NULL,?,?)",
                                (fingerprint, resource, "verifying", json.dumps(body), time.time(), proof))
        return result.rowcount == 1

    def payment_state(self, fingerprint, state, receipt=None):
        with self.connect() as db:
            db.execute("UPDATE payments SET state=?,receipt=?,updated=? WHERE fingerprint=?",
                       (state, json.dumps(receipt) if receipt else None, time.time(), fingerprint))
