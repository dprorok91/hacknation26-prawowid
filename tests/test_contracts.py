import pytest
from pydantic import ValidationError

from prawdowid.api.schemas import AnalysisRequest, AnalysisResponse


def test_text_is_valid_input() -> None:
    result = AnalysisRequest(kind="text", content="ChatGPT Sol")
    assert result.language == "pl"


def test_bad_url_rejected_at_api_boundary() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(kind="url", content="file:///etc/passwd")


def test_missing_analysis_status_not_treated_as_verdict() -> None:
    response = AnalysisResponse(analysis_id="example", status="failed", error_code="timeout")
    assert response.report is None
