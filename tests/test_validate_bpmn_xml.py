import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALID = (ROOT / "samples" / "reklamationsbearbeitung.bpmn").read_text(encoding="utf-8")
WITH_LANES = (ROOT / "samples" / "rechnungsfreigabe.bpmn").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location(
    "validate_bpmn_xml", ROOT / "tools" / "validate_bpmn_xml.py"
)
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


def validate(xml, **kwargs):
    return json.loads(asyncio.run(validator.Tools().validate_bpmn_xml(xml, **kwargs)))


def codes(result, key="errors"):
    return {entry["code"] for entry in result[key]}


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def drop_di(text, element_id):
    pattern = rf'\s*<bpmndi:BPMN(Shape|Edge) id="{element_id}_di".*?</bpmndi:BPMN(Shape|Edge)>'
    result, count = re.subn(pattern, "", text, count=1, flags=re.DOTALL)
    assert count == 1
    return result


# --- gültige Beispiele ------------------------------------------------------------

def test_reference_sample_is_clean():
    result = validate(VALID)
    assert result["ok"] and result["validation_ok"]
    assert result["errors"] == [] and result["warnings"] == []
    assert result["references_checked"] and result["diagram_checked"] and result["profile_checked"]
    assert result["summary"]["pools"] == 2
    assert result["summary"]["message_flows"] == 5


def test_lanes_are_only_a_profile_warning():
    result = validate(WITH_LANES)
    assert result["validation_ok"]
    assert codes(result, "warnings") == {"NOT_IN_PROFILE"}
    assert "1 Hinweis beachten" in result["message"]


# --- Syntax ------------------------------------------------------------------------

def test_parse_error_reports_position():
    result = validate(VALID.replace("</bpmn:task>", "</bpmn:tsk>", 1))
    assert not result["validation_ok"]
    error = result["errors"][0]
    assert error["code"] == "XML_PARSE_ERROR"
    assert error["line"] > 1 and "column" in error


def test_wrong_root():
    result = validate('<definitions xmlns="urn:x"/>')
    assert codes(result) == {"INVALID_BPMN_ROOT"}


# --- Referenzen und incoming/outgoing ----------------------------------------------

def test_duplicate_id():
    xml = replace_once(VALID, '<bpmn:task id="A4"', '<bpmn:task id="A1"')
    assert "DUPLICATE_ID" in codes(validate(xml))


def test_unresolved_target():
    xml = replace_once(VALID, 'sourceRef="A4" targetRef="G3"', 'sourceRef="A4" targetRef="G9"')
    result = validate(xml)
    assert "UNRESOLVED_REFERENCE" in codes(result)
    assert any("G9" in e["message"] for e in result["errors"])


def test_missing_outgoing_entry():
    xml = replace_once(VALID, "<bpmn:outgoing>F12</bpmn:outgoing>\n", "")
    result = validate(xml)
    assert codes(result) == {"INCOMING_OUTGOING_MISMATCH"}
    assert result["errors"][0]["element_id"] == "A4"


def test_message_flow_in_incoming():
    xml = replace_once(VALID, "<bpmn:incoming>F7</bpmn:incoming>",
                       "<bpmn:incoming>F7</bpmn:incoming>\n      <bpmn:incoming>MF3</bpmn:incoming>")
    assert "MESSAGE_FLOW_IN_SEQUENCE_REFS" in codes(validate(xml))


def test_child_order_event_definition_before_incoming():
    block = re.search(r'<bpmn:intermediateCatchEvent id="E1".*?</bpmn:intermediateCatchEvent>', VALID, re.DOTALL).group(0)
    definition = re.search(r"\s*<bpmn:messageEventDefinition[^>]*/>", block).group(0)
    reordered = block.replace(definition, "").replace(">\n", ">" + definition + "\n", 1)
    assert reordered != block
    assert "CHILD_ORDER" in codes(validate(VALID.replace(block, reordered)))


def test_invalid_default_flow():
    xml = replace_once(VALID, '<bpmn:exclusiveGateway id="G2"', '<bpmn:exclusiveGateway id="G2" default="F4"')
    assert "INVALID_DEFAULT" in codes(validate(xml))


def test_message_flow_inside_one_pool():
    xml = replace_once(VALID, 'sourceRef="A2" targetRef="P_Ext"', 'sourceRef="A2" targetRef="S1"')
    assert "MESSAGE_FLOW_SAME_POOL" in codes(validate(xml))


def test_sequence_flow_between_pools():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="D" targetNamespace="urn:t">
  <bpmn:collaboration id="C">
    <bpmn:participant id="PA" processRef="P1" />
    <bpmn:participant id="PB" processRef="P2" />
  </bpmn:collaboration>
  <bpmn:process id="P1" isExecutable="false">
    <bpmn:startEvent id="S"><bpmn:outgoing>F</bpmn:outgoing></bpmn:startEvent>
  </bpmn:process>
  <bpmn:process id="P2" isExecutable="false">
    <bpmn:endEvent id="E"><bpmn:incoming>F</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="F" sourceRef="S" targetRef="E" />
  </bpmn:process>
</bpmn:definitions>"""
    result = validate(xml)
    assert "SEQUENCE_FLOW_CROSSES_POOL" in codes(result)
    assert "NO_DIAGRAM" in codes(result)


def test_start_with_incoming_and_unreachable_node():
    xml = replace_once(VALID, 'sourceRef="E1" targetRef="G0"', 'sourceRef="E1" targetRef="S1"')
    xml = replace_once(xml, "<bpmn:incoming>F8</bpmn:incoming>\n", "")
    xml = replace_once(xml, '<bpmn:outgoing>F1</bpmn:outgoing>',
                       '<bpmn:incoming>F8</bpmn:incoming>\n      <bpmn:outgoing>F1</bpmn:outgoing>')
    result = validate(xml)
    assert "START_WITH_INCOMING" in codes(result)


# --- Basisprofil --------------------------------------------------------------------

def test_parallel_gateway_and_extensions_are_warnings():
    xml = VALID.replace("bpmn:exclusiveGateway id=\"G3\"", "bpmn:parallelGateway id=\"G3\"")
    xml = xml.replace("</bpmn:exclusiveGateway>\n    <bpmn:task id=\"A5\"", "</bpmn:parallelGateway>\n    <bpmn:task id=\"A5\"")
    xml = replace_once(xml, 'xmlns:di="http://www.omg.org/spec/DD/20100524/DI"',
                       'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:x="urn:vendor"')
    xml = replace_once(xml, '<bpmn:task id="A1"', '<bpmn:task id="A1" x:prio="1"')
    result = validate(xml)
    assert result["validation_ok"], result["errors"]
    assert {"NOT_IN_PROFILE", "EXTENSION_NAMESPACE"} <= codes(result, "warnings")


def test_unlabelled_decision_paths():
    xml = replace_once(VALID, ' name="Ja" sourceRef="G3"', ' sourceRef="G3"')
    result = validate(xml)
    assert result["validation_ok"]
    assert "UNNAMED_PATH" in codes(result, "warnings")


def test_duplicate_message_flow_label():
    xml = replace_once(VALID, 'sourceRef="P_Ext" targetRef="S1"', 'sourceRef="P_Ext" targetRef="S1" messageRef="Msg_Reklamation"')
    assert "DUPLICATE_MESSAGE_FLOW_LABEL" in codes(validate(xml), "warnings")


# --- Diagramminformationen ------------------------------------------------------------

def test_missing_shape_and_edge():
    xml = drop_di(drop_di(VALID, "A4"), "MF2")
    result = validate(xml)
    assert {"DI_MISSING_SHAPE", "DI_MISSING_EDGE"} <= codes(result)


def test_edge_needs_two_waypoints():
    xml = replace_once(VALID, '<di:waypoint x="1270" y="180" />\n', "")
    xml = re.sub(r'(<bpmndi:BPMNEdge id="F12_di" bpmnElement="F12">\s*)<di:waypoint[^>]*/>', r"\1", xml)
    assert "DI_WAYPOINT" in codes(validate(xml))


def test_element_outside_pool():
    xml = replace_once(VALID, '<dc:Bounds x="100" y="60" width="1720" height="520" />',
                       '<dc:Bounds x="100" y="60" width="1500" height="520" />')
    result = validate(xml)
    assert "DI_OUTSIDE_POOL" in codes(result)
    assert {e["element_id"] for e in result["errors"]} >= {"Z1", "Z2"}


def test_plane_must_reference_collaboration():
    xml = replace_once(VALID, 'bpmnElement="Collaboration_1"', 'bpmnElement="Process_Reklamation"')
    assert "PLANE_REFERENCE" in codes(validate(xml))


def test_edge_through_other_element_and_detached_end():
    edge = re.search(r'<bpmndi:BPMNEdge id="F3_di".*?</bpmndi:BPMNEdge>', VALID, re.DOTALL).group(0)
    rerouted = ('<bpmndi:BPMNEdge id="F3_di" bpmnElement="F3">\n'
                '        <di:waypoint x="530" y="180" />\n'
                '        <di:waypoint x="560" y="180" />\n'
                '        <di:waypoint x="560" y="340" />\n'
                '        <di:waypoint x="900" y="340" />\n'
                '      </bpmndi:BPMNEdge>')
    result = validate(VALID.replace(edge, rerouted))
    assert result["validation_ok"]
    assert {"DI_EDGE_THROUGH_ELEMENT", "DI_EDGE_ENDPOINT"} <= codes(result, "warnings")


def test_overlapping_elements():
    xml = replace_once(VALID, '<dc:Bounds x="1470" y="130" width="180" height="100" />',
                       '<dc:Bounds x="1330" y="130" width="180" height="100" />')
    assert "DI_OVERLAP" in codes(validate(xml), "warnings")


# --- Begrenzung und Dateierstellung ------------------------------------------------

def test_long_error_lists_are_capped():
    flows = "\n".join(f'<bpmn:sequenceFlow id="F{i}" sourceRef="X{i}" targetRef="Y{i}" />' for i in range(60))
    xml = (f'<bpmn:definitions xmlns:bpmn="{validator.BPMN_NAMESPACE}" id="D" targetNamespace="urn:t">'
           f'<bpmn:process id="P" isExecutable="false">{flows}</bpmn:process></bpmn:definitions>')
    result = validate(xml)
    assert len(result["errors"]) == validator.MAX_REPORTED + 1
    assert result["errors"][-1]["code"] == "MORE_ERRORS"


def test_no_file_when_errors():
    result = validate(drop_di(VALID, "A4"), create_file=True, __user__={"id": "u1"})
    assert not result["ok"] and not result["file_saved"]


def test_filename_sanitizing():
    tools = validator.Tools()
    assert tools._sanitize_filename('Reklamation: "neu".bpmn') == "Reklamation- -neu.bpmn"
    assert tools._sanitize_filename("") == "prozess.bpmn"


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input(text):
    assert codes(validate(text)) == {"EMPTY_XML"}
