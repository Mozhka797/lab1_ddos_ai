# -*- coding: utf-8 -*-
"""
Слой работы с PostgreSQL (psycopg2).

- Все запросы с данными пользователя — только параметризованные (%s / %(name)s),
  никакой склейки строк со значениями.
- Каждая операция записи выполняется в транзакции: COMMIT при успехе,
  ROLLBACK при любой ошибке (см. Database.transaction()).
- Все попытки подключения, DDL/DML и ошибки пишутся в лог-файл app.log.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = BASE_DIR / "schema.sql"
LOG_FILE = BASE_DIR / "app.log"
SCHEMA = "ddos_ai"

# Значения ENUM (используются, пока схема ещё не создана; после создания читаются из БД)
DEFAULT_ENUMS = {
    "attack_type": ["syn_flood", "udp_flood", "http_flood", "icmp_flood",
                    "dns_amplification", "ntp_amplification", "slowloris", "benign"],
    "model_algorithm": ["random_forest", "xgboost", "lstm", "cnn", "transformer",
                        "autoencoder", "isolation_forest"],
    "run_status": ["queued", "running", "finished", "failed"],
    "dataset_source": ["public", "synthetic", "production_mirror"],
}


# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("ddos_lab")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    return logger


log = setup_logging()


def _short_sql(sql: str | bytes, limit: int = 400) -> str:
    if isinstance(sql, bytes):
        sql = sql.decode("utf-8", errors="replace")
    sql = re.sub(r"\s+", " ", sql).strip()
    return sql if len(sql) <= limit else sql[:limit] + " …"


@dataclass
class DbConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "ddos_lab"
    user: str = "postgres"
    password: str = ""
    connect_timeout: int = 5


class Database:
    def __init__(self) -> None:
        self.conn: Optional[psycopg2.extensions.connection] = None
        self.cfg: Optional[DbConfig] = None

    # ------------------------------------------------------------------ connect
    @property
    def connected(self) -> bool:
        return self.conn is not None and self.conn.closed == 0

    def connect(self, cfg: DbConfig) -> None:
        log.info("Попытка подключения: %s@%s:%s/%s", cfg.user, cfg.host, cfg.port, cfg.dbname)
        try:
            self.conn = psycopg2.connect(
                host=cfg.host, port=cfg.port, dbname=cfg.dbname, user=cfg.user,
                password=cfg.password, connect_timeout=cfg.connect_timeout,
                application_name="DDoS-AI-Lab1",
                options=f"-c search_path={SCHEMA},public",
            )
            self.conn.autocommit = False
            self.cfg = cfg
            log.info("Подключение успешно (сервер PostgreSQL %s)", self.conn.server_version)
        except psycopg2.Error as e:
            log.error("Ошибка подключения: %s", str(e).strip())
            self.conn = None
            raise

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            log.info("Соединение закрыто")
        self.conn = None

    def _require(self) -> psycopg2.extensions.connection:
        if not self.connected:
            raise psycopg2.InterfaceError("Нет подключения к БД")
        return self.conn  # type: ignore[return-value]

    # -------------------------------------------------------------- transaction
    @contextmanager
    def transaction(self, name: str, quiet: bool = False) -> Iterator[psycopg2.extensions.cursor]:
        """BEGIN … COMMIT, а при любой ошибке — ROLLBACK и запись в лог.
        quiet=True — для служебных чтений: BEGIN/COMMIT не пишутся в лог (ошибки пишутся всегда)."""
        conn = self._require()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if not quiet:
            log.info("BEGIN: %s", name)
        try:
            yield cur
            conn.commit()
            if not quiet:
                log.info("COMMIT: %s", name)
        except Exception as e:
            conn.rollback()
            pgcode = getattr(e, "pgcode", None)
            constraint = getattr(getattr(e, "diag", None), "constraint_name", None)
            log.error("ROLLBACK: %s | %s (SQLSTATE=%s, constraint=%s): %s",
                      name, type(e).__name__, pgcode, constraint,
                      str(getattr(getattr(e, "diag", None), "message_primary", None) or e).strip())
            raise
        finally:
            cur.close()

    @staticmethod
    def _exec(cur, sql: str, params: Any = None, kind: str = "DML") -> None:
        cur.execute(sql, params)
        log.info("%s: %s", kind, _short_sql(cur.query or sql))

    # ---------------------------------------------------------------------- DDL
    def create_schema(self) -> list[str]:
        """Выполняет schema.sql (CREATE SCHEMA / TYPE / TABLE) одной транзакцией."""
        script = SCHEMA_FILE.read_text(encoding="utf-8")
        # убираем комментарии «-- …» (в скрипте они не встречаются внутри строковых литералов)
        lines = [re.sub(r"--.*$", "", ln) for ln in script.splitlines()]
        statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
        done: list[str] = []
        with self.transaction("создание схемы ddos_ai") as cur:
            for st in statements:
                self._exec(cur, st, kind="DDL")
                done.append(_short_sql(st, 90))
        return done

    # ------------------------------------------------------------------- lookup
    def schema_exists(self) -> bool:
        with self.transaction("проверка схемы", quiet=True) as cur:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{SCHEMA}.runs",))
            return bool(cur.fetchone()["ok"])

    def enum_values(self, type_name: str) -> list[str]:
        try:
            with self.transaction(f"чтение ENUM {type_name}", quiet=True) as cur:
                cur.execute(
                    "SELECT e.enumlabel AS v FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = %s AND t.typname = %s ORDER BY e.enumsortorder",
                    (SCHEMA, type_name))
                vals = [r["v"] for r in cur.fetchall()]
            return vals or DEFAULT_ENUMS[type_name]
        except psycopg2.Error:
            return DEFAULT_ENUMS[type_name]

    def list_datasets(self) -> list[dict]:
        with self.transaction("список датасетов", quiet=True) as cur:
            cur.execute("SELECT dataset_id, name FROM datasets ORDER BY name")
            return list(cur.fetchall())

    def list_models(self) -> list[dict]:
        with self.transaction("список моделей", quiet=True) as cur:
            cur.execute("SELECT model_id, name || ' v' || version AS label, algorithm "
                        "FROM models ORDER BY name, version")
            return list(cur.fetchall())

    def list_experiments(self) -> list[dict]:
        with self.transaction("список экспериментов", quiet=True) as cur:
            cur.execute("SELECT experiment_id, title, "
                        "COALESCE((SELECT max(run_number) FROM runs r "
                        "          WHERE r.experiment_id = e.experiment_id), 0) + 1 AS next_run "
                        "FROM experiments e ORDER BY title")
            return list(cur.fetchall())

    # ------------------------------------------------------------------- INSERT
    def insert_dataset(self, name: Optional[str], source: str, collected_on: date,
                       rows_count: Optional[str], attack_types: list[str],
                       is_labeled: bool) -> int:
        with self.transaction("INSERT datasets") as cur:
            self._exec(cur,
                "INSERT INTO datasets (name, source, collected_on, rows_count, attack_types, is_labeled) "
                "VALUES (%s, %s, %s, %s, %s::attack_type[], %s) RETURNING dataset_id",
                (name, source, collected_on, rows_count, attack_types, is_labeled))
            return cur.fetchone()["dataset_id"]

    def insert_model(self, name: Optional[str], version: Optional[str], algorithm: str,
                     features: list[str]) -> int:
        with self.transaction("INSERT models") as cur:
            self._exec(cur,
                "INSERT INTO models (name, version, algorithm, features) "
                "VALUES (%s, %s, %s, %s::text[]) RETURNING model_id",
                (name, version, algorithm, features))
            return cur.fetchone()["model_id"]

    def insert_experiment(self, title: Optional[str], dataset_id: Optional[int],
                          model_id: Optional[int], target_attack: str, started_on: date,
                          gpu_hours: Optional[str], is_production: bool) -> int:
        with self.transaction("INSERT experiments") as cur:
            self._exec(cur,
                "INSERT INTO experiments (title, dataset_id, model_id, target_attack, started_on, "
                "gpu_hours, is_production) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING experiment_id",
                (title, dataset_id, model_id, target_attack, started_on, gpu_hours, is_production))
            return cur.fetchone()["experiment_id"]

    def insert_run(self, experiment_id: Optional[int], run_number: Optional[str], status: str,
                   started_at: datetime, finished_at: Optional[datetime],
                   accuracy: Optional[str], precision: Optional[str], recall: Optional[str],
                   f1: Optional[str], latency_ms: Optional[str], threshold: Optional[str],
                   tags: list[str]) -> int:
        with self.transaction("INSERT runs") as cur:
            self._exec(cur,
                "INSERT INTO runs (experiment_id, run_number, status, started_at, finished_at, "
                "accuracy, precision_, recall, f1_score, latency_ms, threshold, tags) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::text[]) RETURNING run_id",
                (experiment_id, run_number, status, started_at, finished_at, accuracy, precision,
                 recall, f1, latency_ms, threshold, tags))
            return cur.fetchone()["run_id"]

    def insert_demo_data(self) -> dict[str, int]:
        """Пакет демо-данных одной транзакцией (всё или ничего)."""
        with self.transaction("INSERT демо-данных") as cur:
            psycopg2.extras.execute_values(cur,
                "INSERT INTO datasets (name, source, collected_on, rows_count, attack_types, is_labeled) "
                "VALUES %s ON CONFLICT (name) DO NOTHING",
                [("CIC-DDoS2019", "public", date(2019, 12, 1), 50_063_112,
                  ["syn_flood", "udp_flood", "dns_amplification", "ntp_amplification", "benign"], True),
                 ("CIC-IDS2017 (DDoS subset)", "public", date(2017, 7, 7), 225_745,
                  ["http_flood", "slowloris", "benign"], True),
                 ("Синтетика: SYN-шторм 10 Гбит/с", "synthetic", date(2026, 3, 14), 1_200_000,
                  ["syn_flood", "benign"], True),
                 ("Зеркало прод-трафика, сентябрь", "production_mirror", date(2026, 9, 30), 8_750_000,
                  ["http_flood", "icmp_flood", "benign"], False)],
                template="(%s, %s, %s, %s, %s::attack_type[], %s)")
            log.info("DML: INSERT INTO datasets … (execute_values, %d строк)", cur.rowcount)
            psycopg2.extras.execute_values(cur,
                "INSERT INTO models (name, version, algorithm, features) VALUES %s "
                "ON CONFLICT (name, version) DO NOTHING",
                [("FlowGuard", "1.0", "random_forest", ["pkt_rate", "syn_ratio", "src_entropy"]),
                 ("FlowGuard", "2.0", "xgboost", ["pkt_rate", "syn_ratio", "src_entropy", "ttl_var"]),
                 ("SeqSentinel", "0.3", "lstm", ["pkt_size_seq", "iat_seq"]),
                 ("TrafficBERT", "1.1.0", "transformer", ["flow_tokens", "http_headers"]),
                 ("AnomalyAE", "0.9", "autoencoder", ["flow_stats_64d"])],
                template="(%s, %s, %s, %s::text[])")
            log.info("DML: INSERT INTO models … (execute_values, %d строк)", cur.rowcount)

            def ds(name: str) -> int:
                cur.execute("SELECT dataset_id FROM datasets WHERE name = %s", (name,))
                return cur.fetchone()["dataset_id"]

            def md(name: str, ver: str) -> int:
                cur.execute("SELECT model_id FROM models WHERE name = %s AND version = %s", (name, ver))
                return cur.fetchone()["model_id"]

            experiments = [
                ("RF baseline против SYN flood", ds("CIC-DDoS2019"), md("FlowGuard", "1.0"),
                 "syn_flood", date(2026, 9, 2), "3.5", False),
                ("XGBoost: амплификация DNS/NTP", ds("CIC-DDoS2019"), md("FlowGuard", "2.0"),
                 "dns_amplification", date(2026, 9, 10), "12.0", True),
                ("LSTM по последовательностям пакетов", ds("Синтетика: SYN-шторм 10 Гбит/с"),
                 md("SeqSentinel", "0.3"), "syn_flood", date(2026, 9, 21), "48.25", False),
                ("Transformer для L7 HTTP flood", ds("CIC-IDS2017 (DDoS subset)"),
                 md("TrafficBERT", "1.1.0"), "http_flood", date(2026, 10, 1), "160.0", False),
                ("Автоэнкодер на прод-зеркале", ds("Зеркало прод-трафика, сентябрь"),
                 md("AnomalyAE", "0.9"), "icmp_flood", date(2026, 10, 12), "20.75", False),
            ]
            exp_ids = []
            for e in experiments:
                self._exec(cur,
                    "INSERT INTO experiments (title, dataset_id, model_id, target_attack, started_on, "
                    "gpu_hours, is_production) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (title) DO UPDATE SET title = EXCLUDED.title RETURNING experiment_id", e)
                exp_ids.append(cur.fetchone()["experiment_id"])

            def ts(s: str) -> datetime:
                return datetime.strptime(s, "%Y-%m-%d %H:%M")

            runs = [
                (exp_ids[0], 1, "finished", ts("2026-09-02 10:00"), ts("2026-09-02 10:40"),
                 "0.9612", "0.9401", "0.9550", "0.9475", 1.8, 0.5, ["baseline"]),
                (exp_ids[0], 2, "finished", ts("2026-09-03 11:00"), ts("2026-09-03 11:35"),
                 "0.9705", "0.9622", "0.9583", "0.9602", 1.9, 0.45, ["tuned", "class_weight"]),
                (exp_ids[1], 1, "finished", ts("2026-09-10 09:00"), ts("2026-09-10 12:00"),
                 "0.9931", "0.9902", "0.9877", "0.9889", 0.9, 0.5, ["prod_candidate"]),
                (exp_ids[1], 2, "finished", ts("2026-09-12 09:00"), ts("2026-09-12 11:30"),
                 "0.9948", "0.9915", "0.9903", "0.9909", 0.8, 0.55, ["prod", "shap"]),
                (exp_ids[2], 1, "failed", ts("2026-09-21 14:00"), ts("2026-09-21 14:12"),
                 None, None, None, None, None, 0.5, ["oom"]),
                (exp_ids[2], 2, "finished", ts("2026-09-22 08:00"), ts("2026-09-22 20:00"),
                 "0.9811", "0.9750", "0.9790", "0.9770", 6.4, 0.5, ["gpu", "seq_len_256"]),
                (exp_ids[3], 1, "finished", ts("2026-10-01 10:00"), ts("2026-10-03 02:00"),
                 "0.9874", "0.9820", "0.9701", "0.9760", 14.2, 0.6, ["gpu", "l7"]),
                (exp_ids[3], 2, "running", ts("2026-10-18 09:00"), None,
                 None, None, None, None, None, 0.6, ["gpu", "distillation"]),
                (exp_ids[4], 1, "finished", ts("2026-10-12 12:00"), ts("2026-10-12 15:00"),
                 "0.9120", "0.8455", "0.9302", "0.8858", 2.3, 0.7, ["unsupervised"]),
                (exp_ids[4], 2, "queued", ts("2026-10-19 08:00"), None,
                 None, None, None, None, None, 0.65, []),
            ]
            psycopg2.extras.execute_values(cur,
                "INSERT INTO runs (experiment_id, run_number, status, started_at, finished_at, "
                "accuracy, precision_, recall, f1_score, latency_ms, threshold, tags) VALUES %s "
                "ON CONFLICT (experiment_id, run_number) DO NOTHING",
                runs, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::text[])")
            log.info("DML: INSERT INTO runs … (execute_values, %d строк)", cur.rowcount)

            cur.execute("SELECT (SELECT count(*) FROM datasets) AS datasets, "
                        "(SELECT count(*) FROM models) AS models, "
                        "(SELECT count(*) FROM experiments) AS experiments, "
                        "(SELECT count(*) FROM runs) AS runs")
            return dict(cur.fetchone())

    # ------------------------------------------------------------------- SELECT
    SUMMARY_COLUMNS = [
        ("experiment_id", "ID эксп."), ("title", "Эксперимент"), ("dataset", "Датасет"),
        ("dataset_attacks", "Атаки в датасете"), ("model", "Модель"), ("algorithm", "Алгоритм"),
        ("target_attack", "Целевая атака"), ("started_on", "Дата начала"),
        ("is_production", "Прод"), ("run_number", "Запуск №"), ("status", "Статус"),
        ("accuracy", "Accuracy"), ("precision_", "Precision"), ("recall", "Recall"),
        ("f1_score", "F1"), ("latency_ms", "Latency, мс"), ("threshold", "Порог"), ("tags", "Теги"),
    ]

    def fetch_summary(self, attack: Optional[str] = None, algorithm: Optional[str] = None,
                      status: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None, min_f1: Optional[float] = None,
                      max_latency: Optional[float] = None, tag: Optional[str] = None,
                      production_only: bool = False) -> list[dict]:
        """Сводка EXPERIMENTS ⋈ DATASETS ⟕ MODELS ⟕ RUNS с фильтрами (параметризовано)."""
        where, params = [], []
        if attack:
            where.append("e.target_attack = %s::attack_type")
            params.append(attack)
        if algorithm:
            where.append("m.algorithm = %s::model_algorithm")
            params.append(algorithm)
        if status:
            where.append("r.status = %s::run_status")
            params.append(status)
        if date_from:
            where.append("e.started_on >= %s")
            params.append(date_from)
        if date_to:
            where.append("e.started_on <= %s")
            params.append(date_to)
        if min_f1 is not None:
            where.append("r.f1_score >= %s")
            params.append(min_f1)
        if max_latency is not None:
            where.append("r.latency_ms <= %s")
            params.append(max_latency)
        if tag:
            where.append("%s = ANY(r.tags)")      # поиск в массиве
            params.append(tag)
        if production_only:
            where.append("e.is_production")

        sql = (
            "SELECT e.experiment_id, e.title, d.name AS dataset, "
            "       array_to_string(d.attack_types, ', ') AS dataset_attacks, "
            "       COALESCE(m.name || ' v' || m.version, '— (модель удалена)') AS model, "
            "       m.algorithm, e.target_attack, e.started_on, e.is_production, "
            "       r.run_number, r.status, r.accuracy, r.precision_, r.recall, r.f1_score, "
            "       r.latency_ms, r.threshold, array_to_string(r.tags, ', ') AS tags "
            "FROM experiments e "
            "JOIN datasets d ON d.dataset_id = e.dataset_id "
            "LEFT JOIN models m ON m.model_id = e.model_id "
            "LEFT JOIN runs r ON r.experiment_id = e.experiment_id "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY e.started_on DESC, e.experiment_id, r.run_number"
        )
        with self.transaction("SELECT сводка", quiet=True) as cur:
            self._exec(cur, sql, params, kind="SELECT")
            return list(cur.fetchall())

    TABLES = {"datasets": "Датасеты", "models": "Модели",
              "experiments": "Эксперименты", "runs": "Запуски"}

    def fetch_table(self, table: str) -> tuple[list[str], list[dict]]:
        if table not in self.TABLES:          # имя таблицы — только из белого списка
            raise ValueError(table)
        with self.transaction(f"SELECT {table}", quiet=True) as cur:
            self._exec(cur, f"SELECT * FROM {table} ORDER BY 1", kind="SELECT")
            cols = [d.name for d in cur.description]
            return cols, list(cur.fetchall())

    # ------------------------------------------------ ссылочная целостность (демо)
    def demo_fk_insert(self) -> None:
        """INSERT запуска с несуществующим experiment_id → ForeignKeyViolation."""
        with self.transaction("ДЕМО: INSERT runs с несуществующим experiment_id") as cur:
            self._exec(cur, "INSERT INTO runs (experiment_id, run_number, status) VALUES (%s, %s, %s)",
                       (999_999, 1, "queued"))

    def demo_restrict_delete(self) -> str:
        """DELETE датасета, на который ссылаются эксперименты → RESTRICT."""
        with self.transaction("ДЕМО: поиск используемого датасета", quiet=True) as cur:
            cur.execute("SELECT d.dataset_id, d.name FROM datasets d "
                        "WHERE EXISTS (SELECT 1 FROM experiments e WHERE e.dataset_id = d.dataset_id) "
                        "LIMIT 1")
            row = cur.fetchone()
        if not row:
            return "Нет датасета, на который ссылаются эксперименты. Сначала внесите данные."
        with self.transaction("ДЕМО: DELETE используемого датасета (RESTRICT)") as cur:
            self._exec(cur, "DELETE FROM datasets WHERE dataset_id = %s", (row["dataset_id"],))
        return "Датасет удалён (неожиданно!)"

    def demo_cascade_and_set_null(self) -> str:
        """
        Внутри одной транзакции показывает CASCADE и SET NULL, затем делает ROLLBACK,
        чтобы данные не пострадали.
        """
        conn = self._require()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        out: list[str] = []
        log.info("BEGIN: ДЕМО CASCADE / SET NULL / ON UPDATE CASCADE (будет ROLLBACK)")
        try:
            cur.execute("SELECT e.experiment_id, e.title, count(r.run_id) AS n FROM experiments e "
                        "JOIN runs r ON r.experiment_id = e.experiment_id "
                        "GROUP BY e.experiment_id ORDER BY n DESC LIMIT 1")
            exp = cur.fetchone()
            if exp:
                self._exec(cur, "DELETE FROM experiments WHERE experiment_id = %s", (exp["experiment_id"],))
                cur.execute("SELECT count(*) AS n FROM runs WHERE experiment_id = %s", (exp["experiment_id"],))
                left = cur.fetchone()["n"]
                out.append(f"1) ON DELETE CASCADE (experiments → runs)\n"
                           f"   Удалён эксперимент «{exp['title']}» с {exp['n']} запусками.\n"
                           f"   Осталось его запусков в runs: {left} — удалены каскадно.")

            cur.execute("SELECT m.model_id, m.name || ' v' || m.version AS label, count(*) AS n "
                        "FROM models m JOIN experiments e ON e.model_id = m.model_id "
                        "GROUP BY m.model_id ORDER BY n DESC LIMIT 1")
            mdl = cur.fetchone()
            if mdl:
                cur.execute("SELECT array_agg(experiment_id) AS ids FROM experiments WHERE model_id = %s",
                            (mdl["model_id"],))
                ids = cur.fetchone()["ids"]
                self._exec(cur, "DELETE FROM models WHERE model_id = %s", (mdl["model_id"],))
                cur.execute("SELECT experiment_id, model_id FROM experiments "
                            "WHERE experiment_id = ANY(%s)", (ids,))
                after = ", ".join(f"эксп.{r['experiment_id']}: model_id="
                                  f"{'NULL' if r['model_id'] is None else r['model_id']}"
                                  for r in cur.fetchall())
                out.append(f"2) ON DELETE SET NULL (models → experiments)\n"
                           f"   Удалена модель «{mdl['label']}».\n"
                           f"   Ссылавшиеся эксперименты сохранились: {after}")

            cur.execute("SELECT dataset_id, name FROM datasets d WHERE EXISTS "
                        "(SELECT 1 FROM experiments e WHERE e.dataset_id = d.dataset_id) LIMIT 1")
            ds = cur.fetchone()
            if ds:
                new_id = ds["dataset_id"] + 1000
                self._exec(cur, "UPDATE datasets SET dataset_id = %s WHERE dataset_id = %s",
                           (new_id, ds["dataset_id"]))
                cur.execute("SELECT count(*) AS n FROM experiments WHERE dataset_id = %s", (new_id,))
                n = cur.fetchone()["n"]
                out.append(f"3) ON UPDATE CASCADE (datasets → experiments)\n"
                           f"   Ключ датасета «{ds['name']}» изменён {ds['dataset_id']} → {new_id}.\n"
                           f"   Экспериментов, автоматически получивших новый dataset_id: {n}")
            if not out:
                out.append("Нет данных для демонстрации. Сначала внесите данные.")
        finally:
            conn.rollback()
            cur.close()
            log.info("ROLLBACK: ДЕМО CASCADE / SET NULL (данные восстановлены)")
        out.append("Все изменения отменены (ROLLBACK) — данные в БД не пострадали.")
        return "\n\n".join(out)
