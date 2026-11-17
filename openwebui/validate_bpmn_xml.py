"""
title: BPMN XML prüfen und exportieren
author: ms
version: 0.3.0
description: Prüft BPMN-XML auf Syntax, Referenzen, Basisprofil und Diagramminformationen und stellt auf ausdrücklichen Wunsch eine BPMN-Datei über die Open-WebUI-Dateiverwaltung bereit.
required_open_webui_version: 0.10.0
requirements:
  - defusedxml>=0.7.1
licence: MIT
"""

import asyncio
import hashlib
import inspect
import io
import json
import logging
import math
import re
import unicodedata
import uuid
from typing import Any

from defusedxml import ElementTree as SafeET

log = logging.getLogger(__name__)

BPMN_NAMESPACE = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NAMESPACE = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NAMESPACE = "http://www.omg.org/spec/DD/20100524/DC"
DI_NAMESPACE = "http://www.omg.org/spec/DD/20100524/DI"
BPMN_ROOT = f"{{{BPMN_NAMESPACE}}}definitions"
BPMN_CONTENT_TYPE = "application/xml"

MAX_XML_BYTES = 10 * 1024 * 1024
MAX_REPORTED = 40  # je Liste; mehr Einträge überfordern das Modell nur

# Abstand in Pixeln, ab dem ein Linienende nicht mehr als "am Rand" gilt.
EDGE_END_TOLERANCE = 15

# --- Basisprofil -------------------------------------------------------------

FLOW_NODE_TYPES = {
    "task", "sendTask", "receiveTask", "userTask", "manualTask", "serviceTask",
    "scriptTask", "businessRuleTask", "subProcess", "adHocSubProcess", "transaction",
    "callActivity", "startEvent", "endEvent", "intermediateCatchEvent",
    "intermediateThrowEvent", "boundaryEvent", "exclusiveGateway", "parallelGateway",
    "inclusiveGateway", "eventBasedGateway", "complexGateway",
}
EVENT_DEFINITIONS = {
    "messageEventDefinition", "timerEventDefinition", "errorEventDefinition",
    "signalEventDefinition", "escalationEventDefinition", "compensateEventDefinition",
    "conditionalEventDefinition", "linkEventDefinition", "cancelEventDefinition",
    "terminateEventDefinition", "eventDefinitionRef",
}
PROFILE_NODE_TYPES = {"task", "sendTask", "startEvent", "endEvent",
                      "intermediateCatchEvent", "exclusiveGateway"}
PROFILE_PROCESS_CHILDREN = PROFILE_NODE_TYPES | {"sequenceFlow", "documentation"}
PROFILE_ROOT_ELEMENTS = {"process", "collaboration", "message", "documentation"}
PROFILE_COLLABORATION_CHILDREN = {"participant", "messageFlow", "documentation"}

TYPE_NAMES = {
    "task": "Aufgabe", "sendTask": "Sendeaufgabe", "startEvent": "Startereignis",
    "endEvent": "Endereignis", "intermediateCatchEvent": "Zwischenereignis",
    "exclusiveGateway": "Exklusives Gateway", "sequenceFlow": "Sequenzfluss",
    "messageFlow": "Nachrichtenfluss", "participant": "Pool", "lane": "Lane",
    "process": "Prozess", "collaboration": "Kollaboration", "message": "Nachricht",
}

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


def _label(element) -> str:
    kind = _local(element.tag)
    name = (element.get("name") or "").strip()
    text = TYPE_NAMES.get(kind, kind)
    if name:
        text += f" „{name}“"
    return f"{text} ({element.get('id') or 'ohne ID'})"


class _Report:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.hidden = {"errors": 0, "warnings": 0}

    def _add(self, target: list, kind: str, code: str, message: str, element_id=None):
        if len(target) >= MAX_REPORTED:
            self.hidden[kind] += 1
            return
        entry = {"code": code, "message": message}
        if element_id:
            entry["element_id"] = element_id
        if entry not in target:
            target.append(entry)

    def error(self, code, message, element_id=None):
        self._add(self.errors, "errors", code, message, element_id)

    def warning(self, code, message, element_id=None):
        self._add(self.warnings, "warnings", code, message, element_id)


# --- Geometrie -----------------------------------------------------------------

def _bounds_of(shape):
    bounds = shape.find(f"{{{DC_NAMESPACE}}}Bounds")
    if bounds is None:
        return None
    try:
        x, y, w, h = (float(bounds.get(k)) for k in ("x", "y", "width", "height"))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    return (x, y, w, h)


def _inside(inner, outer, slack=1.0) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return (ix >= ox - slack and iy >= oy - slack
            and ix + iw <= ox + ow + slack and iy + ih <= oy + oh + slack)


def _overlap(a, b, shrink=2.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax + shrink < bx + bw - shrink and bx + shrink < ax + aw - shrink
            and ay + shrink < by + bh - shrink and by + shrink < ay + ah - shrink)


def _distance_to_border(point, rect) -> float:
    px, py = point
    x, y, w, h = rect
    dx = max(x - px, 0, px - (x + w))
    dy = max(y - py, 0, py - (y + h))
    if dx or dy:  # außerhalb
        return math.hypot(dx, dy)
    return min(px - x, x + w - px, py - y, y + h - py)  # innerhalb


def _segment_hits_rect(p, q, rect, shrink=4.0) -> bool:
    """Liang-Barsky: schneidet die Strecke p-q das (verkleinerte) Rechteck?"""
    x, y, w, h = rect
    xmin, ymin, xmax, ymax = x + shrink, y + shrink, x + w - shrink, y + h - shrink
    if xmin >= xmax or ymin >= ymax:
        return False
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        if pk == 0:
            if qk < 0:
                return False
        else:
            t = qk / pk
            if pk < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


# --- Prüfung -------------------------------------------------------------------

def check_bpmn(root) -> dict:
    """Prüft ein geparstes BPMN-Dokument. Ergebnis: errors, warnings, summary."""
    report = _Report()
    B = f"{{{BPMN_NAMESPACE}}}"
    D = f"{{{BPMNDI_NAMESPACE}}}"

    if not (root.get("targetNamespace") or "").strip():
        report.error("MISSING_TARGET_NAMESPACE", "definitions benötigt das Attribut targetNamespace.")

    # IDs sammeln
    by_id: dict[str, Any] = {}
    for element in root.iter():
        element_id = element.get("id")
        if element_id is None:
            continue
        if element_id in by_id:
            report.error("DUPLICATE_ID", f"Die ID „{element_id}“ ist mehrfach vergeben.", element_id)
        else:
            by_id[element_id] = element
        if not ID_PATTERN.match(element_id):
            report.warning(
                "ID_FORMAT",
                f"Die ID „{element_id}“ sollte mit einem Buchstaben beginnen und nur "
                "Buchstaben ohne Umlaute, Ziffern, _, - oder . enthalten.",
                element_id,
            )

    def kind_of(element_id):
        element = by_id.get(element_id)
        return _local(element.tag) if element is not None else None

    # Fremde Namensräume (Hersteller-Erweiterungen)
    known = {BPMN_NAMESPACE, BPMNDI_NAMESPACE, DC_NAMESPACE, DI_NAMESPACE}
    foreign_elements = sorted({_ns(e.tag) for e in root.iter() if _ns(e.tag) and _ns(e.tag) not in known})
    foreign_attributes = sorted({
        _ns(a) for e in root.iter() for a in e.attrib
        if _ns(a) and _ns(a) not in known | {"http://www.w3.org/2001/XMLSchema-instance"}
    })
    for namespace in sorted(set(foreign_elements) | set(foreign_attributes)):
        report.warning("EXTENSION_NAMESPACE",
                       f"Elemente oder Attribute aus dem Fremd-Namensraum „{namespace}“ "
                       "sind Hersteller-Erweiterungen und nicht Teil des Basisprofils.")
    for element in root.iter(f"{B}extensionElements"):
        report.warning("EXTENSION_ELEMENTS", "extensionElements sind nicht Teil des Basisprofils.")
        break

    # Wurzelelemente
    processes = root.findall(f"{B}process")
    collaborations = root.findall(f"{B}collaboration")
    for child in root:
        name = _local(child.tag)
        if _ns(child.tag) == BPMN_NAMESPACE and name not in PROFILE_ROOT_ELEMENTS:
            report.warning("NOT_IN_PROFILE", f"{_label(child)} ist nicht Teil des Basisprofils.", child.get("id"))
    if not processes:
        report.error("NO_PROCESS", "Das Dokument enthält keinen process.")
    if len(collaborations) > 1:
        report.warning("MULTIPLE_COLLABORATIONS", "Das Dokument enthält mehrere collaboration-Elemente.")

    # Flussknoten je Prozess (inklusive verschachtelter Elemente)
    node_process: dict[str, str] = {}
    sequence_flows: dict[str, Any] = {}
    flow_process: dict[str, str] = {}
    for process in processes:
        pid = process.get("id") or ""
        if (process.get("isExecutable") or "").strip().lower() != "false":
            report.warning("EXECUTABLE", f"{_label(process)}: isExecutable sollte false sein.", pid)
        for child in process:
            name = _local(child.tag)
            if _ns(child.tag) == BPMN_NAMESPACE and name not in PROFILE_PROCESS_CHILDREN:
                if name == "laneSet":
                    report.warning("NOT_IN_PROFILE", "Lanes sind nur nach ausdrücklicher Freigabe erlaubt.", pid)
                else:
                    report.warning("NOT_IN_PROFILE", f"{_label(child)} ist nicht Teil des Basisprofils.", child.get("id"))
        for element in process.iter():
            name = _local(element.tag)
            if _ns(element.tag) != BPMN_NAMESPACE or not element.get("id"):
                continue
            if name in FLOW_NODE_TYPES:
                node_process[element.get("id")] = pid
            elif name == "sequenceFlow":
                sequence_flows[element.get("id")] = element
                flow_process[element.get("id")] = pid
        for element in process.iter():
            if _ns(element.tag) == BPMN_NAMESPACE and _local(element.tag) in FLOW_NODE_TYPES - PROFILE_NODE_TYPES \
                    and element is not process:
                report.warning("NOT_IN_PROFILE", f"{_label(element)} ist nicht Teil des Basisprofils.", element.get("id"))

    nodes = {nid: by_id[nid] for nid in node_process}

    # Ereignistypen im Basisprofil
    for nid, node in nodes.items():
        name = _local(node.tag)
        definitions = [_local(c.tag) for c in node if _local(c.tag) in EVENT_DEFINITIONS]
        if name == "startEvent" and any(d != "messageEventDefinition" for d in definitions):
            report.warning("NOT_IN_PROFILE", f"{_label(node)}: nur Start ohne Typ oder Nachrichten-Start sind im Basisprofil.", nid)
        if name == "endEvent" and definitions:
            report.warning("NOT_IN_PROFILE", f"{_label(node)}: Endereignisse im Basisprofil haben keinen Ereignistyp.", nid)
        if name == "intermediateCatchEvent" and definitions != ["messageEventDefinition"]:
            report.warning("NOT_IN_PROFILE", f"{_label(node)}: im Basisprofil nur als empfangendes Nachrichtenereignis.", nid)
        if len(definitions) > 1:
            report.warning("MULTIPLE_EVENT_DEFINITIONS", f"{_label(node)} hat mehrere Ereignisdefinitionen.", nid)

    # Sequenzflüsse
    incoming_expected = {nid: set() for nid in nodes}
    outgoing_expected = {nid: set() for nid in nodes}
    for fid, flow in sequence_flows.items():
        source, target = flow.get("sourceRef"), flow.get("targetRef")
        for role, ref in (("sourceRef", source), ("targetRef", target)):
            if not ref:
                report.error("MISSING_REFERENCE", f"{_label(flow)}: {role} fehlt.", fid)
            elif ref not in by_id:
                report.error("UNRESOLVED_REFERENCE", f"{_label(flow)}: {role} „{ref}“ existiert nicht.", fid)
            elif ref not in nodes:
                report.error("INVALID_REFERENCE", f"{_label(flow)}: {role} „{ref}“ ist kein Flussknoten ({kind_of(ref)}).", fid)
        if source in nodes and target in nodes:
            if node_process[source] != node_process[target] or node_process[source] != flow_process[fid]:
                report.error("SEQUENCE_FLOW_CROSSES_POOL",
                             f"{_label(flow)} verbindet Elemente verschiedener Prozesse. "
                             "Zwischen Pools sind nur Nachrichtenflüsse erlaubt.", fid)
            outgoing_expected[source].add(fid)
            incoming_expected[target].add(fid)
            if source == target:
                report.warning("SELF_LOOP", f"{_label(flow)} führt auf dasselbe Element zurück.", fid)

    # incoming / outgoing und Reihenfolge der Kindelemente
    for nid, node in nodes.items():
        children = [c for c in node if _ns(c.tag) == BPMN_NAMESPACE]
        declared_in = [(c.text or "").strip() for c in children if _local(c.tag) == "incoming"]
        declared_out = [(c.text or "").strip() for c in children if _local(c.tag) == "outgoing"]
        for direction, declared, expected in (("incoming", declared_in, incoming_expected[nid]),
                                              ("outgoing", declared_out, outgoing_expected[nid])):
            for ref in declared:
                if kind_of(ref) == "messageFlow":
                    report.error("MESSAGE_FLOW_IN_SEQUENCE_REFS",
                                 f"{_label(node)}: {direction} „{ref}“ ist ein Nachrichtenfluss. "
                                 "Nachrichtenflüsse gehören nicht in incoming/outgoing.", nid)
                elif ref not in expected:
                    report.error("INCOMING_OUTGOING_MISMATCH",
                                 f"{_label(node)}: {direction} „{ref}“ passt zu keinem Sequenzfluss mit "
                                 f"{'targetRef' if direction == 'incoming' else 'sourceRef'}=„{nid}“.", nid)
            for ref in sorted(expected - set(declared)):
                report.error("INCOMING_OUTGOING_MISMATCH",
                             f"{_label(node)}: {direction} „{ref}“ fehlt.", nid)
            if len(declared) != len(set(declared)):
                report.error("INCOMING_OUTGOING_MISMATCH", f"{_label(node)}: {direction} enthält Einträge doppelt.", nid)
        # Schema-Reihenfolge: documentation/extensionElements, incoming, outgoing, Rest
        rank = {"documentation": 0, "extensionElements": 0, "incoming": 1, "outgoing": 2}
        previous = 0
        for child in children:
            item = _local(child.tag)
            current = rank.get(item, 3)
            if current < previous:
                report.error("CHILD_ORDER", f"{_label(node)}: {item} steht an der falschen Stelle. Reihenfolge: "
                             "documentation, incoming, outgoing, danach z. B. Ereignisdefinitionen.", nid)
                break
            previous = current

        kind = _local(node.tag)
        has_in, has_out = bool(incoming_expected[nid]), bool(outgoing_expected[nid])
        if kind == "startEvent" and has_in:
            report.error("START_WITH_INCOMING", f"{_label(node)} darf keine eingehenden Sequenzflüsse haben.", nid)
        if kind == "endEvent" and has_out:
            report.error("END_WITH_OUTGOING", f"{_label(node)} darf keine ausgehenden Sequenzflüsse haben.", nid)
        if kind not in ("startEvent", "boundaryEvent") and not has_in:
            report.warning("NO_INCOMING", f"{_label(node)} hat keinen eingehenden Sequenzfluss.", nid)
        if kind != "endEvent" and not has_out:
            report.warning("NO_OUTGOING", f"{_label(node)} hat keinen ausgehenden Sequenzfluss (Sackgasse).", nid)
        if kind in ("task", "sendTask") and not (node.get("name") or "").strip():
            report.warning("UNNAMED", f"{_label(node)} hat keinen Namen.", nid)

        # Gateways
        if kind == "exclusiveGateway":
            default = node.get("default")
            if default and default not in outgoing_expected[nid]:
                report.error("INVALID_DEFAULT", f"{_label(node)}: Standardpfad „{default}“ ist kein ausgehender "
                             "Sequenzfluss dieses Gateways.", nid)
            if len(outgoing_expected[nid]) > 1:
                if not (node.get("name") or "").strip():
                    report.warning("UNNAMED_DECISION", f"{_label(node)} verzweigt, ist aber nicht als Frage beschriftet.", nid)
                for fid in sorted(outgoing_expected[nid]):
                    if fid != default and not (sequence_flows[fid].get("name") or "").strip():
                        report.warning("UNNAMED_PATH", f"{_label(sequence_flows[fid])} verlässt ein verzweigendes "
                                       "Gateway ohne Beschriftung.", fid)

        # Nachrichten-Verweise an Ereignissen und Sendeaufgaben
        for holder in [node] + [c for c in node if _local(c.tag) == "messageEventDefinition"]:
            ref = holder.get("messageRef")
            if ref and kind_of(ref) != "message":
                report.error("UNRESOLVED_REFERENCE", f"{_label(node)}: messageRef „{ref}“ ist keine message.", nid)

    # Erreichbarkeit je Prozess
    for process in processes:
        pid = process.get("id")
        members = [nid for nid, p in node_process.items() if p == pid]
        starts = [nid for nid in members if _local(nodes[nid].tag) == "startEvent"]
        if members and not starts:
            report.error("NO_START_EVENT", f"{_label(process)} hat kein Startereignis.", pid)
        if members and not any(_local(nodes[n].tag) == "endEvent" for n in members):
            report.warning("NO_END_EVENT", f"{_label(process)} hat kein Endereignis.", pid)
        reached, stack = set(starts), list(starts)
        while stack:
            current = stack.pop()
            for fid in outgoing_expected.get(current, ()):
                target = sequence_flows[fid].get("targetRef")
                if target in nodes and target not in reached:
                    reached.add(target)
                    stack.append(target)
        for nid in members:
            if starts and nid not in reached and _local(nodes[nid].tag) != "boundaryEvent":
                report.warning("UNREACHABLE", f"{_label(nodes[nid])} ist vom Start aus nicht erreichbar.", nid)

    # Pools und Nachrichtenflüsse
    participants = {p.get("id"): p for c in collaborations for p in c.findall(f"{B}participant") if p.get("id")}
    process_participant: dict[str, str] = {}
    for part_id, participant in participants.items():
        ref = participant.get("processRef")
        if ref:
            if kind_of(ref) != "process":
                report.error("UNRESOLVED_REFERENCE", f"{_label(participant)}: processRef „{ref}“ ist kein process.", part_id)
            elif ref in process_participant:
                report.error("PROCESS_IN_MULTIPLE_POOLS", f"Prozess „{ref}“ ist mehreren Pools zugeordnet.", part_id)
            else:
                process_participant[ref] = part_id
    if collaborations:
        for process in processes:
            if process.get("id") not in process_participant and any(p == process.get("id") for p in node_process.values()):
                report.error("PROCESS_WITHOUT_POOL", f"{_label(process)} ist keinem Pool zugeordnet.", process.get("id"))
    for collaboration in collaborations:
        for child in collaboration:
            if _ns(child.tag) == BPMN_NAMESPACE and _local(child.tag) not in PROFILE_COLLABORATION_CHILDREN:
                report.warning("NOT_IN_PROFILE", f"{_label(child)} ist nicht Teil des Basisprofils.", child.get("id"))

    def pool_of(ref):
        if ref in participants:
            return ref
        if ref in node_process:
            return process_participant.get(node_process[ref])
        return None

    message_flows = {f.get("id"): f for c in collaborations for f in c.findall(f"{B}messageFlow") if f.get("id")}
    for fid, flow in message_flows.items():
        ends = []
        for role in ("sourceRef", "targetRef"):
            ref = flow.get(role)
            if not ref:
                report.error("MISSING_REFERENCE", f"{_label(flow)}: {role} fehlt.", fid)
            elif ref not in by_id:
                report.error("UNRESOLVED_REFERENCE", f"{_label(flow)}: {role} „{ref}“ existiert nicht.", fid)
            elif ref not in participants and ref not in nodes:
                report.error("INVALID_REFERENCE", f"{_label(flow)}: {role} „{ref}“ ist weder Pool noch Flussknoten.", fid)
            else:
                ends.append(pool_of(ref))
        if len(ends) == 2:
            if None in ends:
                report.error("MESSAGE_FLOW_WITHOUT_POOL", f"{_label(flow)}: ein Ende liegt in keinem Pool.", fid)
            elif ends[0] == ends[1]:
                report.error("MESSAGE_FLOW_SAME_POOL", f"{_label(flow)} verbindet Elemente desselben Pools. "
                             "Innerhalb eines Pools sind nur Sequenzflüsse erlaubt.", fid)
        ref = flow.get("messageRef")
        if ref and kind_of(ref) != "message":
            report.error("UNRESOLVED_REFERENCE", f"{_label(flow)}: messageRef „{ref}“ ist keine message.", fid)
        elif ref and (flow.get("name") or "").strip() and (by_id[ref].get("name") or "").strip():
            report.warning("DUPLICATE_MESSAGE_FLOW_LABEL",
                           f"{_label(flow)} hat einen eigenen Namen und verweist auf eine benannte Nachricht. "
                           "Die Vorschau zeigt dann zwei Beschriftungen; eines von beiden weglassen.", fid)

    lanes = {}
    for process in processes:
        for lane in process.iter(f"{B}lane"):
            lanes[lane.get("id")] = lane
            for ref_element in lane.findall(f"{B}flowNodeRef"):
                ref = (ref_element.text or "").strip()
                if ref not in nodes:
                    report.error("UNRESOLVED_REFERENCE", f"{_label(lane)}: flowNodeRef „{ref}“ ist kein Flussknoten.", lane.get("id"))

    # --- Diagramminformationen (BPMN-DI) ---------------------------------------
    planes = [p for d in root.findall(f"{D}BPMNDiagram") for p in d.findall(f"{D}BPMNPlane")]
    shapes: dict[str, tuple] = {}
    edges: dict[str, list] = {}
    if not planes:
        report.error("NO_DIAGRAM", "BPMN-DI fehlt: Es gibt kein BPMNDiagram mit BPMNPlane. "
                     "Ohne Diagramminformationen kann nichts angezeigt werden.")
    else:
        expected_plane = collaborations[0].get("id") if collaborations else (processes[0].get("id") if processes else None)
        plane_refs = [p.get("bpmnElement") for p in planes]
        if expected_plane and expected_plane not in plane_refs:
            report.error("PLANE_REFERENCE", f"Die BPMNPlane muss auf „{expected_plane}“ verweisen "
                         f"(gefunden: {', '.join(r or '–' for r in plane_refs)}).")
        for plane in planes:
            for item in plane:
                kind = _local(item.tag)
                ref = item.get("bpmnElement")
                if kind not in ("BPMNShape", "BPMNEdge"):
                    continue
                if not ref or ref not in by_id:
                    report.error("DI_UNRESOLVED", f"{kind} „{item.get('id')}“ verweist auf unbekanntes Element „{ref}“.", item.get("id"))
                    continue
                if kind == "BPMNShape":
                    if ref in shapes:
                        report.error("DI_DUPLICATE", f"Für „{ref}“ gibt es mehrere BPMNShape.", ref)
                        continue
                    bounds = _bounds_of(item)
                    if bounds is None:
                        report.error("DI_BOUNDS", f"BPMNShape für „{ref}“ hat keine gültigen Bounds.", ref)
                    elif bounds[2] <= 0 or bounds[3] <= 0:
                        report.error("DI_BOUNDS", f"BPMNShape für „{ref}“ hat keine positive Breite und Höhe.", ref)
                    else:
                        shapes[ref] = bounds
                else:
                    if ref in edges:
                        report.error("DI_DUPLICATE", f"Für „{ref}“ gibt es mehrere BPMNEdge.", ref)
                        continue
                    points = []
                    for waypoint in item.findall(f"{{{DI_NAMESPACE}}}waypoint"):
                        try:
                            points.append((float(waypoint.get("x")), float(waypoint.get("y"))))
                        except (TypeError, ValueError):
                            report.error("DI_WAYPOINT", f"BPMNEdge für „{ref}“ hat einen ungültigen waypoint.", ref)
                    if len(points) < 2:
                        report.error("DI_WAYPOINT", f"BPMNEdge für „{ref}“ braucht mindestens zwei waypoints.", ref)
                    else:
                        edges[ref] = points

        # Vollständigkeit
        for part_id in participants:
            if part_id not in shapes:
                report.error("DI_MISSING_SHAPE", f"{_label(participants[part_id])} hat keine BPMNShape.", part_id)
        for nid, node in nodes.items():
            if nid not in shapes:
                report.error("DI_MISSING_SHAPE", f"{_label(node)} hat keine BPMNShape.", nid)
        for lane_id, lane in lanes.items():
            if lane_id not in shapes:
                report.error("DI_MISSING_SHAPE", f"{_label(lane)} hat keine BPMNShape.", lane_id)
        for fid, flow in list(sequence_flows.items()) + list(message_flows.items()):
            if fid not in edges:
                report.error("DI_MISSING_EDGE", f"{_label(flow)} hat keine BPMNEdge.", fid)

        # Pools umschließen ihre Elemente
        for nid in nodes:
            part_id = process_participant.get(node_process[nid])
            if nid in shapes and part_id in shapes and not _inside(shapes[nid], shapes[part_id]):
                report.error("DI_OUTSIDE_POOL", f"{_label(nodes[nid])} liegt nicht vollständig innerhalb von "
                             f"{_label(participants[part_id])}. Pool vergrößern oder Element verschieben.", nid)
        for lane_id in lanes:
            owner = next((process_participant.get(p.get("id")) for p in processes if lanes[lane_id] in list(p.iter())), None)
            if lane_id in shapes and owner in shapes and not _inside(shapes[lane_id], shapes[owner]):
                report.error("DI_OUTSIDE_POOL", f"{_label(lanes[lane_id])} liegt nicht innerhalb ihres Pools.", lane_id)
        pool_ids = [p for p in participants if p in shapes]
        for i, first in enumerate(pool_ids):
            for second in pool_ids[i + 1:]:
                if _overlap(shapes[first], shapes[second]):
                    report.warning("DI_OVERLAP", f"Die Pools „{first}“ und „{second}“ überlappen.", first)

        # Überlappende Elemente
        placed = [nid for nid in nodes if nid in shapes]
        for i, first in enumerate(placed):
            for second in placed[i + 1:]:
                if _overlap(shapes[first], shapes[second]):
                    report.warning("DI_OVERLAP", f"{_label(nodes[first])} und {_label(nodes[second])} überlappen.", first)

        # Linien: Anfang/Ende am Rand, keine Linie durch fremde Elemente
        for fid, points in edges.items():
            flow = sequence_flows[fid] if fid in sequence_flows else message_flows.get(fid)
            if flow is None:
                continue
            source, target = flow.get("sourceRef"), flow.get("targetRef")
            for end, ref, point in (("Anfang", source, points[0]), ("Ende", target, points[-1])):
                if ref in shapes and _distance_to_border(point, shapes[ref]) > EDGE_END_TOLERANCE:
                    report.warning("DI_EDGE_ENDPOINT", f"{_label(flow)}: {end} der Linie liegt nicht am Rand von „{ref}“.", fid)
            for nid in placed:
                if nid in (source, target):
                    continue
                if any(_segment_hits_rect(points[k], points[k + 1], shapes[nid]) for k in range(len(points) - 1)):
                    report.warning("DI_EDGE_THROUGH_ELEMENT", f"{_label(flow)} verläuft durch {_label(nodes[nid])}.", fid)

    summary = {
        "pools": len(participants),
        "processes": len(processes),
        "flow_nodes": len(nodes),
        "sequence_flows": len(sequence_flows),
        "message_flows": len(message_flows),
        "shapes": len(shapes),
        "edges": len(edges),
    }
    if report.hidden["errors"]:
        report.errors.append({"code": "MORE_ERRORS", "message": f"… und {report.hidden['errors']} weitere Fehler."})
    if report.hidden["warnings"]:
        report.warnings.append({"code": "MORE_WARNINGS", "message": f"… und {report.hidden['warnings']} weitere Hinweise."})
    return {"errors": report.errors, "warnings": report.warnings, "summary": summary}


class Tools:
    def __init__(self):
        self.citation = False

    async def validate_bpmn_xml(
        self,
        xml_content: str,
        filename: str = "prozess.bpmn",
        create_file: bool = False,
        __user__: dict | None = None,
        __event_emitter__=None,
        __metadata__: dict | None = None,
    ) -> str:
        """
        Prüft vollständiges BPMN-XML: Wohlgeformtheit, Wurzelelement und
        Namensraum, eindeutige IDs, Referenzen, incoming/outgoing,
        Reihenfolge der Kindelemente, Pool-Regeln für Sequenz- und
        Nachrichtenflüsse, Erreichbarkeit, das Basisprofil sowie die
        Vollständigkeit und Plausibilität der Diagramminformationen (BPMN-DI).

        errors machen das Dokument ungültig und müssen korrigiert werden.
        warnings sind Hinweise; Abweichungen vom Basisprofil sind nur nach
        ausdrücklicher Freigabe durch den Anwender zulässig.

        Setze create_file nur dann auf True, wenn der Anwender ausdrücklich
        eine BPMN-Datei oder eine erneute Dateierstellung verlangt.

        Übergib ausschließlich XML, ohne umschließende Markdown-Codeblöcke.

        Bei create_file=True wird die geprüfte Datei in der
        Open-WebUI-Dateiverwaltung gespeichert und nach Möglichkeit
        als Anhang angezeigt. Bei errors wird keine Datei erstellt.

        Eine XSD-Validierung oder ein BIC-Importtest werden nicht durchgeführt.

        Wenn mehrere Dateien innerhalb derselben Antwort erzeugt werden,
        weise darauf hin, dass Open WebUI möglicherweise zunächst nur die
        letzte Datei anzeigt und ein Neuladen des Chats erforderlich ist.

        :param xml_content: Vollständiger BPMN-XML-Inhalt.
        :param filename: Gewünschter Dateiname mit oder ohne .bpmn-Endung.
        :param create_file: True für Speicherung und Dateianhang.
        :return: JSON-Status ohne die vollständigen Dateidaten.
        """
        result = {
            "ok": False,
            "validation_ok": False,
            "xml_well_formed": False,
            "bpmn_root_valid": False,
            "references_checked": False,
            "profile_checked": False,
            "diagram_checked": False,
            "xsd_validated": False,
            "bic_import_tested": False,
            "file_saved": False,
            "file_registered": False,
            "attachment_event_sent": False,
            "file": None,
            "errors": [],
            "warnings": [],
            "sha256": None,
        }

        # 1. Eingabe prüfen
        if not isinstance(xml_content, str) or not xml_content.strip():
            result["errors"].append(
                {
                    "code": "EMPTY_XML",
                    "message": "Es wurde kein XML-Inhalt übergeben.",
                }
            )
            return self._as_json(result)

        try:
            xml_bytes = xml_content.encode("utf-8")
        except UnicodeError as exc:
            result["errors"].append(
                {
                    "code": "XML_ENCODING_ERROR",
                    "message": "Der Inhalt lässt sich nicht als UTF-8 kodieren.",
                    "exception_type": type(exc).__name__,
                }
            )
            return self._as_json(result)

        if len(xml_bytes) > MAX_XML_BYTES:
            result["errors"].append(
                {
                    "code": "XML_TOO_LARGE",
                    "message": "Der XML-Inhalt überschreitet 10 MiB.",
                }
            )
            return self._as_json(result)

        result["sha256"] = hashlib.sha256(xml_bytes).hexdigest()

        # 2. XML sicher parsen; DTDs und Entitäten sind nicht zugelassen.
        try:
            root = await asyncio.to_thread(
                SafeET.fromstring,
                xml_bytes,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
        except Exception as exc:
            error = {
                "code": "XML_PARSE_ERROR",
                "message": (
                    "Das XML ist nicht wohlgeformt oder enthält "
                    "nicht zugelassene DTD-/Entitätsdefinitionen."
                ),
                "exception_type": type(exc).__name__,
            }
            # Zeile und Spalte helfen dem Modell beim Korrigieren.
            position = getattr(exc, "position", None)
            if position:
                error["line"], error["column"] = position
            detail = str(exc).strip()
            if detail:
                error["detail"] = detail[:300]
            result["errors"].append(error)
            return self._as_json(result)

        result["xml_well_formed"] = True
        result["root_element"] = root.tag
        result["element_count"] = sum(1 for _ in root.iter())

        if root.tag != BPMN_ROOT:
            result["errors"].append(
                {
                    "code": "INVALID_BPMN_ROOT",
                    "message": (
                        "Erwartet wird das Element 'definitions' im Namensraum "
                        f"'{BPMN_NAMESPACE}'."
                    ),
                }
            )
            return self._as_json(result)

        result["bpmn_root_valid"] = True

        # 3. Inhaltliche und grafische Prüfung
        try:
            checks = await asyncio.to_thread(check_bpmn, root)
        except Exception as exc:
            log.exception("BPMN-Prüfung fehlgeschlagen")
            result["errors"].append(
                {
                    "code": "CHECK_FAILED",
                    "message": "Die inhaltliche Prüfung ist mit einem internen Fehler abgebrochen.",
                    "exception_type": type(exc).__name__,
                }
            )
            return self._as_json(result)

        result["references_checked"] = True
        result["profile_checked"] = True
        result["diagram_checked"] = True
        result["errors"].extend(checks["errors"])
        result["warnings"].extend(checks["warnings"])
        result["summary"] = checks["summary"]

        if result["errors"]:
            result["message"] = (
                f"{len(checks['errors'])} Fehler gefunden. Das Dokument muss korrigiert und "
                "erneut geprüft werden. Keine XSD- oder BIC-Prüfung."
            )
            return self._as_json(result)

        result["validation_ok"] = True
        result["ok"] = True
        result["message"] = (
            "Keine Fehler gefunden: XML, Referenzen, incoming/outgoing, Pool-Regeln "
            "und Diagramminformationen sind in Ordnung"
            + (f"; {len(checks['warnings'])} Hinweis{'e' if len(checks['warnings']) != 1 else ''} beachten"
               if checks["warnings"] else "")
            + ". Keine XSD- oder BIC-Prüfung."
        )

        if not create_file:
            return self._as_json(result)

        # 4. Benutzerzuordnung prüfen
        user_id = self._get_user_value(__user__, "id")

        if not user_id:
            result["ok"] = False
            result["errors"].append(
                {
                    "code": "MISSING_USER",
                    "message": (
                        "Die Datei konnte keinem angemeldeten Benutzer "
                        "zugeordnet werden."
                    ),
                }
            )
            return self._as_json(result)

        safe_filename = self._sanitize_filename(filename)
        file_id = str(uuid.uuid4())
        file_path = None
        stage = "initialization"

        # 5. Datei speichern und in Open WebUI registrieren
        try:
            from open_webui.models.files import FileForm, Files
            from open_webui.storage.provider import Storage

            metadata = {
                "generated_by": "bpmn_xml_export_tool",
                "format": "bpmn",
            }

            chat_id = self._get_chat_id(__metadata__)
            if chat_id:
                metadata["chat_id"] = chat_id

            tags = {
                "OpenWebUI-User-Id": user_id,
                "OpenWebUI-File-Id": file_id,
                "OpenWebUI-Generated-By": "bpmn_xml_export_tool",
            }

            user_email = self._get_user_value(__user__, "email")
            user_name = self._get_user_value(__user__, "name")

            if user_email:
                tags["OpenWebUI-User-Email"] = user_email
            if user_name:
                tags["OpenWebUI-User-Name"] = user_name

            stage = "storage_upload"

            _contents, file_path = await self._call(
                Storage.upload_file,
                io.BytesIO(xml_bytes),
                f"{file_id}_{safe_filename}",
                tags,
            )

            if not file_path:
                raise RuntimeError(
                    "Der Speicherprovider hat keinen Dateipfad zurückgegeben."
                )

            result["file_saved"] = True

            stage = "file_form"

            file_form = FileForm(
                id=file_id,
                filename=safe_filename,
                path=file_path,
                data={},
                meta={
                    "name": safe_filename,
                    "content_type": BPMN_CONTENT_TYPE,
                    "size": len(xml_bytes),
                    "file_hash": result["sha256"],
                    "data": metadata,
                },
            )

            stage = "database_registration"

            file_record = await self._call(
                Files.insert_new_file,
                user_id,
                file_form,
            )

            if not file_record:
                raise RuntimeError("Open WebUI konnte keinen Dateieintrag anlegen.")

            result["file_registered"] = True
            result["file"] = {
                "id": file_id,
                "name": safe_filename,
                "url": (f"/api/v1/files/{file_id}/content?attachment=true"),
                "size": len(xml_bytes),
                "content_type": BPMN_CONTENT_TYPE,
            }

        except Exception as exc:
            log.exception(
                "BPMN-Dateierstellung fehlgeschlagen; Phase=%s; Datei-ID=%s",
                stage,
                file_id,
            )

            # Eine bereits hochgeladene, aber nicht registrierte Datei
            # nach Möglichkeit wieder entfernen.
            if file_path and not result["file_registered"]:
                try:
                    from open_webui.storage.provider import Storage

                    await self._call(Storage.delete_file, file_path)
                    result["file_saved"] = False
                except Exception:
                    log.exception(
                        "Nicht registrierte BPMN-Datei konnte nicht "
                        "entfernt werden; Datei-ID=%s",
                        file_id,
                    )
                    result["warnings"].append(
                        {
                            "code": "CLEANUP_FAILED",
                            "message": (
                                "Eine möglicherweise verwaiste Datei konnte "
                                "nicht entfernt werden. Serverprotokoll prüfen."
                            ),
                        }
                    )

            result["ok"] = False
            result["message"] = (
                "Die XML-Prüfung war erfolgreich, "
                "aber die Dateierstellung ist fehlgeschlagen."
            )
            result["errors"].append(
                {
                    "code": "FILE_CREATION_ERROR",
                    "message": (
                        "Die Dateierstellung ist fehlgeschlagen. "
                        "Bitte Serverprotokoll prüfen."
                    ),
                    "stage": stage,
                    "exception_type": type(exc).__name__,
                }
            )
            return self._as_json(result)

        # 6. Anhang anzeigen: Ein Fehler hier verwirft die Datei nicht.
        if __event_emitter__ is not None:
            try:
                await self._call(
                    __event_emitter__,
                    {
                        "type": "files",
                        "data": {
                            "files": [
                                {
                                    "type": "file",
                                    **result["file"],
                                }
                            ]
                        },
                    },
                )
                result["attachment_event_sent"] = True

            except Exception as exc:
                log.exception(
                    "BPMN-Anhang konnte nicht gesendet werden; Datei-ID=%s",
                    file_id,
                )
                result["warnings"].append(
                    {
                        "code": "ATTACHMENT_EVENT_ERROR",
                        "message": (
                            "Die Datei wurde gespeichert und registriert, "
                            "aber das Anhangsereignis konnte nicht gesendet werden."
                        ),
                        "exception_type": type(exc).__name__,
                    }
                )
        else:
            result["warnings"].append(
                {
                    "code": "NO_EVENT_EMITTER",
                    "message": (
                        "Die Datei wurde gespeichert und registriert. "
                        "Es steht kein Event-Emitter für den Dateianhang bereit."
                    ),
                }
            )

        result["message"] = (
            f"Die BPMN-Datei „{safe_filename}“ wurde gespeichert " "und registriert."
        )

        if result["attachment_event_sent"]:
            result["message"] += " Das Anhangsereignis wurde gesendet."

        return self._as_json(result)

    async def _call(self, function, *args, **kwargs) -> Any:
        """
        Unterstützt synchrone und asynchrone Funktionen.

        Synchrone Funktionen laufen in einem Worker-Thread.
        Falls eine solche Funktion ein Awaitable zurückgibt,
        wird dieses anschließend ebenfalls abgewartet.
        """
        if inspect.iscoroutinefunction(function):
            return await function(*args, **kwargs)

        value = await asyncio.to_thread(function, *args, **kwargs)

        if inspect.isawaitable(value):
            return await value

        return value

    def _sanitize_filename(self, filename: str) -> str:
        candidate = unicodedata.normalize(
            "NFKC",
            str(filename or "prozess"),
        )

        candidate = re.sub(r"[\x00-\x1f\x7f]", "", candidate)
        candidate = candidate.replace("/", "-").replace("\\", "-")
        candidate = re.sub(r'[<>:"|?*]', "-", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip(" .-")

        if candidate.lower().endswith(".bpmn"):
            candidate = candidate[:-5].rstrip(" .-")

        candidate = candidate[:120].rstrip(" .-") or "prozess"
        return f"{candidate}.bpmn"

    def _get_user_value(self, user: dict | None, key: str) -> str:
        if not isinstance(user, dict):
            return ""

        value = user.get(key)
        return str(value).strip() if value is not None else ""

    def _get_chat_id(self, metadata: dict | None) -> str | None:
        if not isinstance(metadata, dict):
            return None

        for key in ("chat_id", "chatId"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        chat = metadata.get("chat")
        if isinstance(chat, dict):
            value = chat.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()

        return None

    def _as_json(self, result: dict) -> str:
        return json.dumps(result, ensure_ascii=False, indent=2)
