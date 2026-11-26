from __future__ import annotations

import asyncio
import multiprocessing
from contextlib import suppress

import uvicorn

from .api import create_app
from .container import Container


async def serve() -> None:
    container = Container()
    app = create_app(container)
    config = uvicorn.Config(
        app,
        host=container.settings.api_host,
        port=container.settings.api_port,
        log_level=container.settings.log_level.casefold(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    app.state.server = server
    await server.serve()


def main() -> None:
    multiprocessing.freeze_support()
    with suppress(KeyboardInterrupt):
        asyncio.run(serve())


if __name__ == "__main__":
    main()
