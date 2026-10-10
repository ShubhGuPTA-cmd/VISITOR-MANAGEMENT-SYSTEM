import json
import sqlite3
import time
from contextlib import contextmanager

import numpy as np

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hosts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT DEFAULT '', phone TEXT DEFAULT '',
  unit TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '',
  kind TEXT DEFAULT 'visitor',           -- visitor | staff | resident | vip
  status TEXT DEFAULT 'active',          -- active | blocked
  host_id INTEGER, notes TEXT DEFAULT '', photo TEXT DEFAULT '', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  vec BLOB NOT NULL, quality REAL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER, name TEXT DEFAULT '', phone TEXT DEFAULT '',
  email TEXT DEFAULT '', purpose TEXT DEFAULT '', host_id INTEGER,
  status TEXT NOT NULL,                  -- pending | approved | denied | blocked | expired
  method TEXT DEFAULT 'face',            -- face (auto) | manual (host approved)
  score REAL DEFAULT 0, snapshot TEXT DEFAULT '', token TEXT NOT NULL, vec BLOB,
  created_at REAL NOT NULL, decided_at REAL, decided_by TEXT DEFAULT '', checkin_at REAL, checkout_at REAL);
CREATE INDEX IF NOT EXISTS idx_visits_created ON visits(created_at);
CREATE INDEX IF NOT EXISTS idx_visits_status ON visits(status);
CREATE INDEX IF NOT EXISTS idx_emb_person ON embeddings(person_id);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SNAP_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


def get_settings() -> dict:
    s = dict(config.DEFAULT_SETTINGS)
    with conn() as c:
        for r in c.execute("SELECT key,value FROM settings"):
            if r["key"] in s:
                try:
                    s[r["key"]] = json.loads(r["value"])
                except ValueError:
                    pass
    return s


def set_settings(values: dict):
    with conn() as c:
        for k, v in values.items():
            if k in config.DEFAULT_SETTINGS:
                c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (k, json.dumps(v)))


def vec_to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def now() -> float:
    return time.time()
