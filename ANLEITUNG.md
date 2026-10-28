# PDF2PVS – Labor-PDFs prüfen und nach x.isynet übernehmen

PDF2PVS liest Laborbefunde aus PDF-Dateien. Das können Text-PDFs oder gescannte Befunde sein. Die Werte erscheinen als bearbeitbare Tabelle neben dem Original-PDF. Nach der Prüfung schreibt das Programm eine **LDT-Datei**, die x.isynet wie einen normalen Laborbefund einliest.

Das Programm arbeitet vollständig offline. Es werden keine Patientendaten ins Internet übertragen.

---

## 1. Installation

### Variante A – fertige EXE
Kopiere `PDF2PVS.exe` auf den Rechner, zum Beispiel nach `C:\Programme\PDF2PVS\`, und starte sie. Eine Installation ist nicht nötig.

### Variante B – EXE selbst bauen (einmalig)
1. Installiere Python 3.10 oder neuer von https://www.python.org. Setze dabei den Haken bei „Add Python to PATH“.
2. Doppelklicke `build.bat` in diesem Ordner. Das dauert einige Minuten.
3. Danach liegt das Programm unter `dist\PDF2PVS.exe`.

Zum Ausprobieren ohne EXE reicht ein Doppelklick auf `starten.bat`.

### Texterkennung für gescannte PDFs (optional, empfohlen)
Gescannte Befunde wie das ALBIS-Laborblatt enthalten keinen Text, nur ein Bild. Dafür braucht PDF2PVS **Tesseract-OCR**:
1. Lade den Installer von https://github.com/UB-Mannheim/tesseract/wiki herunter.
2. Wähle bei der Installation unter „Additional language data“ die Sprache **German** aus.
3. Wenn Tesseract am Standardort installiert wurde (`C:\Program Files\Tesseract-OCR`), findet PDF2PVS es automatisch. Sonst trägst du den Pfad unter *Einstellungen → tesseract.exe* ein.

---

## 2. Einrichtung (einmalig)

**Einstellungen** öffnen und Folgendes festlegen:

| Einstellung | Empfehlung |
|---|---|
| Eingangsordner | Ordner, in dem die Labor-PDFs ankommen (z. B. Scan- oder Fax-Ordner) |
| LDT-Exportordner | **Das Verzeichnis, aus dem x.isynet Labordaten einliest** |
| Ordner für erledigte PDFs | optional, z. B. `…\Labor-PDF\erledigt` |
| Zuordnungstabelle | Standard. Bei mehreren Arbeitsplätzen eine gemeinsame CSV auf dem Netzlaufwerk |

**In x.isynet** muss der LDT-Exportordner von PDF2PVS als Importverzeichnis für Labordaten eingetragen sein. Wo das genau eingestellt wird, hängt von der Version und Konfiguration ab. Im Zweifel kann der CGM-Betreuer das in wenigen Minuten einrichten. Sag ihm, dass eine **LDT-2-Datei (Satzart 8202, LG-Bericht)** aus einem Ordner eingelesen werden soll.

### Erster Test – unbedingt mit Testpatient
1. Lege in x.isynet einen Testpatienten an.
2. Erstelle ein beliebiges Labor-PDF für diesen Testpatienten oder ändere Name und Geburtsdatum in PDF2PVS auf den Testpatienten.
3. Exportiere und lies die Datei in x.isynet ein.
4. Prüfe in der Laborübersicht:
   - Steht jeder Wert in der richtigen Zeile, also beim richtigen Kürzel?
   - Stimmen Dezimalstellen, Umlaute und Einheiten?

Falls etwas nicht passt, helfen diese Einstellungen weiter:

| Problem | Einstellung |
|---|---|
| „Falsches Datumsformat“ | Datumsformat `JJJJMMTT` (Standard für x.isynet) ↔ `TTMMJJJJ` |
| „Unbekannte Feldkennung 3000“ | Haken „Patientennummer mitsenden“ entfernen (Standard: aus) |
| Werte um Faktor 10/100 falsch oder fehlen | Dezimaltrennzeichen `.` ↔ `,` |
| Umlaute kaputt (Ã¤ o. ä.) | Zeichensatz `iso-8859-15` ↔ `cp437` |
| Datei wird gar nicht angenommen | Satzart 8202 ↔ 8201, Version `LDT1014.01`; ggf. Labor-BSNR / Kundennummer eintragen |
| Werte landen in neuer Zeile statt bei vorhandener Analyse | Kürzel in der Zuordnung exakt wie in x.isynet schreiben |

---

## 3. Täglicher Ablauf

1. **PDF öffnen.** Du kannst einen Doppelklick in der Liste „Eingangsordner“ machen, das PDF ins Fenster ziehen oder `Strg+O` drücken.
2. **Kopfdaten prüfen:** Name, Vorname, Geburtsdatum, Abnahmedatum. Die Pat.-Nr. ist optional. Wenn x.isynet die Patienten-Nr. kennt, erleichtert sie die Zuordnung.
3. **Tabelle prüfen.** Ein Klick auf eine Zeile markiert die Stelle im PDF rechts.
   - **Rot:** Es fehlt ein Kürzel oder ein Wert. Ein Export ist erst möglich, wenn das behoben ist.
   - **Gelb:** Es gibt einen Hinweis. Beispiele: Die Markierung im PDF passt nicht zum Referenzbereich, die Einheit weicht ab, ein Wert ist unplausibel oder eine Bezeichnung wurde nur unscharf erkannt.
   - **Grau:** Die Zeile wird nicht übernommen, weil der Haken fehlt oder das Kürzel „-“ ist.
4. **Korrigieren.** Doppelklick auf die Zelle.
   - Das Kürzel wählst du aus der Liste. Die Suche funktioniert auch mit dem Namen.
   - Rechtsklick → „Vorschlag übernehmen“, wenn die Texterkennung ein Komma verloren hat.
   - Rechtsklick → „Kommentar bearbeiten“ für einen Hinweis zu einem einzelnen Wert.
5. **LDT exportieren** (`Strg+S`). Die Datei landet im Exportordner. x.isynet liest sie von dort ein.

Optional zeigt die **LDT-Vorschau** die erzeugte Datei vor dem Export an.

### Was das Programm automatisch erkennt
- **Mehrere Abnahmezeitpunkte oder Aufträge in einem Kumulativbefund.** Jeder Wert bekommt Datum, Uhrzeit und Auftragsnummer seiner Spalte. Pro Auftrag entsteht ein eigener Bericht.
- **Laborkommentare.** Sie werden dem passenden Wert zugeordnet (z. B. das ANA-Muster zu ANA) oder als Auftragshinweis mitgeschickt.
- **Markierungen (+ / −).** Sie werden gegen den Referenzbereich nachgerechnet. Widersprüche werden gelb angezeigt.
- **Urin-Abschnitte.** Leukozyten, Glucose und andere Werte gehen dort auf die Urin-Kürzel (LEUU, GLUU …), wenn das Labor eine Abschnittsüberschrift wie „URINSTATUS“ druckt.

---

## 4. Zuordnungstabelle (Laborbezeichnung → x.isynet-Kürzel)

Die Tabelle öffnest du unter **Zuordnung…**. Jede Zeile enthält:
- **Kürzel:** exakt so, wie die Analyse in x.isynet heißt (z. B. `CREA`, `HBA1C`).
- **Aliase:** alle Schreibweisen der Labore, getrennt mit `|`. Ein Alias mit `@urin` gilt nur in Urin-Abschnitten.
- **Einheit:** die erwartete Einheit. Weicht die Einheit im PDF ab, gibt es einen Hinweis (z. B. CRP mg/dl statt mg/l).
- Das Kürzel **`-`** bedeutet „immer ignorieren“, zum Beispiel für Hämolyse-Index oder Gruppenüberschriften.

Die Kürzel aus eurer Laborübersicht und aus dem LG-Ausdruck sind bereits eingetragen. Zeilen mit dem Vermerk **„Vorschlag – in x.isynet prüfen“** sind von mir ergänzt (z. B. CA, LDH, B12, ANA). Bitte gleiche sie einmal mit den Analysen in x.isynet ab.

Wenn du beim Prüfen einer unbekannten Zeile ein Kürzel zuweist, **merkt sich PDF2PVS diese Bezeichnung**. Beim nächsten PDF dieses Labors wird sie automatisch erkannt.

Zwei Kürzel weichen voneinander ab: Der LG-Ausdruck zeigt `NA` und `TSHB`, die Laborübersicht `Na` und `TSH`. Aktuell geht beides auf `Na` bzw. `TSH`. Falls x.isynet die Werte der Laborgemeinschaft unter `NA`/`TSHB` führt, ändere das Kürzel in der Zuordnung.

---

## 5. Grenzen

- **Gescannte PDFs:** Die Texterkennung ist gut, aber nicht fehlerfrei, vor allem bei schlechten Scans, Faxen oder Punktlinien. Werte aus Scans bitte immer mit dem PDF rechts vergleichen.
- **Neue Laborformate:** Bei unbekannten Formaten erscheinen Zeilen ohne Zuordnung rot. Weise dort einmal die Kürzel zu, danach werden sie erkannt.
- **Abrechnung:** Es werden keine Gebührenziffern übertragen. Der Import erzeugt keine Abrechnungsdaten.
- PDF2PVS ist ein Hilfsmittel und kein Medizinprodukt. Für die Richtigkeit der übernommenen Werte bleibt die prüfende Person verantwortlich.

## 6. Dateien

- Einstellungen, Zuordnung und Export-Protokoll liegen unter `%APPDATA%\PDF2PVS\`.
- Das Export-Protokoll (`export_protokoll.csv`) enthält Name, Geburtsdatum und Dateiname jedes Exports. Es dient zur Nachvollziehbarkeit und bleibt lokal.
