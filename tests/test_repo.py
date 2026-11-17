"""Repo-weite Prüfungen: Konventionen und Modell-Konfiguration."""

import pytest

import check
import owui_lib


def test_repository_passes_check():
    errors, warnings = check.check()
    assert errors == []
    assert warnings == []


def test_model_needs_base_model_from_environment(monkeypatch):
    [model] = [m for m in owui_lib.discover_models() if m.id == "bic-bpmn-assistent-v2"]
    monkeypatch.delenv("BPMN_BASISMODELL", raising=False)
    with pytest.raises(KeyError):
        owui_lib.build_model_form(model)
    monkeypatch.setenv("BPMN_BASISMODELL", "mein-modell")
    form = owui_lib.build_model_form(model)
    assert form["base_model_id"] == "mein-modell"
    assert form["params"]["system"].startswith("ROLLE UND ZIEL")
    assert form["meta"]["toolIds"] == ["validate_bpmn_xml"]
    assert form["meta"]["actionIds"] == ["bpmn_vorschau"]
