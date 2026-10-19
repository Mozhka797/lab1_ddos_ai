-- =====================================================================
-- Лабораторная работа №1. Предметная область:
-- «Эксперименты по внедрению ИИ в обнаружение DDoS-атак» (MLOps-мини-система)
--
-- Скрипт выполняется кнопкой «Создать схему и таблицы».
-- Он идемпотентен: старая схема удаляется и создаётся заново.
-- =====================================================================

DROP SCHEMA IF EXISTS ddos_ai CASCADE;
CREATE SCHEMA ddos_ai;
SET search_path TO ddos_ai;

-- ---------------------------------------------------------------------
-- 1. Пользовательские типы (ENUM)
-- ---------------------------------------------------------------------
CREATE TYPE attack_type AS ENUM (
    'syn_flood', 'udp_flood', 'http_flood', 'icmp_flood',
    'dns_amplification', 'ntp_amplification', 'slowloris', 'benign'
);

CREATE TYPE model_algorithm AS ENUM (
    'random_forest', 'xgboost', 'lstm', 'cnn', 'transformer', 'autoencoder', 'isolation_forest'
);

CREATE TYPE run_status AS ENUM ('queued', 'running', 'finished', 'failed');

CREATE TYPE dataset_source AS ENUM ('public', 'synthetic', 'production_mirror');

-- ---------------------------------------------------------------------
-- 2. Наборы данных (сетевой трафик)
-- ---------------------------------------------------------------------
CREATE TABLE datasets (
    dataset_id    SERIAL          PRIMARY KEY,
    name          VARCHAR(100)    NOT NULL,
    source        dataset_source  NOT NULL,
    collected_on  DATE            NOT NULL,
    rows_count    BIGINT          NOT NULL,
    attack_types  attack_type[]   NOT NULL,                 -- массив ENUM
    is_labeled    BOOLEAN         NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_datasets_name          UNIQUE (name),
    CONSTRAINT chk_datasets_name_len     CHECK (char_length(trim(name)) >= 3),
    CONSTRAINT chk_datasets_rows         CHECK (rows_count > 0),
    CONSTRAINT chk_datasets_collected    CHECK (collected_on BETWEEN DATE '2000-01-01' AND CURRENT_DATE + 1), -- +1 день: запас на разницу часовых поясов клиента и сервера
    CONSTRAINT chk_datasets_attacks_nonempty CHECK (cardinality(attack_types) >= 1)
);

-- ---------------------------------------------------------------------
-- 3. ML-модели
-- ---------------------------------------------------------------------
CREATE TABLE models (
    model_id      SERIAL           PRIMARY KEY,
    name          VARCHAR(100)     NOT NULL,
    version       VARCHAR(20)      NOT NULL,
    algorithm     model_algorithm  NOT NULL,
    features      TEXT[]           NOT NULL,                -- массив признаков
    created_at    TIMESTAMP        NOT NULL DEFAULT now(),
    CONSTRAINT uq_models_name_version  UNIQUE (name, version),
    CONSTRAINT chk_models_version_fmt  CHECK (version ~ '^\d+\.\d+(\.\d+)?$'),
    CONSTRAINT chk_models_features     CHECK (cardinality(features) >= 1)
);

-- ---------------------------------------------------------------------
-- 4. Эксперименты (модель × датасет × целевой тип атаки)
--    dataset_id: ON DELETE RESTRICT  — нельзя удалить датасет, пока он используется
--    model_id:   ON DELETE SET NULL  — эксперимент остаётся в истории без модели
--    обе ссылки: ON UPDATE CASCADE  — изменение ключа родителя переносится в потомков
-- ---------------------------------------------------------------------
CREATE TABLE experiments (
    experiment_id  SERIAL         PRIMARY KEY,
    title          VARCHAR(150)   NOT NULL,
    dataset_id     INTEGER        NOT NULL,
    model_id       INTEGER,
    target_attack  attack_type    NOT NULL,
    started_on     DATE           NOT NULL DEFAULT CURRENT_DATE,
    gpu_hours      NUMERIC(8,2)   NOT NULL DEFAULT 0,
    is_production  BOOLEAN        NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_experiments_title   UNIQUE (title),
    CONSTRAINT chk_experiments_gpu    CHECK (gpu_hours >= 0 AND gpu_hours <= 10000),
    CONSTRAINT chk_experiments_target CHECK (target_attack <> 'benign'),
    CONSTRAINT fk_experiments_dataset FOREIGN KEY (dataset_id)
        REFERENCES datasets (dataset_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_experiments_model   FOREIGN KEY (model_id)
        REFERENCES models (model_id)     ON DELETE SET NULL ON UPDATE CASCADE
);

-- ---------------------------------------------------------------------
-- 5. Запуски (runs) эксперимента с метриками
--    experiment_id: ON DELETE CASCADE — удаляем эксперимент → удаляются его запуски
-- ---------------------------------------------------------------------
CREATE TABLE runs (
    run_id         SERIAL         PRIMARY KEY,
    experiment_id  INTEGER        NOT NULL,
    run_number     SMALLINT       NOT NULL,
    status         run_status     NOT NULL DEFAULT 'queued',
    started_at     TIMESTAMP      NOT NULL DEFAULT now(),
    finished_at    TIMESTAMP,
    accuracy       NUMERIC(5,4),
    precision_     NUMERIC(5,4),
    recall         NUMERIC(5,4),
    f1_score       NUMERIC(5,4),
    latency_ms     REAL,
    threshold      REAL           NOT NULL DEFAULT 0.5,
    tags           TEXT[]         NOT NULL DEFAULT '{}',    -- массив тегов
    CONSTRAINT uq_runs_exp_number   UNIQUE (experiment_id, run_number),
    CONSTRAINT chk_runs_number      CHECK (run_number > 0),
    CONSTRAINT chk_runs_metrics     CHECK (
        (accuracy    IS NULL OR accuracy    BETWEEN 0 AND 1) AND
        (precision_  IS NULL OR precision_  BETWEEN 0 AND 1) AND
        (recall      IS NULL OR recall      BETWEEN 0 AND 1) AND
        (f1_score    IS NULL OR f1_score    BETWEEN 0 AND 1)),
    CONSTRAINT chk_runs_latency     CHECK (latency_ms IS NULL OR latency_ms > 0),
    CONSTRAINT chk_runs_threshold   CHECK (threshold > 0 AND threshold < 1),
    CONSTRAINT chk_runs_time_order  CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT chk_runs_finished_has_metrics CHECK (
        status <> 'finished' OR (f1_score IS NOT NULL AND finished_at IS NOT NULL)),
    CONSTRAINT fk_runs_experiment   FOREIGN KEY (experiment_id)
        REFERENCES experiments (experiment_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX idx_runs_experiment ON runs (experiment_id);
CREATE INDEX idx_experiments_dataset ON experiments (dataset_id);
