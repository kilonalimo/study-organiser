"""
Database layer — every function that touches the database lives here,
so app.py doesn't need to know any SQL.

We're using SQLite: a whole database stored in a single file
(study.db), no separate server to install or run. Perfect for one
person's app running on one machine.
"""

import sqlite3
from datetime import timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "study.db"

# The three "hats" the app organizes tasks by.
ROLES = ["Student", "TA", "Association"]

# Python's date.weekday(): Monday = 0 ... Sunday = 6
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# How many weeks ahead we keep recurring tasks generated for.
RECURRING_WEEKS_AHEAD = 8


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    # Makes rows behave like dictionaries (row["title"]) instead of
    # plain tuples (row[0]) — much easier to read in templates.
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist yet. Safe to call every time the app starts."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            role TEXT NOT NULL,
            course TEXT,
            opens_date TEXT,
            due_date TEXT,
            planned_date TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            recurring_template_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recurring_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            role TEXT NOT NULL,
            course TEXT,
            weekday INTEGER NOT NULL,
            until_date TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


# ---------- tasks ----------

def get_all_tasks():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY completed ASC, due_date IS NULL, due_date ASC"
    ).fetchall()
    conn.close()
    return rows


def get_task(task_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row


def get_planned_tasks_in_range(start_date, end_date):
    """Tasks whose planned_date falls within [start_date, end_date], both ISO 'YYYY-MM-DD' strings."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE planned_date IS NOT NULL AND planned_date != ''
          AND planned_date >= ? AND planned_date <= ?
        ORDER BY planned_date ASC
        """,
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return rows


def get_unplanned_tasks():
    """Tasks with no planned_date yet, not yet completed — the ones still waiting to be put on the calendar."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE (planned_date IS NULL OR planned_date = '')
          AND completed = 0
        ORDER BY due_date IS NULL, due_date ASC
        """
    ).fetchall()
    conn.close()
    return rows


def add_task(title, role, course, opens_date, due_date, planned_date=None, recurring_template_id=None):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO tasks (title, role, course, opens_date, due_date, planned_date, recurring_template_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (title, role, course or None, opens_date or None, due_date or None, planned_date or None, recurring_template_id),
    )
    conn.commit()
    conn.close()


def update_task(task_id, title, role, course, opens_date, due_date, planned_date):
    conn = get_connection()
    conn.execute(
        """
        UPDATE tasks
        SET title = ?, role = ?, course = ?, opens_date = ?, due_date = ?, planned_date = ?
        WHERE id = ?
        """,
        (title, role, course or None, opens_date or None, due_date or None, planned_date or None, task_id),
    )
    conn.commit()
    conn.close()


def set_planned_date(task_id, planned_date):
    conn = get_connection()
    conn.execute("UPDATE tasks SET planned_date = ? WHERE id = ?", (planned_date or None, task_id))
    conn.commit()
    conn.close()


def set_completed(task_id, completed):
    conn = get_connection()
    conn.execute("UPDATE tasks SET completed = ? WHERE id = ?", (1 if completed else 0, task_id))
    conn.commit()
    conn.close()


def delete_task(task_id):
    conn = get_connection()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


# ---------- recurring templates ----------

def get_all_templates():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM recurring_templates ORDER BY weekday ASC").fetchall()
    conn.close()
    return rows


def add_template(title, role, course, weekday, until_date=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO recurring_templates (title, role, course, weekday, until_date) VALUES (?, ?, ?, ?, ?)",
        (title, role, course or None, weekday, until_date or None),
    )
    conn.commit()
    conn.close()


def delete_template(template_id, delete_future_instances=True):
    conn = get_connection()
    if delete_future_instances:
        # Only remove instances that haven't happened yet and aren't done,
        # so history (past occurrences, completed ones) stays intact.
        conn.execute(
            """
            DELETE FROM tasks
            WHERE recurring_template_id = ? AND completed = 0
            """,
            (template_id,),
        )
    else:
        conn.execute(
            "UPDATE tasks SET recurring_template_id = NULL WHERE recurring_template_id = ?",
            (template_id,),
        )
    conn.execute("DELETE FROM recurring_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()


def ensure_recurring_instances(today):
    """
    Make sure every active recurring template has a generated task for each
    of the next RECURRING_WEEKS_AHEAD weeks (starting this week). Safe to
    call often — it only inserts instances that don't already exist yet.
    """
    conn = get_connection()
    templates = conn.execute("SELECT * FROM recurring_templates").fetchall()
    if not templates:
        conn.close()
        return

    this_monday = today - timedelta(days=today.weekday())

    for template in templates:
        existing_dates = {
            row["due_date"]
            for row in conn.execute(
                "SELECT due_date FROM tasks WHERE recurring_template_id = ?",
                (template["id"],),
            ).fetchall()
        }
        for week in range(RECURRING_WEEKS_AHEAD):
            occurrence = this_monday + timedelta(days=week * 7 + template["weekday"])
            if occurrence < today:
                continue  # don't backfill days already passed this week
            iso = occurrence.isoformat()
            if template["until_date"] and iso > template["until_date"]:
                break  # past the requested end date — stop generating further weeks
            if iso in existing_dates:
                continue
            conn.execute(
                """
                INSERT INTO tasks (title, role, course, due_date, planned_date, recurring_template_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (template["title"], template["role"], template["course"], iso, iso, template["id"]),
            )

    conn.commit()
    conn.close()
