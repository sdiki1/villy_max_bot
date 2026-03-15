from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import SessionFactory
from app.models import (
    MessageTemplate,
    SupportMessage,
    SupportSession,
    User,
    WbAutoReplySetting,
)
from app.web.auth import authenticate_admin, is_admin_authenticated

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/admin", tags=["admin"])
_WB_SETTING_ID = 1
_DEFAULT_FEEDBACK_AI_PROMPT = (
    "Ты менеджер поддержки магазина VillyPrint на Wildberries.\n"
    "Сформируй короткий, вежливый и естественный ответ на отзыв покупателя.\n"
    "Пиши только на русском языке, без эмодзи, без шаблонных канцеляризмов.\n"
    "Если отзыв положительный — поблагодари.\n"
    "Если отзыв содержит проблему — извинись и предложи связаться с поддержкой для решения.\n"
    "Не выдумывай факты, которых нет в отзыве.\n"
    "Верни только текст ответа (2-5000 символов), без кавычек и без служебных комментариев."
)


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=303)


def _ensure_api_admin(request: Request) -> None:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="Требуется авторизация")


def _serialize_wb_auto_reply_setting(
    setting: WbAutoReplySetting,
) -> dict[str, str | bool]:
    return {
        "is_enabled": setting.is_enabled,
        "answer_template": setting.answer_template,
        "feedback_ai_enabled": setting.feedback_ai_enabled,
        "feedback_ai_prompt": setting.feedback_ai_prompt,
        "updated_at": setting.updated_at.isoformat(),
    }


def _serialize_support_message(message: SupportMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "sender_role": message.sender_role,
        "text": message.text or "",
        "attachment_data": message.attachment_data or [],
        "created_at": message.created_at.isoformat(),
        "max_message_id": message.max_message_id or "",
    }


async def _get_or_create_wb_auto_reply_setting(
    db: AsyncSession,
) -> WbAutoReplySetting:
    setting = await db.get(WbAutoReplySetting, _WB_SETTING_ID)
    if setting is not None:
        if not setting.feedback_ai_prompt:
            setting.feedback_ai_prompt = _DEFAULT_FEEDBACK_AI_PROMPT
            await db.commit()
            await db.refresh(setting)
        return setting

    setting = WbAutoReplySetting(
        id=_WB_SETTING_ID,
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
        setting = await db.get(WbAutoReplySetting, _WB_SETTING_ID)
        if setting is None:
            raise
        return setting

    await db.refresh(setting)
    return setting


@router.get("", include_in_schema=False)
async def admin_root() -> RedirectResponse:
    return RedirectResponse(url="/admin/chats", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_admin_authenticated(request):
        return RedirectResponse(url="/admin/chats", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    if authenticate_admin(username=username, password=password):
        request.session["admin_logged_in"] = True
        request.session["admin_name"] = username
        return RedirectResponse(url="/admin/chats", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Неверный логин или пароль"},
        status_code=401,
    )


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/chats", response_class=HTMLResponse)
async def chats_page(
    request: Request,
    session_id: int | None = None,
    message_id: int | None = None,
    archived: int = 0,
):
    if not is_admin_authenticated(request):
        return _redirect_to_login()

    archived_view = bool(archived)
    async with SessionFactory() as db:
        sessions = (
            await db.scalars(
                select(SupportSession)
                .options(selectinload(SupportSession.user))
                .where(SupportSession.user.has(User.is_archived.is_(archived_view)))
                .order_by(SupportSession.is_open.desc(), SupportSession.created_at.desc())
            )
        ).all()
        if session_id is not None and all(item.id != session_id for item in sessions):
            target_session = await db.scalar(
                select(SupportSession)
                .options(selectinload(SupportSession.user))
                .where(SupportSession.id == session_id)
            )
            if target_session is not None:
                sessions = [target_session, *sessions]

        unread_rows = await db.execute(
            select(SupportMessage.session_id, func.count(SupportMessage.id))
            .where(
                SupportMessage.sender_role == "user",
                SupportMessage.is_read.is_(False),
            )
            .group_by(SupportMessage.session_id)
        )
        unread_counts = {row[0]: row[1] for row in unread_rows.all()}

        selected_session: SupportSession | None = None
        messages: list[SupportMessage] = []
        message_templates = (
            await db.scalars(
                select(MessageTemplate).order_by(
                    MessageTemplate.updated_at.desc(),
                    MessageTemplate.id.desc(),
                )
            )
        ).all()
        if sessions:
            selected_id = session_id or sessions[0].id
            selected_session = next(
                (item for item in sessions if item.id == selected_id),
                None,
            )
            if selected_session is None:
                selected_session = sessions[0]

            if selected_session:
                messages = (
                    await db.scalars(
                        select(SupportMessage)
                        .join(
                            SupportSession,
                            SupportMessage.session_id == SupportSession.id,
                        )
                        .where(SupportSession.user_id == selected_session.user_id)
                        .order_by(SupportMessage.id.asc())
                    )
                ).all()

                unread_messages = [
                    msg for msg in messages if msg.sender_role == "user" and not msg.is_read
                ]
                if unread_messages:
                    for msg in unread_messages:
                        msg.is_read = True
                    await db.commit()

    return templates.TemplateResponse(
        request=request,
        name="chats.html",
        context={
            "admin_name": request.session.get("admin_name") or "admin",
            "sessions": sessions,
            "selected_session": selected_session,
            "messages": messages,
            "unread_counts": unread_counts,
            "templates": message_templates,
            "archived_view": archived_view,
            "target_message_id": message_id,
        },
    )


@router.get("/wb", response_class=HTMLResponse)
async def wb_page(request: Request):
    if not is_admin_authenticated(request):
        return _redirect_to_login()

    async with SessionFactory() as db:
        wb_auto_reply_setting = await _get_or_create_wb_auto_reply_setting(db)

    return templates.TemplateResponse(
        request=request,
        name="wb.html",
        context={
            "admin_name": request.session.get("admin_name") or "admin",
            "wb_auto_reply": _serialize_wb_auto_reply_setting(wb_auto_reply_setting),
        },
    )


@router.get("/api/chats/{session_id}/messages")
async def api_get_messages(
    request: Request,
    session_id: int,
    after_id: int = 0,
) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        support_session = await db.scalar(
            select(SupportSession).where(SupportSession.id == session_id)
        )
        if not support_session:
            raise HTTPException(status_code=404, detail="Чат не найден")

        messages = (
            await db.scalars(
                select(SupportMessage)
                .join(
                    SupportSession,
                    SupportMessage.session_id == SupportSession.id,
                )
                .where(
                    SupportSession.user_id == support_session.user_id,
                    SupportMessage.id > after_id,
                )
                .order_by(SupportMessage.id.asc())
            )
        ).all()

        updated = False
        for message in messages:
            if message.sender_role == "user" and not message.is_read:
                message.is_read = True
                updated = True

        if updated:
            await db.commit()

    return JSONResponse(
        {
            "messages": [
                _serialize_support_message(msg)
                for msg in messages
            ]
        }
    )


@router.post("/api/chats/{session_id}/messages")
async def api_send_message(
    request: Request,
    session_id: int,
    text: Annotated[str | None, Form()] = None,
    file: UploadFile | None = File(default=None),
) -> JSONResponse:
    _ensure_api_admin(request)

    clean_text = (text or "").strip()
    file_bytes: bytes | None = None
    if file is not None:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Файл пустой")

    if not clean_text and not file_bytes:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    async with SessionFactory() as db:
        support_session = await db.scalar(
            select(SupportSession)
            .options(selectinload(SupportSession.user))
            .where(SupportSession.id == session_id)
        )

        if not support_session:
            raise HTTPException(status_code=404, detail="Чат не найден")

        bot_service = getattr(request.app.state, "bot_service", None)
        if bot_service is None:
            raise HTTPException(status_code=503, detail="Сервис бота не инициализирован")

        try:
            await bot_service.send_admin_message(
                user_id=support_session.user.max_user_id,
                chat_id=support_session.user.chat_id,
                text=clean_text or None,
                file_bytes=file_bytes,
                file_name=file.filename if file is not None else None,
                file_content_type=file.content_type if file is not None else None,
                support_session_id=support_session.id,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось отправить сообщение в MAX: {exc}",
            ) from exc

        message = await db.scalar(
            select(SupportMessage)
            .where(
                SupportMessage.session_id == support_session.id,
                SupportMessage.sender_role == "admin",
            )
            .order_by(SupportMessage.id.desc())
            .limit(1)
        )
        if message is None:
            raise HTTPException(
                status_code=500,
                detail="Сообщение отправлено, но не найдено в логе",
            )

    return JSONResponse(
        {
            "message": _serialize_support_message(message)
        }
    )


@router.post("/api/chats/{session_id}/close")
async def api_close_chat(
    request: Request,
    session_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        support_session = await db.get(SupportSession, session_id)
        if not support_session:
            raise HTTPException(status_code=404, detail="Чат не найден")

        if support_session.is_open:
            support_session.is_open = False
            support_session.closed_at = datetime.now(timezone.utc)
            await db.commit()

    return JSONResponse({"ok": True})


@router.put("/api/chats/{session_id}/user")
async def api_update_chat_user(
    request: Request,
    session_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)
    payload = await request.json()
    display_name = str(payload.get("display_name") or "").strip()

    async with SessionFactory() as db:
        support_session = await db.scalar(
            select(SupportSession)
            .options(selectinload(SupportSession.user))
            .where(SupportSession.id == session_id)
        )
        if support_session is None:
            raise HTTPException(status_code=404, detail="Чат не найден")

        user = support_session.user
        user.admin_display_name = display_name or None
        await db.commit()
        await db.refresh(user)

    effective_name = user.admin_display_name or user.full_name or user.first_name
    return JSONResponse(
        {
            "user": {
                "id": user.id,
                "display_name": effective_name,
                "admin_display_name": user.admin_display_name or "",
                "username": user.username or "",
                "is_archived": user.is_archived,
            }
        }
    )


@router.put("/api/chats/{session_id}/archive")
async def api_archive_chat_user(
    request: Request,
    session_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)
    payload = await request.json()
    raw_is_archived = payload.get("is_archived")
    is_archived = True if raw_is_archived is None else bool(raw_is_archived)

    async with SessionFactory() as db:
        support_session = await db.scalar(
            select(SupportSession)
            .options(selectinload(SupportSession.user))
            .where(SupportSession.id == session_id)
        )
        if support_session is None:
            raise HTTPException(status_code=404, detail="Чат не найден")

        user = support_session.user
        user.is_archived = is_archived
        if is_archived and support_session.is_open:
            support_session.is_open = False
            support_session.closed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "is_archived": user.is_archived,
            },
        }
    )


@router.delete("/api/messages/{message_id}")
async def api_delete_message_for_all(
    request: Request,
    message_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        message = await db.get(SupportMessage, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")

        if not message.max_message_id:
            raise HTTPException(
                status_code=400,
                detail="Для этого сообщения удаление у всех недоступно",
            )

        bot_service = getattr(request.app.state, "bot_service", None)
        if bot_service is None or not getattr(bot_service, "bot", None):
            raise HTTPException(status_code=503, detail="Сервис бота не инициализирован")

        try:
            await bot_service.bot.delete_message(message_id=message.max_message_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось удалить сообщение у всех: {exc}",
            ) from exc

        await db.delete(message)
        await db.commit()

    return JSONResponse({"ok": True})


@router.get("/api/templates")
async def api_list_templates(request: Request) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        templates = (
            await db.scalars(
                select(MessageTemplate).order_by(
                    MessageTemplate.updated_at.desc(),
                    MessageTemplate.id.desc(),
                )
            )
        ).all()

    return JSONResponse(
        {
            "templates": [
                {
                    "id": item.id,
                    "title": item.title,
                    "text": item.text,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in templates
            ]
        }
    )


@router.post("/api/templates")
async def api_create_template(request: Request) -> JSONResponse:
    _ensure_api_admin(request)

    payload = await request.json()
    title = str(payload.get("title") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название шаблона пустое")
    if not text:
        raise HTTPException(status_code=400, detail="Текст шаблона пустой")

    async with SessionFactory() as db:
        template = MessageTemplate(title=title, text=text)
        db.add(template)
        await db.commit()
        await db.refresh(template)

    return JSONResponse(
        {
            "template": {
                "id": template.id,
                "title": template.title,
                "text": template.text,
                "created_at": template.created_at.isoformat(),
                "updated_at": template.updated_at.isoformat(),
            }
        }
    )


@router.put("/api/templates/{template_id}")
async def api_update_template(
    request: Request,
    template_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)

    payload = await request.json()
    title = str(payload.get("title") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название шаблона пустое")
    if not text:
        raise HTTPException(status_code=400, detail="Текст шаблона пустой")

    async with SessionFactory() as db:
        template = await db.get(MessageTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="Шаблон не найден")

        template.title = title
        template.text = text
        await db.commit()
        await db.refresh(template)

    return JSONResponse(
        {
            "template": {
                "id": template.id,
                "title": template.title,
                "text": template.text,
                "created_at": template.created_at.isoformat(),
                "updated_at": template.updated_at.isoformat(),
            }
        }
    )


@router.get("/api/wb/auto-reply")
async def api_get_wb_auto_reply_settings(request: Request) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        setting = await _get_or_create_wb_auto_reply_setting(db)

    return JSONResponse({"settings": _serialize_wb_auto_reply_setting(setting)})


@router.put("/api/wb/auto-reply")
async def api_update_wb_auto_reply_settings(request: Request) -> JSONResponse:
    _ensure_api_admin(request)

    payload = await request.json()
    raw_is_enabled = payload.get("is_enabled")
    raw_answer_template = payload.get("answer_template")
    raw_feedback_ai_enabled = payload.get("feedback_ai_enabled")
    raw_feedback_ai_prompt = payload.get("feedback_ai_prompt")

    async with SessionFactory() as db:
        setting = await _get_or_create_wb_auto_reply_setting(db)

        is_enabled = setting.is_enabled if raw_is_enabled is None else bool(raw_is_enabled)
        answer_template = (
            setting.answer_template
            if raw_answer_template is None
            else str(raw_answer_template or "").strip()
        )

        feedback_ai_enabled = (
            setting.feedback_ai_enabled
            if raw_feedback_ai_enabled is None
            else bool(raw_feedback_ai_enabled)
        )
        feedback_ai_prompt = (
            setting.feedback_ai_prompt
            if raw_feedback_ai_prompt is None
            else str(raw_feedback_ai_prompt or "").strip()
        )

        if is_enabled and not answer_template:
            raise HTTPException(
                status_code=400,
                detail="Чтобы включить автоответы на вопросы, заполните шаблон ответа",
            )

        if len(answer_template) > 5000:
            raise HTTPException(
                status_code=400,
                detail="Шаблон ответа на вопросы слишком длинный (максимум 5000 символов)",
            )

        if feedback_ai_enabled and not feedback_ai_prompt:
            raise HTTPException(
                status_code=400,
                detail="Чтобы включить AI-ответы на отзывы, заполните AI промпт",
            )

        if len(feedback_ai_prompt) > 8000:
            raise HTTPException(
                status_code=400,
                detail="AI промпт для отзывов слишком длинный (максимум 8000 символов)",
            )

        setting.is_enabled = is_enabled
        setting.answer_template = answer_template
        setting.feedback_ai_enabled = feedback_ai_enabled
        setting.feedback_ai_prompt = feedback_ai_prompt
        await db.commit()
        await db.refresh(setting)

    return JSONResponse({"settings": _serialize_wb_auto_reply_setting(setting)})


@router.post("/api/chats/{session_id}/templates/{template_id}/send")
async def api_send_template_to_chat(
    request: Request,
    session_id: int,
    template_id: int,
) -> JSONResponse:
    _ensure_api_admin(request)

    async with SessionFactory() as db:
        support_session = await db.scalar(
            select(SupportSession)
            .options(selectinload(SupportSession.user))
            .where(SupportSession.id == session_id)
        )
        if support_session is None:
            raise HTTPException(status_code=404, detail="Чат не найден")

        template = await db.get(MessageTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="Шаблон не найден")

        bot_service = getattr(request.app.state, "bot_service", None)
        if bot_service is None:
            raise HTTPException(
                status_code=503,
                detail="Сервис бота не инициализирован",
            )

        try:
            await bot_service.send_admin_message(
                user_id=support_session.user.max_user_id,
                chat_id=support_session.user.chat_id,
                text=template.text,
                support_session_id=support_session.id,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось отправить шаблон: {exc}",
            ) from exc

        message = await db.scalar(
            select(SupportMessage)
            .where(
                SupportMessage.session_id == support_session.id,
                SupportMessage.sender_role == "admin",
            )
            .order_by(SupportMessage.id.desc())
            .limit(1)
        )
        if message is None:
            raise HTTPException(
                status_code=500,
                detail="Шаблон отправлен, но сообщение не найдено в логе",
            )

    return JSONResponse(
        {
            "message": _serialize_support_message(message)
        }
    )
