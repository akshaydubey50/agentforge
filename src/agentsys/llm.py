import litellm
from pydantic import BaseModel

from agentsys.config import settings
from agentsys.sanitize import scrub_nul


def _api_key_for(model: str) -> str:
    if model.startswith("anthropic/"):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your local .env file.")
        return settings.anthropic_api_key
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your local .env file.")
    return settings.openai_api_key


def complete(
    prompt: str, *, model: str | None = None, web_search_options: dict | None = None
) -> tuple[str, object]:
    """Returns (text, completion) rather than just text -- callers that need
    to record cost (see agentsys.cost) need the raw completion for its
    model/usage fields; callers that don't just take the first element.

    web_search_options is passed through to the provider only when set, for
    search-capable models (tools/web_search.py's OpenAI-hosted backend is
    the one caller). It's here rather than in that tool so this file stays
    the single place that imports the provider SDK -- a seam worth keeping,
    since it's also where api-key routing, NUL scrubbing, and any future
    retry/caching layer belong."""
    model = model or settings.llm_model
    extra = {"web_search_options": web_search_options} if web_search_options else {}
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_key=_api_key_for(model),
        **extra,
    )
    # Scrub at the source: a NUL the model emits must never reach a DB write.
    return scrub_nul(response.choices[0].message.content or ""), response


def structured_complete(
    prompt: str, response_model: type[BaseModel], *, model: str | None = None
) -> tuple[BaseModel, object]:
    """Structured-output equivalent of complete() -- every call site that
    used to do get_client().beta.chat.completions.parse(...) and read
    completion.choices[0].message.parsed goes through this instead.

    LiteLLM has no .parse()-style auto-validating helper (unlike the OpenAI
    SDK's beta client) -- response_format guarantees the model's raw text is
    valid JSON matching the schema, but you still get raw text back, so this
    does the model_validate_json() step every call site used to get for free."""
    model = model or settings.llm_model
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=response_model,
        api_key=_api_key_for(model),
    )
    parsed = response_model.model_validate_json(scrub_nul(response.choices[0].message.content))
    return parsed, response


def embed_texts(texts: list[str], *, model: str = "openai/text-embedding-3-small") -> list[list[float]]:
    if not texts:
        return []
    response = litellm.embedding(model=model, input=texts, api_key=_api_key_for(model))
    return [item["embedding"] for item in response.data]
