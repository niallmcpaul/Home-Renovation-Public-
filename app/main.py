import asyncio
import importlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.db import init_db
from app.web.common import LoginRequired

WEB_MODULES = ("app.web.account", "app.web.items", "app.web.pages")


def build_private_app() -> FastAPI:
    app = FastAPI(title="Renovation Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax",
                       https_only=config.SECURE_COOKIES, max_age=60 * 60 * 24 * 30)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
    for name in WEB_MODULES:
        app.include_router(importlib.import_module(name).router)

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, _):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    return app


async def serve():
    from app.public import build_public_app
    init_db()
    servers = [
        uvicorn.Server(uvicorn.Config(build_private_app(), host=config.PRIVATE_HOST, port=config.PRIVATE_PORT, proxy_headers=False)),
        uvicorn.Server(uvicorn.Config(build_public_app(), host=config.PUBLIC_HOST, port=config.PUBLIC_PORT,
                                      proxy_headers=True, forwarded_allow_ips="*")),
    ]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(serve())
