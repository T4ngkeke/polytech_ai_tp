"""
test_monitoring_access.py — only a current admin gets through to Grafana.
"""

import pytest

from backend.app.auth import create_access_token
from backend.app.models import UserRole
from backend.app.routers.monitoring import COOKIE, monitoring_token
from backend.tests.conftest import make_client, make_user


@pytest.mark.asyncio
async def test_only_a_current_admin_passes_the_grafana_check(db_session):
    admin, teacher = make_user(role=UserRole.admin), make_user(role=UserRole.teacher)
    db_session.add_all([admin, teacher])
    await db_session.commit()
    client = await make_client(db_session, admin)
    async with client:
        opened = await client.post("/api/admin/monitoring/session")
        assert opened.status_code == 204
        cookie = opened.headers["set-cookie"]
        assert COOKIE in cookie and "HttpOnly" in cookie and "Path=/grafana/" in cookie
        assert "samesite=strict" in cookie.lower()
        token = cookie.split(f"{COOKIE}=", 1)[1].split(";", 1)[0].strip('"')

        async def check(value):
            client.cookies.clear()
            if value:
                client.cookies.set(COOKIE, value)
            return (await client.get("/api/admin/monitoring/check")).status_code

        assert await check(token) == 204
        assert await check(None) == 401
        assert await check("forged") == 401
        assert await check(create_access_token(admin.id)) == 401    # an app token is not a monitoring cookie
        assert await check(monitoring_token(teacher.id)) == 401     # not an admin
        admin.role = UserRole.teacher                               # demoted: the cookie stops working
        await db_session.commit()
        assert await check(token) == 401

    other = await make_client(db_session, teacher)
    async with other:
        assert (await other.post("/api/admin/monitoring/session")).status_code == 403
