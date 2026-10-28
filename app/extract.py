"""Text mit Positionen aus PDFs holen – direkt aus der Textebene oder per OCR."""
from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass, field
from statistics import median

import pymupdf

OCR_DPI = 400
_OCR_JUNK = "‚„'\"“”«»°|_!;~`´"


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    page: int

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2


@dataclass
class Row:
    words: list[Word]
    page: int
    ocr: bool = False

    @property
    def text(self):
        return " ".join(w.text for w in self.words)

    @property
    def y0(self):
        return min(w.y0 for w in self.words)

    @property
    def y1(self):
        return max(w.y1 for w in self.words)

    @property
    def x0(self):
        return min(w.x0 for w in self.words)

    @property
    def x1(self):
        return max(w.x1 for w in self.words)


@dataclass
class Extracted:
    pages: list[list[Row]] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_sizes: list[tuple[float, float]] = field(default_factory=list)
    raw_text: str = ""   # unbereinigter OCR-Text (für Patientendaten im Briefkopf)


def _norm_word(t: str) -> list[str]:
    t = t.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2212", "-")
    # weiches Trennzeichen zwischen Ziffern = Bereichsstrich
    out = []
    for part in t.split():
        p = ""
        for i, ch in enumerate(part):
            if ch == "\u00ad":
                nxt = part[i + 1] if i + 1 < len(part) else ""
                p += "-" if (nxt and (nxt.isdigit() or nxt == ",")) else "\u00ad"
            else:
                p += ch
        out.append(p)
    return out


def _group_rows(words: list[Word], page: int) -> list[Row]:
    if not words:
        return []
    h = median([w.y1 - w.y0 for w in words]) or 8
    words = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    rows: list[list[Word]] = []
    ycs: list[float] = []
    for w in words:
        yc = (w.y0 + w.y1) / 2
        if rows and abs(ycs[-1] - yc) < h * 0.5:
            rows[-1].append(w)
        else:
            rows.append([w])
            ycs.append(yc)
    return [Row(sorted(r, key=lambda w: w.x0), page) for r in rows]


def find_tesseract(cfg_path: str = "") -> str | None:
    cands = [cfg_path] if cfg_path else []
    cands += [
        shutil.which("tesseract") or "",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _clean_scan(gray):
    """Entfernt Punktlinien, Unterstriche und Tabellenlinien (benötigt OpenCV)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    s = OCR_DPI / 400
    # durchgezogene Tabellenlinien nur pixelweise abziehen (Ziffern, die sie berühren, bleiben)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (int(60 * s), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(60 * s)))
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk) | cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    bw = cv2.subtract(bw, lines)
    n, lab, st, cent = cv2.connectedComponentsWithStats(bw, 8)
    remove = np.zeros(n, dtype=bool)
    small = []
    for i in range(1, n):
        x, y, w, h, ar = st[i]
        if h <= 14 * s and w <= 40 * s and ar < 160 * s * s:  # Punkt / kurzer Strich
            small.append(i)
        elif h <= 6 * s and w > 40 * s:
            remove[i] = True
    # Kleine Teile (Punktlinien) entfernen – außer Komma/Dezimalpunkt zwischen zwei Ziffern.
    big = [i for i in range(1, n) if st[i][3] > 18 * s and st[i][3] < 120 * s and st[i][2] < 80 * s]
    rows_big: dict[int, list[int]] = {}
    for j in big:
        rows_big.setdefault(int((st[j][1] + st[j][3]) // (20 * s)), []).append(j)
    for i in small:
        x, y, w, h, ar = st[i]
        if h <= 5 * s and w >= 2.5 * h:      # flacher Strich
            remove[i] = True
            continue
        key = int((y + h) // (20 * s))
        cand = rows_big.get(key, []) + rows_big.get(key - 1, []) + rows_big.get(key + 1, [])
        left = right = False
        for j in cand:
            jx, jy, jw, jh, _ = st[j]
            bottom = jy + jh
            if not (y - 6 * s <= bottom <= y + h + 6 * s):
                continue
            if x - 14 * s <= jx + jw <= x + 2 * s:
                left = True
            if x + w - 2 * s <= jx <= x + w + 14 * s:
                right = True
        if not (left and right):
            remove[i] = True
    bw[remove[lab]] = 0
    return 255 - bw


def _ocr_page(page, tess_cmd: str, page_no: int, want_raw: bool = False):
    import numpy as np
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = tess_cmd
    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    cleaned = _clean_scan(gray)
    img = Image.fromarray(cleaned if cleaned is not None else gray)
    try:
        langs = pytesseract.get_languages(config="")
    except Exception:
        langs = []
    lang = "deu" if "deu" in langs else "eng"
    d = pytesseract.image_to_data(img, lang=lang, config="--psm 6",
                                  output_type=pytesseract.Output.DICT)
    sc = 72 / OCR_DPI
    lines: dict[tuple, list[Word]] = {}
    for i, t in enumerate(d["text"]):
        t = (t or "").strip().replace("ı", "i").strip(_OCR_JUNK)
        if not t or t in {".", "-", "—", "–", ":", ","}:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        lines.setdefault(key, []).append(Word(t, x * sc, (x + w) * sc, y * sc, (y + h) * sc, page_no))
    rows = [Row(sorted(ws, key=lambda w: w.x0), page_no, ocr=True) for ws in lines.values()]
    rows.sort(key=lambda r: r.y0)
    raw = ""
    if want_raw:
        top = Image.fromarray(gray[: int(gray.shape[0] * 0.3)])
        raw = pytesseract.image_to_string(top, lang=lang, config="--psm 6")
    return rows, raw


def extract(path: str, tesseract_path: str = "") -> Extracted:
    ex = Extracted()
    doc = pymupdf.open(path)
    tess = None
    for pno, page in enumerate(doc):
        ex.page_sizes.append((page.rect.width, page.rect.height))
        raw = page.get_text("words")
        words = []
        for x0, y0, x1, y1, t, *_ in raw:
            for part in _norm_word(t):
                words.append(Word(part, x0, x1, y0, y1, pno))
        if len(words) >= 8:
            ex.pages.append(_group_rows(words, pno))
            continue
        # keine Textebene -> OCR
        if tess is None:
            tess = find_tesseract(tesseract_path) or ""
        if not tess:
            ex.warnings.append(f"Seite {pno + 1} ist ein Scan, aber Tesseract-OCR ist nicht installiert.")
            ex.pages.append([])
            continue
        try:
            rows, raw = _ocr_page(page, tess, pno, want_raw=(pno == 0))
            ex.pages.append(rows)
            ex.raw_text += raw
            ex.ocr_pages.append(pno)
        except Exception as e:  # noqa: BLE001
            ex.warnings.append(f"OCR auf Seite {pno + 1} fehlgeschlagen: {e}")
            ex.pages.append([])
    doc.close()
    return ex


def render_page(path: str, page_no: int, zoom: float = 1.6) -> bytes:
    doc = pymupdf.open(path)
    page = doc[page_no]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    data = pix.tobytes("png")
    doc.close()
    return data
