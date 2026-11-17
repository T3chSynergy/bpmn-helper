"""
title: BPMN-Vorschau
author: bpmn-helper
version: 0.1.0
required_open_webui_version: 0.8.4
description: Zeigt das BPMN aus einer Antwort als Diagramm direkt im Chat an.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMzc0MTUxIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cmVjdCB4PSIxLjUiIHk9IjkuNSIgd2lkdGg9IjUiIGhlaWdodD0iNSIgcng9IjEuMiIvPjxwYXRoIGQ9Ik02LjUgMTJIOU0xMiA5bDMgMy0zIDMtMy0zek0xNSAxMmgyLjUiLz48Y2lyY2xlIGN4PSIyMCIgY3k9IjEyIiByPSIyLjUiLz48L3N2Zz4=
"""

# Open-WebUI-Aktion: Button unter einer Modellantwort, der das BPMN aus dieser
# Antwort als Diagramm einbettet. Das BPMN wird nicht verändert; angezeigt und
# heruntergeladen wird exakt der Text, den das Modell geliefert hat.

import asyncio
import html
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

DEFAULT_BPMN_JS_URL = (
    "https://unpkg.com/bpmn-js@18.33.0/dist/bpmn-navigated-viewer.production.min.js"
)

# Vollständiges <definitions>…</definitions>, mit oder ohne Namensraum-Präfix
# (bpmn:, bpmn2:, semantic: …) und optionaler XML-Deklaration davor.
_DEFINITIONS_RE = re.compile(
    r"(?:<\?xml[^>]*\?>\s*)?"
    r"<(?P<prefix>(?:[A-Za-z_][\w.-]*:)?)definitions\b.*?</(?P=prefix)definitions\s*>",
    re.DOTALL,
)
_DEFINITIONS_START_RE = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?definitions\b")

_bundle_cache: dict[str, str] = {}


def extract_bpmn_xml(text: str) -> tuple[Optional[str], str]:
    """Sucht das letzte vollständige BPMN in einem Nachrichtentext.

    Rückgabe: (xml, status) mit status "ok", "truncated" (Anfang gefunden, Ende
    fehlt) oder "none".
    """
    matches = list(_DEFINITIONS_RE.finditer(text or ""))
    if matches:
        return matches[-1].group(0).strip(), "ok"
    if _DEFINITIONS_START_RE.search(text or ""):
        return None, "truncated"
    return None, "none"


def message_text(body: dict) -> str:
    """Text der Nachricht, unter der der Button geklickt wurde."""
    messages = body.get("messages") or []
    if not messages:
        return ""
    clicked = next((m for m in messages if m.get("id") == body.get("id")), messages[-1])
    content = clicked.get("content", "")
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content if isinstance(content, str) else ""


def _read_bundle(source: str) -> str:
    if source not in _bundle_cache:
        if re.match(r"^https?://", source, re.IGNORECASE):
            request = urllib.request.Request(source, headers={"User-Agent": "bpmn-vorschau"})
            with urllib.request.urlopen(request, timeout=20) as response:
                code = response.read().decode("utf-8")
        else:
            code = Path(source).read_text(encoding="utf-8")
        _bundle_cache[source] = code
    return _bundle_cache[source]


def build_preview_html(xml: str, script_tag: str, height: int) -> str:
    """Baut die eingebettete HTML-Seite mit bpmn-js-Betrachter."""
    payload = json.dumps({"xml": xml}, ensure_ascii=False)
    # Verhindert, dass Inhalt aus dem BPMN den <script>-Block verlässt.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        _TEMPLATE.replace("__HEIGHT__", str(int(height)))
        .replace("__BPMN_JS_TAG__", script_tag)
        .replace("__PAYLOAD__", payload)
    )


class Action:
    class Valves(BaseModel):
        bpmn_js_url: str = Field(
            default=DEFAULT_BPMN_JS_URL,
            description=(
                "Adresse von bpmn-navigated-viewer.production.min.js. Muss vom Browser "
                "der Anwender erreichbar sein. Ist 'bpmn_js_inline' aktiv, darf hier "
                "auch ein Dateipfad auf dem Open-WebUI-Server stehen."
            ),
        )
        bpmn_js_inline: bool = Field(
            default=False,
            description=(
                "bpmn-js serverseitig laden und in jede Vorschau einbetten. Nötig, wenn "
                "die Browser der Anwender die Adresse nicht erreichen. Vergrößert jede "
                "gespeicherte Vorschau um etwa 200 KB."
            ),
        )
        height: int = Field(default=480, description="Höhe der Diagrammfläche in Pixeln.")

    def __init__(self):
        self.valves = self.Valves()

    async def _script_tag(self, notify) -> str:
        source = self.valves.bpmn_js_url.strip() or DEFAULT_BPMN_JS_URL
        if self.valves.bpmn_js_inline:
            try:
                code = await asyncio.to_thread(_read_bundle, source)
                return "<script>" + code.replace("</script", "<\\/script") + "</script>"
            except Exception as error:  # Netz- oder Dateifehler: auf Verweis ausweichen
                await notify(
                    "warning",
                    f"bpmn-js konnte serverseitig nicht geladen werden ({error}). "
                    "Die Vorschau lädt es stattdessen im Browser.",
                )
                if not re.match(r"^https?://", source, re.IGNORECASE):
                    source = DEFAULT_BPMN_JS_URL
        return f'<script src="{html.escape(source, quote=True)}"></script>'

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> None:
        async def notify(level: str, content: str) -> None:
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "notification", "data": {"type": level, "content": content}}
                )

        xml, status = extract_bpmn_xml(message_text(body))
        if status == "truncated":
            await notify(
                "warning",
                "Das BPMN in dieser Antwort ist unvollständig (das schließende "
                "</definitions> fehlt). Vermutlich wurde die Antwort abgeschnitten.",
            )
            return None
        if xml is None:
            await notify("info", "In dieser Antwort wurde kein BPMN gefunden.")
            return None
        if not __event_emitter__:
            return None

        page = build_preview_html(xml, await self._script_tag(notify), self.valves.height)
        # "replace" sorgt dafür, dass ein erneuter Klick die Vorschau ersetzt,
        # statt eine zweite darunterzusetzen (Open WebUI ab 0.9.3).
        await __event_emitter__(
            {"type": "embeds", "data": {"embeds": [page], "replace": True}}
        )
        return None


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BPMN-Vorschau</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: transparent; }
  body { font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2937; }
  #app { display: flex; flex-direction: column; background: #fff; border: 1px solid #d1d5db; border-radius: 12px; overflow: hidden; }
  #app:fullscreen { border: 0; border-radius: 0; height: 100vh; }
  .bar { display: flex; align-items: center; gap: 6px; padding: 6px 8px; flex-wrap: wrap; background: #f9fafb; }
  .bar.top { border-bottom: 1px solid #e5e7eb; }
  .bar.bottom { border-top: 1px solid #e5e7eb; }
  .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .hint { color: #6b7280; font-size: 12px; white-space: nowrap; }
  .spacer { flex: 1 1 auto; }
  button { font: inherit; color: inherit; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 3px 9px; cursor: pointer; white-space: nowrap; }
  button:hover { background: #f3f4f6; }
  button:focus-visible { outline: 2px solid #2563eb; outline-offset: 1px; }
  button.icon { min-width: 28px; padding: 3px 0; font-weight: 600; }
  #canvas { height: __HEIGHT__px; background: #fff; cursor: grab; touch-action: none; }
  #canvas:active { cursor: grabbing; }
  #app:fullscreen #canvas { height: auto; flex: 1 1 auto; }
  #selection-text { min-width: 0; overflow-wrap: anywhere; }
  .notice { padding: 8px 10px; border-top: 1px solid #e5e7eb; }
  .notice.warn { background: #fffbeb; color: #78350f; }
  .notice.error { background: #fef2f2; color: #7f1d1d; }
  .notice summary { cursor: pointer; font-weight: 600; }
  .notice ul { margin: 6px 0; padding-left: 20px; }
  .notice li { overflow-wrap: anywhere; }
  .notice p { margin: 0 0 6px; overflow-wrap: anywhere; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<div id="app">
  <div class="bar top">
    <span class="title" id="title">BPMN-Vorschau</span>
    <span class="spacer"></span>
    <span class="hint" id="hint">Ziehen = verschieben · Strg + Mausrad = zoomen</span>
    <button class="icon" id="zoom-out" title="Verkleinern" aria-label="Verkleinern">−</button>
    <button class="icon" id="zoom-in" title="Vergrößern" aria-label="Vergrößern">+</button>
    <button id="fit" title="Ganzes Diagramm zeigen">Einpassen</button>
    <button id="fullscreen" hidden>Vollbild</button>
    <button id="download" title="BPMN unverändert als Datei speichern">Als .bpmn speichern</button>
  </div>
  <div id="canvas"></div>
  <div class="bar bottom" id="selection" hidden>
    <span id="selection-text"></span>
    <span class="spacer"></span>
    <button id="selection-prompt" title="Bezug auf dieses Element ins Eingabefeld des Chats schreiben">Im Chat ansprechen</button>
  </div>
  <details class="notice warn" id="warnings" hidden>
    <summary id="warnings-summary"></summary>
    <ul id="warnings-list"></ul>
    <button id="warnings-prompt">Hinweise an das Modell geben</button>
  </details>
  <div class="notice error" id="error" hidden>
    <p><strong>Das Diagramm kann nicht angezeigt werden.</strong></p>
    <p id="error-text"></p>
    <ul id="error-list" hidden></ul>
    <button id="error-prompt" hidden>Fehler an das Modell geben</button>
  </div>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
__BPMN_JS_TAG__
<script>
(function () {
  'use strict';

  var data = JSON.parse(document.getElementById('payload').textContent);
  var app = document.getElementById('app');
  var canvasEl = document.getElementById('canvas');
  var fileBase = 'prozess';
  var viewer = null;
  var canvas = null;

  function el(id) { return document.getElementById(id); }

  // --- Verbindung zu Open WebUI -------------------------------------------
  function reportHeight() {
    if (document.fullscreenElement) return;
    parent.postMessage({ type: 'iframe:height', height: Math.ceil(app.getBoundingClientRect().height) }, '*');
  }
  function sendToChatInput(text) {
    parent.postMessage({ type: 'input:prompt', text: text }, '*');
  }
  if (window.ResizeObserver) { new ResizeObserver(reportHeight).observe(app); }
  window.addEventListener('load', reportHeight);

  // --- Meldungen ------------------------------------------------------------
  function fillList(list, messages) {
    list.textContent = '';
    messages.slice(0, 25).forEach(function (message) {
      var item = document.createElement('li');
      item.textContent = message;
      list.appendChild(item);
    });
    if (messages.length > 25) {
      var more = document.createElement('li');
      more.textContent = '… und ' + (messages.length - 25) + ' weitere';
      list.appendChild(more);
    }
  }
  function messagesOf(warnings) {
    var seen = {};
    return (warnings || []).map(function (w) { return String((w && w.message) || w); })
      .filter(function (m) { if (seen[m]) return false; seen[m] = true; return true; });
  }
  function showWarnings(warnings) {
    var messages = messagesOf(warnings);
    if (!messages.length) return;
    el('warnings-summary').textContent = messages.length === 1
      ? '1 Hinweis beim Einlesen des BPMN'
      : messages.length + ' Hinweise beim Einlesen des BPMN';
    fillList(el('warnings-list'), messages);
    el('warnings-prompt').onclick = function () {
      sendToChatInput('Beim Anzeigen des BPMN gab es folgende Hinweise. Bitte korrigiere das BPMN entsprechend:\n- '
        + messages.slice(0, 25).join('\n- '));
    };
    el('warnings').hidden = false;
  }
  function showError(text, warnings, offerPrompt) {
    var messages = messagesOf(warnings);
    canvasEl.hidden = true;
    el('hint').hidden = true;
    ['zoom-in', 'zoom-out', 'fit', 'fullscreen'].forEach(function (id) { el(id).hidden = true; });
    el('error-text').textContent = text;
    if (messages.length) { fillList(el('error-list'), messages); el('error-list').hidden = false; }
    if (offerPrompt) {
      el('error-prompt').hidden = false;
      el('error-prompt').onclick = function () {
        sendToChatInput('Das BPMN lässt sich nicht anzeigen. Bitte korrigiere es. Fehlermeldung:\n' + text
          + (messages.length ? '\n- ' + messages.slice(0, 25).join('\n- ') : ''));
      };
    }
    el('error').hidden = false;
    reportHeight();
  }

  // --- Herunterladen: exakt der Text aus der Antwort ----------------------
  el('download').onclick = function () {
    var url = URL.createObjectURL(new Blob([data.xml], { type: 'application/xml' }));
    var link = document.createElement('a');
    link.href = url;
    link.download = fileBase + '.bpmn';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  };

  if (typeof BpmnJS === 'undefined') {
    showError('Die Anzeige-Bibliothek bpmn-js konnte nicht geladen werden. Bitte die Administration informieren: '
      + 'In den Einstellungen (Valves) der Funktion „BPMN-Vorschau“ muss eine erreichbare Adresse stehen '
      + 'oder das Einbetten aktiviert sein.', [], false);
    return;
  }

  // --- Elementbeschreibung -------------------------------------------------
  var TYPE_NAMES = {
    'bpmn:Task': 'Aufgabe', 'bpmn:UserTask': 'Benutzeraufgabe', 'bpmn:ServiceTask': 'Service-Aufgabe',
    'bpmn:ManualTask': 'Manuelle Aufgabe', 'bpmn:SendTask': 'Sende-Aufgabe', 'bpmn:ReceiveTask': 'Empfangs-Aufgabe',
    'bpmn:ScriptTask': 'Skript-Aufgabe', 'bpmn:BusinessRuleTask': 'Geschäftsregel-Aufgabe',
    'bpmn:SubProcess': 'Teilprozess', 'bpmn:CallActivity': 'Aufruf-Aktivität', 'bpmn:Transaction': 'Transaktion',
    'bpmn:StartEvent': 'Startereignis', 'bpmn:EndEvent': 'Endereignis',
    'bpmn:IntermediateCatchEvent': 'Zwischenereignis', 'bpmn:IntermediateThrowEvent': 'Zwischenereignis',
    'bpmn:BoundaryEvent': 'Angeheftetes Ereignis',
    'bpmn:ExclusiveGateway': 'Exklusives Gateway', 'bpmn:ParallelGateway': 'Paralleles Gateway',
    'bpmn:InclusiveGateway': 'Inklusives Gateway', 'bpmn:EventBasedGateway': 'Ereignisbasiertes Gateway',
    'bpmn:ComplexGateway': 'Komplexes Gateway',
    'bpmn:SequenceFlow': 'Sequenzfluss', 'bpmn:MessageFlow': 'Nachrichtenfluss', 'bpmn:Association': 'Assoziation',
    'bpmn:DataInputAssociation': 'Datenassoziation', 'bpmn:DataOutputAssociation': 'Datenassoziation',
    'bpmn:Participant': 'Pool', 'bpmn:Lane': 'Lane',
    'bpmn:DataObjectReference': 'Datenobjekt', 'bpmn:DataStoreReference': 'Datenspeicher',
    'bpmn:TextAnnotation': 'Anmerkung', 'bpmn:Group': 'Gruppe'
  };
  function clean(name) { return String(name || '').replace(/\s+/g, ' ').trim(); }
  function describe(bo) {
    var type = TYPE_NAMES[bo.$type] || String(bo.$type || '').replace(/^bpmn:/, '');
    var name = clean(bo.name || bo.text);
    var text = name ? type + ' „' + name + '“' : type;
    if (bo.sourceRef && bo.targetRef && !Array.isArray(bo.sourceRef)) {
      var from = clean(bo.sourceRef.name) || bo.sourceRef.id;
      var to = clean(bo.targetRef.name) || bo.targetRef.id;
      text += ' von „' + from + '“ nach „' + to + '“';
    }
    return text + ' (ID: ' + bo.id + ')';
  }

  // --- Diagramm --------------------------------------------------------------
  viewer = new BpmnJS({ container: canvasEl });
  canvas = viewer.get('canvas');

  // Mausrad soll im Chat weiter die Seite scrollen; gezoomt wird mit Strg + Mausrad.
  var zoomScroll = viewer.get('zoomScroll', false);
  if (zoomScroll) { zoomScroll.toggle(false); }
  function zoomBy(factor, center) {
    var next = Math.min(4, Math.max(0.1, canvas.zoom() * factor));
    canvas.zoom(next, center || 'auto');
  }
  canvasEl.addEventListener('wheel', function (event) {
    if (document.fullscreenElement) return;
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    var rect = canvasEl.getBoundingClientRect();
    zoomBy(event.deltaY < 0 ? 1.15 : 1 / 1.15, { x: event.clientX - rect.left, y: event.clientY - rect.top });
  }, { passive: false });

  function fit() {
    canvas.resized();
    canvas.zoom('fit-viewport', 'auto');
    canvas.zoom(canvas.zoom() * 0.94, 'auto'); // etwas Rand lassen
  }
  el('zoom-in').onclick = function () { zoomBy(1.25); };
  el('zoom-out').onclick = function () { zoomBy(1 / 1.25); };
  el('fit').onclick = fit;

  if (document.fullscreenEnabled && app.requestFullscreen) {
    el('fullscreen').hidden = false;
    el('fullscreen').onclick = function () {
      if (document.fullscreenElement) { document.exitFullscreen(); } else { app.requestFullscreen().catch(function () {}); }
    };
    document.addEventListener('fullscreenchange', function () {
      var active = !!document.fullscreenElement;
      el('fullscreen').textContent = active ? 'Vollbild beenden' : 'Vollbild';
      el('hint').textContent = active ? 'Ziehen = verschieben · Mausrad = scrollen · Strg + Mausrad = zoomen'
        : 'Ziehen = verschieben · Strg + Mausrad = zoomen';
      if (zoomScroll) { zoomScroll.toggle(active); }
      setTimeout(function () { fit(); reportHeight(); }, 80);
    });
  }

  viewer.get('eventBus').on('element.click', function (event) {
    var element = event.element && (event.element.labelTarget || event.element);
    var bo = element && element.businessObject;
    if (!bo || element === canvas.getRootElement() || !bo.id) { el('selection').hidden = true; return; }
    var text = describe(bo);
    el('selection-text').textContent = text;
    el('selection-prompt').onclick = function () { sendToChatInput('Bitte ändere ' + text + ' wie folgt: '); };
    el('selection').hidden = false;
  });

  function processName(definitions) {
    var roots = (definitions && definitions.rootElements) || [];
    var name = '';
    roots.forEach(function (root) {
      if (name) return;
      if (root.$type === 'bpmn:Collaboration') {
        (root.participants || []).forEach(function (p) { if (!name) name = clean(p.name); });
      }
    });
    roots.forEach(function (root) { if (!name && root.$type === 'bpmn:Process') name = clean(root.name); });
    return name;
  }

  viewer.importXML(data.xml).then(function (result) {
    var name = processName(viewer.getDefinitions());
    if (name) {
      el('title').textContent = name;
      fileBase = name.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80) || fileBase;
    }
    fit();
    showWarnings(result && result.warnings);
    reportHeight();
  }).catch(function (error) {
    var message = String((error && error.message) || error);
    if (/no diagram to display/i.test(message)) {
      message = 'Das BPMN enthält keine Layout-Angaben (Abschnitt BPMNDiagram mit Positionen fehlt oder ist leer).';
    }
    showError(message, error && error.warnings, true);
  });
})();
</script>
</body>
</html>
"""
