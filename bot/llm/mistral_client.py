import httpx

from mistralai.client import Mistral
from typing import Any

from bot.cache import TTLCache, hash_key

class MistralClient:
    """Client pour l'API Mistral (OpenAI-compatible)"""

    DEFAULT_TIMEOUT_SEC = 120.0
    DEFAULT_MAX_TOKENS = 32768
    DEFAULT_CACHE_TTL_SEC = 21600.0
    DEFAULT_CACHE_MAX_SIZE = 128

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
        cache_max_size: int = DEFAULT_CACHE_MAX_SIZE,
        base_url: str = "https://api.mistral.ai/v1",
    ) -> None:
        self._timeout_sec = timeout
        self._client = Mistral(
            api_key=api_key,
            timeout=httpx.Timeout(self._timeout_sec),
            base_url = base_url
        )
        self._model = model
        self._max_tokens = max_tokens
        self._cache: TTLCache | None = (
            TTLCache(max_size=cache_max_size, ttl_sec=cache_ttl_sec)
            if cache_ttl_sec is not None
            else None
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        cacheable: bool = False
    ) -> str:
        """Appel bas niveau à l'API Mistral."""
        if cacheable and self._cache:
            cache_key = hash_key("llm", self._model, messages)
            cached = await self._cache.get(cache_key)
            if cached: 
                return str(cached)
            
        mistral_messages = self._convert_ollama_to_mistral(messages)

        response = await self._client.chat(                                                                                                    
           model=self._model,                                                                                                                 
           messages=mistral_messages,                                                                                   
           max_tokens=self._max_tokens
        )

        content = response.choices[0].message.content

        if cacheable and self._cache and cache_key:
            await self._cache.set(cache_key, content)
        
        return content
    
    def _convert_ollama_to_mistral(
            self, 
            ollama_messages: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
        """Convertit une liste de messages du format Ollama → format Mistral"""

        mistral_messages = []
        for msg in ollama_messages:
            role = msg["role"]
            content = msg.get("content", "")
            images = msg.get("images", [])

            if not images:
                mistral_messages.append({"role": role, "content": content})
                continue

            mistral_content = [{"type": "text", "text": content}]
            for img_b64 in images:
                mistral_content.append({
                    "type": "image_url",
                    "image_url": f"data:image/jpeg;base64,{img_b64}"
                })

            mistral_messages.append({"role": role, "content": mistral_content})

        return mistral_messages