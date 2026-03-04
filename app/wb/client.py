from __future__ import annotations

from typing import Any

import httpx


class WbApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WbFeedbacksClient:
    def __init__(
        self,
        *,
        api_token: str,
        timeout: float = 20.0,
    ) -> None:
        clean_token = api_token.strip()
        if not clean_token:
            raise ValueError("WB API token is required")

        self._client = httpx.AsyncClient(
            base_url="https://feedbacks-api.wildberries.ru",
            timeout=timeout,
            headers={
                "Authorization": clean_token,
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_unanswered_count(self) -> int:
        payload = await self._request_json("GET", "/api/v1/questions/count-unanswered")
        data = payload.get("data")
        if not isinstance(data, dict):
            return 0
        value = data.get("countUnanswered")
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0

    async def get_unanswered_feedback_count(self) -> int:
        payload = await self._request_json("GET", "/api/v1/feedbacks/count-unanswered")
        data = payload.get("data")
        if not isinstance(data, dict):
            return 0
        value = data.get("countUnanswered")
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0

    async def list_questions(
        self,
        *,
        is_answered: bool,
        take: int,
        skip: int,
        order: str = "dateAsc",
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "GET",
            "/api/v1/questions",
            params={
                "isAnswered": "true" if is_answered else "false",
                "take": str(take),
                "skip": str(skip),
                "order": order,
            },
        )

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        questions = data.get("questions")
        if not isinstance(questions, list):
            return []

        return [item for item in questions if isinstance(item, dict)]

    async def list_feedbacks(
        self,
        *,
        is_answered: bool,
        take: int,
        skip: int,
        order: str = "dateAsc",
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "GET",
            "/api/v1/feedbacks",
            params={
                "isAnswered": "true" if is_answered else "false",
                "take": str(take),
                "skip": str(skip),
                "order": order,
            },
        )

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        feedbacks = data.get("feedbacks")
        if not isinstance(feedbacks, list):
            return []

        return [item for item in feedbacks if isinstance(item, dict)]

    async def answer_question(self, *, question_id: str, answer_text: str) -> None:
        clean_question_id = question_id.strip()
        clean_answer_text = answer_text.strip()
        if not clean_question_id:
            raise ValueError("Question ID is required")
        if not clean_answer_text:
            raise ValueError("Answer text is required")

        await self._request_json(
            "PATCH",
            "/api/v1/questions",
            json={
                "id": clean_question_id,
                "answer": {
                    "text": clean_answer_text,
                },
                "state": "wbRu",
            },
        )

    async def answer_feedback(self, *, feedback_id: str, answer_text: str) -> None:
        clean_feedback_id = feedback_id.strip()
        clean_answer_text = answer_text.strip()
        if not clean_feedback_id:
            raise ValueError("Feedback ID is required")
        if len(clean_answer_text) < 2:
            raise ValueError("Answer text should contain at least 2 characters")
        if len(clean_answer_text) > 5000:
            clean_answer_text = clean_answer_text[:5000].strip()

        await self._request_json(
            "POST",
            "/api/v1/feedbacks/answer",
            json={
                "id": clean_feedback_id,
                "text": clean_answer_text,
            },
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            method,
            path,
            params=params,
            json=json,
        )

        if response.status_code >= 400:
            detail = self._extract_error_text(response)
            raise WbApiError(
                f"WB API request failed [{response.status_code}]: {detail}",
                status_code=response.status_code,
            )

        if response.status_code == 204 or not response.text.strip():
            return {}

        payload: dict[str, Any]
        try:
            maybe_payload = response.json()
            payload = maybe_payload if isinstance(maybe_payload, dict) else {}
        except ValueError as exc:  # pragma: no cover
            raise WbApiError("WB API returned invalid JSON") from exc

        if payload.get("error"):
            detail = str(payload.get("errorText") or "Unknown WB API error")
            raise WbApiError(detail, status_code=response.status_code)

        return payload

    @staticmethod
    def _extract_error_text(response: httpx.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                if payload.get("errorText"):
                    return str(payload["errorText"])
                if payload.get("detail"):
                    return str(payload["detail"])
                if payload.get("title"):
                    return str(payload["title"])
        except ValueError:
            pass

        text = response.text.strip()
        return text or "Unknown HTTP error"
