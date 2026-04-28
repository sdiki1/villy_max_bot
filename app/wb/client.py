from __future__ import annotations

from typing import Any

import httpx


class WbApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WbFeedbacksClient:
    _MAX_QUESTIONS_WINDOW = 10_000
    _MAX_FEEDBACKS_TAKE = 5_000
    _MAX_FEEDBACKS_SKIP = 199_990

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
                "Accept": "application/json",
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
        nm_id: int | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
    ) -> list[dict[str, Any]]:
        clean_take = max(0, min(take, self._MAX_QUESTIONS_WINDOW))
        clean_skip = max(0, min(skip, self._MAX_QUESTIONS_WINDOW))
        if clean_take + clean_skip > self._MAX_QUESTIONS_WINDOW:
            clean_take = max(0, self._MAX_QUESTIONS_WINDOW - clean_skip)

        params = self._build_list_params(
            is_answered=is_answered,
            take=clean_take,
            skip=clean_skip,
            order=order,
            nm_id=nm_id,
            date_from=date_from,
            date_to=date_to,
        )

        payload = await self._request_json(
            "GET",
            "/api/v1/questions",
            params=params,
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
        nm_id: int | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        order_status: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_take = max(0, min(take, self._MAX_FEEDBACKS_TAKE))
        clean_skip = max(0, min(skip, self._MAX_FEEDBACKS_SKIP))

        params = self._build_list_params(
            is_answered=is_answered,
            take=clean_take,
            skip=clean_skip,
            order=order,
            nm_id=nm_id,
            date_from=date_from,
            date_to=date_to,
        )
        if order_status:
            params["orderStatus"] = order_status

        payload = await self._request_json(
            "GET",
            "/api/v1/feedbacks",
            params=params,
        )

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        feedbacks = data.get("feedbacks")
        if not isinstance(feedbacks, list):
            return []

        return [item for item in feedbacks if isinstance(item, dict)]

    async def get_question(self, *, question_id: str) -> dict[str, Any]:
        clean_question_id = question_id.strip()
        if not clean_question_id:
            raise ValueError("Question ID is required")

        payload = await self._request_json(
            "GET",
            "/api/v1/question",
            params={"id": clean_question_id},
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def mark_question_viewed(self, *, question_id: str) -> None:
        clean_question_id = question_id.strip()
        if not clean_question_id:
            raise ValueError("Question ID is required")

        await self._request_json(
            "PATCH",
            "/api/v1/questions",
            json={
                "id": clean_question_id,
                "wasViewed": True,
            },
        )

    async def answer_question(self, *, question_id: str, answer_text: str) -> None:
        clean_question_id = question_id.strip()
        clean_answer_text = answer_text.strip()
        if not clean_question_id:
            raise ValueError("Question ID is required")
        if len(clean_answer_text) < 2:
            raise ValueError("Answer text should contain at least 2 characters")

        await self._request_json(
            "PATCH",
            "/api/v1/questions",
            json={
                "id": clean_question_id,
                "text": clean_answer_text,
            },
        )

    async def get_feedback(self, *, feedback_id: str) -> dict[str, Any]:
        clean_feedback_id = feedback_id.strip()
        if not clean_feedback_id:
            raise ValueError("Feedback ID is required")

        payload = await self._request_json(
            "GET",
            "/api/v1/feedback",
            params={"id": clean_feedback_id},
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

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

    async def edit_feedback_answer(self, *, feedback_id: str, answer_text: str) -> None:
        clean_feedback_id = feedback_id.strip()
        clean_answer_text = answer_text.strip()
        if not clean_feedback_id:
            raise ValueError("Feedback ID is required")
        if len(clean_answer_text) < 2:
            raise ValueError("Answer text should contain at least 2 characters")
        if len(clean_answer_text) > 5000:
            clean_answer_text = clean_answer_text[:5000].strip()

        await self._request_json(
            "PATCH",
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
            detail_parts = [str(payload.get("errorText") or "").strip()]
            additional_errors = payload.get("additionalErrors")
            if isinstance(additional_errors, list):
                detail_parts.extend(
                    str(item).strip() for item in additional_errors if str(item).strip()
                )
            detail = "; ".join(part for part in detail_parts if part) or "Unknown WB API error"
            raise WbApiError(detail, status_code=response.status_code)

        return payload

    @staticmethod
    def _build_list_params(
        *,
        is_answered: bool,
        take: int,
        skip: int,
        order: str,
        nm_id: int | None,
        date_from: int | None,
        date_to: int | None,
    ) -> dict[str, str]:
        params = {
            "isAnswered": "true" if is_answered else "false",
            "take": str(take),
            "skip": str(skip),
            "order": order,
        }
        if nm_id is not None:
            params["nmId"] = str(nm_id)
        if date_from is not None:
            params["dateFrom"] = str(date_from)
        if date_to is not None:
            params["dateTo"] = str(date_to)
        return params

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
                if payload.get("message"):
                    return str(payload["message"])
        except ValueError:
            pass

        text = response.text.strip()
        return text or "Unknown HTTP error"
