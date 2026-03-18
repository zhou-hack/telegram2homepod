"""
downloader.py — 音频搜索与下载
- 搜索：ytmusicapi
- 下载：yt-dlp + ffmpeg 转 mp3
- 下载前检查历史缓存，已有文件直接复用
"""

import asyncio
import logging
import os

import yt_dlp
from ytmusicapi import YTMusic

import history
from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)
ytmusic = YTMusic()


# ---------- 搜索 ----------

def search_songs(query: str, limit: int = 5) -> list[dict]:
    """
    返回列表，每项:
      {title, artist, duration, video_id}
    """
    try:
        results = ytmusic.search(query, filter="songs", limit=limit)
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return []

    songs = []
    for r in results:
        if r.get("resultType") != "song":
            continue
        video_id = r.get("videoId")
        if not video_id:
            continue
        # ytmusicapi 返回结构：title=歌名, artists=歌手列表, album=专辑
        # 取干净歌名，不拼专辑
        title   = r.get("title", "未知")
        artists = ", ".join(a["name"] for a in r.get("artists", []))
        songs.append({
            "title":    title,
            "artist":   artists,
            "album":    (r.get("album") or {}).get("name", ""),
            "duration": r.get("duration", "?"),
            "video_id": video_id,
        })
        if len(songs) >= limit:
            break
    return songs


# ---------- 下载 ----------

def _do_download(url: str) -> tuple[str, str, str]:
    """
    同步下载，返回 (file_path, title, video_id)
    """
    output_tpl = os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_tpl,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=True)
        video_id = meta.get("id", "unknown")
        title    = meta.get("title", "未知")
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
    return file_path, title, video_id


async def download(video_id_or_url: str, artist: str = "", title: str = "") -> dict:
    """
    异步下载，返回 item dict:
      {title, artist, video_id, file_path}

    title/artist 优先用调用方传入的（来自 ytmusicapi 搜索结果，比 yt-dlp 的干净）。
    优先从历史缓存复用，避免重复下载。
    """
    # 解析 URL
    if video_id_or_url.startswith("http"):
        url = video_id_or_url
        import urllib.parse as up
        qs = up.parse_qs(up.urlparse(url).query)
        video_id_guess = qs.get("v", [None])[0] or video_id_or_url
    else:
        video_id_guess = video_id_or_url
        url = f"https://www.youtube.com/watch?v={video_id_or_url}"

    # 缓存命中
    cached = history.file_exists(video_id_guess)
    if cached:
        logger.info(f"缓存命中: {video_id_guess}")
        for r in history.get_all():
            if r.get("video_id") == video_id_guess:
                return r
        return {"title": title or video_id_guess, "artist": artist,
                "video_id": video_id_guess, "file_path": cached}

    # 真正下载（在线程池里跑，不阻塞事件循环）
    loop = asyncio.get_event_loop()
    file_path, yt_title, video_id = await loop.run_in_executor(
        None, _do_download, url
    )

    item = {
        "title":     title or yt_title,   # 优先用搜索拿到的 title，没有才用 yt-dlp 的
        "artist":    artist,
        "video_id":  video_id,
        "file_path": file_path,
    }
    return item
