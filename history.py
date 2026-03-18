"""
history.py — 播放历史管理
- 存储到 HISTORY_DIR/history.json
- 最多保留 CACHE_MAX 首，超出删除最旧的音频文件
- 提供按编号重播的查询接口
"""

import json
import os
import logging
from config import HISTORY_FILE, CACHE_MAX

logger = logging.getLogger(__name__)

# 内存缓存，结构: [{"title": str, "artist": str, "video_id": str, "file_path": str}, ...]
# 最新的在列表末尾
_records: list[dict] = []


def load():
    """启动时从磁盘加载"""
    global _records
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                _records = json.load(f)
            logger.info(f"历史记录加载完成，共 {len(_records)} 首")
        except Exception as e:
            logger.warning(f"历史记录加载失败: {e}")
            _records = []


def _save():
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(_records, f, ensure_ascii=False, indent=2)


def add(item: dict):
    """
    添加一条播放记录，自动去重（同 video_id 移到末尾），
    超出 CACHE_MAX 时删除最旧的条目及其音频文件。
    item 格式: {title, artist, video_id, file_path}
    """
    global _records

    # 去重：如果已有相同 video_id，先移除
    _records = [r for r in _records if r.get("video_id") != item.get("video_id")]
    _records.append(item)

    # 超出上限：删掉最旧的（列表头部）
    while len(_records) > CACHE_MAX:
        oldest = _records.pop(0)
        fp = oldest.get("file_path", "")
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
                logger.info(f"缓存已满，删除旧文件: {fp}")
            except Exception as e:
                logger.warning(f"删除旧文件失败: {fp} — {e}")

    _save()


def get_all() -> list[dict]:
    """返回全部历史，最新在末尾"""
    return list(_records)


def get_by_index(n: int) -> dict | None:
    """
    按 /history 输出的编号取记录。
    /history 显示时是倒序（最新=1），所以这里反转索引。
    n 从 1 开始。
    """
    reversed_list = list(reversed(_records))
    if 1 <= n <= len(reversed_list):
        return reversed_list[n - 1]
    return None


def format_history() -> str:
    """生成 /history 输出文本"""
    if not _records:
        return "📭 暂无播放历史"
    lines = ["🕘 最近播放（发送编号可重播）：\n"]
    for i, item in enumerate(reversed(_records), 1):
        artist = item.get("artist", "")
        suffix = f" — {artist}" if artist else ""
        lines.append(f"{i}. {item['title']}{suffix}")
    return "\n".join(lines)


def file_exists(video_id: str) -> str | None:
    """如果 video_id 已在历史且文件存在，返回文件路径，否则 None"""
    for r in _records:
        if r.get("video_id") == video_id:
            fp = r.get("file_path", "")
            if fp and os.path.exists(fp):
                return fp
    return None


def clear_all() -> tuple[int, int]:
    """
    清除全部历史记录及对应音频文件。
    返回 (删除记录数, 删除文件数)
    """
    global _records
    record_count = len(_records)
    file_count = 0
    for r in _records:
        fp = r.get("file_path", "")
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
                file_count += 1
            except Exception as e:
                logger.warning(f"删除文件失败: {fp} — {e}")
    _records = []
    _save()
    logger.info(f"clear_all: 删除 {record_count} 条记录，{file_count} 个文件")
    return record_count, file_count
