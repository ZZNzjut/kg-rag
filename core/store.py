"""持久化：SQLite（文件元数据、问答历史）+ 带 TTL 的缓存表（模拟 Redis）。

参考实现里 Redis 负责问答缓存、MySQL 负责历史；本地复现统一用 SQLite，
但接口（get/set/ttl/delete）保持一致，换成真实 Redis 只需改这一层。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from . import config

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  file_id TEXT PRIMARY KEY,
  title TEXT, ext TEXT, name TEXT, size INTEGER,
  chunks INTEGER, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS qa_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id TEXT, question TEXT, answer TEXT,
  citations TEXT, routes TEXT, use_graph INTEGER,
  latency_ms REAL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS cache (
  k TEXT PRIMARY KEY, v TEXT, expires_at REAL
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


init_db()


# ---------- 文件 ----------
def upsert_file(meta: Dict) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO files(file_id,title,ext,name,size,chunks,created_at) VALUES(?,?,?,?,?,?,?)",
            (meta["file_id"], meta.get("title", ""), meta.get("ext", ""), meta.get("name", ""),
             meta.get("size", 0), meta.get("chunks", 0), int(time.time())),
        )
        conn.commit()


def list_files() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM files ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_file(file_id: str) -> Optional[Dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def delete_file(file_id: str) -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))
        conn.commit()


# ---------- 历史 ----------
def add_history(item: Dict) -> int:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO qa_history(file_id,question,answer,citations,routes,use_graph,latency_ms,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (item.get("file_id", ""), item["question"], item["answer"],
             json.dumps(item.get("citations", []), ensure_ascii=False),
             json.dumps(item.get("routes", {}), ensure_ascii=False),
             1 if item.get("use_graph") else 0, item.get("latency_ms", 0.0), int(time.time())),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_history(limit: int = 20) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM qa_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d.get("citations") or "[]")
        d["routes"] = json.loads(d.get("routes") or "{}")
        out.append(d)
    return out


def clear_history() -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM qa_history")
        conn.commit()


# ---------- 缓存（TTL） ----------
def cache_get(key: str):
    now = time.time()
    with _conn() as conn:
        row = conn.execute("SELECT v,expires_at FROM cache WHERE k=?", (key,)).fetchone()
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < now:
        return None
    try:
        return json.loads(row["v"])
    except Exception:
        return None


def cache_set(key: str, value, ttl: int = 300) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache(k,v,expires_at) VALUES(?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), time.time() + ttl),
        )
        conn.commit()


def cache_clear() -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM cache")
        conn.commit()


def stats() -> Dict:
    with _conn() as conn:
        files = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        hist = conn.execute("SELECT COUNT(*) c FROM qa_history").fetchone()["c"]
        cache = conn.execute("SELECT COUNT(*) c FROM cache").fetchone()["c"]
    return {"files": files, "history": hist, "cache": cache}