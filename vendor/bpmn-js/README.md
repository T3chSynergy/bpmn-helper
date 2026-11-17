# bpmn-js (eingebundene Kopie)

Unveränderte Kopie der Anzeige-Bibliothek, die die BPMN-Vorschau braucht. So kann sie auf einem internen Webserver liegen, und die Browser müssen nicht ins Internet.

| | |
| --- | --- |
| Paket | `bpmn-js` 18.33.0 aus der npm-Registry (`registry.npmjs.org/bpmn-js/-/bpmn-js-18.33.0.tgz`) |
| Datei | `dist/bpmn-navigated-viewer.production.min.js` (nur Anzeige, kein Editor) |
| Prüfsumme | siehe `18.33.0/SHA256SUMS` |
| Lizenz | `18.33.0/LICENSE`: MIT-artig; das bpmn.io-Logo im Diagramm darf nicht entfernt oder verdeckt werden |

## Prüfen

```bash
cd vendor/bpmn-js/18.33.0 && sha256sum -c SHA256SUMS
```

Unter Windows (PowerShell):

```powershell
Get-FileHash .\vendor\bpmn-js\18.33.0\bpmn-navigated-viewer.production.min.js -Algorithm SHA256
```

## Neue Version einbinden

1. Neues Verzeichnis mit der Versionsnummer anlegen, zum Beispiel `vendor/bpmn-js/18.34.0/`.
2. Paket holen und die beiden Dateien kopieren:

   ```bash
   npm pack bpmn-js@18.34.0
   tar -xzf bpmn-js-18.34.0.tgz
   cp package/dist/bpmn-navigated-viewer.production.min.js package/LICENSE vendor/bpmn-js/18.34.0/
   ```

3. Im neuen Verzeichnis `sha256sum bpmn-navigated-viewer.production.min.js > SHA256SUMS` ausführen.
4. Die Vorschau mit dem Beispiel aus `samples/` testen, dann die Adresse in den Valves der Funktion umstellen. Das alte Verzeichnis erst danach löschen.
