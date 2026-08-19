"""
Study Organizer — a tiny Flask app.

Flask is a Python library for building web apps. The core idea:
- You define "routes" (URLs) and a Python function that runs when
  someone visits that URL.
- That function can read/write data (via db.py) and hand it to an
  HTML template, which Flask fills in and sends back to the browser.
"""

from datetime import date, timedelta

from flask import Flask, render_template, request, redirect, url_for

import db

app = Flask(__name__)
db.init_db()


@app.before_request
def keep_recurring_tasks_fresh():
    # Cheap idempotent check — makes sure recurring tasks are generated
    # for the next several weeks before any page renders.
    db.ensure_recurring_instances(date.today())


@app.route("/")
def index():
    tasks = db.get_all_tasks()
    return render_template("index.html", tasks=tasks, roles=db.ROLES)


@app.route("/tasks", methods=["POST"])
def create_task():
    title = request.form.get("title", "").strip()
    role = request.form.get("role", "").strip()
    course = request.form.get("course", "").strip()
    opens_date = request.form.get("opens_date", "").strip()
    due_date = request.form.get("due_date", "").strip()

    if title and role in db.ROLES:
        db.add_task(title, role, course, opens_date, due_date)

    return redirect(url_for("index"))


@app.route("/tasks/<int:task_id>/edit", methods=["GET"])
def edit_task_form(task_id):
    task = db.get_task(task_id)
    if task is None:
        return redirect(url_for("index"))
    return render_template("edit.html", task=task, roles=db.ROLES)


@app.route("/tasks/<int:task_id>/edit", methods=["POST"])
def edit_task(task_id):
    title = request.form.get("title", "").strip()
    role = request.form.get("role", "").strip()
    course = request.form.get("course", "").strip()
    opens_date = request.form.get("opens_date", "").strip()
    due_date = request.form.get("due_date", "").strip()
    planned_date = request.form.get("planned_date", "").strip()

    if title and role in db.ROLES:
        db.update_task(task_id, title, role, course, opens_date, due_date, planned_date)

    return redirect(url_for("index"))


@app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task(task_id):
    task = db.get_task(task_id)
    if task is not None:
        db.set_completed(task_id, not task["completed"])
    return redirect(request.form.get("next") or url_for("index"))


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    db.delete_task(task_id)
    return redirect(request.form.get("next") or url_for("index"))


@app.route("/tasks/<int:task_id>/plan", methods=["POST"])
def plan_task(task_id):
    planned_date = request.form.get("planned_date", "").strip()
    db.set_planned_date(task_id, planned_date)
    return redirect(request.form.get("next") or url_for("calendar_view"))


def _week_start(week_param):
    """Turn a 'week=YYYY-MM-DD' query param into the Monday of that week. Defaults to this week."""
    if week_param:
        try:
            d = date.fromisoformat(week_param)
        except ValueError:
            d = date.today()
    else:
        d = date.today()
    return d - timedelta(days=d.weekday())


@app.route("/calendar")
def calendar_view():
    monday = _week_start(request.args.get("week"))
    sunday = monday + timedelta(days=6)

    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        days.append(day)

    planned = db.get_planned_tasks_in_range(monday.isoformat(), sunday.isoformat())
    tasks_by_day = {day.isoformat(): [] for day in days}
    for task in planned:
        if task["planned_date"] in tasks_by_day:
            tasks_by_day[task["planned_date"]].append(task)

    unplanned = db.get_unplanned_tasks()

    return render_template(
        "calendar.html",
        days=days,
        tasks_by_day=tasks_by_day,
        unplanned=unplanned,
        today=date.today(),
        monday=monday,
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        current_week=monday.isoformat(),
    )


@app.route("/recurring")
def recurring_view():
    templates = db.get_all_templates()
    return render_template(
        "recurring.html", templates=templates, roles=db.ROLES, weekday_names=db.WEEKDAY_NAMES
    )


@app.route("/recurring", methods=["POST"])
def create_template():
    title = request.form.get("title", "").strip()
    role = request.form.get("role", "").strip()
    course = request.form.get("course", "").strip()
    weekday = request.form.get("weekday", "").strip()
    until_date = request.form.get("until_date", "").strip()

    if title and role in db.ROLES and weekday.isdigit() and 0 <= int(weekday) <= 6:
        db.add_template(title, role, course, int(weekday), until_date)
        # Generate this template's instances immediately so it shows up right away.
        db.ensure_recurring_instances(date.today())

    return redirect(url_for("recurring_view"))


@app.route("/recurring/<int:template_id>/delete", methods=["POST"])
def delete_template(template_id):
    db.delete_template(template_id)
    return redirect(url_for("recurring_view"))


if __name__ == "__main__":
    # debug=True auto-reloads the server when you save a file, and
    # shows helpful error pages in the browser while we're developing.
    app.run(debug=True, host="0.0.0.0", port=5000)
