import litellm
from pydantic import BaseModel

from rag.config import settings


def _api_key_for(model: str) -> str:
    if model.startswith("anthropic/"):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your local .env file.")
        return settings.anthropic_api_key
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your local .env file.")
    return settings.openai_api_key


def complete(prompt: str, *, model: str | None = None) -> str:
    model = model or settings.llm_model
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        api_key=_api_key_for(model),
    )
    return response.choices[0].message.content or ""


def structured_complete(
    prompt: str, response_model: type[BaseModel], *, model: str | None = None
) -> BaseModel:
    """Structured-output equivalent of complete() -- replaces every
    get_client().beta.chat.completions.parse(...) call site (judge_correctness,
    verify_citations, llm_rerank). LiteLLM has no .parse()-style auto-validating
    helper like the OpenAI SDK's beta client does -- response_format guarantees
    valid JSON matching the schema, but you still get raw text back, so this
    does the model_validate_json() step those call sites used to get for free."""
    model = model or settings.llm_model
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=response_model,
        api_key=_api_key_for(model),
    )
    return response_model.model_validate_json(response.choices[0].message.content)


def describe_image(image_b64: str, mime_type: str, *, prompt: str, model: str | None = None) -> str:
    """Turns an image into text via a vision-capable chat call -- the one step
    that makes images fit into an otherwise text-only pipeline (text
    embeddings, text-only generation prompts). Returns plain text, so
    everything downstream of this call (chunking, dense+sparse indexing,
    hybrid retrieval, generation, citation verification) treats an image
    exactly like any other document, unmodified.

    Deliberately reuses the standard chat completion path (litellm's OpenAI
    vision message format: an image_url content part alongside the text
    prompt) rather than adding an OCR library -- gpt-4o-mini is already
    vision-capable, so this needed no new dependency and, unlike OCR, also
    describes non-text visual content (a chart's shape, a diagram's layout),
    not just embedded text."""
    model = model or settings.vision_model
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            }
        ],
        api_key=_api_key_for(model),
    )
    return response.choices[0].message.content or ""


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Batch-embed texts. OpenAI accepts up to ~2048 inputs per call; we chunk at 256 to be safe."""
    if not texts:
        return []
    embed_model = model or settings.embedding_model
    api_key = _api_key_for(embed_model)
    vectors: list[list[float]] = []
    batch_size = 256
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = litellm.embedding(model=embed_model, input=batch, api_key=api_key)
        vectors.extend(item["embedding"] for item in response.data)
    return vectors
