"""
Direct HTTPS LLM helper — drop-in replacement for `emergentintegrations`.

Uses Emergent's OpenAI-compatible endpoint, so it works on ANY hosting platform
(Render, Railway, Fly.io, AWS, Hostinger VPS) without needing the private
`emergentintegrations` PyPI CDN.

You only need an EMERGENT_LLM_KEY (the same Universal Key you use in dev).
"""

import os
import httpx

# Emergent's LLM proxy speaks the OpenAI Chat Completions API.
EMERGENT_LLM_URL = "https://integrations.emergentagent.com/llm/chat/completions"


async def llm_chat(
    system_message: str,
    user_message: str,
    model: str = "claude-sonnet-4-5-20250929",
    timeout: float = 55.0,
) -> str:
    """Send a chat completion request to Emergent's LLM endpoint and return the text content.

    Args:
        system_message: System instruction for the model.
        user_message: User's prompt.
        model: Model identifier. Examples:
               - "claude-sonnet-4-5-20250929"  (recommended for long-form astrology output)
               - "claude-haiku-4-5-20250929"   (fastest)
               - "gpt-5.2", "gpt-5.2-mini"     (OpenAI)
               - "gemini-3-flash"              (Google)
        timeout: HTTPX timeout in seconds. Default 55s to stay under most CDN edge timeouts (60s).

    Returns:
        The assistant's text response.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY environment variable is not set")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(EMERGENT_LLM_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]
