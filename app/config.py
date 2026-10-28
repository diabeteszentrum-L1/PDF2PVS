"""Einstellungen (liegen in %APPDATA%\\PDF2PVS bzw. ~/.pdf2pvs)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA")
    d = Path(base) / "PDF2PVS" if base else Path.home() / ".pdf2pvs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource(rel: str) -> Path:
    """Pfad zu mitgelieferten Dateien (auch innerhalb der PyInstaller-EXE)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


@dataclass
class Settings:
    # Ordner
    input_dir: str = ""
    export_dir: str = ""
    done_dir: str = ""
    move_done: bool = False
    mapping_file: str = ""
    tesseract_path: str = ""
    # LDT
    ldt_version: str = "LDT1014.01"
    satzart: str = "8202"            # 8201 Labor, 8202 LG-Bericht, 8203 Privat, 8204 sonstige
    encoding: str = "iso-8859-15"    # iso-8859-15 (9106=3) oder cp437 (9106=2)
    decimal: str = "."
    date_format: str = "JJJJMMTT"    # x.isynet; klassisches LDT 2 = TTMMJJJJ
    send_patnr: bool = False         # Feld 3000 kennt x.isynet im LDT nicht
    sex_codes: str = "M/W"           # Darstellung in Feld 3110
    lab_name: str = "PDF-Import"
    lab_bsnr: str = ""
    customer_no: str = ""
    kbv_pruefnummer: str = ""
    send_ref: bool = True
    send_source_note: bool = True
    file_pattern: str = "PDF2PVS_{datum}_{zeit}.ldt"
    learn_aliases: bool = True

    @classmethod
    def load(cls) -> "Settings":
        p = app_dir() / "einstellungen.json"
        s = cls()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in names:
                        setattr(s, k, v)
            except Exception:  # noqa: BLE001
                pass
        if not s.mapping_file:
            s.mapping_file = str(app_dir() / "analysen.csv")
        if not Path(s.mapping_file).exists():
            src = resource("daten/analysen.csv")
            if src.exists():
                Path(s.mapping_file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, s.mapping_file)
        if not s.export_dir:
            s.export_dir = str(Path.home() / "PDF2PVS_Export")
        return s

    def save(self):
        (app_dir() / "einstellungen.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
