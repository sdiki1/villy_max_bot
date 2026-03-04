from __future__ import annotations

from typing import Any

import httpx


class GeminiApiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.4,
        timeout: float = 30.0,
    ) -> None:
        clean_key = api_key.strip()
        clean_model = model.strip()
        if not clean_key:
            raise ValueError("Gemini API key is required")
        if not clean_model:
            raise ValueError("Gemini model is required")

        self._api_key = clean_key
        self._model = clean_model
        self._temperature = temperature
        self._client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com",
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def generate_feedback_reply(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 300,
    ) -> str:
        response = await self._client.post(
            f"/v1beta/models/{self._model}:generateContent",
            params={"key": self._api_key},
            json={
                "system_instruction": {
                    "parts": [{"text": system_prompt}],
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": user_prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": self._temperature,
                    "maxOutputTokens": max_output_tokens,
                },
            },
        )

        if response.status_code >= 400:
            raise GeminiApiError(
                f"Gemini API request failed [{response.status_code}]: {response.text.strip() or 'Unknown error'}"
            )

        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover
            raise GeminiApiError("Gemini API returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise GeminiApiError("Gemini API returned unexpected response type")

        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            prompt_feedback = payload.get("promptFeedback")
            if isinstance(prompt_feedback, dict):
                reason = prompt_feedback.get("blockReason")
                if reason:
                    raise GeminiApiError(f"Gemini blocked the prompt: {reason}")
            raise GeminiApiError("Gemini API returned no candidates")

        text = self._extract_candidate_text(candidates[0])
        if len(text) < 2:
            raise GeminiApiError("Gemini returned empty reply")

        if len(text) > 5000:
            text = text[:5000].strip()

        return text

    @staticmethod
    def _extract_candidate_text(candidate: Any) -> str:
        if not isinstance(candidate, dict):
            return ""

        content = candidate.get("content")
        if not isinstance(content, dict):
            return ""

        parts = content.get("parts")
        if not isinstance(parts, list):
            return ""

        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            value = part.get("text")
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())

        return "\n".join(text_parts).strip()
