// Логика страницы: общается с сервером через fetch и перерисовывает списки.
// Весь текст от пользователя вставляем через textContent, а не innerHTML,
// чтобы имя вроде "<script>..." не выполнилось как код.

const messageEl = document.getElementById("message");

// ---------- общение с сервером ----------

async function api(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Что-то пошло не так");
  }
  return data;
}

function showError(text) {
  messageEl.textContent = text;
  messageEl.hidden = false;
}

function clearError() {
  messageEl.hidden = true;
}

// ---------- форматирование ----------

// 3725 секунд -> "1:02:05"
function formatDuration(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Сервер хранит время как "2026-11-02 09:30:00" (локальное время).
function parseServerTime(text) {
  return new Date(text.replace(" ", "T"));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------- сотрудники и таймеры ----------

async function loadEmployees() {
  const employees = await api("GET", "/api/employees");
  const list = document.getElementById("employees");

  // Список перерисовывается целиком, поэтому запоминаем, что уже набрано
  // в полях «Над чем работаем?» у других сотрудников, и возвращаем обратно.
  const typed = {};
  for (const input of list.querySelectorAll("input[data-id]")) {
    typed[input.dataset.id] = input.value;
  }

  list.replaceChildren(...employees.map(renderEmployee));

  for (const input of list.querySelectorAll("input[data-id]")) {
    input.value = typed[input.dataset.id] || "";
  }
  document.getElementById("no-employees").hidden = employees.length > 0;
  updateTimers();
}

function renderEmployee(employee) {
  const li = el("li", "employee");
  li.append(el("span", "name", employee.name));

  if (employee.running) {
    // Таймер идёт: показываем задачу, сколько прошло, и кнопку «Стоп».
    li.append(el("span", "task", employee.running.description));
    const timer = el("span", "timer");
    timer.dataset.start = employee.running.start_time;
    li.append(timer);

    const stopBtn = el("button", "stop", "Стоп");
    // После остановки появилась новая запись — обновляем и отчёт.
    stopBtn.onclick = () =>
      run(() => api("POST", `/api/employees/${employee.id}/stop`)).then(loadReport);
    li.append(stopBtn);
  } else {
    // Таймер не идёт: поле для описания задачи и кнопка «Старт».
    const input = el("input", "task");
    input.placeholder = "Над чем работаем?";
    input.dataset.id = employee.id;
    const startBtn = el("button", "", "Старт");
    const start = () =>
      run(() => api("POST", `/api/employees/${employee.id}/start`, { description: input.value }));
    startBtn.onclick = start;
    input.onkeydown = (e) => { if (e.key === "Enter") start(); };
    li.append(input, startBtn);
  }
  return li;
}

// Обновляем цифры у всех запущенных таймеров.
function updateTimers() {
  for (const timer of document.querySelectorAll(".timer")) {
    const seconds = Math.max(0, Math.floor((Date.now() - parseServerTime(timer.dataset.start)) / 1000));
    timer.textContent = formatDuration(seconds);
  }
}
setInterval(updateTimers, 1000);

// Выполнить действие, потом обновить экран; ошибку показать пользователю.
async function run(action) {
  clearError();
  try {
    await action();
  } catch (err) {
    showError(err.message);
  }
  await loadEmployees();
}

document.getElementById("add-employee-form").onsubmit = (event) => {
  event.preventDefault(); // не перезагружать страницу
  const input = document.getElementById("employee-name");
  run(async () => {
    await api("POST", "/api/employees", { name: input.value });
    input.value = "";
  });
};

// ---------- отчёт ----------

async function loadReport() {
  const from = document.getElementById("date-from").value;
  const to = document.getElementById("date-to").value;
  const container = document.getElementById("report");
  try {
    const data = await api("GET", `/api/report?from=${from}&to=${to}`);
    container.replaceChildren(...renderReport(data));
  } catch (err) {
    showError(err.message);
  }
}

function renderReport(data) {
  const parts = [el("h3", "", "Итого по сотрудникам")];
  if (data.totals.length === 0) {
    parts.push(el("p", "muted", "Сотрудников пока нет."));
  } else {
    parts.push(makeTable(
      ["Сотрудник", "Время"],
      data.totals.map((t) => [t.name, formatDuration(t.seconds)]),
    ));
  }

  parts.push(el("h3", "", "Записи"));
  if (data.entries.length === 0) {
    parts.push(el("p", "muted", "За выбранный период записей нет."));
  } else {
    parts.push(makeTable(
      ["Сотрудник", "Задача", "Начало", "Конец", "Длительность"],
      data.entries.map((e) => [
        e.employee, e.description, e.start_time, e.end_time, formatDuration(e.seconds),
      ]),
    ));
  }
  return parts;
}

function makeTable(headers, rows) {
  const table = el("table");
  const headRow = el("tr");
  headers.forEach((h) => headRow.append(el("th", "", h)));
  table.append(headRow);
  for (const row of rows) {
    const tr = el("tr");
    row.forEach((cell, i) => tr.append(el("td", i === row.length - 1 ? "num" : "", cell)));
    table.append(tr);
  }
  return table;
}

document.getElementById("report-form").onsubmit = (event) => {
  event.preventDefault();
  clearError();
  loadReport();
};

// ---------- старт страницы ----------

// По умолчанию отчёт с 1-го числа текущего месяца по сегодня (по местному времени).
function toInputDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
const today = new Date();
document.getElementById("date-from").value = toInputDate(new Date(today.getFullYear(), today.getMonth(), 1));
document.getElementById("date-to").value = toInputDate(today);

loadEmployees();
loadReport();
