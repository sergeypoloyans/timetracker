"""
Веб-приложение для учёта рабочего времени сотрудников.

Запуск:  python app.py   → открыть http://127.0.0.1:5000

Бэкенд отдаёт одну HTML-страницу и небольшой JSON API:
    GET  /api/employees                  — список сотрудников (+ запущенный таймер)
    POST /api/employees                  — добавить сотрудника {"name": "..."}
    POST /api/employees/<id>/start       — запустить таймер {"description": "..."}
    POST /api/employees/<id>/stop        — остановить таймер
    GET  /api/report?from=...&to=...     — записи и итоги за период (даты YYYY-MM-DD)
"""

import os
import sqlite3
from datetime import datetime, date, timedelta

from flask import Flask, jsonify, request, render_template, g

import db

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def create_app(db_path=None, clock=None):
    """
    Создаёт приложение.

    db_path — путь к файлу базы (в тестах подставляем временный файл).
    clock   — функция, возвращающая текущее время. В тестах подменяем её,
              чтобы время было предсказуемым.
    """
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("TIMETRACKER_DB", "timetracker.db")
    app.config["CLOCK"] = clock or datetime.now
    app.json.ensure_ascii = False  # кириллица в JSON без А...

    db.init_db(app.config["DB_PATH"])

    # ---------- вспомогательные функции ----------

    def get_db():
        """Одно соединение на один запрос (хранится в объекте g)."""
        if "db" not in g:
            g.db = db.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def now():
        # Отбрасываем микросекунды: секундной точности достаточно.
        return app.config["CLOCK"]().replace(microsecond=0)

    def error(message, status):
        return jsonify({"error": message}), status

    def find_employee(employee_id):
        return get_db().execute(
            "SELECT id, name FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()

    def running_entry(employee_id):
        """Запущенный (не остановленный) таймер сотрудника или None."""
        return get_db().execute(
            "SELECT * FROM time_entries WHERE employee_id = ? AND end_time IS NULL",
            (employee_id,),
        ).fetchone()

    # ---------- страница ----------

    @app.get("/")
    def index():
        return render_template("index.html")

    # ---------- сотрудники ----------

    @app.get("/api/employees")
    def list_employees():
        rows = get_db().execute("SELECT id, name FROM employees ORDER BY name").fetchall()
        result = []
        for row in rows:
            entry = running_entry(row["id"])
            result.append({
                "id": row["id"],
                "name": row["name"],
                "running": None if entry is None else {
                    "description": entry["description"],
                    "start_time": entry["start_time"],
                },
            })
        return jsonify(result)

    @app.post("/api/employees")
    def add_employee():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return error("Введите имя сотрудника", 400)
        conn = get_db()
        try:
            cur = conn.execute("INSERT INTO employees (name) VALUES (?)", (name,))
            conn.commit()
        except sqlite3.IntegrityError:
            return error("Сотрудник с таким именем уже есть", 409)
        return jsonify({"id": cur.lastrowid, "name": name, "running": None}), 201

    # ---------- таймер ----------

    @app.post("/api/employees/<int:employee_id>/start")
    def start_timer(employee_id):
        if find_employee(employee_id) is None:
            return error("Сотрудник не найден", 404)
        data = request.get_json(silent=True) or {}
        description = str(data.get("description", "")).strip()
        if not description:
            return error("Опишите задачу перед запуском таймера", 400)
        if running_entry(employee_id) is not None:
            return error("Таймер уже запущен — сначала остановите его", 409)

        conn = get_db()
        start = now().strftime(TIME_FORMAT)
        try:
            cur = conn.execute(
                "INSERT INTO time_entries (employee_id, description, start_time) VALUES (?, ?, ?)",
                (employee_id, description, start),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Два почти одновременных запроса «старт» могли оба пройти проверку выше.
            # Тогда второй упрётся в уникальный индекс one_running_timer.
            return error("Таймер уже запущен — сначала остановите его", 409)
        return jsonify({"id": cur.lastrowid, "description": description, "start_time": start}), 201

    @app.post("/api/employees/<int:employee_id>/stop")
    def stop_timer(employee_id):
        if find_employee(employee_id) is None:
            return error("Сотрудник не найден", 404)
        entry = running_entry(employee_id)
        if entry is None:
            return error("Таймер не запущен", 409)

        conn = get_db()
        end = now().strftime(TIME_FORMAT)
        conn.execute("UPDATE time_entries SET end_time = ? WHERE id = ?", (end, entry["id"]))
        conn.commit()
        return jsonify({"id": entry["id"], "end_time": end})

    # ---------- отчёт ----------

    @app.get("/api/report")
    def report():
        try:
            date_from = date.fromisoformat(request.args.get("from", ""))
            date_to = date.fromisoformat(request.args.get("to", ""))
        except ValueError:
            return error("Укажите даты в формате ГГГГ-ММ-ДД", 400)
        if date_from > date_to:
            return error("Дата начала позже даты окончания", 400)

        # Период — с начала первого дня до начала дня ПОСЛЕ последнего.
        # Если сравнивать с просто 'YYYY-MM-DD', записи за последний день
        # не попадут в отчёт: строка '2026-11-02 15:00:00' > '2026-11-02'.
        period_start = datetime.combine(date_from, datetime.min.time())
        period_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

        conn = get_db()
        rows = conn.execute(
            """
            SELECT e.id AS employee_id, e.name, t.description, t.start_time, t.end_time
            FROM time_entries t JOIN employees e ON e.id = t.employee_id
            WHERE t.end_time IS NOT NULL
              AND t.start_time >= ? AND t.start_time < ?
            ORDER BY t.start_time
            """,
            (period_start.strftime(TIME_FORMAT), period_end.strftime(TIME_FORMAT)),
        ).fetchall()

        # Итог нужен по КАЖДОМУ сотруднику, поэтому сначала всем ставим 0.
        totals = {
            emp["id"]: {"name": emp["name"], "seconds": 0}
            for emp in conn.execute("SELECT id, name FROM employees")
        }

        entries = []
        for row in rows:
            start = datetime.strptime(row["start_time"], TIME_FORMAT)
            end = datetime.strptime(row["end_time"], TIME_FORMAT)
            # total_seconds(), а не .seconds: .seconds отбрасывает целые сутки.
            seconds = int((end - start).total_seconds())
            entries.append({
                "employee": row["name"],
                "description": row["description"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "seconds": seconds,
            })
            totals[row["employee_id"]]["seconds"] += seconds

        return jsonify({
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "entries": entries,
            "totals": sorted(totals.values(), key=lambda t: t["name"]),
        })

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
