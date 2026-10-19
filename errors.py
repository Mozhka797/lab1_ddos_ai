# -*- coding: utf-8 -*-
"""
Перевод исключений psycopg2 в понятные человеку сообщения.

Коды ошибок PostgreSQL (SQLSTATE):
  23502 not_null_violation      23505 unique_violation
  23503 foreign_key_violation   23514 check_violation
  22P02 invalid_text_representation (несоответствие типов / неверное значение ENUM)
  22003 numeric_value_out_of_range, 22007/22008 ошибки даты/времени
  42P01 undefined_table, 3F000 invalid_schema_name, 08xxx — ошибки соединения
"""
import psycopg2
from psycopg2 import errors as pg_errors

# Пояснения к именованным ограничениям из schema.sql
CONSTRAINT_HINTS = {
    "uq_datasets_name": "Датасет с таким названием уже существует.",
    "chk_datasets_name_len": "Название датасета должно содержать минимум 3 символа.",
    "chk_datasets_rows": "Количество строк в датасете должно быть больше нуля.",
    "chk_datasets_collected": "Дата сбора датасета должна быть в диапазоне 2000-01-01 … сегодня.",
    "chk_datasets_attacks_nonempty": "Нужно отметить хотя бы один тип трафика/атаки в датасете.",
    "uq_models_name_version": "Модель с таким именем и версией уже зарегистрирована.",
    "chk_models_version_fmt": "Версия модели должна быть в формате X.Y или X.Y.Z (например 1.0 или 2.1.3).",
    "chk_models_features": "Укажите хотя бы один признак (feature) модели.",
    "uq_experiments_title": "Эксперимент с таким названием уже существует.",
    "chk_experiments_gpu": "GPU-часы должны быть в диапазоне от 0 до 10000.",
    "chk_experiments_target": "Целевым типом атаки не может быть 'benign' (обычный трафик).",
    "fk_experiments_dataset": "Ссылка на датасет нарушает ссылочную целостность.",
    "fk_experiments_model": "Ссылка на модель нарушает ссылочную целостность.",
    "uq_runs_exp_number": "У этого эксперимента уже есть запуск с таким номером.",
    "chk_runs_number": "Номер запуска должен быть положительным числом.",
    "chk_runs_metrics": "Метрики accuracy / precision / recall / F1 должны быть в диапазоне от 0 до 1.",
    "chk_runs_latency": "Задержка (latency) должна быть больше нуля.",
    "chk_runs_threshold": "Порог классификации должен быть строго между 0 и 1.",
    "chk_runs_time_order": "Время окончания запуска не может быть раньше времени начала.",
    "chk_runs_finished_has_metrics": "Для статуса 'finished' обязательны F1-score и время окончания.",
    "fk_runs_experiment": "Ссылка на эксперимент нарушает ссылочную целостность.",
}

COLUMN_NAMES_RU = {
    "name": "Название", "source": "Источник", "collected_on": "Дата сбора",
    "rows_count": "Количество строк", "attack_types": "Типы атак", "version": "Версия",
    "algorithm": "Алгоритм", "features": "Признаки", "title": "Название эксперимента",
    "dataset_id": "Датасет", "model_id": "Модель", "target_attack": "Целевая атака",
    "experiment_id": "Эксперимент", "run_number": "Номер запуска", "status": "Статус",
}


def humanize(exc: Exception) -> tuple[str, str]:
    """Возвращает (заголовок, понятное сообщение) для исключения."""
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag else None
    column = getattr(diag, "column_name", None) if diag else None
    detail = getattr(diag, "message_detail", None) if diag else None
    hint = CONSTRAINT_HINTS.get(constraint or "", "")

    def with_detail(text: str) -> str:
        parts = [text]
        if detail:
            parts.append(f"Подробности СУБД: {detail}")
        if constraint:
            parts.append(f"Ограничение: {constraint}")
        return "\n\n".join(parts)

    if isinstance(exc, pg_errors.UniqueViolation):
        return "Нарушение уникальности (UNIQUE)", with_detail(hint or "Такое значение уже есть в таблице.")

    if isinstance(exc, pg_errors.NotNullViolation):
        col = COLUMN_NAMES_RU.get(column or "", column or "?")
        return "Пустое обязательное поле (NOT NULL)", with_detail(
            f"Поле «{col}» обязательно для заполнения.")

    if isinstance(exc, pg_errors.CheckViolation):
        return "Нарушение условия (CHECK)", with_detail(hint or "Значение не прошло проверку CHECK.")

    if isinstance(exc, pg_errors.ForeignKeyViolation):
        # «still referenced» (англ.) / «есть ссылки» (рус. локаль сервера) → удаление родителя
        det = (detail or "").lower()
        if "still referenced" in det or "ссылки" in det:
            text = ("Нельзя удалить/изменить запись: на неё ссылаются другие записи "
                    "(ON DELETE RESTRICT).")
        else:
            text = "Указана ссылка на несуществующую запись (FOREIGN KEY)."
        return "Нарушение ссылочной целостности (FOREIGN KEY)", with_detail(
            text + ("\n" + hint if hint else ""))

    if isinstance(exc, (pg_errors.InvalidTextRepresentation, pg_errors.DatatypeMismatch,
                        pg_errors.InvalidDatetimeFormat, pg_errors.DatetimeFieldOverflow,
                        pg_errors.InvalidParameterValue)):
        return "Несоответствие типов данных", (
            "Введённое значение не соответствует типу столбца (например, текст вместо числа "
            "или несуществующее значение ENUM).\n\n"
            f"Сообщение СУБД: {getattr(diag, 'message_primary', exc)}")

    if isinstance(exc, pg_errors.NumericValueOutOfRange):
        return "Число вне допустимого диапазона", (
            "Число слишком велико для типа столбца (например NUMERIC(5,4) или SMALLINT).\n\n"
            f"Сообщение СУБД: {getattr(diag, 'message_primary', exc)}")

    if isinstance(exc, pg_errors.StringDataRightTruncation):
        return "Слишком длинная строка", "Текст превышает максимальную длину VARCHAR столбца."

    if isinstance(exc, (pg_errors.UndefinedTable, pg_errors.InvalidSchemaName,
                        pg_errors.UndefinedObject)):
        return "Схема не создана", ("Таблицы или типы ещё не созданы. "
                                    "Нажмите «Создать схему и таблицы».")

    if isinstance(exc, psycopg2.OperationalError):
        raw = str(exc)
        if "password authentication failed" in raw or "аутентификац" in raw:
            text = "Неверное имя пользователя или пароль."
        elif "does not exist" in raw and "database" in raw:
            text = "База данных с таким именем не существует. Создайте её: CREATE DATABASE ...;"
        elif "Connection refused" in raw or "could not connect" in raw or "timeout" in raw:
            text = "Сервер PostgreSQL недоступен. Проверьте, что он запущен, а также хост и порт."
        elif "could not translate host name" in raw:
            text = "Не удалось найти хост. Проверьте адрес сервера."
        else:
            text = "Не удалось выполнить операцию из-за проблемы с соединением."
        return "Ошибка подключения", f"{text}\n\nСообщение драйвера: {raw.strip()}"

    if isinstance(exc, psycopg2.InterfaceError):
        return "Соединение закрыто", "Соединение с БД потеряно. Подключитесь заново."

    if isinstance(exc, psycopg2.Error):
        return "Ошибка базы данных", str(getattr(diag, "message_primary", None) or exc)

    return "Ошибка", str(exc)
