"""Real persistence for saved, reusable agents.

Before this, "save an agent, see its versions, see its run history" was
mockup-only UI with no backend behind it. This is a plain SQLite store -
no external service, no new credentials, just a file on disk - for the
three things a "reusable agent" concept actually needs to be real:
an agent's current prompt/settings, its version history when that prompt
changes, and a log of its runs (which real /tasks job backed each one,
and what it produced).

SQLite calls are synchronous; every call site wraps them in
`asyncio.to_thread` to avoid blocking the event loop, same pattern already
used for the blocking boto3 Bedrock call in api.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

def data_dir() -> Path:
    """Where persistent state lives.

    Overridable via AX_DATA_DIR so a container can mount a volume over it -
    without that, a redeploy replaces the image and every saved agent, run and
    login profile goes with it. Not derived from `__file__`, which resolves
    inside site-packages under this project's non-editable install.
    """
    override = os.environ.get("AX_DATA_DIR")
    return Path(override) if override else Path.cwd() / "data"


DB_PATH = data_dir() / "browser-mcp.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                session TEXT NOT NULL,
                browser_mode TEXT NOT NULL DEFAULT 'Standard Browser',
                persistent INTEGER NOT NULL DEFAULT 0,
                webhook_url TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_versions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                plan TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                version_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                structured_result TEXT,
                error TEXT,
                started_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                session TEXT NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                structured_result TEXT,
                error TEXT,
                log TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_schedules (
                agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                hour INTEGER NOT NULL DEFAULT 9,
                minute INTEGER NOT NULL DEFAULT 0,
                weekday INTEGER,
                day_of_month INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                last_run_at REAL
            );
            """
        )
        # `plan` arrived after agents already existed on disk, so it is added
        # rather than assumed. A version without one runs through the model,
        # which is exactly the behaviour those agents already had.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_versions)")}
        if "plan" not in columns:
            conn.execute("ALTER TABLE agent_versions ADD COLUMN plan TEXT")


def create_agent(
    name: str,
    prompt: str,
    browser_mode: str = "Standard Browser",
    plan: list[dict] | None = None,
) -> dict:
    agent_id = uuid.uuid4().hex[:12]
    version_id = uuid.uuid4().hex[:12]
    session = f"agent-{agent_id}"  # each saved agent gets its own dedicated, stable browser identity
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, session, browser_mode, persistent, created_at) VALUES (?,?,?,?,0,?)",
            (agent_id, name, session, browser_mode, now),
        )
        conn.execute(
            "INSERT INTO agent_versions (id, agent_id, version_number, prompt, note, plan, created_at) VALUES (?,?,1,?,?,?,?)",
            (version_id, agent_id, prompt, "Initial build", json.dumps(plan) if plan else None, now),
        )
    return get_agent(agent_id)


def list_agents() -> list[dict]:
    """Every agent with the summary fields the list view filters and sorts on.

    Computed here rather than in the UI because the counts live in tables the
    list query already has open - fetching them client-side would mean one
    extra request per agent just to render a card.
    """
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        out = []
        for row in rows:
            latest = conn.execute(
                "SELECT * FROM agent_versions WHERE agent_id=? ORDER BY version_number DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            stats = conn.execute(
                "SELECT COUNT(*) AS n, MAX(started_at) AS last FROM agent_runs WHERE agent_id=?",
                (row["id"],),
            ).fetchone()
            sched = conn.execute(
                "SELECT active FROM agent_schedules WHERE agent_id=?", (row["id"],)
            ).fetchone()
            out.append(
                {
                    **dict(row),
                    "latest_version": dict(latest) if latest else None,
                    "run_count": stats["n"] or 0,
                    "last_run_at": stats["last"],
                    "schedule_active": bool(sched["active"]) if sched else False,
                }
            )
        return out


def get_agent(agent_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            return None
        versions = conn.execute(
            "SELECT * FROM agent_versions WHERE agent_id=? ORDER BY version_number DESC", (agent_id,)
        ).fetchall()
        schedule = conn.execute("SELECT * FROM agent_schedules WHERE agent_id=?", (agent_id,)).fetchone()
        return {
            **dict(row),
            "versions": [dict(v) for v in versions],
            "schedule": dict(schedule) if schedule else None,
        }


def latest_version(agent_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_versions WHERE agent_id=? ORDER BY version_number DESC LIMIT 1", (agent_id,)
        ).fetchone()
        return dict(row) if row else None


def add_version(agent_id: str, prompt: str, note: str, plan: list[dict] | None = None) -> dict:
    with _conn() as conn:
        last = conn.execute(
            "SELECT MAX(version_number) AS n FROM agent_versions WHERE agent_id=?", (agent_id,)
        ).fetchone()
        next_number = (last["n"] or 0) + 1
        version_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO agent_versions (id, agent_id, version_number, prompt, note, plan, created_at) VALUES (?,?,?,?,?,?,?)",
            (version_id, agent_id, next_number, prompt, note, json.dumps(plan) if plan else None, time.time()),
        )
        return {
            "id": version_id,
            "agent_id": agent_id,
            "version_number": next_number,
            "prompt": prompt,
            "note": note,
            "plan": json.dumps(plan) if plan else None,
        }


def update_agent_settings(agent_id: str, webhook_url: str | None = None, persistent: bool | None = None) -> None:
    with _conn() as conn:
        if webhook_url is not None:
            conn.execute("UPDATE agents SET webhook_url=? WHERE id=?", (webhook_url, agent_id))
        if persistent is not None:
            conn.execute("UPDATE agents SET persistent=? WHERE id=?", (1 if persistent else 0, agent_id))


def delete_agent(agent_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        return cur.rowcount > 0


def start_run(agent_id: str, version_id: str, job_id: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_runs (id, agent_id, version_id, job_id, status, started_at) VALUES (?,?,?,?,'running',?)",
            (run_id, agent_id, version_id, job_id, time.time()),
        )
    return run_id


def finish_run(run_id: str, status: str, result: str | None, structured_result: str | None, error: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE agent_runs SET status=?, result=?, structured_result=?, error=?, finished_at=? WHERE id=?",
            (status, result, structured_result, error, time.time(), run_id),
        )


def list_runs(agent_id: str, limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY started_at DESC LIMIT ?", (agent_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def set_schedule(agent_id: str, kind: str, hour: int, minute: int, weekday: int | None, day_of_month: int | None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO agent_schedules (agent_id, kind, hour, minute, weekday, day_of_month, active)
               VALUES (?,?,?,?,?,?,1)
               ON CONFLICT(agent_id) DO UPDATE SET
                 kind=excluded.kind, hour=excluded.hour, minute=excluded.minute,
                 weekday=excluded.weekday, day_of_month=excluded.day_of_month, active=1""",
            (agent_id, kind, hour, minute, weekday, day_of_month),
        )


def set_schedule_active(agent_id: str, active: bool) -> None:
    with _conn() as conn:
        conn.execute("UPDATE agent_schedules SET active=? WHERE agent_id=?", (1 if active else 0, agent_id))


def active_schedules() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM agent_schedules WHERE active=1").fetchall()
        return [dict(r) for r in rows]


def mark_schedule_ran(agent_id: str, when: float) -> None:
    with _conn() as conn:
        conn.execute("UPDATE agent_schedules SET last_run_at=? WHERE agent_id=?", (when, agent_id))


def save_job(
    job_id: str,
    session: str,
    task: str,
    status: str,
    result: str | None,
    structured_result: str | None,
    error: str | None,
    log_json: str,
    created_at: float,
) -> None:
    """Upsert a job so run history outlives the server process.

    The live in-memory record keeps the asyncio handles and the Bedrock
    message history; this keeps the parts a person would want to look up
    later - what was asked, what came back, and the activity trail.
    """
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs (id, session, task, status, result, structured_result, error, log, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status, result=excluded.result,
                 structured_result=excluded.structured_result, error=excluded.error,
                 log=excluded.log, updated_at=excluded.updated_at""",
            (
                job_id,
                session,
                task,
                status,
                result,
                structured_result,
                error,
                log_json,
                created_at,
                time.time(),
            ),
        )


def _job_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "job_id": row["id"],
        "session": row["session"],
        "task": row["task"],
        "status": row["status"],
        "result": row["result"],
        "structured_result": json.loads(row["structured_result"]) if row["structured_result"] else None,
        "error": row["error"],
        "log": json.loads(row["log"]) if row["log"] else [],
        "created_at": row["created_at"],
        "from_history": True,
    }


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_row_to_dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_job_row_to_dict(r) for r in rows]
