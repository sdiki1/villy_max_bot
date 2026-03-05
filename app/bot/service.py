from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from maxapi import Bot, Dispatcher
from maxapi.context.base import BaseContext
from maxapi.enums.upload_type import UploadType
from maxapi.filters.middleware import BaseMiddleware
from maxapi.filters import F
from maxapi.types import (
    BotStarted,
    Command,
    InputMedia,
    InputMediaBuffer,
    MessageCreated,
)
from maxapi.types.message import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.constants import (
    BTN_BACK,
    BTN_END_SUPPORT,
    BTN_FAQ,
    BTN_MY_ORDERS,
    BTN_ORDER,
    BTN_SUPPORT,
    DESIGN_PROMPT,
    FAQ_ANSWERS,
    FAQ_HEADER,
    FAQ_QUESTIONS,
    FULL_NAME_PROMPT,
    IMAGE_PROMPT,
    IMAGE_RETRY_PROMPT,
    MUG_OPTIONS,
    MUG_PROMPT,
    ORDER_SUCCESS_TEXT,
    PHONE_PROMPT,
    PRODUCT_IMAGE_PROMPTS,
    PRODUCT_MUG,
    PRODUCT_OPTIONS,
    PRODUCT_PROMPT,
    PRODUCT_SIZE_OPTIONS,
    PRODUCT_SIZE_PROMPTS,
    SOURCE_OPTIONS,
    SOURCE_PROMPT,
    SUPPORT_ACK_TEXT,
    SUPPORT_FINISH_TEXT,
    SUPPORT_INTRO_TEXT,
    UNKNOWN_MENU_TEXT,
    WELCOME_TEXT,
)
from app.bot.keyboards import (
    faq_keyboard,
    main_menu_keyboard,
    mug_keyboard,
    phone_request_keyboard,
    product_keyboard,
    product_size_keyboard,
    source_keyboard,
    support_keyboard,
)
from app.bot.states import FAQStates, OrderStates, SupportStates
from app.models import Order, SupportMessage, SupportSession, User

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"^\+?[0-9\s()\-]{10,20}$")
VCF_TEL_RE = re.compile(r"TEL[^:]*:([+0-9\s()\-]{10,})", re.IGNORECASE)


class IncomingMessageLogMiddleware(BaseMiddleware):
    def __init__(self, service: "MaxBotService") -> None:
        self.service = service

    async def __call__(
        self,
        handler,
        event_object,
        data,
    ):
        if isinstance(event_object, MessageCreated):
            await self.service._log_incoming_user_message(event_object.message)
        return await handler(event_object, data)


class MaxBotService:
    def __init__(
        self,
        *,
        token: str,
        session_factory: async_sessionmaker[AsyncSession],
        skip_updates: bool = True,
        welcome_image_path: str | None = None,
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
        admin_url: str | None = None,
    ) -> None:
        self._token = token
        self._session_factory = session_factory
        self._skip_updates = skip_updates
        self._telegram_bot_token = (telegram_bot_token or "").strip()
        self._telegram_chat_id = (telegram_chat_id or "").strip()
        self._admin_url = (admin_url or "").strip()
        if welcome_image_path:
            self._welcome_image_path = Path(welcome_image_path).expanduser()
        else:
            # Fallback for local/docker run when image is placed in project root.
            default_welcome = Path("welcome.jpeg")
            self._welcome_image_path = (
                default_welcome if default_welcome.exists() else None
            )

        self.bot: Bot | None = Bot(token=token) if token else None
        self.dp = Dispatcher()
        self.dp.outer_middleware(IncomingMessageLogMiddleware(self))
        self._polling_task: asyncio.Task[None] | None = None

        self._register_handlers()

    async def start(self) -> None:
        if not self.bot:
            logger.warning(
                "MAX_BOT_TOKEN не задан. Бот не будет запущен, "
                "но админ-панель останется доступной."
            )
            return

        if self._polling_task and not self._polling_task.done():
            return

        self._polling_task = asyncio.create_task(
            self.dp.start_polling(self.bot, skip_updates=self._skip_updates)
        )
        self._polling_task.add_done_callback(self._on_polling_done)
        logger.info("MAX bot polling запущен")

    async def stop(self) -> None:
        if self._polling_task:
            await self.dp.stop_polling()
            self._polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None

        if self.bot:
            await self.bot.close_session()

    async def send_admin_message(
        self,
        *,
        user_id: int,
        chat_id: int | None,
        text: str | None = None,
        file_bytes: bytes | None = None,
        file_name: str | None = None,
        file_content_type: str | None = None,
        support_session_id: int | None = None,
    ) -> None:
        if not self.bot:
            raise RuntimeError("Бот не запущен: отсутствует MAX_BOT_TOKEN")

        attachments_for_send: list[Any] = []
        attachment_data: list[dict[str, Any]] = []
        clean_text = (text or "").strip()

        if file_bytes:
            safe_file_name = Path(file_name or "attachment").name.strip() or "attachment"
            attachments_for_send.append(
                InputMediaBuffer(
                    buffer=file_bytes,
                    filename=safe_file_name,
                    type=UploadType.FILE,
                )
            )
            attachment_data.append(
                {
                    "type": "upload",
                    "filename": safe_file_name,
                    "size": len(file_bytes),
                    "content_type": file_content_type or "",
                }
            )

        if not clean_text and not attachments_for_send:
            raise ValueError("Нельзя отправить пустое сообщение")

        await self.bot.send_message(
            chat_id=chat_id,
            user_id=user_id,
            text=clean_text or None,
            attachments=attachments_for_send or None,
        )

        await self._log_support_message_by_user(
            max_user_id=user_id,
            chat_id=chat_id,
            sender_role="admin",
            text=clean_text or None,
            attachment_data=attachment_data or None,
            is_read=True,
            support_session_id=support_session_id,
        )

    def _on_polling_done(self, task: asyncio.Task[None]) -> None:
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc:
                logger.exception("Polling завершился с ошибкой", exc_info=exc)

    def _register_handlers(self) -> None:
        @self.dp.bot_started()
        async def on_bot_started(event: BotStarted) -> None:
            async with self._session_factory() as db:
                await self._upsert_user_from_started_event(db, event)
                await db.commit()

            await self._send_welcome(
                chat_id=event.chat_id,
                user_id=event.user.user_id,
            )

        @self.dp.message_created(Command("start"))
        async def on_start(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await self._close_support_for_message(event.message)
            await context.clear()

            async with self._session_factory() as db:
                await self._upsert_user_from_message(db, event.message)
                await db.commit()

            await self._send_welcome(message=event.message)

        @self.dp.message_created(F.message.body.text == BTN_ORDER)
        async def on_make_order(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await context.clear()
            await context.set_state(OrderStates.waiting_phone)

            async with self._session_factory() as db:
                await self._upsert_user_from_message(db, event.message)
                await db.commit()

            await self._send_order_step_message(
                event.message,
                context=context,
                text=PHONE_PROMPT,
                attachments=[phone_request_keyboard()],
            )

        @self.dp.message_created(F.message.body.text == BTN_MY_ORDERS)
        async def on_my_orders(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await context.clear()

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if not user:
                    await db.commit()
                    await self._answer_and_log(
                        event.message,
                        UNKNOWN_MENU_TEXT,
                        attachments=[main_menu_keyboard()],
                    )
                    return

                orders = (
                    await db.scalars(
                        select(Order)
                        .where(Order.user_id == user.id)
                        .order_by(Order.created_at.desc())
                        .limit(10)
                    )
                ).all()
                await db.commit()

            if not orders:
                text = "У вас пока нет заказов. Нажмите «Сделать заказ», чтобы оформить новый."
            else:
                lines = ["Ваши последние заказы:"]
                for order in orders:
                    created = order.created_at.astimezone().strftime(
                        "%d.%m.%Y %H:%M"
                    )
                    product_label = order.product_type
                    if order.mug_type:
                        product_label = f"{product_label} | {order.mug_type}"
                    if order.product_size:
                        product_label = f"{product_label} | {order.product_size}"
                    lines.append(
                        f"• #{order.id} | {product_label} | "
                        f"{self._status_ru(order.status)} | {created}"
                    )
                text = "\n".join(lines)

            await self._answer_and_log(
                event.message,
                text,
                attachments=[main_menu_keyboard()],
            )

        @self.dp.message_created(F.message.body.text == BTN_FAQ)
        async def on_faq(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await context.clear()
            await context.set_state(FAQStates.selecting_question)
            await self._answer_and_log(
                event.message,
                self._faq_text(),
                attachments=[faq_keyboard()],
            )

        @self.dp.message_created(F.message.body.text == BTN_SUPPORT)
        async def on_support(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await context.clear()
            await context.set_state(SupportStates.active_chat)

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if user:
                    await self._get_or_create_open_session(db, user.id)
                await db.commit()

            await self._answer_and_log(
                event.message,
                SUPPORT_INTRO_TEXT,
                attachments=[support_keyboard()],
            )

        @self.dp.message_created(F.message.body.text == BTN_BACK)
        async def on_back(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            await self._delete_order_step_message(context)
            await context.clear()
            await self._answer_and_log(
                event.message,
                "Главное меню:",
                attachments=[main_menu_keyboard()],
            )

        @self.dp.message_created(SupportStates.active_chat)
        async def on_support_message(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            text = self._message_text(event.message)

            if text == BTN_END_SUPPORT:
                await self._close_support_for_message(event.message)
                await context.clear()
                await self._answer_and_log(
                    event.message,
                    f"{SUPPORT_FINISH_TEXT}\n\nВыберите действие:",
                    attachments=[main_menu_keyboard()],
                )
                return

            incoming_attachments = self._serialize_attachments(event.message)
            if not text and not incoming_attachments:
                await self._answer_and_log(
                    event.message,
                    "Напишите текст вопроса или отправьте вложение.",
                    attachments=[support_keyboard()],
                )
                return

            await self._notify_admin_about_user_message(event.message)
            await self._answer_and_log(
                event.message,
                SUPPORT_ACK_TEXT,
                attachments=[support_keyboard()],
            )

        @self.dp.message_created(FAQStates.selecting_question)
        async def on_faq_question(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            text = self._message_text(event.message)

            if text == BTN_BACK:
                await context.clear()
                await self._answer_and_log(
                    event.message,
                    "Главное меню:",
                    attachments=[main_menu_keyboard()],
                )
                return

            if text.isdigit():
                question_num = int(text)
                answer = FAQ_ANSWERS.get(question_num)
                if answer:
                    await self._answer_and_log(
                        event.message,
                        f"{question_num}) {FAQ_QUESTIONS[question_num - 1]}\n\n{answer}",
                        attachments=[faq_keyboard()],
                    )
                    return

            await self._answer_and_log(
                event.message,
                "Введите номер вопроса от 1 до 16 или нажмите «Назад».",
                attachments=[faq_keyboard()],
            )

        @self.dp.message_created(OrderStates.waiting_phone)
        async def on_order_phone(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            phone = self._extract_phone(event.message)
            if not phone:
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text=(
                        "Не удалось распознать номер. Введите его в формате +7 999 123 45 67 "
                        "или используйте кнопку отправки контакта."
                    ),
                    attachments=[phone_request_keyboard()],
                )
                return

            await self._delete_order_step_message(context)
            await context.update_data(phone=phone)
            await context.set_state(OrderStates.waiting_full_name)

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if user:
                    user.phone = phone
                await db.commit()

            await self._answer_and_log(event.message, FULL_NAME_PROMPT)

        @self.dp.message_created(OrderStates.waiting_full_name)
        async def on_order_full_name(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            full_name = self._message_text(event.message)
            if len(full_name.split()) < 2:
                await self._answer_and_log(
                    event.message,
                    "Пожалуйста, укажите имя и фамилию (минимум два слова)."
                )
                return

            await context.update_data(full_name=full_name)
            await context.set_state(OrderStates.waiting_product)

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if user:
                    user.full_name = full_name
                await db.commit()

            await self._send_order_step_message(
                event.message,
                context=context,
                text=PRODUCT_PROMPT,
                attachments=[product_keyboard()],
            )

        @self.dp.message_created(OrderStates.waiting_product)
        async def on_order_product(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            product = self._message_text(event.message)
            if product not in PRODUCT_OPTIONS:
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text="Выберите товар кнопкой ниже.",
                    attachments=[product_keyboard()],
                )
                return

            await context.update_data(
                product_type=product,
                mug_type=None,
                product_size=None,
            )

            if product == PRODUCT_MUG:
                await context.set_state(OrderStates.waiting_mug_type)
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text=MUG_PROMPT,
                    attachments=[mug_keyboard()],
                )
            else:
                size_options = PRODUCT_SIZE_OPTIONS.get(product, [])
                if not size_options:
                    await context.set_state(OrderStates.waiting_source)
                    await self._send_order_step_message(
                        event.message,
                        context=context,
                        text=SOURCE_PROMPT,
                        attachments=[source_keyboard()],
                    )
                    return

                await context.set_state(OrderStates.waiting_product_size)
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text=PRODUCT_SIZE_PROMPTS.get(product, "Выберите размер товара:"),
                    attachments=[product_size_keyboard(size_options)],
                )

        @self.dp.message_created(OrderStates.waiting_mug_type)
        async def on_order_mug_type(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            mug_type = self._message_text(event.message)
            if mug_type not in MUG_OPTIONS:
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text="Выберите тип кружки кнопкой ниже.",
                    attachments=[mug_keyboard()],
                )
                return

            await context.update_data(mug_type=mug_type, product_size=None)
            await context.set_state(OrderStates.waiting_source)
            await self._send_order_step_message(
                event.message,
                context=context,
                text=SOURCE_PROMPT,
                attachments=[source_keyboard()],
            )

        @self.dp.message_created(OrderStates.waiting_product_size)
        async def on_order_product_size(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            selected_size = self._message_text(event.message)
            data = dict(await context.get_data())
            product_type = str(data.get("product_type") or "")
            size_options = PRODUCT_SIZE_OPTIONS.get(product_type, [])
            if selected_size not in size_options:
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text="Выберите размер кнопкой ниже.",
                    attachments=[product_size_keyboard(size_options)],
                )
                return

            await context.update_data(product_size=selected_size, mug_type=None)
            await context.set_state(OrderStates.waiting_source)
            await self._send_order_step_message(
                event.message,
                context=context,
                text=SOURCE_PROMPT,
                attachments=[source_keyboard()],
            )

        @self.dp.message_created(OrderStates.waiting_source)
        async def on_order_source(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            source = self._message_text(event.message)
            if source not in SOURCE_OPTIONS:
                await self._send_order_step_message(
                    event.message,
                    context=context,
                    text="Выберите вариант кнопкой ниже.",
                    attachments=[source_keyboard()],
                )
                return

            await context.update_data(source_channel=source)
            await context.set_state(OrderStates.waiting_image)
            await self._delete_order_step_message(context)

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if user:
                    user.source_channel = source
                await db.commit()

            data = dict(await context.get_data())
            product_type = str(data.get("product_type") or "")
            image_prompt = PRODUCT_IMAGE_PROMPTS.get(product_type, IMAGE_PROMPT)
            await self._answer_and_log(event.message, image_prompt)

        @self.dp.message_created(OrderStates.waiting_image)
        async def on_order_image(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            attachment = self._extract_primary_attachment(event.message)
            if not attachment:
                await self._answer_and_log(event.message, IMAGE_RETRY_PROMPT)
                return

            await context.update_data(image_attachment=attachment)
            await context.set_state(OrderStates.waiting_design_notes)
            await self._answer_and_log(event.message, DESIGN_PROMPT)

        @self.dp.message_created(OrderStates.waiting_design_notes)
        async def on_order_design_notes(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            design_notes = self._message_text(event.message)
            if len(design_notes) < 3:
                await self._answer_and_log(
                    event.message,
                    "Напишите чуть подробнее пожелания к дизайну."
                )
                return

            data = dict(await context.get_data())
            order_id: int | None = None
            order_product_type = ""
            order_design_notes = design_notes
            order_mug_type: str | None = None
            order_product_size: str | None = None

            async with self._session_factory() as db:
                user = await self._upsert_user_from_message(db, event.message)
                if not user:
                    await db.commit()
                    await self._answer_and_log(
                        event.message,
                        "Не удалось оформить заказ. Попробуйте снова через /start"
                    )
                    return

                phone = str(data.get("phone") or user.phone or "")
                full_name = str(data.get("full_name") or user.full_name or "")
                product_type = str(data.get("product_type") or "")
                source_channel = str(data.get("source_channel") or "")
                mug_type_raw = data.get("mug_type")
                mug_type = str(mug_type_raw) if mug_type_raw else None
                product_size_raw = data.get("product_size")
                product_size = str(product_size_raw) if product_size_raw else None
                image_data = data.get("image_attachment")

                if not all([phone, full_name, product_type, source_channel]):
                    await db.commit()
                    await context.clear()
                    await self._answer_and_log(
                        event.message,
                        "Сессия заказа устарела. Начните оформление заново.",
                        attachments=[main_menu_keyboard()],
                    )
                    return

                order = Order(
                    user_id=user.id,
                    status="new",
                    phone=phone,
                    full_name=full_name,
                    product_type=product_type,
                    mug_type=mug_type,
                    product_size=product_size,
                    source_channel=source_channel,
                    design_notes=design_notes,
                    image_url=(image_data or {}).get("url")
                    if isinstance(image_data, dict)
                    else None,
                    image_token=(image_data or {}).get("token")
                    if isinstance(image_data, dict)
                    else None,
                    image_size=self._safe_int((image_data or {}).get("size"))
                    if isinstance(image_data, dict)
                    else None,
                    raw_attachment=image_data if isinstance(image_data, dict) else None,
                )

                db.add(order)
                user.phone = phone
                user.full_name = full_name
                user.source_channel = source_channel
                await db.commit()
                order_id = order.id
                order_product_type = product_type
                order_mug_type = mug_type
                order_product_size = product_size

            await context.clear()
            await self._notify_admin_about_order(
                event.message,
                order_id=order_id,
                product_type=order_product_type,
                mug_type=order_mug_type,
                product_size=order_product_size,
                design_notes=order_design_notes,
            )
            await self._answer_and_log(
                event.message,
                ORDER_SUCCESS_TEXT,
                attachments=[main_menu_keyboard()],
            )

        @self.dp.message_created()
        async def on_unknown_message(
            event: MessageCreated,
            context: BaseContext,
        ) -> None:
            state = await context.get_state()
            text = self._message_text(event.message)

            if state:
                await self._answer_and_log(
                    event.message,
                    "Не понял ответ. Используйте кнопки или /start для возврата в меню."
                )
                return

            if text and text.startswith("/"):
                await self._answer_and_log(
                    event.message,
                    "Неизвестная команда. Используйте /start",
                    attachments=[main_menu_keyboard()],
                )
                return

            if text == BTN_END_SUPPORT:
                had_open_session = await self._close_support_for_message(
                    event.message
                )
                if had_open_session:
                    await context.clear()
                    await self._answer_and_log(
                        event.message,
                        f"{SUPPORT_FINISH_TEXT}\n\nВыберите действие:",
                        attachments=[main_menu_keyboard()],
                    )
                    return

            if await self._has_open_support_session_for_message(event.message):
                incoming_attachments = self._serialize_attachments(event.message)
                if not text and not incoming_attachments:
                    await self._answer_and_log(
                        event.message,
                        "Напишите текст вопроса или отправьте вложение.",
                        attachments=[support_keyboard()],
                    )
                    return

                await context.set_state(SupportStates.active_chat)
                await self._notify_admin_about_user_message(event.message)
                await self._answer_and_log(
                    event.message,
                    SUPPORT_ACK_TEXT,
                    attachments=[support_keyboard()],
                )
                return

            incoming_attachments = self._serialize_attachments(event.message)
            if text or incoming_attachments:
                await self._notify_admin_about_user_message(event.message)
            await self._answer_and_log(
                event.message,
                UNKNOWN_MENU_TEXT,
                attachments=[main_menu_keyboard()],
            )

    async def _send_welcome(
        self,
        *,
        message: Message | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        attachments: list[Any] = []

        if self._welcome_image_path and self._welcome_image_path.exists():
            attachments.append(InputMedia(str(self._welcome_image_path)))

        attachments.append(main_menu_keyboard())

        if message is not None:
            await self._answer_and_log(
                message,
                WELCOME_TEXT,
                attachments=attachments,
            )
            return

        if not self.bot or user_id is None:
            return

        await self._send_to_user_and_log(
            chat_id=chat_id,
            user_id=user_id,
            text=WELCOME_TEXT,
            attachments=attachments,
        )

    async def _upsert_user_from_message(
        self,
        db: AsyncSession,
        message: Message,
    ) -> User | None:
        sender = message.sender
        if sender is None:
            return None

        user = await self._find_user_by_max_id(db, sender.user_id)
        if user is None:
            user = User(
                max_user_id=sender.user_id,
                first_name=sender.first_name,
                last_name=sender.last_name,
                username=sender.username,
                chat_id=message.recipient.chat_id,
            )
            db.add(user)
            await db.flush()

        user.chat_id = message.recipient.chat_id
        user.first_name = sender.first_name
        user.last_name = sender.last_name
        user.username = sender.username
        return user

    async def _upsert_user_from_started_event(
        self,
        db: AsyncSession,
        event: BotStarted,
    ) -> User:
        user = await self._find_user_by_max_id(db, event.user.user_id)
        if user is None:
            user = User(
                max_user_id=event.user.user_id,
                first_name=event.user.first_name,
                last_name=event.user.last_name,
                username=event.user.username,
                chat_id=event.chat_id,
            )
            db.add(user)
            await db.flush()

        user.chat_id = event.chat_id
        user.first_name = event.user.first_name
        user.last_name = event.user.last_name
        user.username = event.user.username
        return user

    async def _close_support_for_message(self, message: Message) -> bool:
        async with self._session_factory() as db:
            user = await self._upsert_user_from_message(db, message)
            if not user:
                await db.commit()
                return False

            session = await self._get_user_session(db, user.id)
            if session is None:
                await db.commit()
                return False

            was_open = session.is_open
            if was_open:
                session.is_open = False
                session.closed_at = datetime.now(timezone.utc)
            await db.commit()
            return was_open

    async def _find_user_by_max_id(
        self,
        db: AsyncSession,
        max_user_id: int,
    ) -> User | None:
        stmt = select(User).where(User.max_user_id == max_user_id)
        return (await db.scalars(stmt)).first()

    async def _get_or_create_open_session(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> SupportSession:
        session = await self._get_or_create_user_session(db, user_id)
        if not session.is_open:
            session.is_open = True
            session.closed_at = None
        return session

    async def _get_user_session(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> SupportSession | None:
        return (
            await db.scalars(
                select(SupportSession)
                .where(
                    SupportSession.user_id == user_id,
                )
                .limit(1)
            )
        ).first()

    async def _get_or_create_user_session(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> SupportSession:
        session = await self._get_user_session(db, user_id)
        if session is not None:
            return session

        session = SupportSession(
            user_id=user_id,
            is_open=False,
            closed_at=datetime.now(timezone.utc),
        )
        db.add(session)
        await db.flush()
        return session

    async def _get_open_session(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> SupportSession | None:
        session = await self._get_user_session(db, user_id)
        if session and session.is_open:
            return session
        return None

    async def _has_open_support_session_for_message(self, message: Message) -> bool:
        async with self._session_factory() as db:
            user = await self._upsert_user_from_message(db, message)
            if not user:
                await db.commit()
                return False

            session = await self._get_open_session(db, user.id)
            await db.commit()
            return session is not None

    async def _log_incoming_user_message(self, message: Message) -> None:
        sender = message.sender
        if sender is None or sender.is_bot:
            return

        try:
            await self._log_support_message_from_message(
                message=message,
                sender_role="user",
                text=self._message_text(message) or None,
                attachment_data=self._serialize_attachments(message) or None,
                is_read=False,
                create_session_if_missing=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось записать входящее сообщение в лог", exc_info=exc)

    async def _answer_and_log(
        self,
        message: Message,
        text: str | None = None,
        attachments: list[Any] | None = None,
    ) -> None:
        await message.answer(text=text, attachments=attachments)
        await self._log_support_message_from_message(
            message=message,
            sender_role="bot",
            text=(text or "").strip() or None,
            attachment_data=self._serialize_outgoing_attachments(attachments),
            is_read=True,
            create_session_if_missing=True,
        )

    async def _send_order_step_message(
        self,
        message: Message,
        *,
        context: BaseContext,
        text: str | None = None,
        attachments: list[Any] | None = None,
    ) -> None:
        await self._delete_order_step_message(context)
        sent_message = await message.answer(text=text, attachments=attachments)
        await context.update_data(
            order_step_message_id=self._extract_sent_message_id(sent_message)
        )
        await self._log_support_message_from_message(
            message=message,
            sender_role="bot",
            text=(text or "").strip() or None,
            attachment_data=self._serialize_outgoing_attachments(attachments),
            is_read=True,
            create_session_if_missing=True,
        )

    async def _delete_order_step_message(self, context: BaseContext) -> None:
        data = dict(await context.get_data())
        message_id = str(data.get("order_step_message_id") or "").strip()
        if not message_id:
            return

        if self.bot:
            try:
                await self.bot.delete_message(message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Не удалось удалить предыдущее step-сообщение: %s",
                    exc,
                )

        await context.update_data(order_step_message_id=None)

    @staticmethod
    def _extract_sent_message_id(sent_message: Any) -> str | None:
        message = getattr(sent_message, "message", None)
        body = getattr(message, "body", None)
        message_id = getattr(body, "mid", None)
        if not message_id:
            return None
        return str(message_id)

    async def _send_to_user_and_log(
        self,
        *,
        user_id: int,
        chat_id: int | None = None,
        text: str | None = None,
        attachments: list[Any] | None = None,
    ) -> None:
        if not self.bot:
            return

        await self.bot.send_message(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            attachments=attachments,
        )
        await self._log_support_message_by_user(
            max_user_id=user_id,
            chat_id=chat_id,
            sender_role="bot",
            text=(text or "").strip() or None,
            attachment_data=self._serialize_outgoing_attachments(attachments),
            is_read=True,
        )

    async def _log_support_message_from_message(
        self,
        *,
        message: Message,
        sender_role: str,
        text: str | None,
        attachment_data: list[dict[str, Any]] | None,
        is_read: bool,
        create_session_if_missing: bool,
    ) -> None:
        async with self._session_factory() as db:
            user = await self._upsert_user_from_message(db, message)
            if not user:
                await db.commit()
                return

            if create_session_if_missing:
                support_session = await self._get_or_create_user_session(db, user.id)
            else:
                support_session = await self._get_open_session(db, user.id)
                if support_session is None:
                    await db.commit()
                    return

            db.add(
                SupportMessage(
                    session_id=support_session.id,
                    sender_role=sender_role,
                    text=text or "[Вложение]",
                    attachment_data=attachment_data,
                    is_read=is_read,
                )
            )
            await db.commit()

    async def _log_support_message_by_user(
        self,
        *,
        max_user_id: int,
        chat_id: int | None,
        sender_role: str,
        text: str | None,
        attachment_data: list[dict[str, Any]] | None,
        is_read: bool,
        support_session_id: int | None = None,
    ) -> None:
        async with self._session_factory() as db:
            user = await self._find_user_by_max_id(db, max_user_id)
            if user is None:
                user = User(
                    max_user_id=max_user_id,
                    first_name=f"User {max_user_id}",
                    last_name=None,
                    username=None,
                    chat_id=chat_id,
                )
                db.add(user)
                await db.flush()
            elif chat_id is not None:
                user.chat_id = chat_id

            support_session: SupportSession | None = None
            if support_session_id is not None:
                support_session = await db.get(SupportSession, support_session_id)

            if support_session is None:
                support_session = await self._get_or_create_user_session(db, user.id)

            db.add(
                SupportMessage(
                    session_id=support_session.id,
                    sender_role=sender_role,
                    text=text or "[Вложение]",
                    attachment_data=attachment_data,
                    is_read=is_read,
                )
            )
            await db.commit()

    async def _notify_admin_about_order(
        self,
        message: Message,
        *,
        order_id: int | None,
        product_type: str,
        mug_type: str | None,
        product_size: str | None,
        design_notes: str,
    ) -> None:
        order_label = f"#{order_id}" if order_id is not None else "без номера"
        safe_notes = design_notes.strip() or "не указаны"
        if len(safe_notes) > 400:
            safe_notes = f"{safe_notes[:397]}..."

        product_parts: list[str] = [product_type or "не указан"]
        if mug_type:
            product_parts.append(mug_type)
        if product_size:
            product_parts.append(product_size)
        product_label = " | ".join(product_parts)

        await self._notify_admin_telegram(
            user_name=self._display_name_for_message(message),
            message_text=(
                f"Заполнена заявка {order_label}. "
                f"Товар: {product_label}. "
                f"Пожелания: {safe_notes}"
            ),
        )

    async def _notify_admin_about_user_message(self, message: Message) -> None:
        preview = self._message_preview_for_notification(message)
        await self._notify_admin_telegram(
            user_name=self._display_name_for_message(message),
            message_text=preview,
        )

    async def _notify_admin_telegram(
        self,
        *,
        user_name: str,
        message_text: str,
    ) -> None:
        if not self._telegram_bot_token or not self._telegram_chat_id:
            return

        safe_message = (message_text or "").strip() or "[пустое сообщение]"
        if len(safe_message) > 800:
            safe_message = f"{safe_message[:797]}..."

        lines = [
            "Новое сообщение в МАХ!",
            f"Пользователь: {user_name}",
            f"Сообщение: {safe_message}",
        ]

        if self._admin_url:
            lines.append("")
            lines.append(
                f"Прочитайте и дайте ответ вот здесь: {self._admin_url}"
            )

        text = "\n".join(lines)
        api_url = (
            f"https://api.telegram.org/bot{self._telegram_bot_token}/sendMessage"
        )
        payload = urlencode(
            {
                "chat_id": self._telegram_chat_id,
                "text": text,
            }
        ).encode("utf-8")

        request = Request(
            api_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            await asyncio.to_thread(
                self._send_telegram_request,
                request,
            )
        except URLError as exc:
            logger.warning(
                "Не удалось отправить Telegram-уведомление: %s",
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Ошибка при отправке Telegram-уведомления: %s",
                exc,
            )

    @staticmethod
    def _send_telegram_request(request: Request) -> None:
        with urlopen(request, timeout=10) as response:
            response.read()

    def _message_preview_for_notification(self, message: Message) -> str:
        text = self._message_text(message)
        if text:
            return text

        attachments = self._serialize_attachments(message)
        if not attachments:
            return "[пустое сообщение]"

        details: list[str] = []
        for item in attachments[:3]:
            attachment_type = str(item.get("type") or "вложение")
            filename = str(item.get("filename") or "").strip()
            if filename:
                details.append(f"{attachment_type}: {filename}")
            else:
                details.append(attachment_type)

        suffix = "" if len(attachments) <= 3 else f" и еще {len(attachments) - 3}"
        return f"[Вложение] {', '.join(details)}{suffix}"

    @staticmethod
    def _display_name_for_message(message: Message) -> str:
        sender = message.sender
        if sender is None:
            return "Неизвестный пользователь"

        full_name = " ".join(
            part for part in [sender.first_name, sender.last_name] if part
        ).strip()
        if full_name:
            return full_name
        if sender.username:
            return f"@{sender.username}"
        return f"ID {sender.user_id}"

    @staticmethod
    def _message_text(message: Message) -> str:
        if not message.body or not message.body.text:
            return ""
        return message.body.text.strip()

    def _extract_phone(self, message: Message) -> str | None:
        text = self._message_text(message)
        if text and PHONE_RE.match(text):
            return self._normalize_phone(text)

        if not message.body or not message.body.attachments:
            return None

        for attachment in message.body.attachments:
            if str(getattr(attachment, "type", "")) != "contact":
                continue

            payload = getattr(attachment, "payload", None)
            if payload is None:
                continue

            payload_dict: dict[str, Any]
            if hasattr(payload, "model_dump"):
                payload_dict = payload.model_dump(exclude_none=True)
            elif isinstance(payload, dict):
                payload_dict = payload
            else:
                continue

            raw_vcf = str(payload_dict.get("vcf_info") or "")
            match = VCF_TEL_RE.search(raw_vcf)
            if match:
                return self._normalize_phone(match.group(1))

        return None

    def _extract_primary_attachment(self, message: Message) -> dict[str, Any] | None:
        for item in self._serialize_attachments(message):
            if item.get("type") in {"image", "file"}:
                return item
        return None

    def _serialize_attachments(self, message: Message) -> list[dict[str, Any]]:
        if not message.body or not message.body.attachments:
            return []

        serialized: list[dict[str, Any]] = []
        for attachment in message.body.attachments:
            item: dict[str, Any] = {
                "type": str(getattr(attachment, "type", "unknown")),
            }

            for attr in ("filename", "size"):
                value = getattr(attachment, attr, None)
                if value is not None:
                    item[attr] = value

            payload = getattr(attachment, "payload", None)
            if payload is not None:
                if hasattr(payload, "model_dump"):
                    payload_data = payload.model_dump(exclude_none=True)
                elif isinstance(payload, dict):
                    payload_data = payload
                else:
                    payload_data = {}

                item["payload"] = payload_data
                if isinstance(payload_data, dict):
                    if payload_data.get("url"):
                        item["url"] = payload_data["url"]
                    if payload_data.get("token"):
                        item["token"] = payload_data["token"]

            serialized.append(item)

        return serialized

    @staticmethod
    def _serialize_outgoing_attachments(
        attachments: list[Any] | None,
    ) -> list[dict[str, Any]] | None:
        if not attachments:
            return None

        serialized: list[dict[str, Any]] = []
        for attachment in attachments:
            item: dict[str, Any] = {
                "kind": attachment.__class__.__name__,
            }

            if isinstance(attachment, InputMedia):
                item["type"] = "input_media"
                item["path"] = Path(attachment.path).name
                item["upload_type"] = str(getattr(attachment, "type", ""))
            elif isinstance(attachment, InputMediaBuffer):
                item["type"] = "input_media_buffer"
                item["filename"] = attachment.filename or "attachment"
                item["size"] = len(attachment.buffer)
                item["upload_type"] = str(getattr(attachment, "type", ""))
            elif hasattr(attachment, "model_dump"):
                item["type"] = "model"
                item["payload"] = attachment.model_dump(exclude_none=True)
            else:
                item["type"] = "unknown"
                item["repr"] = str(attachment)

            serialized.append(item)

        return serialized

    @staticmethod
    def _normalize_phone(raw_phone: str) -> str | None:
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) < 10 or len(digits) > 15:
            return None

        if len(digits) == 11 and digits.startswith("8"):
            digits = f"7{digits[1:]}"

        return f"+{digits}"

    @staticmethod
    def _status_ru(status: str) -> str:
        mapping = {
            "new": "Новый",
            "in_progress": "В работе",
            "done": "Готов",
        }
        return mapping.get(status, status)

    @staticmethod
    def _faq_text() -> str:
        items = [f"{idx}) {question}" for idx, question in enumerate(FAQ_QUESTIONS, start=1)]
        return FAQ_HEADER + "\n".join(items)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except ValueError:
            return None
