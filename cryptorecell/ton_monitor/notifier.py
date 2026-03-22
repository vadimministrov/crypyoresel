"""Уведомления о падении цены."""

import json
import logging
from dataclasses import dataclass
from typing import Callable, Literal, Optional, Protocol, Sequence

import requests

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SentMessage:
    chat_id: str
    message_id: int


@dataclass(frozen=True)
class InlineButton:
    text: str
    kind: Literal["url", "copy"]
    value: str


class Notifier(Protocol):
    def notify(
        self,
        message: str,
        chat_id: Optional[str] = None,
        buttons: Optional[Sequence[InlineButton]] = None,
    ) -> Optional[SentMessage]:
        ...

    def edit(
        self,
        sent_message: SentMessage,
        message: str,
        buttons: Optional[Sequence[InlineButton]] = None,
    ) -> bool:
        ...


class TelegramNotifier:
    """Отправляет текстовые сообщения через Bot API и реагирует на /crypto."""

    BASE_API = "https://api.telegram.org/bot{token}"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._base_url = self.BASE_API.format(token=bot_token)
        self._send_url = f"{self._base_url}/sendMessage"
        self._edit_url = f"{self._base_url}/editMessageText"
        self._updates_url = f"{self._base_url}/getUpdates"
        self._updates_offset = 0

    def notify(
        self,
        message: str,
        chat_id: Optional[str] = None,
        buttons: Optional[Sequence[InlineButton]] = None,
    ) -> Optional[SentMessage]:
        target_chat = chat_id or self._chat_id
        payload = {"chat_id": target_chat, "text": message, "parse_mode": "HTML"}
        if buttons:
            payload["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [self._serialize_button(button)]
                        for button in buttons
                    ]
                },
                ensure_ascii=False,
            )
        try:
            response = requests.post(self._send_url, data=payload, timeout=10)
        except requests.RequestException as exc:
            logger.warning("Telegram: ошибка доставки (%s): %s", target_chat, exc)
            return None

        if not response.ok:
            logger.warning(
                "Telegram: %s (%d) в %s. Ответ: %s",
                response.reason,
                response.status_code,
                target_chat,
                response.text.strip(),
            )
            return None

        payload = response.json().get("result") or {}
        message_id = payload.get("message_id")
        if not isinstance(message_id, int):
            logger.warning("Telegram: сообщение отправлено, но message_id отсутствует для %s", target_chat)
            return None
        logger.info("Telegram: сообщение доставлено в %s", target_chat)
        return SentMessage(chat_id=str(target_chat), message_id=message_id)

    def edit(
        self,
        sent_message: SentMessage,
        message: str,
        buttons: Optional[Sequence[InlineButton]] = None,
    ) -> bool:
        payload = {
            "chat_id": sent_message.chat_id,
            "message_id": sent_message.message_id,
            "text": message,
            "parse_mode": "HTML",
        }
        if buttons:
            payload["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [self._serialize_button(button)]
                        for button in buttons
                    ]
                },
                ensure_ascii=False,
            )
        try:
            response = requests.post(self._edit_url, data=payload, timeout=10)
        except requests.RequestException as exc:
            logger.warning(
                "Telegram: ошибка редактирования (%s:%s): %s",
                sent_message.chat_id,
                sent_message.message_id,
                exc,
            )
            return False

        if response.ok:
            logger.info(
                "Telegram: сообщение отредактировано %s:%s",
                sent_message.chat_id,
                sent_message.message_id,
            )
            return True

        logger.warning(
            "Telegram edit: %s (%d) для %s:%s. Ответ: %s",
            response.reason,
            response.status_code,
            sent_message.chat_id,
            sent_message.message_id,
            response.text.strip(),
        )
        return False

    @staticmethod
    def _serialize_button(button: InlineButton) -> dict:
        if button.kind == "copy":
            return {"text": button.text, "copy_text": {"text": button.value}}
        return {"text": button.text, "url": button.value}

    def _get_updates(self) -> list[dict]:
        params = {
            "offset": self._updates_offset,
            "timeout": 0,
            "allowed_updates": ["message", "edited_message"],
        }
        try:
            response = requests.get(self._updates_url, params=params, timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.debug("Telegram: не удалось получить комманды: %s", exc)
            return []

        payload = response.json()
        updates = payload.get("result") or []
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._updates_offset = update_id + 1
        return updates

    def check_commands(self, handler: Callable[[str], None]) -> None:
        updates = self._get_updates()
        for update in updates:
            message = update.get("message") or update.get("channel_post")
            if not message:
                continue
            text = (message.get("text") or "").strip()
            if not text:
                continue
            command = text.split()[0].lower()
            if command != "/crypto":
                continue
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            if chat_id is None:
                continue
            handler(str(chat_id))
