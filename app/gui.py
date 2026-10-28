"""PDF2PVS – Oberfläche zum Prüfen und Korrigieren von Laborwerten vor dem LDT-Import."""
from __future__ import annotations

import csv
import datetime as _dt
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter,
                               QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget)

from . import __version__
from .config import Settings, app_dir, resource
from .extract import extract, find_tesseract, render_page
from .ldt import build_ldt, check_ldt
from .mapping import IGNORE, Mapping
from .parser import Report, Result, check_result, parse

COLS = ["✓", "Kürzel", "Bezeichnung im PDF", "Wert", "Einheit", "Kz.", "Referenz", "Hinweise",
        "Datum", "Zeit", "Auftrag", "Kommentar"]
C_USE, C_CODE, C_NAME, C_VAL, C_UNIT, C_FLAG, C_REF, C_WARN, C_DATE, C_TIME, C_ORDER, C_COMM = range(12)
RED, YELLOW, GREY = QColor("#f9d6d5"), QColor("#fff1c2"), QColor("#9a9a9a")
SEL = QColor(220, 40, 40, 70)


# ======================================================================= Hintergrund-Einlesen
class Worker(QObject):
    done = Signal(object, object)

    def __init__(self, path, mapping, tess):
        super().__init__()
        self.path, self.mapping, self.tess = path, mapping, tess

    def run(self):
        try:
            ex = extract(self.path, self.tess)
            rep = parse(ex, self.mapping, self.path)
            self.done.emit(rep, None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


# ======================================================================= Kürzel-Auswahl
class CodeDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)
        items = [f"{a.code}  –  {a.name}" for a in self.win.mapping.analysen]
        cb.addItems(items)
        comp = QCompleter(items, cb)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        cb.setCompleter(comp)
        return cb

    def setEditorData(self, editor, index):
        code = index.data() or ""
        for i in range(editor.count()):
            if editor.itemText(i).split("  –  ")[0] == code:
                editor.setCurrentIndex(i)
                return
        editor.setEditText(code)

    def setModelData(self, editor, model, index):
        txt = editor.currentText().split("  –  ")[0].strip()
        model.setData(index, txt)


# ======================================================================= PDF-Ansicht
class PdfView(QWidget):
    def __init__(self):
        super().__init__()
        self.path, self.page, self.pages, self.zoom = None, 0, 1, 1.6
        self.highlight = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.prev = QPushButton("◀")
        self.next = QPushButton("▶")
        self.lbl = QLabel("–")
        self.zin = QPushButton("+")
        self.zout = QPushButton("−")
        for b in (self.prev, self.next, self.zin, self.zout):
            b.setFixedWidth(34)
        bar.addWidget(self.prev)
        bar.addWidget(self.lbl)
        bar.addWidget(self.next)
        bar.addStretch()
        bar.addWidget(self.zout)
        bar.addWidget(self.zin)
        lay.addLayout(bar)
        self.area = QScrollArea()
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.area.setWidget(self.img)
        self.area.setWidgetResizable(True)
        lay.addWidget(self.area)
        self.prev.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next.clicked.connect(lambda: self.show_page(self.page + 1))
        self.zin.clicked.connect(lambda: self.set_zoom(self.zoom * 1.2))
        self.zout.clicked.connect(lambda: self.set_zoom(self.zoom / 1.2))

    def load(self, path, pages, page_width=595):
        self.path, self.pages, self.highlight = path, max(1, pages), None
        avail = self.area.viewport().width() - 24
        if avail > 200:
            self.zoom = max(0.6, min(3.0, avail / page_width))
        self.show_page(0)

    def set_zoom(self, z):
        self.zoom = max(0.6, min(4.0, z))
        self.show_page(self.page)

    def show_page(self, p, highlight=None, scroll=False):
        if not self.path:
            return
        p = max(0, min(self.pages - 1, p))
        self.page = p
        if highlight is not None:
            self.highlight = highlight
        pix = QPixmap()
        pix.loadFromData(render_page(self.path, p, self.zoom))
        if self.highlight and self.highlight[0] == p:
            _, (x0, y0, x1, y1) = self.highlight
            painter = QPainter(pix)
            painter.setPen(QPen(QColor(220, 40, 40), 2))
            painter.setBrush(QBrush(QColor(255, 220, 0, 70)))
            painter.drawRect(QRectF(x0 * self.zoom - 3, y0 * self.zoom - 3,
                                    (x1 - x0) * self.zoom + 6, (y1 - y0) * self.zoom + 6))
            painter.end()
            if scroll:
                self.img.setPixmap(pix)
                self.area.ensureVisible(int(x0 * self.zoom), int(y0 * self.zoom), 50, 150)
        self.img.setPixmap(pix)
        self.lbl.setText(f"Seite {p + 1} / {self.pages}")


# ======================================================================= Hauptfenster
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.mapping = Mapping(Path(self.settings.mapping_file))
        self.report: Report | None = None
        self.path: str | None = None
        self._filling = False
        self._thread = None
        self.setWindowTitle(f"PDF2PVS {__version__} – Laborbefunde prüfen und nach x.isynet übernehmen")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        icon = resource("daten/pdf2pvs.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self._build_ui()
        self.refresh_files()

    # ------------------------------------------------------------------ Aufbau
    def _build_ui(self):
        tb = QToolBar("Aktionen")
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("PDF öffnen…", self.open_pdf, "Ctrl+O")
        act("Neu einlesen", self.reparse, "F5")
        tb.addSeparator()
        act("Zeile hinzufügen", self.add_row)
        act("Zeile löschen", self.delete_row, "Del")
        tb.addSeparator()
        act("Zuordnung…", self.edit_mapping, tip="Laborbezeichnungen ↔ x.isynet-Kürzel")
        act("Einstellungen…", self.edit_settings)
        tb.addSeparator()
        act("LDT-Vorschau", self.preview_ldt)
        self.export_act = act("LDT exportieren ▶", self.export_ldt, "Ctrl+S")
        f = self.export_act.font()
        f.setBold(True)
        self.export_act.setFont(f)

        # Dateiliste
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addWidget(QLabel("<b>Eingangsordner</b>"))
        self.dir_lbl = QLabel()
        self.dir_lbl.setWordWrap(True)
        self.dir_lbl.setStyleSheet("color:#666")
        ll.addWidget(self.dir_lbl)
        self.files = QListWidget()
        self.files.itemDoubleClicked.connect(lambda it: self.load_pdf(it.data(Qt.UserRole)))
        ll.addWidget(self.files)
        rb = QHBoxLayout()
        b1 = QPushButton("Ordner wählen…")
        b1.clicked.connect(self.choose_input_dir)
        b2 = QPushButton("Aktualisieren")
        b2.clicked.connect(self.refresh_files)
        rb.addWidget(b1)
        rb.addWidget(b2)
        ll.addLayout(rb)

        # Mitte: Kopfdaten + Tabelle + Befundtext
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(4, 4, 4, 4)
        self.file_lbl = QLabel("<i>Kein PDF geladen – PDF hierher ziehen oder „PDF öffnen…“</i>")
        ml.addWidget(self.file_lbl)
        grid = QGridLayout()
        self.f_surname, self.f_first, self.f_dob = QLineEdit(), QLineEdit(), QLineEdit()
        self.f_sex = QComboBox()
        self.f_sex.addItems(["", "W", "M"])
        self.f_patnr, self.f_sample, self.f_report = QLineEdit(), QLineEdit(), QLineEdit()
        self.f_order = QLineEdit()
        self.f_status = QComboBox()
        self.f_status.addItems(["E – Endbefund", "T – Teilbefund", "V – vorläufig"])
        for w in (self.f_dob, self.f_sample, self.f_report):
            w.setPlaceholderText("TT.MM.JJJJ")
            w.setMaximumWidth(110)
        self.f_patnr.setPlaceholderText("optional")
        fields = [("Name", self.f_surname), ("Vorname", self.f_first), ("Geb.-Datum", self.f_dob),
                  ("Geschlecht", self.f_sex), ("Pat.-Nr. (PVS)", self.f_patnr),
                  ("Abnahme", self.f_sample), ("Befunddatum", self.f_report),
                  ("Auftrag", self.f_order), ("Befundart", self.f_status)]
        for i, (lab, w) in enumerate(fields):
            r, c = divmod(i, 5)
            grid.addWidget(QLabel(lab), r * 2, c)
            grid.addWidget(w, r * 2 + 1, c)
        ml.addLayout(grid)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#fff1c2;border:1px solid #e0c060;padding:4px;")
        self.banner.hide()
        ml.addWidget(self.banner)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setItemDelegateForColumn(C_CODE, CodeDelegate(self))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in zip(range(len(COLS)), [28, 72, 165, 70, 75, 34, 80, 290, 80, 45, 75, 200]):
            self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.currentCellChanged.connect(self.on_row_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        ml.addWidget(self.table, 1)
        self.summary = QLabel()
        ml.addWidget(self.summary)
        ml.addWidget(QLabel("Befundtext / Kommentare des Labors (geht als Auftragshinweis mit):"))
        self.comment = QPlainTextEdit()
        self.comment.setMaximumHeight(110)
        ml.addWidget(self.comment)

        self.pdf = PdfView()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(self.pdf)
        split.setSizes([200, 820, 520])
        self.setCentralWidget(split)
        self.statusBar().showMessage("Bereit")
        if not find_tesseract(self.settings.tesseract_path):
            self.statusBar().showMessage("Hinweis: Tesseract-OCR nicht gefunden – gescannte PDFs "
                                         "können nicht gelesen werden (siehe Einstellungen).")

    # ------------------------------------------------------------------ Dateien
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self.load_pdf(p)
                break

    def choose_input_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Eingangsordner wählen", self.settings.input_dir)
        if d:
            self.settings.input_dir = d
            self.settings.save()
            self.refresh_files()

    def _exported(self) -> set[str]:
        p = app_dir() / "export_protokoll.csv"
        if not p.exists():
            return set()
        with open(p, encoding="utf-8-sig") as f:
            return {r[1] for r in csv.reader(f, delimiter=";") if len(r) > 1}

    def refresh_files(self):
        self.files.clear()
        d = self.settings.input_dir
        self.dir_lbl.setText(d or "(kein Ordner gewählt)")
        if not d or not os.path.isdir(d):
            return
        done = self._exported()
        for p in sorted(Path(d).glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix.lower() != ".pdf":
                continue
            it = QListWidgetItem(("✓ " if str(p) in done else "") + p.name)
            it.setData(Qt.UserRole, str(p))
            if str(p) in done:
                it.setForeground(QBrush(GREY))
            self.files.addItem(it)

    def open_pdf(self):
        p, _ = QFileDialog.getOpenFileName(self, "Labor-PDF öffnen", self.settings.input_dir,
                                           "PDF-Dateien (*.pdf)")
        if p:
            self.load_pdf(p)

    def reparse(self):
        if self.path:
            self.mapping.load()
            self.load_pdf(self.path)

    def load_pdf(self, path: str):
        if self._thread is not None:
            return
        self.path = path
        self.file_lbl.setText(f"<b>{Path(path).name}</b> wird gelesen …")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Lese PDF … (bei Scans kann die Texterkennung einige Sekunden dauern)")
        self._thread = QThread()
        self._worker = Worker(path, self.mapping, self.settings.tesseract_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._loaded)
        self._thread.start()

    def _loaded(self, rep, err):
        QApplication.restoreOverrideCursor()
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if err is not None:
            self.file_lbl.setText(f"<b>{Path(self.path).name}</b>")
            QMessageBox.critical(self, "Fehler beim Lesen", f"Das PDF konnte nicht gelesen werden:\n{err}")
            return
        self.report = rep
        import pymupdf
        with pymupdf.open(self.path) as d:
            n, pw = len(d), d[0].rect.width
        self.pdf.load(self.path, n, pw)
        self.fill_form()
        self.fill_table()
        self.file_lbl.setText(f"<b>{Path(self.path).name}</b>" + ("  <span style='color:#a60'>(Scan / OCR)</span>" if rep.ocr else ""))
        self.statusBar().showMessage("Fertig – bitte Werte prüfen. Doppelklick zum Bearbeiten.")

    # ------------------------------------------------------------------ Anzeige
    def fill_form(self):
        r = self.report
        self.f_surname.setText(r.surname)
        self.f_first.setText(r.firstname)
        self.f_dob.setText(r.dob)
        self.f_sex.setCurrentText(r.sex)
        self.f_patnr.setText(r.patnr)
        self.f_sample.setText(r.sample_date)
        self.f_report.setText(r.report_date)
        self.f_order.setText(r.order)
        self.f_status.setCurrentIndex({"E": 0, "T": 1, "V": 2}.get(r.status, 0))
        self.comment.setPlainText(r.comment)
        if r.warnings:
            self.banner.setText("⚠ " + "<br>⚠ ".join(r.warnings))
            self.banner.show()
        else:
            self.banner.hide()

    def fill_table(self):
        self._filling = True
        t = self.table
        t.setRowCount(0)
        for res in self.report.results:
            self._append_row(res)
        self._filling = False
        self.update_summary()

    def _append_row(self, res: Result):
        t = self.table
        row = t.rowCount()
        t.insertRow(row)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chk.setCheckState(Qt.Checked if res.use else Qt.Unchecked)
        t.setItem(row, C_USE, chk)
        vals = {C_CODE: res.code, C_NAME: res.name, C_VAL: res.value, C_UNIT: res.unit,
                C_FLAG: res.flag, C_REF: res.ref, C_DATE: res.date, C_TIME: res.time,
                C_ORDER: res.order, C_COMM: res.comment.replace("\n", " "),
                C_WARN: "; ".join(res.warnings)}
        for c, v in vals.items():
            it = QTableWidgetItem(v)
            if c in (C_NAME, C_WARN):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            if c == C_COMM and res.comment:
                it.setToolTip(res.comment)
            if c == C_WARN and res.warnings:
                it.setToolTip("\n".join(res.warnings))
            t.setItem(row, c, it)
        t.item(row, C_VAL).setFont(QFont(t.font().family(), weight=QFont.Bold))
        t.item(row, C_NAME).setData(Qt.UserRole, res)
        self._color_row(row)

    def _color_row(self, row):
        res = self.table.item(row, C_NAME).data(Qt.UserRole)
        use = self.table.item(row, C_USE).checkState() == Qt.Checked
        lvl = res.level
        bg = RED if (lvl == 2 and use) else YELLOW if (lvl == 1 and use) else None
        for c in range(len(COLS)):
            it = self.table.item(row, c)
            if it is None:
                continue
            it.setBackground(QBrush(bg) if bg else QBrush())
            it.setForeground(QBrush(GREY) if not use else QBrush())
        flag = self.table.item(row, C_FLAG)
        if use and res.flag:
            flag.setForeground(QBrush(QColor("#c00000") if res.flag.startswith("+")
                                      else QColor("#0050c0") if res.flag.startswith("-")
                                      else QColor("#a06000")))

    def update_summary(self):
        if not self.report:
            return
        rows = [self.table.item(r, C_NAME).data(Qt.UserRole) for r in range(self.table.rowCount())]
        used = [r for r in rows if r.use]
        err = sum(1 for r in used if r.level == 2)
        warn = sum(1 for r in used if r.level == 1)
        self.summary.setText(f"{len(used)} von {len(rows)} Zeilen werden übernommen  ·  "
                             f"<span style='color:#b00'>{err} Fehler</span>  ·  "
                             f"<span style='color:#a60'>{warn} Hinweise</span>  ·  "
                             f"{len(rows) - len(used)} ignoriert")

    # ------------------------------------------------------------------ Bearbeiten
    def on_item_changed(self, item):
        if self._filling or self.report is None:
            return
        row, col = item.row(), item.column()
        res: Result = self.table.item(row, C_NAME).data(Qt.UserRole)
        if res is None:
            return
        txt = item.text().strip() if col != C_USE else ""
        if col == C_USE:
            res.use = item.checkState() == Qt.Checked
        elif col == C_CODE:
            old_matched = res.matched
            res.code = txt
            a = self.mapping.get(txt)
            if txt and txt != IGNORE and a is None:
                QMessageBox.information(self, "Neues Kürzel",
                                        f"„{txt}“ steht noch nicht in der Zuordnungstabelle.\n"
                                        "Es wird beim Merken als neue Analyse angelegt. Bitte prüfen, "
                                        "ob es in x.isynet genauso heißt.")
            res.warnings = [w for w in res.warnings if not w.startswith(("Keine Zuordnung", "Bezeichnung unscharf"))]
            if txt == IGNORE:
                res.use = False
            elif txt:
                res.use = True
                if self.settings.learn_aliases and old_matched in ("", "unscharf") and res.name:
                    self.mapping.add_alias(txt, res.name)
                    self.statusBar().showMessage(f"Zuordnung gemerkt: „{res.name}“ → {txt}", 6000)
                res.matched = "manuell"
            if not txt:
                res.warnings.insert(0, "Keine Zuordnung – Kürzel wählen oder Zeile ignorieren")
        elif col == C_VAL:
            res.value = txt
        elif col == C_UNIT:
            res.unit = txt
        elif col == C_FLAG:
            res.flag = txt
        elif col == C_REF:
            res.ref = txt
        elif col == C_DATE:
            res.date = txt
        elif col == C_TIME:
            res.time = txt
        elif col == C_ORDER:
            res.order = txt
        elif col == C_COMM:
            res.comment = item.text()
        if col in (C_VAL, C_REF, C_CODE, C_UNIT):
            res.warnings = [w for w in res.warnings if w.startswith(("Keine Zuordnung", "Bezeichnung unscharf"))]
            check_result(res, self.mapping)
        self._filling = True
        self.table.item(row, C_USE).setCheckState(Qt.Checked if res.use else Qt.Unchecked)
        self.table.item(row, C_FLAG).setText(res.flag)
        self.table.item(row, C_UNIT).setText(res.unit)
        self.table.item(row, C_WARN).setText("; ".join(res.warnings))
        self.table.item(row, C_WARN).setToolTip("\n".join(res.warnings))
        self._color_row(row)
        self._filling = False
        self.update_summary()

    def on_row_selected(self, row, *_):
        if row < 0 or self.table.item(row, C_NAME) is None:
            return
        res = self.table.item(row, C_NAME).data(Qt.UserRole)
        if res and res.bbox != (0, 0, 0, 0):
            self.pdf.show_page(res.page, highlight=(res.page, res.bbox), scroll=True)

    def table_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        res = self.table.item(row, C_NAME).data(Qt.UserRole)
        m = QMenu(self)
        if res.suggestion:
            a = m.addAction(f"Vorschlag übernehmen: {res.suggestion}")
            a.triggered.connect(lambda: self.table.item(row, C_VAL).setText(res.suggestion))
        a2 = m.addAction("Kommentar bearbeiten …")
        a2.triggered.connect(lambda: self.edit_comment(row))
        a3 = m.addAction("Zeile duplizieren")
        a3.triggered.connect(lambda: self.dup_row(row))
        a4 = m.addAction("Zeile löschen")
        a4.triggered.connect(lambda: self.table.selectRow(row) or self.delete_row())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def edit_comment(self, row):
        res = self.table.item(row, C_NAME).data(Qt.UserRole)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Kommentar zu {res.code or res.name}")
        lay = QVBoxLayout(dlg)
        ed = QPlainTextEdit(res.comment)
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(500, 250)
        if dlg.exec():
            res.comment = ed.toPlainText().strip()
            self.table.item(row, C_COMM).setText(res.comment.replace("\n", " "))
            self.table.item(row, C_COMM).setToolTip(res.comment)

    def add_row(self):
        if not self.report:
            return
        r = Result(use=True, date=self.f_sample.text().strip(), order=self.f_order.text().strip(),
                   name="(manuell)", matched="manuell")
        r.warnings = ["Keine Zuordnung – Kürzel wählen oder Zeile ignorieren"]
        self.report.results.append(r)
        self._filling = True
        self._append_row(r)
        self._filling = False
        self.table.scrollToBottom()
        self.table.setCurrentCell(self.table.rowCount() - 1, C_CODE)
        self.update_summary()

    def dup_row(self, row):
        import copy
        res = copy.deepcopy(self.table.item(row, C_NAME).data(Qt.UserRole))
        self.report.results.append(res)
        self._filling = True
        self._append_row(res)
        self._filling = False
        self.update_summary()

    def delete_row(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows or self.table.state() == QAbstractItemView.EditingState:
            return
        for r in rows:
            res = self.table.item(r, C_NAME).data(Qt.UserRole)
            if res in self.report.results:
                self.report.results.remove(res)
            self.table.removeRow(r)
        self.update_summary()

    # ------------------------------------------------------------------ Export
    def _collect(self):
        rep = self.report
        rep.surname = self.f_surname.text().strip()
        rep.firstname = self.f_first.text().strip()
        rep.dob = self.f_dob.text().strip()
        rep.sex = self.f_sex.currentText()
        rep.patnr = self.f_patnr.text().strip()
        rep.sample_date = self.f_sample.text().strip()
        rep.report_date = self.f_report.text().strip() or rep.sample_date
        rep.order = self.f_order.text().strip()
        rep.status = self.f_status.currentText()[0]
        rep.comment = self.comment.toPlainText().strip()
        results = []
        for r in range(self.table.rowCount()):
            res = self.table.item(r, C_NAME).data(Qt.UserRole)
            if res.use:
                if not res.date:
                    res.date = rep.sample_date
                results.append(res)
        return rep, results

    def _validate(self, rep, results) -> bool:
        import re
        problems = []
        if not rep.surname or not rep.firstname:
            problems.append("Name/Vorname fehlt")
        for lab, v in (("Geburtsdatum", rep.dob), ("Abnahmedatum", rep.sample_date)):
            if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", v):
                problems.append(f"{lab} fehlt oder nicht im Format TT.MM.JJJJ")
        if not results:
            problems.append("Keine Zeile zum Übernehmen markiert")
        bad = [r for r in results if r.level == 2]
        for r in bad[:10]:
            problems.append(f"Zeile „{r.name or r.code}“: " + ("kein Kürzel" if not r.code or r.code == IGNORE else "kein Wert"))
        if problems:
            QMessageBox.warning(self, "Export nicht möglich", "Bitte zuerst korrigieren:\n\n• " + "\n• ".join(problems))
            return False
        warn = [r for r in results if r.level == 1]
        if warn:
            txt = "\n".join(f"• {r.code}: {'; '.join(r.warnings)}" for r in warn[:12])
            if QMessageBox.question(self, "Hinweise vorhanden",
                                    f"{len(warn)} Wert(e) haben noch Hinweise:\n\n{txt}\n\n"
                                    "Wurden diese geprüft? Trotzdem exportieren?") != QMessageBox.Yes:
                return False
        return True

    def preview_ldt(self):
        if not self.report:
            return
        rep, results = self._collect()
        data = build_ldt(rep, results, self.settings, self.mapping, Path(self.path).name)
        dlg = QDialog(self)
        dlg.setWindowTitle("LDT-Vorschau")
        lay = QVBoxLayout(dlg)
        ed = QPlainTextEdit(data.decode(self.settings.encoding, errors="replace"))
        ed.setReadOnly(True)
        ed.setFont(QFont("Consolas", 10))
        lay.addWidget(ed)
        errs = check_ldt(data, self.settings.encoding)
        lay.addWidget(QLabel("Satzlängen: OK" if not errs else "Fehler: " + "; ".join(errs)))
        dlg.resize(700, 700)
        dlg.exec()

    def export_ldt(self):
        if not self.report:
            return
        rep, results = self._collect()
        if not self._validate(rep, results):
            return
        data = build_ldt(rep, results, self.settings, self.mapping, Path(self.path).name)
        errs = check_ldt(data, self.settings.encoding)
        if errs:
            QMessageBox.critical(self, "Interner Fehler", "LDT-Datei fehlerhaft:\n" + "\n".join(errs))
            return
        out_dir = Path(self.settings.export_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        now = _dt.datetime.now()
        name = self.settings.file_pattern.format(datum=now.strftime("%Y%m%d"), zeit=now.strftime("%H%M%S"),
                                                 name=rep.surname, vorname=rep.firstname)
        target = out_dir / name
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)  # erst vollständig schreiben, dann umbenennen
        with open(app_dir() / "export_protokoll.csv", "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f, delimiter=";").writerow(
                [now.isoformat(timespec="seconds"), self.path, str(target),
                 f"{rep.surname}, {rep.firstname}", rep.dob, len(results)])
        moved = ""
        if self.settings.move_done and self.settings.done_dir:
            try:
                Path(self.settings.done_dir).mkdir(parents=True, exist_ok=True)
                dest = Path(self.settings.done_dir) / Path(self.path).name
                shutil.move(self.path, dest)
                moved = f"\nPDF verschoben nach: {dest}"
            except Exception as e:  # noqa: BLE001
                moved = f"\nPDF konnte nicht verschoben werden: {e}"
        QMessageBox.information(self, "Exportiert", f"{len(results)} Werte für {rep.surname}, {rep.firstname} "
                                                    f"exportiert:\n{target}{moved}")
        self.refresh_files()

    # ------------------------------------------------------------------ Dialoge
    def edit_mapping(self):
        dlg = MappingDialog(self, self.mapping)
        if dlg.exec():
            self.mapping.load()
            if self.report and QMessageBox.question(self, "Neu einlesen?",
                                                    "Zuordnung gespeichert. Aktuelles PDF neu einlesen?") == QMessageBox.Yes:
                self.reparse()

    def edit_settings(self):
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec():
            self.settings.save()
            self.mapping = Mapping(Path(self.settings.mapping_file))
            self.refresh_files()


# ======================================================================= Zuordnungs-Dialog
class MappingDialog(QDialog):
    HEAD = ["Kürzel (x.isynet)", "Bezeichnung", "Einheit", "Aliase (mit | getrennt, @urin = nur im Urin-Abschnitt)", "Bemerkung"]

    def __init__(self, parent, mapping: Mapping):
        super().__init__(parent)
        self.mapping = mapping
        self.setWindowTitle(f"Zuordnung – {mapping.path}")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Das Kürzel muss exakt dem Analysekürzel in x.isynet entsprechen. "
                             "Kürzel „-“ = Zeile immer ignorieren."))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Suchen …")
        self.filter.textChanged.connect(self.apply_filter)
        lay.addWidget(self.filter)
        self.t = QTableWidget(0, 5)
        self.t.setHorizontalHeaderLabels(self.HEAD)
        for a in mapping.analysen:
            r = self.t.rowCount()
            self.t.insertRow(r)
            for c, v in enumerate([a.code, a.name, a.unit, "|".join(a.aliases), a.note]):
                self.t.setItem(r, c, QTableWidgetItem(v))
        for c, w in enumerate([110, 200, 90, 460, 220]):
            self.t.setColumnWidth(c, w)
        lay.addWidget(self.t)
        bl = QHBoxLayout()
        add = QPushButton("Zeile hinzufügen")
        add.clicked.connect(lambda: (self.t.insertRow(self.t.rowCount()), self.t.scrollToBottom()))
        rem = QPushButton("Zeile löschen")
        rem.clicked.connect(lambda: [self.t.removeRow(r) for r in sorted({i.row() for i in self.t.selectedItems()}, reverse=True)])
        bl.addWidget(add)
        bl.addWidget(rem)
        bl.addStretch()
        lay.addLayout(bl)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(1150, 700)

    def apply_filter(self, txt):
        txt = txt.lower()
        for r in range(self.t.rowCount()):
            row_txt = " ".join((self.t.item(r, c).text() if self.t.item(r, c) else "") for c in range(5)).lower()
            self.t.setRowHidden(r, bool(txt) and txt not in row_txt)

    def save(self):
        from .mapping import Analyse
        items = []
        for r in range(self.t.rowCount()):
            vals = [(self.t.item(r, c).text().strip() if self.t.item(r, c) else "") for c in range(5)]
            if not vals[0]:
                continue
            items.append(Analyse(vals[0], vals[1], vals[2], [a.strip() for a in vals[3].split("|") if a.strip()], vals[4]))
        self.mapping.analysen = items
        self.mapping.save()
        self.accept()


# ======================================================================= Einstellungen
class SettingsDialog(QDialog):
    def __init__(self, parent, s: Settings):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Einstellungen")
        form = QFormLayout(self)

        def path_row(value, folder=True):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            e = QLineEdit(value)
            b = QPushButton("…")
            b.setFixedWidth(30)

            def pick():
                p = (QFileDialog.getExistingDirectory(self, "Ordner", e.text()) if folder
                     else QFileDialog.getOpenFileName(self, "Datei", e.text())[0])
                if p:
                    e.setText(p)
            b.clicked.connect(pick)
            h.addWidget(e)
            h.addWidget(b)
            return w, e

        w, self.e_in = path_row(s.input_dir)
        form.addRow("Eingangsordner (PDFs)", w)
        w, self.e_out = path_row(s.export_dir)
        form.addRow("LDT-Exportordner (Importordner von x.isynet)", w)
        w, self.e_done = path_row(s.done_dir)
        form.addRow("Ordner für erledigte PDFs", w)
        self.c_move = QCheckBox("PDF nach Export dorthin verschieben")
        self.c_move.setChecked(s.move_done)
        form.addRow("", self.c_move)
        w, self.e_map = path_row(s.mapping_file, folder=False)
        form.addRow("Zuordnungstabelle (CSV, auch Netzlaufwerk)", w)
        w, self.e_tess = path_row(s.tesseract_path, folder=False)
        form.addRow("tesseract.exe (für Scans)", w)
        form.addRow(QLabel("<b>LDT</b>"))
        self.cb_satz = QComboBox()
        self.cb_satz.addItems(["8202 – LG-Bericht", "8201 – Labor-Bericht", "8203 – Privat", "8204 – sonstige Kostenträger"])
        self.cb_satz.setCurrentIndex(["8202", "8201", "8203", "8204"].index(s.satzart) if s.satzart in ("8202", "8201", "8203", "8204") else 0)
        form.addRow("Satzart", self.cb_satz)
        self.e_ver = QLineEdit(s.ldt_version)
        form.addRow("Version (9212)", self.e_ver)
        self.cb_enc = QComboBox()
        self.cb_enc.addItems(["iso-8859-15", "cp437"])
        self.cb_enc.setCurrentText(s.encoding)
        form.addRow("Zeichensatz", self.cb_enc)
        self.cb_dec = QComboBox()
        self.cb_dec.addItems([".", ","])
        self.cb_dec.setCurrentText(s.decimal)
        form.addRow("Dezimaltrennzeichen", self.cb_dec)
        self.cb_datef = QComboBox()
        self.cb_datef.addItems(["JJJJMMTT", "TTMMJJJJ"])
        self.cb_datef.setCurrentText(s.date_format)
        form.addRow("Datumsformat", self.cb_datef)
        self.e_sex = QLineEdit(s.sex_codes)
        form.addRow("Geschlecht männl./weibl. (3110)", self.e_sex)
        self.e_lab = QLineEdit(s.lab_name)
        form.addRow("Laborname (8300)", self.e_lab)
        self.e_bsnr = QLineEdit(s.lab_bsnr)
        form.addRow("Labor-BSNR (0201)", self.e_bsnr)
        self.e_cust = QLineEdit(s.customer_no)
        form.addRow("Kundennummer (8312)", self.e_cust)
        self.e_kbv = QLineEdit(s.kbv_pruefnummer)
        form.addRow("KBV-Prüfnummer (0101)", self.e_kbv)
        self.e_pat = QLineEdit(s.file_pattern)
        form.addRow("Dateiname ({datum} {zeit} {name})", self.e_pat)
        self.c_ref = QCheckBox("Referenzbereich mitsenden (8460)")
        self.c_ref.setChecked(s.send_ref)
        form.addRow("", self.c_ref)
        self.c_src = QCheckBox("Hinweis „Aus PDF übernommen“ mitsenden")
        self.c_src.setChecked(s.send_source_note)
        form.addRow("", self.c_src)
        self.c_patnr = QCheckBox("Patientennummer mitsenden (Feld 3000 – x.isynet: nein)")
        self.c_patnr.setChecked(s.send_patnr)
        form.addRow("", self.c_patnr)
        self.c_learn = QCheckBox("Manuelle Zuordnungen automatisch merken")
        self.c_learn.setChecked(s.learn_aliases)
        form.addRow("", self.c_learn)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(720, 0)

    def save(self):
        s = self.s
        s.input_dir, s.export_dir, s.done_dir = self.e_in.text(), self.e_out.text(), self.e_done.text()
        s.move_done = self.c_move.isChecked()
        s.mapping_file, s.tesseract_path = self.e_map.text(), self.e_tess.text()
        s.satzart = self.cb_satz.currentText()[:4]
        s.ldt_version, s.encoding, s.decimal = self.e_ver.text().strip(), self.cb_enc.currentText(), self.cb_dec.currentText()
        s.sex_codes, s.lab_name, s.lab_bsnr = self.e_sex.text().strip() or "M/W", self.e_lab.text(), self.e_bsnr.text()
        s.customer_no, s.kbv_pruefnummer, s.file_pattern = self.e_cust.text(), self.e_kbv.text(), self.e_pat.text() or "PDF2PVS_{datum}_{zeit}.ldt"
        s.date_format, s.send_patnr = self.cb_datef.currentText(), self.c_patnr.isChecked()
        s.send_ref, s.send_source_note, s.learn_aliases = self.c_ref.isChecked(), self.c_src.isChecked(), self.c_learn.isChecked()
        self.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    args = [a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.isfile(a)]
    if args:
        win.load_pdf(args[0])
    sys.exit(app.exec())
