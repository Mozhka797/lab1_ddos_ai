# -*- coding: utf-8 -*-
"""Модальные окна: формы ввода, просмотр данных с фильтрами, демонстрация ссылочной целостности."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg2
from PySide6.QtCore import QAbstractTableModel, QDate, QDateTime, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableView, QVBoxLayout, QWidget,
)

from db import Database, log
from errors import humanize


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def show_db_error(parent: QWidget, exc: Exception) -> None:
    title, text = humanize(exc)
    QMessageBox.critical(parent, title, text)


def text_or_none(edit: QLineEdit) -> Optional[str]:
    """Пустое поле → NULL: пусть NOT NULL проверяет сама СУБД."""
    t = edit.text().strip()
    return t or None


def split_list(edit: QLineEdit) -> list[str]:
    return [p.strip() for p in edit.text().split(",") if p.strip()]


def qdate_to_py(d: QDate) -> date:
    return date(d.year(), d.month(), d.day())


def qdt_to_py(d: QDateTime) -> datetime:
    return d.toPython().replace(microsecond=0)


class BaseDialog(QDialog):
    """Модальный диалог: пока он открыт, родительское окно недоступно."""

    def __init__(self, parent: QWidget, title: str, db: Database):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)


class EnumCombo(QComboBox):
    def __init__(self, values: list[str], with_all: bool = False):
        super().__init__()
        if with_all:
            self.addItem("— любой —", None)
        for v in values:
            self.addItem(v, v)

    def value(self) -> Optional[str]:
        return self.currentData()


class RefCombo(QWidget):
    """
    Выбор родительской записи для внешнего ключа.
    Последний пункт «Ввести ID вручную» позволяет указать несуществующий ID
    и увидеть, как СУБД отклонит нарушение FOREIGN KEY.
    """

    def __init__(self, items: list[tuple[str, int]], allow_empty: bool = False):
        super().__init__()
        self.combo = QComboBox()
        if allow_empty:
            self.combo.addItem("— не указана (NULL) —", None)
        for label, pk in items:
            self.combo.addItem(f"{label}  [id={pk}]", pk)
        self.combo.addItem("✎ Ввести ID вручную…", "manual")
        self.spin = QSpinBox()
        self.spin.setRange(1, 999_999_999)
        self.spin.setValue(999_999)
        self.spin.setVisible(False)
        self.combo.currentIndexChanged.connect(
            lambda: self.spin.setVisible(self.combo.currentData() == "manual"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.spin)

    def value(self) -> Optional[int]:
        d = self.combo.currentData()
        return self.spin.value() if d == "manual" else d


class FormDialog(BaseDialog):
    """Общая заготовка формы: поля + кнопки «Сохранить (INSERT)» / «Отмена»."""

    def __init__(self, parent, title, db, note: str = ""):
        super().__init__(parent, title, db)
        self.setMinimumWidth(520)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)
        lay = QVBoxLayout(self)
        if note:
            lbl = QLabel(note)
            lbl.setWordWrap(True)
            lbl.setObjectName("note")
            lay.addWidget(lbl)
        lay.addLayout(self.form)
        buttons = QDialogButtonBox()
        self.save_btn = buttons.addButton("Сохранить (INSERT)", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.on_save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def insert(self) -> int:  # переопределяется
        raise NotImplementedError

    def on_save(self) -> None:
        try:
            new_id = self.insert()
        except psycopg2.Error as e:
            show_db_error(self, e)       # транзакция уже откатилась (ROLLBACK)
            return
        QMessageBox.information(self, "Готово", f"Запись добавлена (id = {new_id}).\nТранзакция зафиксирована (COMMIT).")
        self.accept()


NOTE = ("Проверку данных выполняет сама СУБД: можно оставить поле пустым или ввести "
        "неверное значение — PostgreSQL отклонит его, а приложение покажет причину.")


class DatasetForm(FormDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Добавить датасет", db, NOTE)
        self.name = QLineEdit(); self.name.setPlaceholderText("например, CIC-DDoS2019")
        self.source = EnumCombo(db.enum_values("dataset_source"))
        self.collected = QDateEdit(QDate.currentDate()); self.collected.setCalendarPopup(True)
        self.collected.setDisplayFormat("yyyy-MM-dd")
        self.rows = QLineEdit(); self.rows.setPlaceholderText("целое > 0 (BIGINT)")
        self.attacks = QListWidget(); self.attacks.setMaximumHeight(150)
        for v in db.enum_values("attack_type"):
            it = QListWidgetItem(v); it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked); self.attacks.addItem(it)
        self.labeled = QCheckBox("Размечен (есть метки атака/норма)"); self.labeled.setChecked(True)
        self.form.addRow("Название*:", self.name)
        self.form.addRow("Источник (ENUM):", self.source)
        self.form.addRow("Дата сбора (DATE):", self.collected)
        self.form.addRow("Кол-во строк*:", self.rows)
        self.form.addRow("Типы трафика\n(массив ENUM[]):", self.attacks)
        self.form.addRow("BOOLEAN:", self.labeled)

    def insert(self) -> int:
        attacks = [self.attacks.item(i).text() for i in range(self.attacks.count())
                   if self.attacks.item(i).checkState() == Qt.Checked]
        return self.db.insert_dataset(text_or_none(self.name), self.source.value(),
                                      qdate_to_py(self.collected.date()), text_or_none(self.rows),
                                      attacks, self.labeled.isChecked())


class ModelForm(FormDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Добавить ML-модель", db, NOTE)
        self.name = QLineEdit(); self.name.setPlaceholderText("например, FlowGuard")
        self.version = QLineEdit(); self.version.setPlaceholderText("X.Y или X.Y.Z")
        self.algo = EnumCombo(db.enum_values("model_algorithm"))
        self.features = QLineEdit(); self.features.setPlaceholderText("pkt_rate, syn_ratio, src_entropy")
        self.form.addRow("Название*:", self.name)
        self.form.addRow("Версия*:", self.version)
        self.form.addRow("Алгоритм (ENUM):", self.algo)
        self.form.addRow("Признаки (TEXT[])*:", self.features)

    def insert(self) -> int:
        return self.db.insert_model(text_or_none(self.name), text_or_none(self.version),
                                    self.algo.value(), split_list(self.features))


class ExperimentForm(FormDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Добавить эксперимент", db, NOTE)
        self.title_ = QLineEdit(); self.title_.setPlaceholderText("например, XGBoost против UDP flood")
        self.dataset = RefCombo([(r["name"], r["dataset_id"]) for r in db.list_datasets()])
        self.model = RefCombo([(r["label"], r["model_id"]) for r in db.list_models()], allow_empty=True)
        self.target = EnumCombo(db.enum_values("attack_type"))
        self.started = QDateEdit(QDate.currentDate()); self.started.setCalendarPopup(True)
        self.started.setDisplayFormat("yyyy-MM-dd")
        self.gpu = QLineEdit("0"); self.gpu.setPlaceholderText("NUMERIC(8,2), 0…10000")
        self.prod = QCheckBox("Выведен в прод")
        self.form.addRow("Название*:", self.title_)
        self.form.addRow("Датасет (FK)*:", self.dataset)
        self.form.addRow("Модель (FK):", self.model)
        self.form.addRow("Целевая атака (ENUM):", self.target)
        self.form.addRow("Дата начала:", self.started)
        self.form.addRow("GPU-часы:", self.gpu)
        self.form.addRow("BOOLEAN:", self.prod)

    def insert(self) -> int:
        return self.db.insert_experiment(text_or_none(self.title_), self.dataset.value(),
                                         self.model.value(), self.target.value(),
                                         qdate_to_py(self.started.date()), text_or_none(self.gpu),
                                         self.prod.isChecked())


class RunForm(FormDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Добавить запуск эксперимента", db, NOTE)
        exps = db.list_experiments()
        self._next = {r["experiment_id"]: r["next_run"] for r in exps}
        self.exp = RefCombo([(r["title"], r["experiment_id"]) for r in exps])
        self.number = QLineEdit(); self.number.setPlaceholderText("SMALLINT > 0")
        self.exp.combo.currentIndexChanged.connect(self._suggest_number)
        self.status = EnumCombo(db.enum_values("run_status"))
        self.started = QDateTimeEdit(QDateTime.currentDateTime()); self.started.setCalendarPopup(True)
        self.started.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.has_finish = QCheckBox("указать")
        self.finished = QDateTimeEdit(QDateTime.currentDateTime()); self.finished.setCalendarPopup(True)
        self.finished.setDisplayFormat("yyyy-MM-dd HH:mm"); self.finished.setEnabled(False)
        self.has_finish.toggled.connect(self.finished.setEnabled)
        fin = QWidget(); fl = QHBoxLayout(fin); fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.has_finish); fl.addWidget(self.finished, 1)
        self.acc, self.prec, self.rec, self.f1 = (QLineEdit() for _ in range(4))
        for e in (self.acc, self.prec, self.rec, self.f1):
            e.setPlaceholderText("0…1, например 0.9731 (можно пусто)")
        self.lat = QLineEdit(); self.lat.setPlaceholderText("мс > 0 (REAL)")
        self.thr = QLineEdit("0.5"); self.thr.setPlaceholderText("0 < порог < 1")
        self.tags = QLineEdit(); self.tags.setPlaceholderText("gpu, tuned, baseline")
        self.form.addRow("Эксперимент (FK)*:", self.exp)
        self.form.addRow("Номер запуска*:", self.number)
        self.form.addRow("Статус (ENUM):", self.status)
        self.form.addRow("Начало (TIMESTAMP):", self.started)
        self.form.addRow("Окончание:", fin)
        self.form.addRow("Accuracy:", self.acc)
        self.form.addRow("Precision:", self.prec)
        self.form.addRow("Recall:", self.rec)
        self.form.addRow("F1-score:", self.f1)
        self.form.addRow("Latency, мс:", self.lat)
        self.form.addRow("Порог:", self.thr)
        self.form.addRow("Теги (TEXT[]):", self.tags)
        self._suggest_number()

    def _suggest_number(self) -> None:
        n = self._next.get(self.exp.value())
        if n:
            self.number.setText(str(n))

    def insert(self) -> int:
        return self.db.insert_run(
            self.exp.value(), text_or_none(self.number), self.status.value(),
            qdt_to_py(self.started.dateTime()),
            qdt_to_py(self.finished.dateTime()) if self.has_finish.isChecked() else None,
            text_or_none(self.acc), text_or_none(self.prec), text_or_none(self.rec),
            text_or_none(self.f1), text_or_none(self.lat), text_or_none(self.thr),
            split_list(self.tags))


# ---------------------------------------------------------------------------
# «Внести данные»: модальное меню выбора формы
# ---------------------------------------------------------------------------
class InsertMenuDialog(BaseDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Внести данные", db)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lbl = QLabel("Выберите, что добавить. Каждая форма открывается в отдельном модальном окне.\n"
                     "Порядок: датасет и модель → эксперимент → запуск.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        for text, cls in [("📁  Датасет (datasets)", DatasetForm), ("🧠  ML-модель (models)", ModelForm),
                          ("🧪  Эксперимент (experiments)", ExperimentForm),
                          ("▶  Запуск эксперимента (runs)", RunForm)]:
            b = QPushButton(text); b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, c=cls: self.open_form(c))
            lay.addWidget(b)
        demo = QPushButton("Заполнить демо-данными (одна транзакция)")
        demo.setObjectName("secondary")
        demo.clicked.connect(self.load_demo)
        lay.addWidget(demo)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        lay.addWidget(close)

    def open_form(self, cls) -> None:
        try:
            dlg = cls(self, self.db)
        except psycopg2.Error as e:
            show_db_error(self, e)
            return
        dlg.exec()

    def load_demo(self) -> None:
        try:
            counts = self.db.insert_demo_data()
        except psycopg2.Error as e:
            show_db_error(self, e)
            return
        QMessageBox.information(self, "Демо-данные", "Данные добавлены (повторы пропущены).\n\n" +
                                "\n".join(f"{k}: {v} записей" for k, v in counts.items()))


# ---------------------------------------------------------------------------
# Табличная модель для QTableView
# ---------------------------------------------------------------------------
class DictTableModel(QAbstractTableModel):
    def __init__(self, keys: list[str], headers: list[str], rows: list[dict]):
        super().__init__()
        self.keys, self.headers, self.rows = keys, headers, rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.keys)

    @staticmethod
    def fmt(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "да" if v else "нет"
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M")
        if isinstance(v, (list, tuple)):
            return ", ".join(map(str, v))
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        key = self.keys[index.column()]
        v = self.rows[index.row()].get(key)
        if role == Qt.DisplayRole:
            return self.fmt(v)
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if key == "status":
                return QColor({"finished": "#1b7f3b", "failed": "#c62828",
                               "running": "#1565c0"}.get(str(v), "#6b6b6b"))
            if key == "f1_score" and v is not None:
                return QColor("#1b7f3b" if v >= Decimal("0.97") else
                              "#b26a00" if v >= Decimal("0.9") else "#c62828")
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return self.headers[section] if orientation == Qt.Horizontal else str(section + 1)


# ---------------------------------------------------------------------------
# «Показать данные»: сводная таблица с фильтрами
# ---------------------------------------------------------------------------
class ViewDialog(BaseDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Показать данные — эксперименты и запуски", db)
        self.resize(1280, 680)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Источник:"))
        self.source = QComboBox()
        self.source.addItem("Сводка: EXPERIMENTS ⋈ DATASETS ⟕ MODELS ⟕ RUNS", "summary")
        for t, ru in Database.TABLES.items():
            self.source.addItem(f"Таблица {t} ({ru})", t)
        self.source.currentIndexChanged.connect(self.reload)
        top.addWidget(self.source, 1)
        lay.addLayout(top)

        self.filters = QGroupBox("Фильтры")
        g = QGridLayout(self.filters)
        self.f_attack = EnumCombo(db.enum_values("attack_type"), with_all=True)
        self.f_algo = EnumCombo(db.enum_values("model_algorithm"), with_all=True)
        self.f_status = EnumCombo(db.enum_values("run_status"), with_all=True)
        self.f_use_dates = QCheckBox("Период:")
        self.f_from = QDateEdit(QDate.currentDate().addMonths(-3)); self.f_from.setCalendarPopup(True)
        self.f_to = QDateEdit(QDate.currentDate()); self.f_to.setCalendarPopup(True)
        for d in (self.f_from, self.f_to):
            d.setDisplayFormat("yyyy-MM-dd")
        self.f_use_f1 = QCheckBox("F1 ≥")
        self.f_f1 = QDoubleSpinBox(); self.f_f1.setRange(0, 1); self.f_f1.setDecimals(3)
        self.f_f1.setSingleStep(0.01); self.f_f1.setValue(0.95)
        self.f_use_lat = QCheckBox("Latency ≤")
        self.f_lat = QDoubleSpinBox(); self.f_lat.setRange(0, 100000); self.f_lat.setValue(5)
        self.f_lat.setSuffix(" мс")
        self.f_tag = QLineEdit(); self.f_tag.setPlaceholderText("тег (поиск в массиве tags)")
        self.f_prod = QCheckBox("Только прод")

        g.addWidget(QLabel("Целевая атака:"), 0, 0); g.addWidget(self.f_attack, 0, 1)
        g.addWidget(QLabel("Алгоритм:"), 0, 2); g.addWidget(self.f_algo, 0, 3)
        g.addWidget(QLabel("Статус запуска:"), 0, 4); g.addWidget(self.f_status, 0, 5)
        g.addWidget(self.f_use_dates, 1, 0)
        dates = QHBoxLayout(); dates.addWidget(self.f_from); dates.addWidget(QLabel("—")); dates.addWidget(self.f_to)
        g.addLayout(dates, 1, 1)
        g.addWidget(self.f_use_f1, 1, 2); g.addWidget(self.f_f1, 1, 3)
        g.addWidget(self.f_use_lat, 1, 4); g.addWidget(self.f_lat, 1, 5)
        g.addWidget(QLabel("Тег:"), 2, 0); g.addWidget(self.f_tag, 2, 1)
        g.addWidget(self.f_prod, 2, 2)
        apply_btn = QPushButton("Применить"); apply_btn.clicked.connect(self.reload)
        reset_btn = QPushButton("Сбросить"); reset_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self.reset_filters)
        g.addWidget(apply_btn, 2, 4); g.addWidget(reset_btn, 2, 5)
        lay.addWidget(self.filters)

        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_lbl = QLabel()
        bottom.addWidget(self.count_lbl, 1)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self.reload()

    def reset_filters(self) -> None:
        for c in (self.f_attack, self.f_algo, self.f_status):
            c.setCurrentIndex(0)
        for c in (self.f_use_dates, self.f_use_f1, self.f_use_lat, self.f_prod):
            c.setChecked(False)
        self.f_tag.clear()
        self.reload()

    def reload(self) -> None:
        src = self.source.currentData()
        self.filters.setEnabled(src == "summary")
        try:
            if src == "summary":
                rows = self.db.fetch_summary(
                    attack=self.f_attack.value(), algorithm=self.f_algo.value(),
                    status=self.f_status.value(),
                    date_from=qdate_to_py(self.f_from.date()) if self.f_use_dates.isChecked() else None,
                    date_to=qdate_to_py(self.f_to.date()) if self.f_use_dates.isChecked() else None,
                    min_f1=self.f_f1.value() if self.f_use_f1.isChecked() else None,
                    max_latency=self.f_lat.value() if self.f_use_lat.isChecked() else None,
                    tag=self.f_tag.text().strip() or None,
                    production_only=self.f_prod.isChecked())
                keys = [k for k, _ in Database.SUMMARY_COLUMNS]
                headers = [h for _, h in Database.SUMMARY_COLUMNS]
            else:
                keys, rows = self.db.fetch_table(src)
                headers = keys
        except psycopg2.Error as e:
            show_db_error(self, e)
            rows, keys, headers = [], [], []
        self.model = DictTableModel(keys, headers, rows)
        self.table.setModel(self.model)
        self.table.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            if self.table.columnWidth(c) > 260:
                self.table.setColumnWidth(c, 260)
        self.count_lbl.setText(f"Строк: {len(rows)}")


# ---------------------------------------------------------------------------
# Демонстрация ссылочной целостности
# ---------------------------------------------------------------------------
class IntegrityDialog(BaseDialog):
    def __init__(self, parent, db: Database):
        super().__init__(parent, "Проверка ссылочной целостности", db)
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        intro = QLabel("Каждая кнопка пытается нарушить связи между таблицами. "
                       "СУБД должна отклонить операцию или обработать её по правилу ON DELETE / ON UPDATE.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        cases = [
            ("1. Запуск для несуществующего эксперимента",
             "INSERT INTO runs (experiment_id = 999999 …) → ожидаем ошибку FOREIGN KEY", self.case_fk),
            ("2. Удалить датасет, который используется",
             "DELETE FROM datasets … → ON DELETE RESTRICT, ожидаем отказ", self.case_restrict),
            ("3. CASCADE / SET NULL / ON UPDATE CASCADE",
             "Удаление эксперимента и модели, смена ключа датасета — внутри транзакции с ROLLBACK",
             self.case_cascade),
        ]
        for title, hint, fn in cases:
            row = QHBoxLayout()
            b = QPushButton(title); b.setMinimumWidth(320); b.clicked.connect(fn)
            h = QLabel(hint); h.setWordWrap(True); h.setObjectName("note")
            row.addWidget(b); row.addWidget(h, 1)
            lay.addLayout(row)
        self.out = QPlainTextEdit(); self.out.setReadOnly(True)
        lay.addWidget(self.out, 1)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)

    def _report(self, header: str, body: str) -> None:
        self.out.appendPlainText(f"■ {header}\n{body}\n")

    def _expect_error(self, name: str, fn) -> None:
        try:
            res = fn()
            self._report(name, res or "Операция выполнена без ошибки.")
        except psycopg2.Error as e:
            title, text = humanize(e)
            self._report(f"{name}: СУБД отклонила операцию ✔ — {title}", text + "\nТранзакция откатана (ROLLBACK).")

    def case_fk(self) -> None:
        self._expect_error("FOREIGN KEY при INSERT", self.db.demo_fk_insert)

    def case_restrict(self) -> None:
        self._expect_error("ON DELETE RESTRICT", self.db.demo_restrict_delete)

    def case_cascade(self) -> None:
        try:
            self._report("Каскадные правила", self.db.demo_cascade_and_set_null())
        except psycopg2.Error as e:
            show_db_error(self, e)
