# Лабораторная работа №1 — «ИИ для обнаружения DDoS-атак» (MLOps-мини-система)

Настольное GUI-приложение на **Python 3.12+ · PySide6 · psycopg2 · PostgreSQL 13+**.
Хранит датасеты сетевого трафика, ML-модели, эксперименты и запуски (runs) с метриками качества.

## Запуск

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Создайте пустую базу (один раз), например в psql или pgAdmin:

```sql
CREATE DATABASE ddos_lab;
```

```bash
python main.py
```

В окне введите параметры подключения → **Подключиться** → **① Создать схему и таблицы** →
**② Внести данные** (можно нажать «Заполнить демо-данными») → **③ Показать данные**.

Параметры можно задать заранее через переменные окружения `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`.

**Кроссплатформенность:** Windows / Linux / macOS (PySide6 и psycopg2-binary есть для всех трёх).
Проверено на Linux, Python 3.13, PySide6 6.12, PostgreSQL 16.

## Структура проекта

| Файл | Назначение |
|---|---|
| `main.py` | Главное окно: подключение, 4 кнопки действий, журнал |
| `dialogs.py` | Модальные окна: формы ввода, просмотр с фильтрами, демонстрация ссылочной целостности |
| `db.py` | Работа с БД: транзакции, параметризованные INSERT/SELECT, логирование |
| `errors.py` | Перевод ошибок PostgreSQL в понятные сообщения |
| `schema.sql` | DDL-скрипт: CREATE SCHEMA / TYPE / TABLE |
| `app.log` | Лог-файл (создаётся при запуске) |

## Схема БД (`ddos_ai`)

```
datasets 1 ──< experiments >── 0..1 models
                   │
                   1
                   │
                   ∧
                 runs
```

| Таблица | Ключевые поля |
|---|---|
| `datasets` | name, source (ENUM), collected_on (DATE), rows_count (BIGINT), attack_types (**ENUM[]**), is_labeled (BOOLEAN) |
| `models` | name, version, algorithm (ENUM), features (**TEXT[]**), created_at (TIMESTAMP) |
| `experiments` | title, dataset_id (FK), model_id (FK), target_attack (ENUM), started_on, gpu_hours (NUMERIC), is_production |
| `runs` | experiment_id (FK), run_number (SMALLINT), status (ENUM), accuracy/precision/recall/f1 (NUMERIC(5,4)), latency_ms (REAL), tags (**TEXT[]**) |

## Как выполнены требования

| Требование | Где |
|---|---|
| **1.1 Типы данных**: текст, числа, булевы, дата/время, ENUM, массивы | `VARCHAR`, `TEXT`; `INTEGER`, `SMALLINT`, `BIGINT`, `NUMERIC`, `REAL`; `BOOLEAN`; `DATE`, `TIMESTAMP`; 4 ENUM-типа; массивы `attack_type[]` и `TEXT[]` |
| **1.2 CREATE и INSERT** | `schema.sql` (CREATE SCHEMA/TYPE/TABLE/INDEX), формы ввода и демо-данные (INSERT) |
| **1.3 NOT NULL, UNIQUE, CHECK** | NOT NULL у обязательных полей; UNIQUE: `name`, `(name, version)`, `title`, `(experiment_id, run_number)`; 14 CHECK (диапазоны метрик, формат версии через регулярное выражение, непустые массивы, порядок времени и др.) |
| **1.4 PK, FK, ON DELETE/ON UPDATE** | `experiments.dataset_id` → **RESTRICT**; `experiments.model_id` → **SET NULL**; `runs.experiment_id` → **CASCADE**; на всех FK **ON UPDATE CASCADE** |
| **1.5 Ссылочная целостность на практике** | Кнопка ④: вставка с несуществующим FK, удаление используемого датасета, CASCADE / SET NULL / ON UPDATE CASCADE (выполняется в транзакции с ROLLBACK, поэтому данные не портятся). В формах можно выбрать «Ввести ID вручную» и проверить FK самостоятельно |
| **2.1 GUI** | Главное окно с кнопками «Создать схему и таблицы», «Внести данные», «Показать данные» |
| **2.2 Модальные окна** | Все окна — `QDialog` с `ApplicationModal`: главное окно недоступно, пока форма открыта. Отдельная форма для каждой таблицы |
| **Параметризованные запросы + транзакции** | Только `%s`-параметры; `Database.transaction()` выполняет COMMIT или ROLLBACK |
| **Вывод с фильтрами** | Сводка через JOIN 4 таблиц. Фильтры: целевая атака, алгоритм, статус, период дат, порог F1, максимальная latency, тег (поиск в массиве `= ANY(tags)`), «только прод». Также можно просмотреть каждую таблицу целиком |
| **2.3 Обработка ошибок** | `errors.py` по коду ошибки: UNIQUE, NOT NULL, CHECK (пояснение для каждого ограничения), FK и RESTRICT, несоответствие типов и ENUM, переполнение числа, ошибки подключения (пароль, хост, порт, БД), отсутствие схемы |
| **2.4 Логирование** | `app.log`: уровни INFO/ERROR, время, попытки подключения, каждый DDL/DML с подставленными параметрами, BEGIN/COMMIT/ROLLBACK, нарушения ограничений с SQLSTATE и именем ограничения |
| **2.5 Кроссплатформенность** | Windows / Linux / macOS |

## Как показать ошибки преподавателю

Формы специально не проверяют данные сами: проверку выполняет СУБД.

* Оставить пустым «Название» → **NOT NULL**
* Добавить датасет `CIC-DDoS2019` повторно → **UNIQUE**
* Указать количество строк `-5` или версию модели `v1` → **CHECK**
* Указать количество строк `сто` → **несоответствие типов**
* Указать F1 `1.5` → **CHECK** (метрика должна быть в диапазоне 0…1); F1 `12` → **переполнение NUMERIC(5,4)**
* Статус `finished` без F1 → **CHECK** (правило, связывающее два поля)
* Выбрать эксперимент «Ввести ID вручную» = 999999 → **FOREIGN KEY**
* Неверный пароль или порт при подключении → **ошибка подключения**
