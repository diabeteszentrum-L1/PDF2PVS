"""Zuordnungstabelle: Laborbezeichnungen aus dem PDF -> x.isynet-Analysekürzel."""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

IGNORE = "-"
_JUNK = "‚„'\"“”«»°|_!;:,"


def split_token(text: str) -> list[str]:
    """Teilt ein Wort für den Namensvergleich (Bindestriche zwischen Buchstaben)."""
    text = text.replace("ı", "i").replace("­", "-")
    parts = re.split(r"(?<=[A-Za-zÄÖÜäöüß0-9.])-(?=[A-Za-zÄÖÜäöüß])", text)
    out = []
    for p in parts:
        p = p.strip(_JUNK).lower().rstrip(".")
        p = p.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
        if p:
            out.append(p)
    return out


def norm_tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in text.replace(" ", " ").split():
        out.extend(split_token(w))
    return out


@dataclass
class Analyse:
    code: str
    name: str
    unit: str = ""
    aliases: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Alias:
    tokens: list[str]
    analyse: Analyse
    section: str = ""  # nur gültig, wenn der Abschnitt dieses Wort enthält


UNIT_GROUPS = [
    {"mu/l", "uu/ml", "miu/l", "mie/l", "uie/ml", "uie", "uiu/ml", "mlu/l"},
    {"10^3/ul", "tsd/ul", "tsnd/ul", "1000/ul", "/nl", "g/l(zellen)", "10e3/ul", "10³/ul"},
    {"10^6/ul", "mill/ul", "mio/ul", "1000000/ul", "/pl", "10e6/ul", "10⁶/ul", "t/l"},
    {"ug/dl"}, {"ug/l", "ng/ml"}, {"ng/l", "pg/ml"},
    {"ml/min", "ml/min/1.73m²", "ml/min/1.73m2", "ml/min/ko", "ml/min/1,73m²"},
    {"s", "sec", "sek"}, {"u/l", "iu/l"}, {"mm", "mm/std", "mm/h", "mm/1h"},
]


def unit_key(u: str) -> str:
    u = (u or "").strip().lower().replace("µ", "u").replace("μ", "u").replace(" ", "")
    u = u.replace(".", "").replace("x", "") if re.match(r"^x?(tsnd|tsd|mill)", u) else u
    u = re.sub(r"^m(?=1000)", "", u)  # x.isynet-Anzeige "m1000/µl"
    u = u.replace("tsnd./", "tsnd/").replace("tsd./", "tsd/").replace("mill./", "mill/")
    u = u.replace("min.", "min").replace("std.", "std")
    return u


def same_unit(a: str, b: str) -> bool:
    ka, kb = unit_key(a), unit_key(b)
    if not ka or not kb or ka == kb:
        return True
    for g in UNIT_GROUPS:
        if ka in g and kb in g:
            return True
    return False


class Mapping:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.analysen: list[Analyse] = []
        self.load()

    # ---------- Laden / Speichern ----------
    def load(self):
        self.analysen = []
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                code = (r.get("Kuerzel") or "").strip()
                if not code:
                    continue
                aliases = [a.strip() for a in (r.get("Aliase") or "").split("|") if a.strip()]
                self.analysen.append(Analyse(code, (r.get("Bezeichnung") or "").strip(),
                                             (r.get("Einheit") or "").strip(), aliases,
                                             (r.get("Bemerkung") or "").strip()))
        self._index()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Kuerzel", "Bezeichnung", "Einheit", "Aliase", "Bemerkung"])
            for a in self.analysen:
                w.writerow([a.code, a.name, a.unit, "|".join(a.aliases), a.note])
        self._index()

    def _index(self):
        self.codes = {a.code: a for a in self.analysen if a.code != IGNORE}
        self.alias_list: list[Alias] = []
        for a in self.analysen:
            for al in a.aliases:
                section = ""
                if "@" in al:
                    al, section = al.split("@", 1)
                toks = norm_tokens(al)
                if toks:
                    self.alias_list.append(Alias(toks, a, section.lower().strip()))
        # längste Aliase zuerst
        self.alias_list.sort(key=lambda x: (-len(x.tokens), -len("".join(x.tokens))))

    def get(self, code: str) -> Analyse | None:
        return self.codes.get(code)

    def add_alias(self, code: str, alias: str):
        a = self.codes.get(code)
        if a is None:
            a = Analyse(code, alias, "", [], "beim Prüfen angelegt")
            self.analysen.append(a)
        if alias and alias.lower() not in [x.lower() for x in a.aliases]:
            a.aliases.append(alias)
        self.save()

    # ---------- Zuordnung ----------
    def match_prefix(self, toks: list[str], section: str, unit_hint: str = ""):
        """Sucht den längsten Alias am Anfang der Tokenliste.
        Liefert (Analyse, Anzahl verbrauchter Tokens) oder (None, 0)."""
        section = (section or "").lower()
        hits = []
        best_len = 0
        for al in self.alias_list:
            n = len(al.tokens)
            if n < best_len:
                break
            if toks[:n] == al.tokens:
                if al.section and al.section not in section and not (
                        al.section == "urin" and "harn" in section):
                    continue
                if not al.section and ("urin" in section or "harnstatus" in section):
                    # im Urin-Abschnitt bevorzugt Urin-Aliase; Serumalias nur, wenn kein Urinalias passt
                    pass
                hits.append(al)
                best_len = n
        if not hits:
            return None, 0
        # Abschnittsspezifische Treffer bevorzugen
        hits.sort(key=lambda a: 0 if a.section else 1)
        if len({h.analyse.code for h in hits}) > 1 and unit_hint:
            for h in hits:
                if h.analyse.unit and unit_key(h.analyse.unit) == unit_key(unit_hint):
                    return h.analyse, best_len
        return hits[0].analyse, best_len

    def match_fuzzy(self, toks: list[str], cutoff=0.84):
        best = (0.0, None, 0)
        for n in (4, 3, 2, 1):
            if len(toks) < n:
                continue
            cand = " ".join(toks[:n])
            if len(cand) < 3:
                continue
            for al in self.alias_list:
                s = " ".join(al.tokens)
                if abs(len(s) - len(cand)) > 4:
                    continue
                r = difflib.SequenceMatcher(None, cand, s).ratio()
                if r > best[0]:
                    best = (r, al.analyse, n)
        if best[0] >= cutoff:
            return best[1], best[2], best[0]
        return None, 0, best[0]

    def pick_by_unit(self, analyse: Analyse, unit: str) -> Analyse:
        """Wenn mehrere Analysen denselben Alias haben (z. B. HbA1c % / mmol/mol),
        die mit passender Einheit wählen."""
        if not unit or not analyse or same_unit(analyse.unit, unit):
            return analyse
        names = {a.lower() for a in analyse.aliases}
        for a in self.analysen:
            if a is not analyse and a.unit and unit_key(a.unit) == unit_key(unit):
                if names & {x.lower() for x in a.aliases}:
                    return a
        return analyse
