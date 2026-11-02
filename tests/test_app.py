"""
Проверки основных сценариев. Запуск:  python -m pytest -v

Каждый тест получает свою временную базу (tmp_path), поэтому тесты
не мешают друг другу и не трогают настоящий timetracker.db.
"""

from datetime import datetime, timedelta

import pytest

from app import create_app


class FakeClock:
    """Подменные часы: время меняется только когда мы сами его двигаем."""

    def __init__(self, start):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 2, 9, 0, 0))


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path, clock):
    return create_app(db_path=db_path, clock=clock).test_client()


def add_employee(client, name="Анна Иванова"):
    response = client.post("/api/employees", json={"name": name})
    assert response.status_code == 201
    return response.get_json()["id"]


# ---------- сотрудники ----------

def test_add_employee_and_list(client):
    add_employee(client, "Борис Петров")
    add_employee(client, "Анна Иванова")
    names = [e["name"] for e in client.get("/api/employees").get_json()]
    assert names == ["Анна Иванова", "Борис Петров"]  # по алфавиту


def test_empty_name_is_rejected(client):
    response = client.post("/api/employees", json={"name": "   "})
    assert response.status_code == 400


def test_duplicate_name_is_rejected(client):
    add_employee(client, "Анна Иванова")
    response = client.post("/api/employees", json={"name": "Анна Иванова"})
    assert response.status_code == 409


# ---------- таймер ----------

def test_start_and_stop_timer(client, clock):
    emp = add_employee(client)
    assert client.post(f"/api/employees/{emp}/start", json={"description": "Отчёт"}).status_code == 201

    running = client.get("/api/employees").get_json()[0]["running"]
    assert running["description"] == "Отчёт"

    clock.advance(minutes=30)
    assert client.post(f"/api/employees/{emp}/stop").status_code == 200
    assert client.get("/api/employees").get_json()[0]["running"] is None


def test_second_start_while_running_is_rejected(client):
    """Повторный запуск таймера, пока он идёт, — ошибка, а не вторая запись."""
    emp = add_employee(client)
    client.post(f"/api/employees/{emp}/start", json={"description": "Задача 1"})
    response = client.post(f"/api/employees/{emp}/start", json={"description": "Задача 2"})
    assert response.status_code == 409
    # Остался запущенным первый таймер
    assert client.get("/api/employees").get_json()[0]["running"]["description"] == "Задача 1"


def test_database_blocks_second_running_timer(client, db_path):
    """Даже если проверку в коде обойти, база не даст создать второй запущенный таймер."""
    import sqlite3
    emp = add_employee(client)
    client.post(f"/api/employees/{emp}/start", json={"description": "Задача 1"})
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO time_entries (employee_id, description, start_time) "
            "VALUES (?, 'Задача 2', '2026-11-02 09:00:00')",
            (emp,),
        )
    conn.close()


def test_restart_after_stop_creates_new_entry(client, clock):
    """Повторный запуск после остановки — новая запись, старая не меняется."""
    emp = add_employee(client)
    client.post(f"/api/employees/{emp}/start", json={"description": "Утро"})
    clock.advance(hours=1)
    client.post(f"/api/employees/{emp}/stop")

    clock.advance(minutes=10)
    client.post(f"/api/employees/{emp}/start", json={"description": "После перерыва"})
    clock.advance(minutes=20)
    client.post(f"/api/employees/{emp}/stop")

    data = client.get("/api/report?from=2026-11-02&to=2026-11-02").get_json()
    assert [e["seconds"] for e in data["entries"]] == [3600, 1200]
    assert data["totals"][0]["seconds"] == 4800


def test_stop_without_start_is_rejected(client):
    emp = add_employee(client)
    assert client.post(f"/api/employees/{emp}/stop").status_code == 409


def test_start_requires_description(client):
    emp = add_employee(client)
    response = client.post(f"/api/employees/{emp}/start", json={"description": ""})
    assert response.status_code == 400


def test_unknown_employee(client):
    assert client.post("/api/employees/999/start", json={"description": "x"}).status_code == 404


# ---------- отчёт ----------

def test_empty_report(client):
    """Отчёт за период без записей: пустой список и нулевые итоги, а не ошибка."""
    add_employee(client)
    response = client.get("/api/report?from=2026-01-01&to=2026-01-31")
    assert response.status_code == 200
    data = response.get_json()
    assert data["entries"] == []
    assert data["totals"] == [{"name": "Анна Иванова", "seconds": 0}]


def test_report_includes_whole_last_day(client, clock):
    """Запись, начатая днём в последний день периода, попадает в отчёт."""
    emp = add_employee(client)
    clock.current = datetime(2026, 11, 2, 15, 0, 0)
    client.post(f"/api/employees/{emp}/start", json={"description": "Созвон"})
    clock.advance(minutes=45)
    client.post(f"/api/employees/{emp}/stop")

    data = client.get("/api/report?from=2026-11-01&to=2026-11-02").get_json()
    assert len(data["entries"]) == 1
    assert data["totals"][0]["seconds"] == 45 * 60


def test_report_filters_by_period(client, clock):
    emp = add_employee(client)
    for day in (1, 2, 3):
        clock.current = datetime(2026, 11, day, 10, 0, 0)
        client.post(f"/api/employees/{emp}/start", json={"description": f"День {day}"})
        clock.advance(hours=1)
        client.post(f"/api/employees/{emp}/stop")

    data = client.get("/api/report?from=2026-11-02&to=2026-11-02").get_json()
    assert [e["description"] for e in data["entries"]] == ["День 2"]


def test_long_entry_over_24_hours(client, clock):
    """Таймер, забытый на ночь (больше суток), считается полностью."""
    emp = add_employee(client)
    client.post(f"/api/employees/{emp}/start", json={"description": "Забыли выключить"})
    clock.advance(hours=26)
    client.post(f"/api/employees/{emp}/stop")

    data = client.get("/api/report?from=2026-11-02&to=2026-11-03").get_json()
    assert data["entries"][0]["seconds"] == 26 * 3600


def test_report_totals_per_employee(client, clock):
    anna = add_employee(client, "Анна Иванова")
    boris = add_employee(client, "Борис Петров")
    for emp, minutes in ((anna, 30), (boris, 90), (anna, 15)):
        client.post(f"/api/employees/{emp}/start", json={"description": "Работа"})
        clock.advance(minutes=minutes)
        client.post(f"/api/employees/{emp}/stop")

    totals = client.get("/api/report?from=2026-11-02&to=2026-11-02").get_json()["totals"]
    assert totals == [
        {"name": "Анна Иванова", "seconds": 45 * 60},
        {"name": "Борис Петров", "seconds": 90 * 60},
    ]


def test_report_bad_dates(client):
    assert client.get("/api/report?from=abc&to=2026-11-02").status_code == 400
    assert client.get("/api/report?from=2026-11-05&to=2026-11-01").status_code == 400


# ---------- сохранение данных ----------

def test_data_survives_restart(db_path, clock):
    """Создаём приложение, пишем данные, создаём приложение заново на той же базе."""
    first = create_app(db_path=db_path, clock=clock).test_client()
    emp = add_employee(first)
    first.post(f"/api/employees/{emp}/start", json={"description": "До перезапуска"})

    # «Перезапуск»: новое приложение, тот же файл базы
    second = create_app(db_path=db_path, clock=clock).test_client()
    employees = second.get("/api/employees").get_json()
    assert employees[0]["name"] == "Анна Иванова"
    # Запущенный таймер тоже пережил перезапуск, и его можно остановить
    assert employees[0]["running"]["description"] == "До перезапуска"
    clock.advance(minutes=5)
    assert second.post(f"/api/employees/{emp}/stop").status_code == 200
