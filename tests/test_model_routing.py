from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prawdowid.application.analyses import AnalysisError, ErrorCode, Submission
from prawdowid.infrastructure.ingestion import IngestedArticle
from prawdowid.worker import select_analysis_model


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "content", "allowed", "canonical", "remote"), [
    ("text", "To jest prywatna wiadomość.", True, None, False),
    ("url", "https://example.org/article", False, None, False),
    ("url", "https://example.org/article?user=42", True, "https://example.org/article?user=42", True),
    ("url", "https://example.org/article", True, "https://paper.org/article?user=42", True),
    ("url", "https://example.org/article", True, "https://paper.org/article", True),
])
async def test_analysis_routes_only_approved_public_articles(kind, content, allowed, canonical, remote):
    local_model, public_model = object(), object()
    article = IngestedArticle(canonical, "title", None, "article body") if canonical else None
    corpus = SimpleNamespace(article=AsyncMock(return_value=article))
    request = Submission(kind, content, web_search=True, public_model_allowed=allowed)
    model, loaded = await select_analysis_model(request, corpus, local_model, public_model)
    assert model is (public_model if remote else local_model)
    assert loaded is article
    assert corpus.article.await_count == int(canonical is not None)


@pytest.mark.asyncio
async def test_revoked_article_fails_before_model_selection():
    corpus = SimpleNamespace(article=AsyncMock(side_effect=AnalysisError(
        ErrorCode.SOURCE_UNAVAILABLE, "Source revoked"
    )))
    request = Submission("url", "https://example.org/article", public_model_allowed=True)
    with pytest.raises(AnalysisError, match="Source revoked"):
        await select_analysis_model(request, corpus, object(), object())


@pytest.mark.parametrize("request_kind", ["text", "url"])
async def test_all_input_mode_uses_hosted_even_for_message_demo(request_kind):
    hosted = object()
    request = Submission(request_kind, "test input", public_model_allowed=False)
    model, article = await select_analysis_model(request, None, object(), hosted, all_inputs=True)
    assert model is hosted and article is None


async def test_all_input_mode_cannot_fall_back_to_ollama():
    with pytest.raises(ValueError, match="OpenRouter"):
        await select_analysis_model(Submission("text", "test"), None, object(), None, all_inputs=True)
