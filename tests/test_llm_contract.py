import json

import pytest

from biography.backends import LocalLLM
from biography.schemas import RoleResult


class ChatRecorder:
    def tokenize(self, data):
        return list(range(len(data) // 4))

    def create_chat_completion(self, **kwargs):
        self.request = kwargs
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"speakers":[]}'}}
            ]
        }


def test_schema_is_explained_to_model_and_not_only_enforced_by_grammar():
    llm = LocalLLM.__new__(LocalLLM)
    llm.context = 8192
    llm.sampling = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0}
    llm.model = ChatRecorder()
    result = llm("Определи роли", {"segments": []}, RoleResult)
    assert result.speakers == []
    request = llm.model.request
    assert "$defs" in request["messages"][0]["content"]
    assert "source_segment_ids" in request["messages"][0]["content"]
    assert json.loads(request["messages"][1]["content"]) == {"segments": []}
    assert request["response_format"]["schema"] == RoleResult.model_json_schema()
    assert all(request[k] == v for k, v in llm.sampling.items())


def test_schema_and_payload_are_included_in_context_budget():
    llm = LocalLLM.__new__(LocalLLM)
    llm.context = 2048
    llm.model = ChatRecorder()
    with pytest.raises(ValueError, match="Недостаточно контекста"):
        llm("Роли", {"oversized": "я" * 20000}, RoleResult)
