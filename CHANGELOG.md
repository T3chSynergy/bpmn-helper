# Änderungen

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/). Die Versionen einzelner Erweiterungen stehen in ihren Kopfzeilen.

## [Unveröffentlicht]

### Geändert

- Aufbau nach der Vorlage openwebui-extensions-template: `tools/`, `functions/`, `models/`, `scripts/`, `e2e/`.
- Modell `bic-bpmn-assistent-v2` mit `model.json` und `system-prompt.md`; Einspielen per `scripts/deploy.py`.

### Neu

- Pipe `echo` als Testmodell, End-to-End-Test, GitHub Actions, MIT-Lizenz.

## 2026-11-17

### Neu

- Aktion `bpmn_vorschau` 0.1.0: BPMN als Diagramm im Chat.
- Tool `validate_bpmn_xml` 0.3.0: Prüfung von Referenzen, Basisprofil und Diagramminformationen.
- bpmn-js 18.33.0 zum internen Hosten.
