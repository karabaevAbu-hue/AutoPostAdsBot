"""Одноразовая генерация сессии НА ПК Исполнителя при личной встрече.

Заказчик сама вводит номер, код и пароль 2FA. Код вводить с разделителями
(например 55-555) и НЕ пересылать через Telegram — он сгорит.
На выходе — зашифрованная строка (Fernet). Открытая StringSession нигде не сохраняется.

  API_ID=... API_HASH=... FERNET_KEY=... python scripts/generate_session.py
"""
import asyncio
import os
import sys

from cryptography.fernet import Fernet
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id, api_hash = int(os.environ["API_ID"]), os.environ["API_HASH"]
    key = os.environ["FERNET_KEY"]
    Fernet(key.encode())  # валидация ключа
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start()  # спросит телефон, код, пароль
        me = await client.get_me()
        phone = me.phone or ""
        masked = f"+{phone[:3]}***{phone[-2:]}" if len(phone) > 5 else "***"
        enc = Fernet(key.encode()).encrypt(client.session.save().encode()).decode()
    print(f"PHONE_MASKED={masked}", file=sys.stderr)
    print(enc)


asyncio.run(main())
