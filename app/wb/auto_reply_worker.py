from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import SessionFactory, dispose_db, init_db
from app.models import WbAutoReplySetting
from app.wb.client import WbApiError, WbFeedbacksClient
from app.wb.gemini_client import GeminiApiError, GeminiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

_SINGLETON_SETTINGS_ID = 1
_MAX_QUESTIONS_PER_SCAN = 10_000
_MAX_FEEDBACKS_PER_SCAN = 5_000
_PAGE_SIZE = 200
_REQUEST_INTERVAL_SECONDS = 0.36
_DEFAULT_FEEDBACK_AI_PROMPT = (
    "Ты менеджер поддержки магазина VillyPrint на Wildberries.\n"
    "Сформируй короткий, вежливый и естественный ответ на отзыв покупателя.\n"
    "Пиши только на русском языке, без эмодзи, без шаблонных канцеляризмов.\n"
    "Если отзыв положительный — поблагодари.\n"
    "Если отзыв содержит проблему — извинись и предложи связаться с поддержкой для решения.\n"
    "Не выдумывай факты, которых нет в отзыве.\n"
    "Верни только текст ответа (2-5000 символов), без кавычек и без служебных комментариев."
)
_REPLY_PREFIX_RE = re.compile(r"^ответ\s*:\s*", re.IGNORECASE)


class WbAutoReplyWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        api_token: str,
        gemini_api_key: str,
        gemini_model: str,
        gemini_temperature: float,
        poll_interval_seconds: int,
        wb_api_min_interval_seconds: float = _REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = max(5, poll_interval_seconds)
        self._warned_about_missing_wb_token = False
        self._warned_about_missing_gemini = False
        self._wb_client: WbFeedbacksClient | None = None
        self._gemini_client: GeminiClient | None = None

        clean_wb_token = api_token.strip()
        if clean_wb_token:
            self._wb_client = WbFeedbacksClient(
                api_token=clean_wb_token,
                min_interval_seconds=wb_api_min_interval_seconds,
            )

        clean_gemini_key = gemini_api_key.strip()
        if clean_gemini_key:
            self._gemini_client = GeminiClient(
                api_key=clean_gemini_key,
                model=gemini_model,
                temperature=gemini_temperature,
            )

    async def close(self) -> None:
        if self._wb_client:
            await self._wb_client.close()
        if self._gemini_client:
            await self._gemini_client.close()

    async def run_forever(self) -> None:
        logger.info(
            "WB auto-reply worker started. Poll interval: %s sec",
            self._poll_interval_seconds,
        )
        while True:
            try:
                await self._run_cycle()
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in WB auto-reply cycle")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _run_cycle(self) -> None:
        cfg = await self._load_or_create_settings()

        if self._wb_client is None:
            if (cfg.is_enabled or cfg.feedback_ai_enabled) and not self._warned_about_missing_wb_token:
                logger.warning(
                    "WB_API_TOKEN is empty. Set it in .env to enable WB auto-replies."
                )
                self._warned_about_missing_wb_token = True
            return

        try:
            await self._run_questions_cycle(cfg)
            await self._run_feedback_ai_cycle(cfg)
        except WbApiError as exc:
            if exc.status_code == 429:
                await self._sleep_after_wb_rate_limit(exc)
                return
            raise

    async def _run_questions_cycle(self, cfg: WbAutoReplySetting) -> None:
        template = (cfg.answer_template or "").strip()

        if not cfg.is_enabled:
            return

        if not template:
            logger.warning(
                "WB auto-reply for questions is enabled, but template is empty. "
                "Save a template in admin panel."
            )
            return

        unanswered_count = await self._wb_client.get_unanswered_count()
        if unanswered_count <= 0:
            return

        questions = await self._load_unanswered_questions(unanswered_count)
        if not questions:
            return

        answered = 0
        failed = 0
        for question in questions:
            question_id = str(question.get("id") or "").strip()
            if not question_id:
                continue

            if self._extract_answer_text(question.get("answer")):
                continue

            reply_text = self._render_question_template(template=template, question=question)
            if not reply_text:
                continue

            try:
                await self._wb_client.answer_question(
                    question_id=question_id,
                    answer_text=reply_text,
                )
                answered += 1
            except WbApiError as exc:
                failed += 1
                logger.warning(
                    "Failed to answer question %s: %s",
                    question_id,
                    exc,
                )
                if exc.status_code == 429:
                    await self._sleep_after_wb_rate_limit(exc)
                    break
            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

        if answered or failed:
            logger.info(
                "WB question auto-reply cycle: answered=%s failed=%s scanned=%s",
                answered,
                failed,
                len(questions),
            )

    async def _run_feedback_ai_cycle(self, cfg: WbAutoReplySetting) -> None:
        if not cfg.feedback_ai_enabled:
            return

        if self._gemini_client is None:
            if not self._warned_about_missing_gemini:
                logger.warning(
                    "GEMINI_API_KEY is empty. Set it in .env to enable AI replies for WB feedbacks."
                )
                self._warned_about_missing_gemini = True
            return

        prompt = (cfg.feedback_ai_prompt or "").strip() or _DEFAULT_FEEDBACK_AI_PROMPT
        unanswered_count = await self._wb_client.get_unanswered_feedback_count()
        if unanswered_count <= 0:
            return

        feedbacks = await self._load_unanswered_feedbacks(unanswered_count)
        if not feedbacks:
            return

        answered = 0
        failed = 0
        for feedback in feedbacks:
            feedback_id = str(feedback.get("id") or "").strip()
            if not feedback_id:
                continue

            if self._extract_answer_text(feedback.get("answer")):
                continue

            user_prompt = self._build_feedback_user_prompt(feedback)
            try:
                ai_reply = await self._gemini_client.generate_feedback_reply(
                    system_prompt=prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=220,
                )
                reply_text = self._normalize_feedback_reply(ai_reply)
                await self._wb_client.answer_feedback(
                    feedback_id=feedback_id,
                    answer_text=reply_text,
                )
                answered += 1
            except (GeminiApiError, WbApiError, ValueError) as exc:
                failed += 1
                logger.warning(
                    "Failed to answer feedback %s: %s",
                    feedback_id,
                    exc,
                )
                if isinstance(exc, WbApiError) and exc.status_code == 429:
                    await self._sleep_after_wb_rate_limit(exc)
                    break
            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

        if answered or failed:
            logger.info(
                "WB feedback AI cycle: answered=%s failed=%s scanned=%s",
                answered,
                failed,
                len(feedbacks),
            )

    async def _load_unanswered_questions(self, unanswered_count: int) -> list[dict[str, Any]]:
        target = min(max(unanswered_count, 0), _MAX_QUESTIONS_PER_SCAN)
        questions: list[dict[str, Any]] = []
        skip = 0

        while skip < target:
            take = min(_PAGE_SIZE, target - skip)
            batch = await self._wb_client.list_questions(
                is_answered=False,
                take=take,
                skip=skip,
                order="dateAsc",
            )
            if not batch:
                break

            questions.extend(batch)
            skip += len(batch)

            if len(batch) < take:
                break

            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

        return questions

    async def _load_unanswered_feedbacks(self, unanswered_count: int) -> list[dict[str, Any]]:
        target = min(max(unanswered_count, 0), _MAX_FEEDBACKS_PER_SCAN)
        feedbacks: list[dict[str, Any]] = []
        skip = 0

        while skip < target:
            take = min(_PAGE_SIZE, target - skip)
            batch = await self._wb_client.list_feedbacks(
                is_answered=False,
                take=take,
                skip=skip,
                order="dateAsc",
            )
            if not batch:
                break

            feedbacks.extend(batch)
            skip += len(batch)

            if len(batch) < take:
                break

            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

        return feedbacks

    async def _sleep_after_wb_rate_limit(self, exc: WbApiError) -> None:
        wait_seconds = exc.retry_after
        if wait_seconds is None or wait_seconds <= 0:
            wait_seconds = exc.reset_after
        if wait_seconds is None or wait_seconds <= 0:
            wait_seconds = max(float(self._poll_interval_seconds), 60.0)

        logger.warning(
            "WB API rate limit reached. Sleeping %.1f sec before next WB request: %s",
            wait_seconds,
            exc,
        )
        await asyncio.sleep(wait_seconds)

    async def _load_or_create_settings(self) -> WbAutoReplySetting:
        async with self._session_factory() as db:
            setting = await db.get(WbAutoReplySetting, _SINGLETON_SETTINGS_ID)
            if setting is not None:
                if not setting.feedback_ai_prompt:
                    setting.feedback_ai_prompt = _DEFAULT_FEEDBACK_AI_PROMPT
                    await db.commit()
                    await db.refresh(setting)
                return setting

            setting = WbAutoReplySetting(
                id=_SINGLETON_SETTINGS_ID,
                is_enabled=False,
                answer_template="",
                feedback_ai_enabled=False,
                feedback_ai_prompt=_DEFAULT_FEEDBACK_AI_PROMPT,
            )
            db.add(setting)

            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                setting = await db.get(WbAutoReplySetting, _SINGLETON_SETTINGS_ID)
                if setting is None:
                    raise
                return setting

            await db.refresh(setting)
            return setting

    @staticmethod
    def _render_question_template(template: str, question: dict[str, Any]) -> str:
        question_text = str(question.get("text") or "").strip()

        product = question.get("productDetails")
        product_name = ""
        nm_id = ""
        brand_name = ""

        if isinstance(product, dict):
            product_name = str(product.get("productName") or "").strip()
            nm_id_value = product.get("nmId")
            if nm_id_value is not None:
                nm_id = str(nm_id_value).strip()
            brand_name = str(product.get("brandName") or "").strip()

        replacements = {
            "{question_id}": str(question.get("id") or "").strip(),
            "{question_text}": question_text,
            "{product_name}": product_name,
            "{nm_id}": nm_id,
            "{brand_name}": brand_name,
            "{today}": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        result = template
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)

        return result.strip()

    @staticmethod
    def _build_feedback_user_prompt(feedback: dict[str, Any]) -> str:
        product = feedback.get("productDetails")
        if not isinstance(product, dict):
            product = {}

        def val(key: str, *, source: dict[str, Any] | None = None) -> str:
            src = source if source is not None else feedback
            value = src.get(key)
            if value is None:
                return ""
            return str(value).strip()

        lines = [
            "Сформируй ответ на отзыв покупателя.",
            "",
            f"ID отзыва: {val('id') or '-'}",
            f"Имя покупателя: {val('userName') or '-'}",
            f"Оценка: {val('productValuation') or '-'}",
            f"Статус заказа: {val('orderStatus') or '-'}",
            f"Текст отзыва: {val('text') or '-'}",
            f"Плюсы: {val('pros') or '-'}",
            f"Минусы: {val('cons') or '-'}",
            f"Товар: {val('productName', source=product) or '-'}",
            f"Бренд: {val('brandName', source=product) or '-'}",
            f"Артикул WB (nmId): {val('nmId', source=product) or '-'}",
            f"Артикул продавца: {val('supplierArticle', source=product) or '-'}",
        ]

        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_feedback_reply(raw_text: str) -> str:
        text = raw_text.strip()
        text = text.strip("\"'").strip()
        text = _REPLY_PREFIX_RE.sub("", text).strip()

        if len(text) < 2:
            raise ValueError("AI reply is too short")

        if len(text) > 5000:
            text = text[:5000].strip()

        return text

    @staticmethod
    def _extract_answer_text(answer: Any) -> str:
        if isinstance(answer, dict):
            return str(answer.get("text") or "").strip()
        if isinstance(answer, str):
            return answer.strip()
        return ""


async def _run_worker() -> None:
    settings = get_settings()
    await init_db()

    worker = WbAutoReplyWorker(
        session_factory=SessionFactory,
        api_token=settings.wb_api_token,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.gemini_model,
        gemini_temperature=settings.gemini_temperature,
        poll_interval_seconds=settings.wb_auto_reply_poll_interval,
        wb_api_min_interval_seconds=settings.wb_api_min_interval_seconds,
    )

    try:
        await worker.run_forever()
    finally:
        await worker.close()
        await dispose_db()


def main() -> None:
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
