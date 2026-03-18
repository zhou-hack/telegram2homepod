"""
http_server.py — 本地 HTTP 文件服务

HomePod 通过 AirPlay play_url 拉取音频，需要一个可访问的 HTTP URL。
这里起一个简单的静态文件服务，只暴露 DOWNLOAD_DIR 目录。

URL 格式: http://{HTTP_HOST}:{HTTP_PORT}/{video_id}.mp3
"""

import asyncio
import logging
import os
from aiohttp import web

from config import DOWNLOAD_DIR, HTTP_HOST, HTTP_PORT

logger = logging.getLogger(__name__)
_runner: web.AppRunner | None = None


def file_url(video_id: str) -> str:
    """生成供 HomePod 访问的 HTTP URL"""
    return f"http://{HTTP_HOST}:{HTTP_PORT}/{video_id}.mp3"


def file_url_from_path(file_path: str) -> str:
    filename = os.path.basename(file_path)
    return f"http://{HTTP_HOST}:{HTTP_PORT}/{filename}"


async def start():
    global _runner
    app = web.Application()

    async def serve_file(request: web.Request):
        filename = request.match_info["filename"]
        # 安全检查：只允许 .mp3 文件，禁止路径穿越
        if not filename.endswith(".mp3") or "/" in filename or ".." in filename:
            raise web.HTTPForbidden()
        full_path = os.path.join(DOWNLOAD_DIR, filename)
        if not os.path.exists(full_path):
            raise web.HTTPNotFound()
        return web.FileResponse(full_path)

    app.router.add_get("/{filename}", serve_file)

    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    logger.info(f"HTTP 文件服务启动: http://0.0.0.0:{HTTP_PORT}  (对外: http://{HTTP_HOST}:{HTTP_PORT})")


async def stop():
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
