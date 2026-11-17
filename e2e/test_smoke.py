"""End-to-End-Test gegen eine laufende Open-WebUI-Instanz.

Erwartet eine Wegwerf-Instanz (siehe e2e/docker-compose.yml und e2e/run.sh).
NICHT gegen die produktive Instanz laufen lassen: Der Test spielt alle
Erweiterungen ein und legt einen Test-Chat an.

    OWUI_URL=http://localhost:3000 uv run pytest e2e

Als Basismodell dient die Pipe „echo“; sie gibt zurück, was beim Modell ankommt.
"""

import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["BPMN_BASISMODELL"] = "echo"

from deploy import deploy  # noqa: E402
from owui_lib import OpenWebUI, discover_extensions, discover_models, signin_without_auth  # noqa: E402

URL = os.environ.get("OWUI_URL", "http://localhost:3000").rstrip("/")
MODEL = "bic-bpmn-assistent-v2"
SAMPLE = (ROOT / "samples" / "reklamationsbearbeitung.bpmn").read_text(encoding="utf-8")


def wait_until_healthy(timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(URL + "/health", timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    pytest.fail(f"Open WebUI unter {URL} antwortet nicht.")


@pytest.fixture(scope="module")
def client():
    wait_until_healthy()
    key = os.environ.get("OWUI_API_KEY") or signin_without_auth(URL)
    return OpenWebUI(URL, key, timeout=120)


@pytest.fixture(scope="module")
def deployed(client):
    first = deploy(client, log=lambda *_: None)
    second = deploy(client, log=lambda *_: None)
    return first, second


def test_deploy_is_idempotent(deployed):
    first, second = deployed
    expected = {e.id for e in discover_extensions()} | {m.id for m in discover_models()}
    assert {ident for _, ident, _ in first} == expected
    assert all(outcome == "unverändert" for _, _, outcome in second), second


def test_extensions_are_registered(client, deployed):
    for ext in discover_extensions():
        if ext.is_function:
            state = client.get(f"/api/v1/functions/id/{ext.id}")
            assert state["type"] == ext.kind and state["is_active"], ext.id
        else:
            state = client.get(f"/api/v1/tools/id/{ext.id}")
            assert state["specs"], f"{ext.id}: Open WebUI hat keine Tool-Beschreibung erzeugt"
        assert state["meta"]["manifest"].get("version") == ext.frontmatter["version"]


def test_validation_tool_spec(client, deployed):
    tool = client.get("/api/v1/tools/id/validate_bpmn_xml")
    spec = next(s for s in tool["specs"] if s["name"] == "validate_bpmn_xml")
    assert spec["parameters"]["required"] == ["xml_content"]
    assert set(spec["parameters"]["properties"]) == {"xml_content", "filename", "create_file"}


def test_model_offers_preview_button(client, deployed):
    available = {m["id"]: m for m in client.get("/api/models")["data"]}
    assert MODEL in available
    assert "bpmn_vorschau" in {a["id"] for a in available[MODEL].get("actions", [])}


def test_chat_receives_system_prompt(client, deployed):
    answer = client.post("/api/chat/completions", {
        "model": MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": "Modelliere eine Reklamation"}],
    })
    text = answer["choices"][0]["message"]["content"]
    assert "ROLLE UND ZIEL" in text
    assert "Modelliere eine Reklamation" in text


def test_preview_action_stores_diagram_in_chat(client, deployed):
    user_id, answer_id = str(uuid.uuid4()), str(uuid.uuid4())
    messages = {
        user_id: {"id": user_id, "parentId": None, "childrenIds": [answer_id], "role": "user",
                  "content": "Bitte als BPMN", "timestamp": int(time.time())},
        answer_id: {"id": answer_id, "parentId": user_id, "childrenIds": [], "role": "assistant",
                    "content": f"```xml\n{SAMPLE}\n```", "model": MODEL, "done": True,
                    "timestamp": int(time.time())},
    }
    chat = client.post("/api/v1/chats/new", {"chat": {
        "title": "E2E-Test", "models": [MODEL],
        "history": {"messages": messages, "currentId": answer_id},
        "messages": list(messages.values()),
    }})
    client.post("/api/chat/actions/bpmn_vorschau", {
        "model": MODEL,
        "messages": [{"id": m["id"], "role": m["role"], "content": m["content"]} for m in messages.values()],
        "chat_id": chat["id"],
        "id": answer_id,
        "session_id": "e2e",
    })
    stored = client.get(f"/api/v1/chats/{chat['id']}")
    embeds = stored["chat"]["history"]["messages"][answer_id].get("embeds") or []
    assert len(embeds) == 1
    assert "BpmnJS" in embeds[0] and "Kundenservice" in embeds[0]
    client.request("DELETE", f"/api/v1/chats/{chat['id']}")
