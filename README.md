# BPMN-Helper

Hilfsmittel rund um das Open-WebUI-Modell, das aus einer Prozessbeschreibung eine BPMN-Datei für BIC Process Design erzeugt.

| Datei | Zweck |
| --- | --- |
| [`openwebui/bpmn_vorschau.py`](openwebui/bpmn_vorschau.py) | Aktion (Button), die das BPMN einer Antwort direkt im Chat als Diagramm zeigt |
| [`openwebui/validate_bpmn_xml.py`](openwebui/validate_bpmn_xml.py) | Tool, mit dem das Modell sein BPMN prüft und auf Wunsch als Datei bereitstellt |
| [`openwebui/system-prompt.md`](openwebui/system-prompt.md) | System-Prompt des Modells, mit allgemeinem Referenzmodell |

Damit lässt sich prüfen, ob eine eigene Webanwendung überhaupt nötig ist.

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

Die benötigte Datei liegt im Repo unter [`vendor/bpmn-js/18.33.0/`](vendor/bpmn-js/README.md), zusammen mit Lizenz und Prüfsumme. Für den internen Webserver diese Datei dort ablegen, zum Beispiel unter `https://intranet.example/static/bpmn-js/18.33.0/bpmn-navigated-viewer.production.min.js`, und die Adresse in `bpmn_js_url` eintragen. Die Versionsnummer im Pfad sorgt dafür, dass Browser nach einem Update nicht die alte Datei aus dem Cache nehmen.

Zum Einbetten kann `bpmn_js_url` auch auf diese Datei im Dateisystem des Open-WebUI-Servers zeigen, etwa wenn das Repo dort ausgecheckt oder die Datei in den Container gemountet ist.

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

## Prüf-Tool `validate_bpmn_xml`

Das Modell ruft das Tool vor jeder XML-Ausgabe auf (siehe System-Prompt, Abschnitt 7). Name, Parameter und Rückgabefelder sind dieselben wie in Version 0.2.0; neu sind die festen Prüfungen nach der XML-Syntax.

**Fehler** (`errors`, das Dokument gilt als ungültig, es wird keine Datei erstellt):

- doppelte IDs, fehlender `targetNamespace`
- Referenzen, die ins Leere zeigen: `sourceRef`, `targetRef`, `processRef`, `messageRef`, `default`, `flowNodeRef`, `bpmnElement`
- `incoming`/`outgoing`, die nicht zu den Sequenzflüssen passen, oder Nachrichtenflüsse darin
- falsche Reihenfolge von `incoming`, `outgoing` und Ereignisdefinitionen
- Startereignis mit eingehendem, Endereignis mit ausgehendem Fluss, Prozess ohne Start
- Sequenzfluss zwischen Pools, Nachrichtenfluss innerhalb eines Pools, Prozess ohne Pool
- BPMN-DI: fehlendes Diagramm, fehlende Shapes oder Edges, ungültige Bounds, weniger als zwei waypoints, Element außerhalb seines Pools, BPMNPlane verweist nicht auf die Kollaboration

**Hinweise** (`warnings`, das Dokument bleibt gültig):

- Elemente außerhalb des Basisprofils, z. B. Lanes oder parallele Gateways (nur nach Freigabe erlaubt), Hersteller-Erweiterungen
- IDs mit Umlauten oder Leerzeichen, `isExecutable` nicht `false`
- Sackgassen, nicht erreichbare Elemente, unbeschriftete Entscheidungen und Pfade
- überlappende Elemente, Linien, die nicht am Elementrand beginnen oder enden oder durch fremde Elemente laufen
- Nachrichtenfluss mit eigenem Namen und zusätzlich benannter Nachricht (doppelte Beschriftung in der Vorschau)

Je Liste werden höchstens 40 Einträge gemeldet. XSD-Konformität, fachliche Richtigkeit und der BIC-Import werden nicht geprüft.

**Einrichtung:** Unter **Arbeitsbereich → Tools** das bestehende Tool öffnen und den Inhalt durch [`openwebui/validate_bpmn_xml.py`](openwebui/validate_bpmn_xml.py) ersetzen. Die Tool-ID `validate_bpmn_xml` muss gleich bleiben, sonst findet das Modell das Tool nicht mehr.

Danach den System-Prompt des Modells durch [`openwebui/system-prompt.md`](openwebui/system-prompt.md) ersetzen. Gegenüber der bisherigen Fassung sind dort das Referenzmodell allgemeiner, die Abschnitte zur Prüfung an das Tool angepasst und ein Hinweis zu benannten Nachrichtenflüssen ergänzt.

## Beispiele

| Datei | Inhalt |
| --- | --- |
| `samples/reklamationsbearbeitung.bpmn` | Referenzmodell aus dem System-Prompt: interner Pool, Black-Box-Pool, Nachrichtenflüsse, Sendeaufgaben, Nachrichtenereignisse, Rücksprünge. Das Prüf-Tool meldet dafür weder Fehler noch Hinweise. Nicht in BIC importgeprüft. |
| `samples/rechnungsfreigabe.bpmn` | Einfaches Beispiel mit Lanes. Lanes sind im Basisprofil nur nach Freigabe erlaubt. |

## Tests

```bash
uv run --no-project --with pytest --with pydantic --with defusedxml pytest tests
```
