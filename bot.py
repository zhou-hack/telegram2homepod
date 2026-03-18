"""
bot.py — Telegram Bot handlers

指令列表:
  /start    — 显示状态 + 控制按钮
  /whoami   — 查看自己的 user_id（无需权限）
  /volume [0-100] — 查看/设置音量
  /pause    — 暂停
  /play     — 继续
  /stop     — 停止 + 清空队列
  /next     — 下一首
  /prev     — 上一首
  /queue    — 查看队列
  /history  — 查看历史，输入编号重播

直接发文字 → 搜索歌曲（显示候选按钮）
发 YouTube/YTMusic URL → 直接下载播放
发纯数字 → /history 模式下按编号重播
"""

import logging
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import airplay
import downloader
import history
import queue_manager
from config import BOT_TOKEN, ALLOWED_USERS

logger = logging.getLogger(__name__)

# 记录用户上一个操作是否是 /history，用于数字重播
_pending_history: dict[int, bool] = {}

YT_PREFIXES = (
    "https://www.youtube.com/",
    "https://youtu.be/",
    "https://music.youtube.com/",
)

# ---------- 工具 ----------

def control_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏮", callback_data="ctrl:prev"),
        InlineKeyboardButton("⏸", callback_data="ctrl:pause"),
        InlineKeyboardButton("▶️", callback_data="ctrl:play"),
        InlineKeyboardButton("⏭", callback_data="ctrl:next"),
        InlineKeyboardButton("⏹", callback_data="ctrl:stop"),
    ]])


def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            await update.effective_message.reply_text("⛔ 无权限")
            logger.warning(f"未授权访问: user_id={user_id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


async def _vol_str() -> str:
    vol = await airplay.get_volume()
    return f"{vol}%" if vol is not None else "未知"


# ---------- 指令 ----------

async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"你的 user\\_id: `{uid}`", parse_mode="Markdown")


@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vol = await _vol_str()
    cur = queue_manager.current()
    cur_str = f"\n▶️ 正在播放：{cur['title']}" if cur else ""
    await update.message.reply_text(
        f"🎵 *HomePod 音乐机器人*{cur_str}\n\n"
        f"🔊 当前音量：{vol}\n\n"
        f"发歌名搜索 / 发 YouTube 链接直接播 / 发数字重播历史\n\n"
        f"`/volume 50` 设音量 · `/queue` 查队列 · `/history` 历史",
        parse_mode="Markdown",
        reply_markup=control_keyboard(),
    )


@restricted
async def cmd_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"🔊 当前音量：{await _vol_str()}\n用法：`/volume 50`",
            parse_mode="Markdown",
        )
        return
    try:
        val = int(context.args[0])
        assert 0 <= val <= 100
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ 请输入 0-100 的整数")
        return
    ok = await airplay.set_volume(val)
    if ok:
        await update.message.reply_text(f"🔊 音量已设为 {val}%", reply_markup=control_keyboard())
    else:
        await update.message.reply_text("❌ 设置失败，检查 HomePod 连接")


@restricted
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = await queue_manager.pause()
    await update.message.reply_text("⏸ 已暂停" if ok else "❌ 当前没有播放中的内容", reply_markup=control_keyboard())


@restricted
async def cmd_play_ctrl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = await queue_manager.resume()
    await update.message.reply_text("▶️ 继续播放" if ok else "❌ 没有可继续的内容", reply_markup=control_keyboard())


@restricted
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await queue_manager.stop_all()
    await update.message.reply_text("⏹ 已停止，队列已清空")


@restricted
async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = await queue_manager.skip_next()
    if ok:
        await update.message.reply_text("⏭ 跳到下一首", reply_markup=control_keyboard())
    else:
        await update.message.reply_text("📭 队列已空")


@restricted
async def cmd_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prev = await queue_manager.skip_prev()
    if prev:
        await update.message.reply_text(f"⏮ 上一首：{prev['title']}", reply_markup=control_keyboard())
    else:
        await update.message.reply_text("📭 没有上一首")


@restricted
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = queue_manager.current()
    q = queue_manager.queue_list()
    if not cur and not q:
        await update.message.reply_text("📭 队列为空")
        return
    lines = []
    if cur:
        lines.append(f"▶️ 正在播放：{cur['title']}")
    for i, item in enumerate(q, 1):
        lines.append(f"{i}. {item['title']}")
    await update.message.reply_text("\n".join(lines))


@restricted
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    _pending_history[uid] = True   # 标记：下一条数字消息 = 重播
    await update.message.reply_text(history.format_history())


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎵 HomePod 音乐机器人\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "▶️ 播放控制\n"
        "/play — 继续播放\n"
        "/pause — 暂停\n"
        "/stop — 停止并清空队列\n"
        "/next — 下一首\n"
        "/prev — 上一首\n"
        "/queue — 查看当前队列\n"
        "\n"
        "🔊 音量\n"
        "/volume — 查看当前音量\n"
        "/volume 50 — 设置音量 (0-100)\n"
        "\n"
        "🕘 历史\n"
        "/history — 最近 20 首，发编号重播\n"
        "\n"
        "🗑 缓存\n"
        "/clear — 清除续播临时文件\n"
        "/clearforce — ⚠️ 清除全部历史和音频\n"
        "\n"
        "ℹ️ 其他\n"
        "/start — 状态面板\n"
        "/whoami — 查看自己的 user_id\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "发歌名搜索 / 发 YouTube 链接直接播"
    )
    await update.message.reply_text(text, reply_markup=control_keyboard())


@restricted
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = queue_manager.clear_resume_cache()
    await update.message.reply_text(f"🗑 已清除 {count} 个续播临时文件")


@restricted
async def cmd_clearforce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await queue_manager.stop_all()
    queue_manager.clear_resume_cache()
    record_count, file_count = history.clear_all()
    await update.message.reply_text(
        f"💣 已清除全部缓存\n"
        f"• 历史记录：{record_count} 条\n"
        f"• 音频文件：{file_count} 个"
    )


# ---------- 消息处理 ----------

@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid  = update.effective_user.id

    # ① 纯数字 → 历史重播
    if text.isdigit() and _pending_history.get(uid):
        _pending_history[uid] = False
        n = int(text)
        item = history.get_by_index(n)
        if item is None:
            await update.message.reply_text("❌ 编号不存在，请重新 /history 查看")
            return
        # 检查文件是否还在
        import os
        if not os.path.exists(item.get("file_path", "")):
            msg = await update.message.reply_text(f"📥 文件已清理，重新下载：{item['title']}…")
            try:
                item = await downloader.download(item["video_id"], item.get("artist", ""))
            except Exception as e:
                await msg.edit_text(f"❌ 下载失败：{e}")
                return
            await msg.edit_text(f"✅ 下载完成，加入播放")
        else:
            await update.message.reply_text(f"▶️ 重播：{item['title']}")
        queue_manager.add(item)
        _ensure_play_msg(update, item)
        return

    # ② YouTube / YTMusic URL
    if any(text.startswith(p) for p in YT_PREFIXES):
        msg = await update.message.reply_text("⬇️ 解析下载中…")
        try:
            item = await downloader.download(text)
        except Exception as e:
            await msg.edit_text(f"❌ 下载失败：{e}")
            return
        queue_manager.add(item)
        vol = await _vol_str()
        is_first = not queue_manager.is_playing() or queue_manager.queue_size() == 0
        if is_first:
            await msg.edit_text(
                f"✅ 开始播放\n🎵 *{item['title']}*\n🔊 音量：{vol}",
                parse_mode="Markdown",
                reply_markup=control_keyboard(),
            )
        else:
            await msg.edit_text(
                f"➕ 已加入队列\n🎵 {item['title']}",
                reply_markup=control_keyboard(),
            )
        return

    # ③ 普通文字 → 搜索
    _pending_history[uid] = False
    msg = await update.message.reply_text(f"🔍 搜索：{text}")
    songs = downloader.search_songs(text)
    if not songs:
        await msg.edit_text("❌ 没找到，换个关键词试试")
        return
    lines = []
    keyboard = []
    for i, s in enumerate(songs, 1):
        lines.append(f"{i}. {s['title']} — {s['artist']} [{s['duration']}]")
        keyboard.append([InlineKeyboardButton(
            f"{i}. {s['title']} — {s['artist']}",
            callback_data=f"play:{s['video_id']}:{s['artist']}",
        )])
    await msg.edit_text(
        "找到以下结果，点击播放：\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def _ensure_play_msg(update, item):
    """队列有内容就自动开始（queue_manager.add 已处理，这里只是语义占位）"""
    pass


# ---------- 按钮回调 ----------

@restricted
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # 搜索结果选歌
    if data.startswith("play:"):
        parts = data.split(":", 2)
        video_id = parts[1]
        artist   = parts[2] if len(parts) > 2 else ""
        await query.edit_message_text("⬇️ 下载中…")
        try:
            item = await downloader.download(video_id, artist)
        except Exception as e:
            await query.edit_message_text(f"❌ 下载失败：{e}")
            return
        queue_manager.add(item)
        vol = await _vol_str()
        pos = queue_manager.queue_size()
        if queue_manager.is_playing() and pos > 0:
            await query.edit_message_text(
                f"➕ 已加入队列（第 {pos} 首）\n🎵 {item['title']}",
                reply_markup=control_keyboard(),
            )
        else:
            await query.edit_message_text(
                f"✅ 开始播放\n🎵 *{item['title']}*\n🔊 音量：{vol}",
                parse_mode="Markdown",
                reply_markup=control_keyboard(),
            )
        return

    # 控制按钮
    if data.startswith("ctrl:"):
        action = data.split(":", 1)[1]
        if action == "pause":
            await queue_manager.pause()
            await query.answer("⏸ 已暂停")
        elif action == "play":
            await queue_manager.resume()
            await query.answer("▶️ 继续")
        elif action == "stop":
            await queue_manager.stop_all()
            await query.answer("⏹ 已停止")
        elif action == "next":
            ok = await queue_manager.skip_next()
            await query.answer("⏭ 下一首" if ok else "📭 队列已空")
        elif action == "prev":
            prev = await queue_manager.skip_prev()
            await query.answer(f"⏮ {prev['title']}" if prev else "📭 没有上一首")


# ---------- 注册 ----------

# Telegram 命令选单（Bot Menu 按钮里显示的列表）
BOT_COMMANDS = [
    ("start",      "显示状态面板"),
    ("help",       "所有指令说明"),
    ("play",       "继续播放"),
    ("pause",      "暂停"),
    ("stop",       "停止并清空队列"),
    ("next",       "下一首"),
    ("prev",       "上一首"),
    ("volume",     "查看/设置音量"),
    ("queue",      "查看当前队列"),
    ("history",    "播放历史（发编号重播）"),
    ("clear",      "清除续播临时文件"),
    ("clearforce", "⚠️ 清除全部历史和音频"),
    ("whoami",     "查看自己的 user_id"),
]


def register(app: Application):
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("whoami",     cmd_whoami))
    app.add_handler(CommandHandler("volume",     cmd_volume))
    app.add_handler(CommandHandler("pause",      cmd_pause))
    app.add_handler(CommandHandler("play",       cmd_play_ctrl))
    app.add_handler(CommandHandler("stop",       cmd_stop))
    app.add_handler(CommandHandler("next",       cmd_next))
    app.add_handler(CommandHandler("prev",       cmd_prev))
    app.add_handler(CommandHandler("queue",      cmd_queue))
    app.add_handler(CommandHandler("history",    cmd_history))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("clearforce", cmd_clearforce))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
