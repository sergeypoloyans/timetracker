"""
Работа с базой данных SQLite.

SQLite хранит всё в одном файле (по умолчанию timetracker.db),
поэтому данные сохраняются после перезапуска приложения.
"""

import sqlite3

# Схема базы данных: две таблицы.
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS time_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    description TEXT NOT NULL,
    start_time  TEXT NOT NULL,   -- формат 'YYYY-MM-DD HH:MM:SS'
    end_time    TEXT             -- NULL, пока таймер идёт
);

-- Защита на уровне базы: у сотрудника не может быть
-- двух записей одновременно с end_time = NULL (двух запущенных таймеров).
CREATE UNIQUE INDEX IF NOT EXISTS one_running_timer
    ON time_entries(employee_id) WHERE end_time IS NULL;
"""


def connect(db_path):
    """Открывает соединение с базой."""
    conn = sqlite3.connect(db_path)
    # Строки будут вести себя как словари: row["name"] вместо row[1].
    conn.row_factory = sqlite3.Row
    # В SQLite проверка внешних ключей по умолчанию выключена — включаем.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    """Создаёт таблицы, если их ещё нет."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
