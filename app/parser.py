"""Erkennt Patientendaten und Laborwerte in den extrahierten Zeilen."""
from __future__ import annotations

import datetime as _dt
import re
from collections import Counter
from dataclasses import dataclass, field

from .extract import Extracted, Row, Word
from .mapping import IGNORE, Mapping, norm_tokens, same_unit, split_token

# ---------------------------------------------------------------- Muster
NUM = r"\d+(?:[.,]\d+)?"
RE_DATE = re.compile(r"^(\d{1,2})[.](\d{1,2})[.](\d{4}|\d{2}),?$")
RE_DATE_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4}),?$")
RE_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?,?$")
RE_RANGE = re.compile(rf"^({NUM})\s*-\s*({NUM})$")
RE_CMP = re.compile(rf"^([<>≤≥]=?)\s*({NUM}|1:\d+)$")
RE_VAL = re.compile(rf"^({NUM})(\+{{1,3}}|-{{1,3}}|­|\*|!|\(\+\)|\(-\)|\(\+|\(-)?$")
RE_TITER = re.compile(r"^(1:\d+)(\+|-|­|\*)?$")
RE_ORDER = re.compile(r"^\d{6,14}$")
FLAG_WORDS = {"+": "+", "++": "++", "+++": "++", "-": "-", "--": "--", "---": "--", "(+)": "+",
              "(-)": "-", "(+": "+", "(-": "-", "!": "!", "!!": "!", "h": "+", "l": "-", "↑": "+",
              "↓": "-", "­": "?", "()": "?", "(#)": "?", "(*)": "?", "*": ""}
QUAL_WORDS = {"negativ", "positiv", "grenzwertig", "erl.", "erl", "n.n.", "nn", "folgt", "reaktiv",
              "nachweisbar", "normal", "unauffällig", "neg.", "pos.", "neg", "pos", "fraglich",
              "spuren", "vereinzelt"}
UNIT_WORDS = {"%", "fl", "pg", "mm", "sec", "s", "titer", "coi", "index", "ratio", "u/l", "iu/l",
              "mmol/l", "mg/dl", "g/dl", "g/l", "mg/l", "µg/l", "ng/l", "ng/ml", "pg/ml",
              "µg/dl", "ug/dl", "mu/l", "µu/ml", "uu/ml", "uie", "µie/ml", "pmol/l", "nmol/l",
              "µmol/l", "umol/l", "mmol/mol", "ml/min", "mm/std.", "mm/h", "mosmol/kg", "kU/l",
              "ka"}
HEADER_COL_WORDS = {"probenentnahmedat.", "probenentnahmedatum", "probenentnahme", "abnahmedatum",
                    "abnahme", "entnahme", "entnahmedatum", "normbereich", "bezeichnung",
                    "referenzbereich", "einheit", "eingang", "eingangsdatum", "analyse", "datum"}
STOP_WORDS = ("fall-nr", "auftragsnummer", "probenentnahme", "gedruckt", "seite", "tel.", "tel:",
              "lanr", "bsnr", "kumulativbefund", "beurteilung", "validierender", "geb.", "pat.:",
              "str.", "straße", "fax", "praxis", "befundbericht", "eingang", "version", "build",
              "für:", "id:", "laborblatt", "kommentar", "dr.", "einsender", "arzt", "telefon")
SECTION_IGNORE = {"beurteilung", "kommentar", "bemerkung", "bemerkungen"}


def _parse_date(s: str) -> str:
    s = s.strip().rstrip(",;")
    m = RE_DATE.match(s) or RE_DATE_SLASH.match(s)
    if not m:
        return ""
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(y) == 2:
        yy = int(y)
        y = 2000 + yy if yy <= (_dt.date.today().year % 100) + 1 else 1900 + yy
    y = int(y)
    try:
        return _dt.date(y, mo, d).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def to_float(v: str):
    m = re.match(rf"^\s*({NUM})\s*$", v or "")
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _titer(v: str):
    m = re.match(r"^\s*1:(\d+)\s*$", v or "")
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- Datenklassen
@dataclass
class Result:
    use: bool = True
    code: str = ""
    name: str = ""          # Bezeichnung wie im PDF
    value: str = ""
    unit: str = ""
    flag: str = ""
    ref: str = ""
    date: str = ""
    time: str = ""
    order: str = ""
    comment: str = ""
    warnings: list[str] = field(default_factory=list)
    page: int = 0
    bbox: tuple = (0, 0, 0, 0)
    ocr: bool = False
    matched: str = ""       # code / alias / unscharf / ""
    suggestion: str = ""    # Korrekturvorschlag (z. B. Komma bei OCR)
    pdf_flag: str | None = None  # Kennzeichen wie im PDF gedruckt

    @property
    def level(self) -> int:
        if not self.code or self.code == IGNORE or self.value.strip() == "":
            return 2
        return 1 if self.warnings else 0


@dataclass
class Report:
    source: str = ""
    surname: str = ""
    firstname: str = ""
    dob: str = ""
    sex: str = ""
    patnr: str = ""
    report_date: str = ""
    sample_date: str = ""
    order: str = ""
    lab: str = ""
    status: str = "E"
    comment: str = ""
    order_comments: dict = field(default_factory=dict)
    results: list[Result] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr: bool = False


# ---------------------------------------------------------------- Hilfsfunktionen
def _merge_ocr_dates(words: list[Word]) -> list[Word]:
    out: list[Word] = []
    i = 0
    while i < len(words):
        w = words[i]
        if (i + 2 < len(words) and re.fullmatch(r"\d{1,2}", w.text)
                and re.fullmatch(r"\d{1,2}", words[i + 1].text)
                and re.fullmatch(r"\d{4}", words[i + 2].text.rstrip(","))
                and words[i + 2].x0 - w.x1 < 40):
            t = f"{w.text}.{words[i + 1].text}.{words[i + 2].text.rstrip(',')}"
            if _parse_date(t):
                out.append(Word(t, w.x0, words[i + 2].x1, w.y0, w.y1, w.page))
                i += 3
                continue
        out.append(w)
        i += 1
    return out


def _merge_tokens(words: list[Word], col_hit) -> list[Word]:
    """Vergleichszeichen an Zahl hängen, 'a - b' zu einem Bereich zusammenfassen."""
    out: list[Word] = []
    i = 0
    while i < len(words):
        w = words[i]
        t = w.text
        nxt = words[i + 1] if i + 1 < len(words) else None
        if t in {"<", ">", "<=", ">=", "≤", "≥"} and nxt and (re.match(rf"^{NUM}", nxt.text) or nxt.text.startswith("1:")):
            out.append(Word(t + nxt.text, w.x0, nxt.x1, w.y0, w.y1, w.page))
            i += 2
            continue
        if (re.fullmatch(NUM, t) and nxt and nxt.text == "-" and i + 2 < len(words)
                and re.fullmatch(NUM, words[i + 2].text)
                and not col_hit(w) and not col_hit(words[i + 2])):
            n2 = words[i + 2]
            out.append(Word(f"{t}-{n2.text}", w.x0, n2.x1, w.y0, w.y1, w.page))
            i += 3
            continue
        out.append(w)
        i += 1
    return out


def _classify(t: str):
    tl = t.lower()
    if t in {"*", "**"} or t.startswith("*"):
        return "SKIP", None
    if RE_DATE.match(t) or RE_DATE_SLASH.match(t) or RE_TIME.match(t):
        return "SKIP", None
    if tl in FLAG_WORDS or re.fullmatch(r"\(.\)", t):
        return "FLAG", FLAG_WORDS.get(tl, "?")
    if RE_RANGE.match(t):
        return "RANGE", t
    if RE_CMP.match(t):
        m = RE_CMP.match(t)
        return "CMP", m.group(1) + m.group(2)
    m = RE_VAL.match(t)
    if m:
        return "NUM", (m.group(1), FLAG_WORDS.get(m.group(2) or "", "?") if m.group(2) else "")
    m = RE_TITER.match(t)
    if m:
        return "TITER", (m.group(1), FLAG_WORDS.get(m.group(2) or "", "?") if m.group(2) else "")
    if tl.rstrip(".") in QUAL_WORDS or tl in QUAL_WORDS:
        return "QUAL", t
    if tl in UNIT_WORDS or (("/" in t) and re.search(r"[A-Za-zµ%]", t) and len(t) <= 22):
        return "UNIT", t
    return "TEXT", t


def _is_section(row: Row) -> bool:
    letters = [c for c in row.text if c.isalpha()]
    if len(letters) < 3:
        return False
    if any(re.search(r"\d", w.text) for w in row.words if not w.text.startswith("(")):
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) > 0.7


def _row_has_stopword(text: str) -> bool:
    tl = text.lower()
    return any(s in tl for s in STOP_WORDS)


# ---------------------------------------------------------------- Kopfdaten
RE_PAT = re.compile(
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'\-]+(?: [A-ZÄÖÜ][A-Za-zÄÖÜäöüß'\-]+)?),\s*"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?: [A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)?),?\s*"
    r"(?:\((\d+)\),?\s*)?(?:geb\.?\s*|Geb\.\s*Dat\.?:?\s*|\*\s*)?"
    r"(\d{1,2}\.\d{1,2}\.\d{4})")


def _header(rep: Report, text: str):
    t = text.replace(" ", " ")
    t = re.sub(r"\b(\d{2}) (\d{2}) (\d{4})\b", r"\1.\2.\3", t)
    m = RE_PAT.search(t)
    if m:
        rep.surname, rep.firstname = m.group(1), m.group(2)
        rep.dob = _parse_date(m.group(4))
        if m.group(3):
            rep.patnr = m.group(3)
        tail = t[m.end():m.end() + 30]
        sm = re.match(r"\s*[,;]?\s*([WMwm])\b", tail)
        if sm:
            rep.sex = sm.group(1).upper()
        if not rep.patnr:
            near = t[max(0, m.start() - 15):m.end() + 15]
            pm = re.search(r"[\(\[](\d{2,9})[\)\]]", near)
            if pm:
                rep.patnr = pm.group(1)
    for kw in (r"Geschlecht:?\s*([WMwm])\b",):
        sm = re.search(kw, t)
        if sm and not rep.sex:
            rep.sex = sm.group(1).upper()
    dm = re.search(r"(?:vom|Befunddatum:?|Berichtsdatum:?)\s*(\d{1,2}\.\d{1,2}\.\d{4})", t)
    if dm:
        rep.report_date = _parse_date(dm.group(1))
    om = re.search(r"(?:\bID:|Auftrags?-?(?:nummer|nr\.?):?)\s*(\d{5,14})", t, re.I)
    if om:
        rep.order = om.group(1)
    if "teilbefund" in t.lower():
        rep.status = "T"
    elif "vorläufig" in t.lower() or "vorbefund" in t.lower():
        rep.status = "V"


# ---------------------------------------------------------------- Hauptfunktion
def parse(ex: Extracted, mapping: Mapping, source: str = "") -> Report:
    rep = Report(source=source, ocr=bool(ex.ocr_pages))
    rep.warnings.extend(ex.warnings)
    if ex.ocr_pages:
        rep.warnings.append("Gescannte Seite(n) per Texterkennung gelesen – bitte alle Werte "
                            "mit dem PDF vergleichen.")
    all_text = ex.raw_text + "\n" + "\n".join(r.text for p in ex.pages[:1] for r in p)
    _header(rep, all_text)
    dob = rep.dob

    columns: list[dict] = []
    section = ""
    comment_mode = False
    comment_blocks: list[dict] = []
    order_numbers: set[str] = set()

    for pno, rows in enumerate(ex.pages):
        page_w = ex.page_sizes[pno][0] if pno < len(ex.page_sizes) else 595
        table_started = False
        page_cols: list[dict] = []
        pending_orders: list[tuple[float, str]] = []
        pending_times: list[tuple[float, str]] = []

        for row in rows:
            words = _merge_ocr_dates(row.words) if row.ocr else list(row.words)
            text = " ".join(w.text for w in words)
            tl = text.lower()

            # ---------- Kommentarteil ----------
            if comment_mode:
                if re.match(r"^(gedruckt am|kumulativbefund|fall-nr|auftragsnummer|probenentnahme)", tl):
                    continue
                if rep.surname and rep.surname.lower() in tl and rep.firstname.lower() in tl and len(words) < 5:
                    continue
                first = words[0].text if words else ""
                dates = [w for w in words if _parse_date(w.text)]
                if RE_ORDER.match(first) and dates and len(words) >= 3:
                    title = " ".join(w.text for w in words[1:] if w.x0 < dates[0].x0)
                    comment_blocks.append({"order": first, "title": title.strip(), "lines": []})
                    continue
                if not comment_blocks:
                    comment_blocks.append({"order": "", "title": "", "lines": []})
                comment_blocks[-1]["lines"].append(text)
                continue
            if re.match(r"^(bemerkungen|kommentare|befundkommentar|befundtext)\b", tl):
                comment_mode = True
                continue

            # ---------- Kopf der Wertetabelle (Datum-/Auftragsspalten) ----------
            date_words = [w for w in words if _parse_date(w.text) and w.xc > page_w * 0.3]
            other = [w for w in words if w not in date_words]
            if date_words and dob and any(_parse_date(w.text) == dob for w in words):
                date_words = []
            if date_words and not _row_has_stopword(text.replace("Probenentnahme", "")) and (
                    len(date_words) >= 2 or all(w.text.lower().rstrip(":") in HEADER_COL_WORDS
                                                or w.text.lower() in HEADER_COL_WORDS for w in other)):
                page_cols = [{"x": w.xc, "date": _parse_date(w.text), "order": "", "time": ""}
                             for w in date_words]
                table_started = True
                continue
            if "auftrag" in tl and not comment_mode:
                pending_orders = [(w.xc, w.text) for w in words if RE_ORDER.match(w.text) and w.xc > page_w * 0.3]
                if pending_orders:
                    table_started = True
                    continue
            if ("zeit" in tl or "uhrzeit" in tl) and any(RE_TIME.match(w.text) for w in words):
                pending_times = [(w.xc, w.text) for w in words if RE_TIME.match(w.text)]
                continue

            cols = page_cols or columns
            if page_cols:
                for c in page_cols:
                    for x, o in pending_orders:
                        if abs(x - c["x"]) < 25:
                            c["order"] = o
                            order_numbers.add(o)
                    for x, t in pending_times:
                        if abs(x - c["x"]) < 25:
                            c["time"] = t
                columns = page_cols
            if len(cols) > 1:
                spacing = min(abs(a["x"] - b["x"]) for a, b in zip(cols, cols[1:]))
                tol = min(30, spacing / 2)
            else:
                tol = 32

            def col_of(w: Word):
                if not cols:
                    return None
                best = min(cols, key=lambda c: abs(c["x"] - w.xc))
                # rechtsbündige Zahlen: auch rechte Kante prüfen
                d = min(abs(best["x"] - w.xc), abs(best["x"] - w.x1) + 6)
                return best if d <= tol else None

            # ---------- Abschnittsüberschriften ----------
            if _is_section(row) and not mapping.match_prefix(norm_tokens(text), section)[0]:
                low = text.lower()
                if not any(s in low for s in SECTION_IGNORE):
                    section = text
                continue

            # ---------- Analyt erkennen ----------
            toks: list[tuple[str, int]] = []  # (normalisiertes Token, Wortindex)
            for i, w in enumerate(words):
                for st in split_token(w.text):
                    toks.append((st, i))
            if not toks:
                continue
            norm = [t for t, _ in toks]

            analyse, matched, used = None, "", 0
            name_word_start = 0
            first = words[0].text
            if first in mapping.codes and first != IGNORE:
                analyse, matched, used = mapping.codes[first], "Kürzel", 1
                name_word_start = 1 if len(words) > 1 else 0
                # folgende Bezeichnung (x.isynet-Ausdruck) mitverbrauchen
                rest_from = next((k for k, (_, i) in enumerate(toks) if i >= 1), len(toks))
                a2, n2 = mapping.match_prefix(norm[rest_from:], section)
                consumed_tok = rest_from + (n2 if a2 else 0)
            else:
                consumed_tok = 0
                skip_options = [0]
                if len(toks) > 1:
                    skip_options.append(next((k for k, (_, i) in enumerate(toks) if i >= 1), 1))
                for skip in skip_options:
                    a, n = mapping.match_prefix(norm[skip:], section)
                    if a:
                        analyse, matched, consumed_tok = a, "Alias", skip + n
                        name_word_start = toks[skip][1]
                        break
                if analyse is None and (row.ocr or True):
                    for skip in skip_options:
                        a, n, score = mapping.match_fuzzy(norm[skip:], 0.85 if row.ocr else 0.9)
                        if a:
                            analyse, matched, consumed_tok = a, "unscharf", skip + n
                            name_word_start = toks[skip][1]
                            break
                if analyse is not None:
                    # x.isynet-Ausdruck: Kürzel + Bezeichnung -> Bezeichnung mitverbrauchen
                    a2, n2 = mapping.match_prefix(norm[consumed_tok:], section)
                    if a2 is not None and a2.code == analyse.code:
                        consumed_tok += n2
            if consumed_tok >= len(toks):
                name_word_end = len(words)
            else:
                name_word_end = toks[consumed_tok][1]
                # Wort nur teilweise verbraucht -> ganz zum Namen rechnen
                if consumed_tok > 0 and toks[consumed_tok - 1][1] == name_word_end:
                    name_word_end += 1

            if analyse is not None and not table_started:
                table_started = True
            if not table_started:
                continue  # Briefkopf
            if analyse is None and (_row_has_stopword(text) or
                                    (rep.surname and rep.surname.lower() in tl)):
                continue

            # ---------- Rest der Zeile auswerten ----------
            rest = _merge_tokens(words[name_word_end:], lambda w: col_of(w) is not None)
            if analyse is None:
                # Namen bis zum ersten Daten-Token
                k = 0
                while k < len(rest) and _classify(rest[k].text)[0] == "TEXT":
                    k += 1
                name_words = words[:name_word_end] + rest[:k]
                rest = rest[k:]
            else:
                name_words = words[name_word_start:name_word_end] or words[:name_word_end]
            name = " ".join(w.text for w in name_words).strip(" :")

            items = []
            for w in rest:
                kind, val = _classify(w.text)
                if kind != "SKIP":
                    items.append([kind, val, w])
            if analyse is None:
                # Einheit evtl. noch im Namen (z. B. "Calcium mmol/l")
                pass

            value_items = [it for it in items if it[0] in ("NUM", "TITER", "QUAL", "CMP")]
            results_for_row: list[tuple[list, dict | None]] = []
            if cols:
                by_col: dict[int, list] = {}
                for it in value_items:
                    c = col_of(it[2])
                    if c is not None:
                        by_col.setdefault(id(c), []).append((it, c))
                for lst in by_col.values():
                    it, c = min(lst, key=lambda p: abs(p[1]["x"] - p[0][2].xc))
                    results_for_row.append((it, c))
            else:
                plain = [it for it in value_items if it[0] != "CMP"]
                if plain:
                    if len([p for p in plain if p[0] != "QUAL"]) == 1:
                        it = next(p for p in plain if p[0] != "QUAL")
                    else:
                        it = plain[-1] if plain[-1][0] != "QUAL" or len(plain) > 1 else plain[0]
                    results_for_row.append((it, None))
                elif value_items:
                    results_for_row.append((value_items[-1], None))

            if not results_for_row:
                if "siehe" in tl or "folgt" in tl:
                    continue
                if analyse is not None and analyse.code != IGNORE and cols and items:
                    # erkannte Analyse ohne Wert in der Ergebnisspalte
                    results_for_row.append((None, cols[0] if len(cols) == 1 else None))
                else:
                    continue
            if analyse is None:
                if not re.search(r"[A-Za-zÄÖÜäöü]{2,}", name):
                    continue
                if not cols and not any(it[0] in ("UNIT", "RANGE") for it in items):
                    continue

            unit = next((it[1] for it in items if it[0] == "UNIT"), "")
            if not unit:
                for w in name_words[1:]:
                    if _classify(w.text)[0] == "UNIT":
                        unit = w.text
                        name = name.replace(w.text, "").strip()
                        break
            if unit.lower() == "ka":
                unit = ""

            for it, col in results_for_row:
                r = Result(name=name, page=pno, ocr=row.ocr)
                r.bbox = (row.x0, row.y0, row.x1, row.y1)
                if it is None:
                    r.value = ""
                    r.warnings.append("Kein Wert in der Ergebnisspalte gefunden")
                else:
                    kind, val, w = it
                    if kind in ("NUM", "TITER"):
                        r.value, r.flag = val
                    else:
                        r.value = val
                    # alleinstehendes Kennzeichen direkt hinter dem Wert
                    idx = items.index(it)
                    for nx in items[idx + 1: idx + 3]:
                        if nx[0] == "FLAG" and (col is None or col_of(nx[2]) is not None
                                                or nx[2].x0 - w.x1 < 30):
                            if nx[1]:
                                r.flag = nx[1]
                            break
                        if nx[0] not in ("UNIT",):
                            break
                # Referenz = Bereichs-/Vergleichs-Token, das nicht der Wert ist
                for other_it in items:
                    if other_it is it:
                        continue
                    if other_it[0] in ("RANGE", "CMP"):
                        r.ref = other_it[1]
                        break
                    if other_it[0] == "QUAL" and it is not None and it[0] == "QUAL" and not r.ref:
                        r.ref = other_it[1]
                r.unit = unit
                if col:
                    r.date, r.order, r.time = col["date"], col["order"], col["time"]
                if analyse is not None:
                    a = mapping.pick_by_unit(analyse, unit)
                    r.code = a.code
                    r.matched = matched
                    if a.code == IGNORE:
                        r.use = False
                    if row.ocr and a.unit and r.unit and r.unit.lower() not in UNIT_WORDS:
                        r.unit = a.unit
                    if matched == "unscharf" and a.code != IGNORE:
                        r.warnings.append(f"Bezeichnung unscharf zugeordnet → {a.code} ({a.name})")
                else:
                    r.use = False
                    r.warnings.append("Keine Zuordnung – Kürzel wählen oder Zeile ignorieren")
                rep.results.append(r)

    # ---------- Kommentare zuordnen ----------
    for b in comment_blocks:
        text = "\n".join(b["lines"]).strip()
        if not text:
            continue
        target = None
        if b["title"] and not b["title"].lower().startswith(("beurteilung", "validierender")):
            a, n = mapping.match_prefix(norm_tokens(b["title"]), "")
            if a and a.code != IGNORE:
                target = next((r for r in rep.results if r.code == a.code and
                               (not b["order"] or r.order in ("", b["order"]))), None)
        if target is not None:
            target.comment = (target.comment + "\n" + text).strip()
        else:
            key = b["order"] or ""
            head = (b["title"].rstrip(":") + ": ") if b["title"] and not b["title"].lower().startswith("beurteilung") else ""
            rep.order_comments[key] = (rep.order_comments.get(key, "") + "\n" + head + text).strip()
    rep.comment = "\n\n".join(v for v in rep.order_comments.values() if v)

    # ---------- Datum/Auftrag vervollständigen ----------
    dates = Counter(r.date for r in rep.results if r.date)
    rep.sample_date = dates.most_common(1)[0][0] if dates else rep.report_date
    if not rep.report_date:
        rep.report_date = rep.sample_date
    orders = Counter(r.order for r in rep.results if r.order)
    if orders and not rep.order:
        rep.order = orders.most_common(1)[0][0]
    for r in rep.results:
        if not r.date:
            r.date = rep.sample_date

    for r in rep.results:
        check_result(r, mapping)
    if not rep.surname:
        rep.warnings.append("Patientenname nicht erkannt – bitte eintragen.")
    if not rep.dob:
        rep.warnings.append("Geburtsdatum nicht erkannt – bitte eintragen.")
    if not rep.sample_date:
        rep.warnings.append("Abnahmedatum nicht erkannt – bitte eintragen.")
    return rep


# ---------------------------------------------------------------- Prüfungen
def _ref_bounds(ref: str):
    if not ref:
        return None, None, False
    m = RE_RANGE.match(ref.replace(" ", ""))
    if m:
        return float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", ".")), False
    m = RE_CMP.match(ref.replace(" ", ""))
    if m:
        op, v = m.group(1), m.group(2)
        num = _titer(v) if v.startswith("1:") else float(v.replace(",", "."))
        if op.startswith("<") or op == "≤":
            return None, num, v.startswith("1:")
        return num, None, v.startswith("1:")
    return None, None, False


def computed_flag(value: str, ref: str):
    lo, hi, titer = _ref_bounds(ref)
    if lo is None and hi is None:
        return None
    v = _titer(value) if titer else to_float(value)
    if v is None:
        return None
    if titer and hi is not None:
        return "+" if v >= hi else ""
    if lo is not None and v < lo:
        return "-"
    if hi is not None and v > hi:
        return "+"
    return ""


def check_result(r: Result, mapping: Mapping):
    """Setzt das endgültige Kennzeichen und sammelt Warnungen (ohne Zuordnungswarnungen zu löschen)."""
    keep = [w for w in r.warnings if w.startswith(("Keine Zuordnung", "Bezeichnung unscharf",
                                                    "Kein Wert"))]
    r.warnings = keep
    r.suggestion = ""
    a = mapping.get(r.code)
    if a and not r.unit and a.unit:
        r.unit = a.unit
    if r.pdf_flag is None:
        r.pdf_flag = r.flag
    printed = r.pdf_flag
    comp = computed_flag(r.value, r.ref)
    if comp is None:
        r.flag = "!" if printed == "?" else printed
    else:
        if printed in ("++", "--") and printed[0] == comp:
            r.flag = printed
        else:
            r.flag = comp
        p_abn = printed not in ("", None)
        if p_abn != (comp != ""):
            if p_abn:
                r.warnings.append(f"Im PDF als auffällig markiert, laut Referenz {r.ref} unauffällig")
            else:
                r.warnings.append(f"Laut Referenz {r.ref} auffällig, im PDF nicht markiert")
    v = to_float(r.value)
    lo, hi, titer = _ref_bounds(r.ref)
    if v is not None and not titer:
        if hi and v > hi * 6:
            sug = v / 10
            hint = ""
            if r.ocr:
                r.suggestion = str(round(sug, 2)).replace(".", ",")
                hint = f" (OCR: evtl. {r.suggestion}?)"
            r.warnings.append(f"Wert sehr weit über Referenz – Komma prüfen{hint}")
        elif lo and lo > 0 and v < lo / 6:
            r.warnings.append("Wert sehr weit unter Referenz – Komma prüfen")
    if r.value and v is None and _titer(r.value) is None and not RE_CMP.match(r.value.replace(" ", "")) \
            and r.value.lower().rstrip(".") not in QUAL_WORDS:
        r.warnings.append("Wert ist weder Zahl noch bekannter Text")
    if a and a.unit and r.unit and not same_unit(a.unit, r.unit):
        r.warnings.append(f"Einheit {r.unit} weicht von {a.unit} (Zuordnung) ab")
    if r.ocr and r.value and not r.warnings and r.matched == "unscharf":
        r.warnings.append("OCR – bitte prüfen")
