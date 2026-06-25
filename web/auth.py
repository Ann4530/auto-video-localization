"""Bảo vệ truy cập khi mở web ra public (Cloudflare Tunnel / VPS).

Cơ chế (đơn giản, hợp single-user):
- Đặt APP_PASSWORD trong .env -> bật bảo vệ. Người dùng đăng nhập ở /login,
  server set cookie ký bằng HMAC. Để TRỐNG = chế độ local mở (không cần đăng nhập).
- API_KEY (header X-API-Key) dùng cho máy gọi (n8n). Cũng được middleware chấp nhận.

Không thêm dependency: dùng hmac/hashlib trong thư viện chuẩn.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

COOKIE = "ls_session"
_OPEN_PREFIXES = ("/login", "/static", "/logout")
_OPEN_EXACT = ("/healthz", "/favicon.ico")


def make_token(password: str) -> str:
    """Token cookie suy ra từ mật khẩu (đổi mật khẩu -> phiên cũ vô hiệu)."""
    return hmac.new(b"localize-studio", password.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def check_token(token: str | None, password: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, make_token(password))


class AuthMiddleware(BaseHTTPMiddleware):
    """Chặn mọi route nếu chưa xác thực (khi đã bật bảo vệ)."""

    def __init__(self, app, get_password: Callable[[], str],
                 get_api_key: Callable[[], str]):
        super().__init__(app)
        self.get_password = get_password
        self.get_api_key = get_api_key

    async def dispatch(self, request, call_next):
        password = self.get_password()
        api_key = self.get_api_key()
        # Không đặt gì -> local mở hoàn toàn
        if not password and not api_key:
            return await call_next(request)

        path = request.url.path
        if path in _OPEN_EXACT or any(path.startswith(p) for p in _OPEN_PREFIXES):
            return await call_next(request)

        # 1) cookie phiên (trình duyệt sau khi đăng nhập)
        if password and check_token(request.cookies.get(COOKIE), password):
            return await call_next(request)
        # 2) header X-API-Key (n8n / máy gọi)
        if api_key and request.headers.get("x-api-key") == api_key:
            return await call_next(request)

        # Từ chối
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Cần đăng nhập hoặc X-API-Key"}, status_code=401)
        nxt = request.url.path
        return RedirectResponse(f"/login?next={nxt}", status_code=302)
