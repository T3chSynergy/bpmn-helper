import asyncio
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = (ROOT / "samples" / "rechnungsfreigabe.bpmn").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location(
    "bpmn_vorschau", ROOT / "openwebui" / "bpmn_vorschau.py"
)
bpmn_vorschau = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpmn_vorschau)


def run_action(body, valves=None):
    events = []

    async def emitter(event):
        events.append(event)

    action = bpmn_vorschau.Action()
    for key, value in (valves or {}).items():
        setattr(action.valves, key, value)
    result = asyncio.run(action.action(body, __event_emitter__=emitter))
    return result, events


def body_with(content, message_id="m2"):
    return {
        "id": message_id,
        "chat_id": "c1",
        "model": "bpmn",
        "messages": [
            {"id": "m1", "role": "user", "content": "Beschreibe den Prozess"},
            {"id": message_id, "role": "assistant", "content": content},
        ],
    }


def payload_of(page):
    raw = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', page, re.DOTALL
    ).group(1)
    return json.loads(raw)


def test_extracts_fenced_xml_with_declaration():
    text = f"Hier ist der Prozess:\n\n```xml\n{SAMPLE}\n```\n\nPasst das so?"
    xml, status = bpmn_vorschau.extract_bpmn_xml(text)
    assert status == "ok"
    assert xml.startswith("<?xml")
    assert xml.endswith("</bpmn:definitions>")
    assert xml == SAMPLE.strip()


def test_extracts_unprefixed_and_other_prefixes():
    plain = '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p"/></definitions>'
    assert bpmn_vorschau.extract_bpmn_xml(f"Text {plain} Ende") == (plain, "ok")
    other = '<bpmn2:definitions id="d"><bpmn2:process id="p"/></bpmn2:definitions>'
    assert bpmn_vorschau.extract_bpmn_xml(other) == (other, "ok")


def test_takes_last_complete_document():
    first = '<definitions id="alt"></definitions>'
    second = '<definitions id="neu"></definitions>'
    xml, status = bpmn_vorschau.extract_bpmn_xml(f"{first}\nKorrektur:\n{second}")
    assert (xml, status) == (second, "ok")


def test_reports_truncated_and_missing():
    assert bpmn_vorschau.extract_bpmn_xml('<bpmn:definitions id="d"><bpmn:process') == (
        None,
        "truncated",
    )
    assert bpmn_vorschau.extract_bpmn_xml("Gern, was soll ich ändern?") == (None, "none")
    assert bpmn_vorschau.extract_bpmn_xml("") == (None, "none")


def test_uses_clicked_message_not_last():
    body = body_with("kein Diagramm hier", message_id="m2")
    body["messages"].append(
        {"id": "m3", "role": "assistant", "content": '<definitions id="x"></definitions>'}
    )
    assert bpmn_vorschau.message_text(body) == "kein Diagramm hier"


def test_action_emits_replacing_embed_with_unchanged_xml():
    result, events = run_action(body_with(f"```xml\n{SAMPLE}\n```"))
    assert result is None
    assert [e["type"] for e in events] == ["embeds"]
    data = events[0]["data"]
    assert data["replace"] is True
    assert len(data["embeds"]) == 1
    page = data["embeds"][0]
    assert payload_of(page)["xml"] == SAMPLE.strip()
    assert f'<script src="{bpmn_vorschau.DEFAULT_BPMN_JS_URL}"></script>' in page
    assert "height: 480px" in page
    assert "__" not in re.sub(r'<script id="payload".*?</script>', "", page, flags=re.DOTALL)


def test_payload_cannot_break_out_of_script_block():
    evil = '<definitions name="</script><script>alert(1)</script>"></definitions>'
    _, events = run_action(body_with(evil))
    page = events[0]["data"]["embeds"][0]
    assert "<script>alert(1)" not in page
    assert payload_of(page)["xml"] == evil


def test_action_notifies_when_nothing_to_show():
    _, events = run_action(body_with("Gern, was soll ich ändern?"))
    assert [(e["type"], e["data"]["type"]) for e in events] == [("notification", "info")]
    _, events = run_action(body_with('<bpmn:definitions id="d"><bpmn:process'))
    assert [(e["type"], e["data"]["type"]) for e in events] == [("notification", "warning")]


def test_inline_bundle_from_file(tmp_path):
    bundle = tmp_path / "bpmn.js"
    bundle.write_text('var BpmnJS = function () {}; var s = "</script>";', encoding="utf-8")
    _, events = run_action(
        body_with(SAMPLE), {"bpmn_js_inline": True, "bpmn_js_url": str(bundle)}
    )
    page = events[0]["data"]["embeds"][0]
    assert 'var s = "<\\/script>";' in page
    assert "<script src=" not in page


def test_inline_bundle_falls_back_when_unreadable(tmp_path):
    _, events = run_action(
        body_with(SAMPLE),
        {"bpmn_js_inline": True, "bpmn_js_url": str(tmp_path / "fehlt.js")},
    )
    assert [e["type"] for e in events] == ["notification", "embeds"]
    assert events[0]["data"]["type"] == "warning"
    page = events[1]["data"]["embeds"][0]
    assert f'<script src="{bpmn_vorschau.DEFAULT_BPMN_JS_URL}"></script>' in page


def test_vendored_bundle_matches_checksum_and_embeds():
    import hashlib

    folder = ROOT / "vendor" / "bpmn-js" / "18.33.0"
    bundle = folder / "bpmn-navigated-viewer.production.min.js"
    expected = (folder / "SHA256SUMS").read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == expected
    assert "18.33.0" in bpmn_vorschau.DEFAULT_BPMN_JS_URL

    _, events = run_action(
        body_with(SAMPLE), {"bpmn_js_inline": True, "bpmn_js_url": str(bundle)}
    )
    page = events[-1]["data"]["embeds"][0]
    assert "bpmn-navigated-viewer v18.33.0" in page
    assert "<script src=" not in page
