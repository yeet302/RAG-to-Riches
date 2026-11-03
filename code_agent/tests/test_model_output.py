"""Strict JSON handoff, bounded tool execution, and safe failure diagnostics."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from code_agent.agent import CodeAgent, CodeModelResult, CodeModelOutputError, MODEL_FORMAT, parse_model_result, final_output_text


def payload(**changes):
    result = dict(summary="Verified source match", hypotheses=[], confidence=0.5,
                  evidence=[dict(path="ad.java", commit="abc123", start_line=1, end_line=2,
                                 summary="Verified GetAds implementation")],
                  upstream_refs_used=[], limitations=[])
    result.update(changes)
    return result


def agent_with_output(monkeypatch, raw, verified=None):
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(project_endpoint="https://foundry.invalid", search_endpoint="https://search.invalid")
    agent.retriever = SimpleNamespace(url=lambda *args: "https://example.invalid/source")
    monkeypatch.setattr(agent, "_investigate", lambda *args: (raw, verified or {}, ["source"], {}))
    return agent


@pytest.mark.parametrize("raw,reason", [
    ("", "empty_output"), (None, "empty_output"),
    ("Here is the result: {}", "invalid_json"), ('{"summary":', "invalid_json"),
    ("[]", "schema_mismatch"), ("{}", "schema_mismatch"),
    (json.dumps(payload(confidence=5)), "schema_mismatch"),
    (json.dumps(payload(evidence="not a list")), "schema_mismatch"),
])
def test_bad_outputs_have_specific_safe_handoffs(monkeypatch, caplog, raw, reason):
    agent = agent_with_output(monkeypatch, raw)
    finding = asyncio.run(agent.run("GetAds", {"request_id": "parse-check"}))
    assert finding.status == "error" and not finding.evidence
    assert finding.error.code == "CODE_MODEL_RESPONSE_FAILED"
    assert finding.error.stage == "validation"
    assert f"model_output_reason={reason}" in caplog.text
    if raw:
        assert raw not in caplog.text


@pytest.mark.parametrize("fenced", [True, False])
def test_valid_output_keeps_only_source_ranges_actually_read(monkeypatch, fenced):
    result = payload()
    result["evidence"].append(dict(path="invented.java", commit="abc123", start_line=1, end_line=2,
                                   summary="Unverified source"))
    raw = json.dumps(result)
    if fenced:
        raw = "```json\n" + raw + "\n```"
    agent = agent_with_output(monkeypatch, raw, {("ad.java", "abc123"): {1, 2}})
    finding = asyncio.run(agent.run("GetAds", {"request_id": "valid-check"}))
    assert finding.status == "ok" and len(finding.evidence) == 1
    assert finding.evidence[0].ref == "ad.java@abc123:L1-L2"
    assert "invented" not in finding.model_dump_json()


def test_no_verified_source_never_becomes_success(monkeypatch):
    agent = agent_with_output(monkeypatch, json.dumps(payload()))
    finding = asyncio.run(agent.run("GetAds", {"request_id": "no-source"}))
    assert finding.status == "no_evidence" and not finding.evidence and not finding.hypotheses


def test_model_rounds_use_agent_format_without_forbidden_request_overrides(monkeypatch):
    requests = []
    references = []
    responses = [
        SimpleNamespace(output=[SimpleNamespace(type="function_call", name="read_source", call_id="read-1",
            arguments=json.dumps({"path":"ad.java", "ref":"untrusted-branch"}))], output_text="", status="completed"),
        SimpleNamespace(output=[], output_text=json.dumps(payload()), status="completed"),
    ]
    def create(**kwargs):
        requests.append(kwargs)
        return responses.pop(0)
    def read_source(**kwargs):
        references.append(kwargs["ref"])
        return {"path":"ad.java", "commit":"abc123", "lines":[{"line":1},{"line":2}]}
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(repo="approved/repo")
    agent.retriever = SimpleNamespace(resolve_commit=lambda: "abc123", read_source=read_source)
    agent.client = SimpleNamespace(conversations=SimpleNamespace(create=lambda: SimpleNamespace(id="conv")),
                                   responses=SimpleNamespace(create=create))
    monkeypatch.setattr(agent, "_agent_reference", lambda: {"agent_reference":{"name":"test"}})
    raw, verified, _, _ = agent._investigate("GetAds", {})
    assert len(requests) == 2 and all("text" not in request for request in requests)
    assert all("agent_reference" in request["extra_body"] for request in requests)
    assert references == ["abc123"] and verified == {("ad.java","abc123"): {1,2}}
    assert parse_model_result(raw)["summary"] == "Verified source match"


def test_agent_definition_sets_strict_schema_once():
    definitions = []
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(agent_name="test", model_deployment="deployment")
    def create_version(**kwargs):
        definitions.append(kwargs["definition"])
        return SimpleNamespace(name="test", version="7")
    agent.project = SimpleNamespace(agents=SimpleNamespace(create_version=create_version))
    agent._version = None
    reference = agent._agent_reference()
    agent._agent_reference()
    assert reference["agent_reference"]["version"] == "7"
    assert len(definitions) == 1
    serialized = definitions[0].as_dict()
    assert serialized["text"]["format"]["type"] == "json_schema"
    assert serialized["text"]["format"]["strict"] is True


@pytest.mark.parametrize("status,output,reason", [
    ("incomplete", [], "incomplete_response"),
    ("completed", [SimpleNamespace(type="function_call",name="unknown",arguments="{}",call_id="x")], "unfinished_tool_calls"),
])
def test_incomplete_or_unfinished_response_is_not_parsed_as_answer(monkeypatch, status, output, reason):
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(repo="approved/repo")
    agent.retriever = SimpleNamespace(resolve_commit=lambda: "abc123")
    agent.client = SimpleNamespace(conversations=SimpleNamespace(create=lambda: SimpleNamespace(id="conv")),
        responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(status=status,output=output,output_text="")))
    monkeypatch.setattr(agent, "_agent_reference", lambda: {})
    with pytest.raises(CodeModelOutputError, match=reason):
        agent._investigate("GetAds", {})


def test_final_message_is_parsed_instead_of_concatenated_intermediate_json():
    first = json.dumps(payload(summary="Intermediate analysis"))
    final = json.dumps(payload(summary="Final grounded result"))
    messages = [SimpleNamespace(type="message",role="assistant",status="completed",
        content=[SimpleNamespace(type="output_text",text=text)]) for text in [first,final]]
    response = SimpleNamespace(output=messages,output_text=first + final)
    assert parse_model_result(final_output_text(response))["summary"] == "Final grounded result"


def test_final_message_refusal_is_not_treated_as_json():
    response = SimpleNamespace(output=[SimpleNamespace(type="message",role="assistant",status="completed",
        content=[SimpleNamespace(type="refusal",refusal="private refusal body")])])
    with pytest.raises(CodeModelOutputError, match="refusal"):
        final_output_text(response)
