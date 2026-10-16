"""Hashing or checking a password does not stop the other requests.

bcrypt takes about 0.2 s of CPU. On the event loop it froze every request,
answers being streamed included, while a class logged in (2026-10-16).
"""
import asyncio
import time

import pytest

from backend.app import auth


@pytest.mark.asyncio
async def test_the_event_loop_keeps_running_while_a_password_is_checked(monkeypatch):
    class SlowContext:
        def verify(self, plain, hashed):
            time.sleep(0.3)
            return True

        def hash(self, plain):
            time.sleep(0.3)
            return 'hashed'

    monkeypatch.setattr(auth, '_pwd_context', SlowContext())
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        assert await auth.verify_password_async('x', 'y')
        assert await auth.hash_password_async('x') == 'hashed'
    finally:
        task.cancel()
    # 0.6 s of hashing: a blocked loop would have ticked about twice.
    assert ticks > 20
