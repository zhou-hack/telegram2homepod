"""
airplay.py — HomePod 连接与播放控制

play_url 在 HomePod 上不被 pyatv 支持，改回 stream_file。
暂停/继续由 queue_manager 控制（cancel task + 记录时间 + 断点续播），
不走 remote_control.pause()，避免 HomePod 回到 Siri 状态。
"""

import asyncio
import logging
import pyatv

from config import HOMEPOD_ID

logger = logging.getLogger(__name__)

_atv: pyatv.interface.AppleTV | None = None


async def _get_atv() -> pyatv.interface.AppleTV | None:
    global _atv
    loop = asyncio.get_event_loop()
    try:
        if _atv is None:
            configs = await pyatv.scan(loop, identifier=HOMEPOD_ID, timeout=5)
            if not configs:
                logger.error("找不到 HomePod，请检查 HOMEPOD_ID 和网络")
                return None
            _atv = await pyatv.connect(configs[0], loop)
            logger.info(f"已连接 HomePod: {configs[0].name}")
        return _atv
    except Exception as e:
        logger.error(f"连接 HomePod 失败: {e}")
        _atv = None
        return None


async def _reset():
    global _atv
    if _atv:
        try:
            _atv.close()
        except Exception:
            pass
    _atv = None


# ---------- 公开接口 ----------

async def stream_file(file_path: str) -> bool:
    """
    推流一个本地文件到 HomePod，阻塞直到播完（或被 cancel）。
    cancel 时会停止推流，HomePod 静音，不会跳回 Siri 状态。
    """
    atv = await _get_atv()
    if atv is None:
        return False
    try:
        await atv.stream.stream_file(file_path)
        return True
    except asyncio.CancelledError:
        raise   # 让 queue_manager 的 task 正常处理 cancel
    except Exception as e:
        logger.error(f"stream_file 失败: {e}")
        await _reset()
        return False


async def stop_stream():
    """强制停止推流（关闭连接，HomePod 会静音）"""
    await _reset()


async def get_volume() -> int | None:
    atv = await _get_atv()
    if atv is None:
        return None
    try:
        return int(atv.audio.volume)  # 属性，不是协程
    except Exception as e:
        logger.error(f"获取音量失败: {e}")
        return None


async def set_volume(val: int) -> bool:
    atv = await _get_atv()
    if atv is None:
        return False
    try:
        await atv.audio.set_volume(float(val))
        return True
    except Exception as e:
        logger.error(f"设置音量失败: {e}")
        return False
