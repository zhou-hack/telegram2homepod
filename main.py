"""
main.py — 入口

启动模式：
  - 仅 WebUI：不填 BOT_TOKEN，只启动 HTTP 服务 + WebUI
  - Bot + WebUI：填 BOT_TOKEN，两个界面都能用
"""

import asyncio
import logging
import sys

import config
import history
import http_server
import webui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# Windows 上 asyncio 默认用 ProactorEventLoop，pyatv/aiohttp 需要 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _services_start(app=None):
    await http_server.start()
    await webui.start()
    if app is not None:
        from telegram import BotCommand
        import bot
        await app.bot.set_my_commands(
            [BotCommand(cmd, desc) for cmd, desc in bot.BOT_COMMANDS]
        )
    logger.info(f"WebUI: http://{config.HTTP_HOST}:{config.WEBUI_PORT}/?token={config.WEBUI_TOKEN}")
    logger.info("所有服务就绪")


async def _services_stop():
    await http_server.stop()
    await webui.stop()


def run_webui_only():
    async def _main():
        await _services_start()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await _services_stop()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


def run_with_bot():
    from telegram.ext import Application
    import bot

    async def post_init(app: Application):
        await _services_start(app)

    async def post_shutdown(app: Application):
        await _services_stop()

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    bot.register(app)
    logger.info("Bot + WebUI 启动中…")
    app.run_polling(drop_pending_updates=True)


def main():
    config.validate()
    history.load()

    if config.TELEGRAM_ENABLED:
        run_with_bot()
    else:
        logger.info("BOT_TOKEN 未设置，仅启动 WebUI 模式")
        run_webui_only()


if __name__ == "__main__":
    main()
