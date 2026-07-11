from collections.abc import Mapping
from copy import deepcopy
import json
import re
from pathlib import Path

import pytest

from app.schemas import ValuationResponse


_DOC_PATH = Path(__file__).resolve().parents[1] / "API_DOCUMENTATION.md"
_ILLUSTRATIVE_RESPONSE_PATTERN = re.compile(
    r"^#### Illustrative response: (?P<label>.+?)\n.*?^```json\n"
    r"(?P<payload>.*?)^```",
    re.MULTILINE | re.DOTALL,
)


def _strictly_validate_payload(payload: object, *, path: str = "response") -> None:
    validated = ValuationResponse.model_validate(payload)
    _assert_input_keys_are_serialized(
        payload,
        validated.model_dump(mode="json"),
        path=path,
    )


def _assert_input_keys_are_serialized(
    payload: object, serialized: object, *, path: str
) -> None:
    if isinstance(payload, Mapping):
        assert isinstance(serialized, Mapping), (
            f"{path} must serialize as an object"
        )
        unexpected_keys = payload.keys() - serialized.keys()
        assert not unexpected_keys, (
            f"{path} contains undeclared keys: {sorted(unexpected_keys)}"
        )
        for key, value in payload.items():
            _assert_input_keys_are_serialized(
                value,
                serialized[key],
                path=f"{path}.{key}",
            )
    elif isinstance(payload, list):
        assert isinstance(serialized, list), f"{path} must serialize as an array"
        assert len(payload) == len(serialized), f"{path} array length changed"
        for index, value in enumerate(payload):
            _assert_input_keys_are_serialized(
                value,
                serialized[index],
                path=f"{path}[{index}]",
            )


def test_illustrative_valuation_responses_match_the_public_schema() -> None:
    document = _DOC_PATH.read_text(encoding="utf-8")
    examples = list(_ILLUSTRATIVE_RESPONSE_PATTERN.finditer(document))

    assert [example["label"] for example in examples] == [
        "SGX bank",
        "ordinary U.S. company",
        "ordinary SGX company",
        "SGX REIT",
    ]

    for example in examples:
        _strictly_validate_payload(
            json.loads(example["payload"]),
            path=f"illustrative response {example['label']}",
        )


def test_documentation_validation_rejects_an_injected_nested_extra() -> None:
    document = _DOC_PATH.read_text(encoding="utf-8")
    example = next(_ILLUSTRATIVE_RESPONSE_PATTERN.finditer(document))
    payload = deepcopy(json.loads(example["payload"]))
    payload["intrinsic_value"]["injected_extra"] = True

    with pytest.raises(AssertionError, match="intrinsic_value.*injected_extra"):
        _strictly_validate_payload(payload)
