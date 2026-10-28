"""Schreibt geprüfte Laborwerte als LDT-Datei (LDT 2.x, Satzarten 8220 / 82xx / 8221).

Jede Zeile: 3 Stellen Zeilenlänge + 4 Stellen Feldkennung + Inhalt + CR LF.
Die Zeilenlänge zählt Bytes im gewählten Zeichensatz, inklusive CR LF.
"""
from __future__ import annotations

import datetime as _dt
import re
import textwrap
from collections import OrderedDict

from .parser import Report, Result, to_float

CHARSET_CODE = {"iso-8859-15": "3", "iso-8859-1": "3", "cp437": "2"}


DATE_FMT = "JJJJMMTT"   # x.isynet erwartet JJJJMMTT; klassisches LDT 2: TTMMJJJJ
MAX_TEXT = 60           # max. Länge von Textfeldern (8490, 8470, 8480)
MAX_ID = 13             # max. Länge der Anforderungs-Ident (8310)


def _d(date: str) -> str:
    """TT.MM.JJJJ -> JJJJMMTT (bzw. TTMMJJJJ je nach Einstellung)"""
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", (date or "").strip())
    if not m:
        return ""
    dd, mm, yy = m.group(1), m.group(2), m.group(3)
    return f"{dd}{mm}{yy}" if DATE_FMT == "TTMMJJJJ" else f"{yy}{mm}{dd}"


def _today(now) -> str:
    return _d(now.strftime("%d.%m.%Y"))


def _t(time: str) -> str:
    m = re.match(r"^(\d{1,2}):(\d{2})", (time or "").strip())
    return f"{int(m.group(1)):02d}{m.group(2)}" if m else ""


class _Rec:
    def __init__(self, enc: str):
        self.enc = enc
        self.lines: list[tuple[str, str]] = []

    def add(self, fid: str, content):
        content = "" if content is None else str(content)
        content = content.replace("\r", " ").replace("\n", " ").strip()
        if content:
            self.lines.append((fid, content))

    def add_text(self, fid: str, text: str, width: int = MAX_TEXT):
        for para in (text or "").splitlines():
            para = para.strip()
            if not para:
                continue
            for chunk in textwrap.wrap(para, width) or [para]:
                self.add(fid, chunk)

    def encode_line(self, fid: str, content: str) -> bytes:
        body = content.encode(self.enc, errors="replace")
        n = 3 + 4 + len(body) + 2
        return f"{n:03d}{fid}".encode("ascii") + body + b"\r\n"

    def to_bytes(self, satzart: str) -> bytes:
        body = b"".join(self.encode_line(f, c) for f, c in self.lines)
        head = self.encode_line("8000", satzart)
        len_line_size = 14  # "014" + "8100" + 5 Ziffern + CRLF
        total = len(head) + len_line_size + len(body)
        return head + self.encode_line("8100", f"{total:05d}") + body


def _sex(code: str, settings) -> str:
    m, w = (settings.sex_codes.split("/") + ["M", "W"])[:2]
    return {"M": m, "W": w}.get((code or "").upper(), "")


def _value_fields(rec: _Rec, r: Result, settings):
    v = (r.value or "").strip()
    num = to_float(v)
    if num is not None:
        txt = v.replace(",", ".") if settings.decimal == "." else v.replace(".", ",")
        rec.add("8420", txt)
        rec.add("8421", r.unit)
    else:
        rec.add("8421", r.unit)
        rec.add_text("8480", v)
    if r.flag:
        rec.add("8422", r.flag)


def build_ldt(rep: Report, results: list[Result], settings, mapping=None,
              source_name: str = "") -> bytes:
    global DATE_FMT
    DATE_FMT = getattr(settings, "date_format", "JJJJMMTT") or "JJJJMMTT"
    enc = settings.encoding or "iso-8859-15"
    now = _dt.datetime.now()

    # ---------- Kopf 8220
    head = _Rec(enc)
    head.add("9212", settings.ldt_version)
    head.add("0201", settings.lab_bsnr)
    head.add("0203", settings.lab_name)
    head.add("0101", settings.kbv_pruefnummer)
    head.add("8300", settings.lab_name)
    head.add("8312", settings.customer_no)
    head.add("9106", CHARSET_CODE.get(enc.lower(), "3"))
    head.add("9103", _today(now))
    out = [head.to_bytes("8220")]

    # ---------- Berichte je Auftrag / Abnahmezeitpunkt
    groups: "OrderedDict[tuple, list[Result]]" = OrderedDict()
    for r in results:
        key = (r.order or rep.order or "", r.date or rep.sample_date, r.time or "")
        groups.setdefault(key, []).append(r)

    for gi, ((order, date, time), rs) in enumerate(groups.items(), 1):
        rec = _Rec(enc)
        ident = (order or f"P{now:%y%m%d%H%M}{gi:02d}")[-MAX_ID:]
        rec.add("8310", ident)
        if order:
            rec.add("8311", order[-MAX_ID:])
        rec.add("8301", _d(date))
        rec.add("8302", _d(rep.report_date or date))
        rec.add("8303", now.strftime("%H%M"))
        rec.add("8401", rep.status or "E")
        if getattr(settings, "send_patnr", False):
            rec.add("3000", rep.patnr)
        rec.add("3101", rep.surname)
        rec.add("3102", rep.firstname)
        rec.add("3103", _d(rep.dob))
        rec.add("3110", _sex(rep.sex, settings))
        rec.add("8432", _d(date))
        rec.add("8433", _t(time))
        target = next((k for k in rep.order_comments if k and any(g[0] == k for g in groups)), None)
        is_target = (order == target) if target else gi == 1
        comment = rep.comment if is_target else ""
        rec.add_text("8490", comment)
        if settings.send_source_note and source_name:
            rec.add_text("8490", f"Aus PDF übernommen am {now:%d.%m.%Y %H:%M}:")
            rec.add_text("8490", source_name[:MAX_TEXT])

        for r in rs:
            a = mapping.get(r.code) if mapping else None
            rec.add("8410", r.code)
            rec.add("8411", (a.name if a and a.name else r.name)[:60])
            _value_fields(rec, r, settings)
            if settings.send_ref and r.ref:
                rec.add("8460", r.ref)
            rec.add_text("8470", r.comment)
        out.append(rec.to_bytes(settings.satzart))

    # ---------- Abschluss 8221 (Gesamtlänge inkl. Abschlusssatz)
    body_len = sum(len(b) for b in out)
    tail = _Rec(enc)
    tail.add("9202", "0" * 8)
    tail_len = len(tail.to_bytes("8221"))
    tail = _Rec(enc)
    tail.add("9202", f"{body_len + tail_len:08d}")
    out.append(tail.to_bytes("8221"))
    return b"".join(out)


def check_ldt(data: bytes, enc: str = "iso-8859-15") -> list[str]:
    """Prüft Zeilen- und Satzlängen einer LDT-Datei."""
    errors = []
    lines = data.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines.pop()
    rec_start, rec_len, rec_bytes = None, None, 0
    total = len(data)
    for i, ln in enumerate(lines, 1):
        n = int(ln[:3])
        if n != len(ln) + 2:
            errors.append(f"Zeile {i}: Längenangabe {n} ≠ {len(ln) + 2}")
        fid = ln[3:7].decode()
        if fid == "8000":
            if rec_start is not None and rec_len != rec_bytes:
                errors.append(f"Satz ab Zeile {rec_start}: 8100={rec_len}, tatsächlich {rec_bytes}")
            rec_start, rec_bytes = i, 0
        if fid == "8100":
            rec_len = int(ln[7:])
        if fid == "9202" and int(ln[7:]) != total:
            errors.append(f"9202={int(ln[7:])}, Datei hat {total} Bytes")
        rec_bytes += len(ln) + 2
    if rec_start is not None and rec_len != rec_bytes:
        errors.append(f"Satz ab Zeile {rec_start}: 8100={rec_len}, tatsächlich {rec_bytes}")
    return errors
