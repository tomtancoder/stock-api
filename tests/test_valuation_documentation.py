import json
import re
from pathlib import Path

from app.schemas import ValuationResponse


_DOC_PATH = Path(__file__).resolve().parents[1] / "API_DOCUMENTATION.md"
_ILLUSTRATIVE_RESPONSE_PATTERN = re.compile(
    r"^#### Illustrative response: (?P<label>.+?)\n.*?^```json\n"
    r"(?P<payload>.*?)^```",
    re.MULTILINE | re.DOTALL,
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
        ValuationResponse.model_validate(json.loads(example["payload"]))
