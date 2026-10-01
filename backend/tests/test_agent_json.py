import json

import pytest
from pydantic import ValidationError

from app.agent.service import _validated_json
from app.contracts.spec import AnalysisSpec


def test_tool_json_accepts_only_complete_object_or_stray_closing_brace():
    spec = {"base_dataset": "orders", "metrics": [{"name": "orders", "agg": "count"}]}
    raw = json.dumps(spec)
    assert _validated_json(AnalysisSpec, raw).base_dataset == "orders"
    assert _validated_json(AnalysisSpec, raw + " }").metrics[0].name == "orders"
    for malformed in (raw + " trailing text", raw[:-1] + ",}", raw + " } SELECT 1"):
        with pytest.raises((ValidationError, ValueError)):
            _validated_json(AnalysisSpec, malformed)
    with pytest.raises(ValidationError):
        _validated_json(AnalysisSpec, json.dumps({**spec, "sql": "SELECT 1"}) + " }")
