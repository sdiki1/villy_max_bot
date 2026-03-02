from __future__ import annotations

from maxapi.enums.attachment import AttachmentType
from maxapi.types import (
    Attachment,
    ButtonsPayload,
    MessageButton,
    RequestContactButton,
)

from app.bot.constants import (
    BTN_BACK,
    BTN_END_SUPPORT,
    BTN_FAQ,
    BTN_MY_ORDERS,
    BTN_ORDER,
    BTN_SHARE_CONTACT,
    BTN_SUPPORT,
    MUG_OPTIONS,
    PRODUCT_OPTIONS,
    SOURCE_OPTIONS,
)


def _kb(rows: list[list[MessageButton | RequestContactButton]]) -> Attachment:
    return Attachment(
        type=AttachmentType.INLINE_KEYBOARD,
        payload=ButtonsPayload(buttons=rows),
    )


def main_menu_keyboard() -> Attachment:
    return _kb(
        [
            [MessageButton(text=BTN_ORDER)],
            [MessageButton(text=BTN_MY_ORDERS)],
            [MessageButton(text=BTN_FAQ)],
            [MessageButton(text=BTN_SUPPORT)],
        ]
    )


def phone_request_keyboard() -> Attachment:
    return _kb([[RequestContactButton(text=BTN_SHARE_CONTACT)]])


def product_keyboard() -> Attachment:
    return _kb([[MessageButton(text=title)] for title in PRODUCT_OPTIONS])


def mug_keyboard() -> Attachment:
    return _kb([[MessageButton(text=title)] for title in MUG_OPTIONS])


def source_keyboard() -> Attachment:
    return _kb([[MessageButton(text=title)] for title in SOURCE_OPTIONS])


def faq_keyboard() -> Attachment:
    rows: list[list[MessageButton]] = []
    numeric = [MessageButton(text=str(num)) for num in range(1, 17)]
    for idx in range(0, len(numeric), 4):
        rows.append(numeric[idx : idx + 4])
    rows.append([MessageButton(text=BTN_BACK)])
    return _kb(rows)


def support_keyboard() -> Attachment:
    return _kb([[MessageButton(text=BTN_END_SUPPORT)]])
