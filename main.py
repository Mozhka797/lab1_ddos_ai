# -*- coding: utf-8 -*-
"""
Лабораторная работа №1 — «Эксперименты по внедрению ИИ в обнаружение DDoS-атак».
Python 3.12+ · PySide6 · psycopg2 · PostgreSQL 13+

Запуск:  python main.py
"""
from __future__ import annotations

import logging
import os
import sys

import psycopg2
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from db import LOG_FILE, Database, DbConfig, log
from dialogs import InsertMenuDialog, IntegrityDialog, ViewDialog, show_db_error

STYLE = """
QWidget { font-size: 13px; }
QMainWindow, QDialog { background: #f6f7f9; }
QGroupBox { border: 1px solid #d5d9e0; border-radius: 6px; margin-top: 14px; padding: 10px;
            background: #ffffff; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #33415c; }
QPushButton { background: #2f5bd3; color: white; border: none; border-radius: 5px; padding: 7px 14px; }
QPushButton:hover { background: #2549ad; }
QPushButton:disabled { background: #b8c0cf; color: #eef0f4; }
QPushButton#secondary { background: #e4e8ef; color: #1f2a40; }
QPushButton#secondary:hover { background: #d3d9e3; }
QPushButton#big { font-size: 14px; padding: 12px; text-align: left; }
QLabel#note { color: #5b6475; font-weight: normal; }
QLabel#title { font-size: 18px; font-weight: 700; color: #1f2a40; }
QPlainTextEdit { background: #10151f; color: #cfe3ff; border-radius: 5px;
                 font-family: Consolas, Menlo, "DejaVu Sans Mono", monospace; font-size: 12px; }
QTableView { background: white; gridline-color: #e3e6eb; }
"""


class QtLogBridge(QObject, logging.Handler):
    """Дублирует записи лог-файла в окно приложения."""
    message = Signal(str)

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: N802 (интерфейс logging)
        self.message.emit(self.format(record))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.setWindowTitle("DDoS-AI Lab — эксперименты ИИ для обнаружения DDoS-атак (PostgreSQL)")
        self.resize(980, 720)

        central = QWidget()
        root = QVBoxLayout(central)
        title = QLabel("MLOps-мини-система: ИИ для обнаружения DDoS-атак")
        title.setObjectName("title")
        sub = QLabel("Датасеты трафика → модели → эксперименты → запуски с метриками")
        sub.setObjectName("note")
        root.addWidget(title)
        root.addWidget(sub)

        # --- подключение
        conn_box = QGroupBox("Подключение к PostgreSQL")
        cl = QHBoxLayout(conn_box)
        form = QFormLayout()
        self.host = QLineEdit(os.getenv("PGHOST", "localhost"))
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(int(os.getenv("PGPORT", "5432")))
        self.dbname = QLineEdit(os.getenv("PGDATABASE", "ddos_lab"))
        self.user = QLineEdit(os.getenv("PGUSER", "postgres"))
        self.password = QLineEdit(os.getenv("PGPASSWORD", ""))
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Хост:", self.host)
        form.addRow("Порт:", self.port)
        form.addRow("База данных:", self.dbname)
        form.addRow("Пользователь:", self.user)
        form.addRow("Пароль:", self.password)
        cl.addLayout(form, 1)
        btns = QVBoxLayout()
        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.clicked.connect(self.do_connect)
        self.disconnect_btn = QPushButton("Отключиться")
        self.disconnect_btn.setObjectName("secondary")
        self.disconnect_btn.clicked.connect(self.do_disconnect)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        btns.addWidget(self.connect_btn)
        btns.addWidget(self.disconnect_btn)
        btns.addWidget(self.status_lbl)
        btns.addStretch(1)
        cl.addLayout(btns)
        root.addWidget(conn_box)

        # --- главные действия
        act_box = QGroupBox("Действия")
        al = QGridLayout(act_box)
        self.create_btn = self._big("①  Создать схему и таблицы", "CREATE SCHEMA / TYPE (ENUM) / TABLE", self.do_create)
        self.insert_btn = self._big("②  Внести данные", "модальные формы INSERT с транзакциями", self.do_insert)
        self.view_btn = self._big("③  Показать данные", "сводная таблица (JOIN) с фильтрами", self.do_view)
        self.integrity_btn = self._big("④  Проверка ссылочной целостности", "FK, RESTRICT, CASCADE, SET NULL", self.do_integrity)
        for i, b in enumerate([self.create_btn, self.insert_btn, self.view_btn, self.integrity_btn]):
            al.addWidget(b, i // 2, i % 2)
        root.addWidget(act_box)

        # --- лог
        log_box = QGroupBox(f"Журнал (дублируется в файл {LOG_FILE.name})")
        ll = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        ll.addWidget(self.log_view)
        root.addWidget(log_box, 1)

        self.setCentralWidget(central)

        self.bridge = QtLogBridge()
        self.bridge.message.connect(self.log_view.appendPlainText)
        log.addHandler(self.bridge)
        log.info("Приложение запущено")
        self.update_state()

    def _big(self, text: str, hint: str, slot):
        b = QPushButton(f"{text}\n{hint}")
        b.setObjectName("big")
        b.setMinimumHeight(64)
        b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------------- state
    def update_state(self, schema_ready: bool | None = None) -> None:
        connected = self.db.connected
        if schema_ready is None:
            schema_ready = False
            if connected:
                try:
                    schema_ready = self.db.schema_exists()
                except psycopg2.Error:
                    schema_ready = False
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        for w in (self.host, self.port, self.dbname, self.user, self.password):
            w.setEnabled(not connected)
        self.create_btn.setEnabled(connected)
        for b in (self.insert_btn, self.view_btn, self.integrity_btn):
            b.setEnabled(connected and schema_ready)
        if not connected:
            self.status_lbl.setText("● Не подключено")
            self.status_lbl.setStyleSheet("color:#c62828")
        elif not schema_ready:
            self.status_lbl.setText("● Подключено.\nСхема ddos_ai ещё не создана — нажмите ①")
            self.status_lbl.setStyleSheet("color:#b26a00")
        else:
            self.status_lbl.setText("● Подключено, схема ddos_ai готова")
            self.status_lbl.setStyleSheet("color:#1b7f3b")

    # -------------------------------------------------------------- actions
    def do_connect(self) -> None:
        cfg = DbConfig(host=self.host.text().strip() or "localhost", port=self.port.value(),
                       dbname=self.dbname.text().strip(), user=self.user.text().strip(),
                       password=self.password.text())
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.db.connect(cfg)
        except psycopg2.Error as e:
            QApplication.restoreOverrideCursor()
            show_db_error(self, e)
            self.update_state()
            return
        QApplication.restoreOverrideCursor()
        self.update_state()

    def do_disconnect(self) -> None:
        self.db.close()
        self.update_state()

    def do_create(self) -> None:
        ans = QMessageBox.question(
            self, "Создать схему",
            "Будет выполнен скрипт schema.sql:\n"
            "DROP SCHEMA IF EXISTS ddos_ai CASCADE; CREATE SCHEMA / TYPE / TABLE …\n\n"
            "Существующие данные в схеме ddos_ai будут удалены. Продолжить?")
        if ans != QMessageBox.Yes:
            return
        try:
            stmts = self.db.create_schema()
        except (psycopg2.Error, OSError) as e:
            show_db_error(self, e)
            self.update_state()
            return
        self.update_state(schema_ready=True)
        QMessageBox.information(self, "Схема создана",
                                f"Выполнено DDL-операторов: {len(stmts)}.\n\n"
                                "Созданы ENUM: attack_type, model_algorithm, run_status, dataset_source\n"
                                "Таблицы: datasets, models, experiments, runs")

    def do_insert(self) -> None:
        InsertMenuDialog(self, self.db).exec()

    def do_view(self) -> None:
        ViewDialog(self, self.db).exec()

    def do_integrity(self) -> None:
        IntegrityDialog(self, self.db).exec()

    def closeEvent(self, event) -> None:  # noqa: N802
        log.removeHandler(self.bridge)
        self.db.close()
        log.info("Приложение закрыто")
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont(app.font().family(), 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
