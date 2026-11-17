# BPMN-Helper

Hilfsmittel rund um das Open-WebUI-Modell, das aus einer Prozessbeschreibung eine BPMN-Datei für BIC Process Design erzeugt.

Erster Baustein: ein Button in Open WebUI, der das erzeugte BPMN direkt im Chat als Diagramm zeigt. Damit lässt sich prüfen, ob eine eigene Webanwendung überhaupt nötig ist.

## BPMN-Vorschau für Open WebUI

![BPMN-Vorschau im Chat](docs/bpmn-vorschau.png)

Unter jeder Antwort des Modells erscheint ein Button „BPMN-Vorschau". Ein Klick zeigt das BPMN aus dieser Antwort als Diagramm über dem Antworttext.

- **Verschieben und Zoomen:** Ziehen verschiebt, Strg + Mausrad zoomt, „Vollbild" nutzt den ganzen Bildschirm.
- **Element ansprechen:** Ein Klick auf ein Element zeigt Typ, Name und ID. „Im Chat ansprechen" schreibt den Bezug ins Eingabefeld, zum Beispiel `Bitte ändere Aufgabe „Rechnung sachlich prüfen“ (ID: Task_Pruefen) wie folgt:`.
- **Fehler zurückgeben:** Lässt sich das BPMN nicht oder nur mit Hinweisen einlesen, stehen die Meldungen unter dem Diagramm. Ein Button übergibt sie dem Modell zur Korrektur.
- **Speichern:** „Als .bpmn speichern" lädt exakt den Text herunter, den das Modell geliefert hat. Die Vorschau verändert das BPMN nicht.
- **Verlauf:** Die Vorschau wird mit der Nachricht gespeichert. Jede Antwort hat ihre eigene Vorschau, ältere Stände bleiben also im Chat sichtbar.

Die Funktion sucht in der angeklickten Antwort das letzte vollständige `<definitions>…</definitions>`, mit oder ohne Codeblock und Namensraum-Präfix.

### Einrichtung

1. In Open WebUI als Administrator **Admin-Bereich → Funktionen → Neue Funktion** öffnen.
2. Den Inhalt von [`openwebui/bpmn_vorschau.py`](openwebui/bpmn_vorschau.py) einfügen, als Namen „BPMN-Vorschau" vergeben und speichern.
3. Die Funktion in der Liste einschalten.
4. Die Funktion dem Modell zuweisen: **Arbeitsbereich → Modelle → BPMN-Modell bearbeiten → Aktionen → „BPMN-Vorschau"** anhaken. Alternativ im Menü der Funktion „Global" einschalten, dann erscheint der Button bei allen Modellen.

### bpmn-js bereitstellen

Die Anzeige nutzt [bpmn-js](https://bpmn.io/toolkit/bpmn-js/), getestet mit Version 18.33.0. Woher die Bibliothek kommt, steuern die Einstellungen (Valves) der Funktion:

| Variante | Einstellung | Voraussetzung |
| --- | --- | --- |
| Öffentliches CDN (Standard) | nichts ändern | Die Browser der Anwender erreichen `unpkg.com`. |
| Interner Webserver | `bpmn_js_url` auf die interne Adresse setzen | Die Datei liegt auf einem Server, den die Browser erreichen. |
| Einbetten | `bpmn_js_inline` einschalten; `bpmn_js_url` ist eine Adresse oder ein Dateipfad, den der Open-WebUI-Server lesen kann | keine Anforderung an die Browser |

Beim Einbetten wächst jede gespeicherte Vorschau um etwa 200 KB. Für den Dauerbetrieb ist der interne Webserver die bessere Wahl.

Die benötigte Datei heißt `bpmn-navigated-viewer.production.min.js` und liegt im npm-Paket `bpmn-js` unter `dist/`.

Die Höhe der Diagrammfläche stellt `height` ein (Standard 480 Pixel).

### Getestet und nicht getestet

- **Getestet:** Open WebUI 0.13.3 in einer lokalen Installation mit Chromium und dem Beispiel aus [`samples/`](samples/rechnungsfreigabe.bpmn): Anzeige, Fehlerfälle, Speichern, „Im Chat ansprechen", Vollbild und Neuladen des Chats. bpmn-js kam dabei von einem lokalen Webserver und per Einbetten; das öffentliche CDN war in der Testumgebung nicht erreichbar.
- **Nicht getestet:** eine echte Ausgabe eures Modells und der Import der gespeicherten Datei in BIC.
- **Ältere Open-WebUI-Versionen:** nicht getestet. Laut Changelog ersetzt ein erneuter Klick die Vorschau erst ab 0.9.3 (davor entsteht eine zweite), und „Im Chat ansprechen" funktioniert ohne zusätzliche Freigabe ab 0.11.0.

### Grenzen

- bpmn-js zeichnet Standard-BPMN 2.0. BIC-eigene Erweiterungen in der Datei werden nicht dargestellt, bleiben aber erhalten. Das Bild kann deshalb im Detail von der Darstellung in BIC abweichen.
- Das BPMN braucht Layout-Angaben (`BPMNDiagram`). Fehlen sie, meldet die Vorschau das, statt ein leeres Bild zu zeigen.
- Hat die Administration `IFRAME_CSP` gesetzt, muss diese Richtlinie das Laden von bpmn-js erlauben.
- Das bpmn.io-Logo im Diagramm muss laut Lizenz von bpmn-js sichtbar bleiben.

### Tests

```bash
uv run --no-project --with pytest --with pydantic pytest tests
```
