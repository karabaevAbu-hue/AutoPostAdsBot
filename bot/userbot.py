import logging
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

from .crypto import decrypt

log = logging.getLogger(__name__)


class Userbot:
    def __init__(self, api_id: int, api_hash: str, fernet_key: str):
        self.api_id, self.api_hash, self.key = api_id, api_hash, fernet_key
        self.client: TelegramClient | None = None

    async def start(self, session_encrypted: str):
        session = decrypt(self.key, session_encrypted)
        self.client = TelegramClient(StringSession(session), self.api_id, self.api_hash)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("session is not authorized")

    async def stop(self):
        if self.client:
            await self.client.disconnect()

    async def groups(self) -> list[tuple[int, str]]:
        """Только группы (в т.ч. супергруппы), где аккаунт уже состоит."""
        out = []
        async for d in self.client.iter_dialogs():
            if d.is_group:
                out.append((d.id, d.name or str(d.id)))
        return out

    async def send(self, chat_id: int, text: str, photo_path: str | None = None, text_only: bool = False):
        if photo_path and not text_only and os.path.exists(photo_path):
            return await self.client.send_file(chat_id, photo_path, caption=text)
        return await self.client.send_message(chat_id, text)

    async def delete(self, chat_id: int, message_id: int):
        await self.client.delete_messages(chat_id, [message_id])
