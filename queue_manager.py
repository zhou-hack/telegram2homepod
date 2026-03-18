"""
queue_manager.py — 播放队列

暂停/继续方案：
  - 暂停：cancel 推流 task，记录已播放秒数（用 time 计算）
  - 继续：用 ffmpeg 从断点截取剩余音频，再 stream_file 推过去
  - 这样 HomePod 不会跳回 Siri 状态

临时文件清理：
  - 每首歌播完（正常结束 or skip）后，删除对应的 _resume_*.mp3 临时文件
"""

import asyncio
import logging
import os
import subprocess
import time
from collections import deque

import airplay
import history

logger = logging.getLogger(__name__)

_queue: deque[dict] = deque()
_current: dict = {}
_play_task: asyncio.Task | None = None

# 暂停状态
_paused: bool = False
_pause_position: float = 0.0   # 已播放秒数
_play_start_time: float = 0.0  # 本段开始推流的时间戳


# ---------- 内部工具 ----------

def _trim_audio(src: str, start: float) -> str:
    """从 start 秒处截取剩余音频到临时文件，返回临时文件路径"""
    tmp = src.replace(".mp3", f"_resume_{int(start)}.mp3")
    if os.path.exists(tmp):
        return tmp
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-acodec", "copy", tmp],
        capture_output=True, timeout=30
    )
    return tmp


def _cleanup_resume_files(src: str):
    """删除 src 对应的所有 _resume_*.mp3 临时文件"""
    if not src:
        return
    folder = os.path.dirname(src)
    base = os.path.basename(src).replace(".mp3", "")
    try:
        for f in os.listdir(folder):
            if f.startswith(base + "_resume_") and f.endswith(".mp3"):
                full = os.path.join(folder, f)
                os.remove(full)
                logger.info(f"已删除临时文件: {full}")
    except Exception as e:
        logger.warning(f"清理临时文件失败: {e}")


async def _stream_from(file_path: str, start_sec: float = 0.0):
    """推流，支持从 start_sec 秒开始"""
    global _play_start_time
    if start_sec > 0:
        src = await asyncio.get_event_loop().run_in_executor(
            None, _trim_audio, file_path, start_sec
        )
    else:
        src = file_path
    _play_start_time = time.time()
    await airplay.stream_file(src)


async def _play_loop():
    global _current, _play_task, _paused, _pause_position, _play_start_time

    while _queue:
        item = _queue.popleft()
        _current = item
        _paused = False
        _pause_position = 0.0
        history.add(item)

        logger.info(f"开始播放: {item['title']}")
        try:
            await _stream_from(item["file_path"], 0.0)
            # 正常播完 → 清理该曲的所有 resume 临时文件
            _cleanup_resume_files(item["file_path"])
        except asyncio.CancelledError:
            # skip/stop/pause 触发，临时文件留着（pause 续播还要用）
            logger.info(f"播放中断: {item['title']}")
            break
        except Exception as e:
            logger.error(f"播放失败: {e}  跳过")
            _cleanup_resume_files(item["file_path"])
            continue

    if not _paused:
        _current = {}
    _play_task = None


def _cancel_task():
    global _play_task
    if _play_task and not _play_task.done():
        _play_task.cancel()
        _play_task = None


def _start_task():
    global _play_task
    if _play_task is None or _play_task.done():
        _play_task = asyncio.create_task(_play_loop())


# ---------- 公开接口 ----------

def add(item: dict, play_now: bool = False):
    global _paused
    if play_now:
        _cancel_task()
        _queue.clear()
        _paused = False
        _queue.appendleft(item)
    else:
        _queue.append(item)
    if not _paused:
        _start_task()


def queue_list() -> list[dict]:
    return list(_queue)


def current() -> dict:
    return dict(_current)


def is_playing() -> bool:
    return bool(_current) and _play_task is not None and not _play_task.done() and not _paused


def is_paused() -> bool:
    return _paused


def queue_size() -> int:
    return len(_queue)


async def pause() -> bool:
    global _paused, _pause_position, _play_start_time
    if not _current or _paused:
        return False
    elapsed = time.time() - _play_start_time
    _pause_position += elapsed
    _paused = True
    _cancel_task()
    await airplay.stop_stream()
    logger.info(f"暂停在 {_pause_position:.1f}s")
    return True


async def resume() -> bool:
    global _paused, _play_task, _play_start_time
    if not _current or not _paused:
        return False
    _paused = False
    item = _current
    pos = _pause_position
    logger.info(f"从 {pos:.1f}s 继续播放: {item['title']}")

    async def _resume_task():
        try:
            await _stream_from(item["file_path"], pos)
            # resume 推完 → 清理临时文件，继续队列
            _cleanup_resume_files(item["file_path"])
            _start_task()
        except asyncio.CancelledError:
            pass  # 又被暂停了，临时文件暂时保留

    _play_task = asyncio.create_task(_resume_task())
    return True


async def skip_next() -> bool:
    global _paused
    if _current:
        _cleanup_resume_files(_current.get("file_path", ""))
    _paused = False
    if not _queue:
        return False
    _cancel_task()
    _start_task()
    return True


async def skip_prev() -> dict | None:
    global _paused
    if _current:
        _cleanup_resume_files(_current.get("file_path", ""))
    _paused = False
    all_hist = history.get_all()
    if len(all_hist) < 2:
        return None
    prev = all_hist[-2]
    _cancel_task()
    _queue.appendleft(prev)
    _start_task()
    return prev


async def stop_all():
    global _current, _paused
    if _current:
        _cleanup_resume_files(_current.get("file_path", ""))
    _paused = False
    _cancel_task()
    _queue.clear()
    _current = {}
    await airplay.stop_stream()


def clear_resume_cache() -> int:
    """
    扫描 DOWNLOAD_DIR，删除所有 _resume_*.mp3 临时文件。
    返回删除文件数。
    """
    from config import DOWNLOAD_DIR
    count = 0
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            if "_resume_" in f and f.endswith(".mp3"):
                full = os.path.join(DOWNLOAD_DIR, f)
                try:
                    os.remove(full)
                    count += 1
                    logger.info(f"清除 resume 缓存: {full}")
                except Exception as e:
                    logger.warning(f"删除失败: {full} — {e}")
    except Exception as e:
        logger.warning(f"扫描目录失败: {e}")
    return count
