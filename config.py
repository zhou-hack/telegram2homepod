import os
import secrets
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()
ENV_FILE = BASE_DIR / ".env"

def _dir(env_key: str, default_name: str) -> Path:
    val = os.getenv(env_key, "")
    p = Path(val) if val else BASE_DIR / default_name
    p.mkdir(parents=True, exist_ok=True)
    return p

# ── Telegram（可选）──
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
TELEGRAM_ENABLED = bool(BOT_TOKEN)

# ── HomePod ──
HOMEPOD_ID = os.getenv("HOMEPOD_ID", "")
HOMEPOD_IP = os.getenv("HOMEPOD_IP", "")

# ── 存储 ──
DOWNLOAD_DIR  = str(_dir("DOWNLOAD_DIR", "music"))
HISTORY_DIR   = str(_dir("HISTORY_DIR",  "history"))
HISTORY_FILE  = str(Path(HISTORY_DIR) / "history.json")
CACHE_MAX     = int(os.getenv("CACHE_MAX", "20"))

# ── HTTP 文件服务（HomePod 拉流用）──
HTTP_HOST = os.getenv("HTTP_HOST", HOMEPOD_IP)
HTTP_PORT = int(os.getenv("HTTP_PORT", "8765"))

# ── WebUI ──
WEBUI_HOST = os.getenv("WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(os.getenv("WEBUI_PORT", "8080"))

# WebUI Token：从 .env 读取，没有则自动生成（仅内存，不写回文件）
# Docker 环境下 bind mount 不支持原子替换，所以不写回
_token = os.getenv("WEBUI_TOKEN", "")
if not _token:
    _token = secrets.token_urlsafe(24)
    # 尝试追加写入（非原子，仅非 Docker 环境有效）
    try:
        import stat
        # bind mount 文件 inode 特征：跳过写入
        st = os.stat(str(ENV_FILE)) if ENV_FILE.exists() else None
        if st and not (st.st_dev == 0):
            with open(str(ENV_FILE), "a") as f:
                f.write(f"\nWEBUI_TOKEN={_token}\n")
    except Exception:
        pass  # 写不进去就算了，token 还在内存里能用
WEBUI_TOKEN = _token


def validate():
    """只校验核心必填项；Telegram 是可选的"""
    missing = []
    if not HOMEPOD_ID: missing.append("HOMEPOD_ID")
    if not HOMEPOD_IP: missing.append("HOMEPOD_IP")
    if not HTTP_HOST:  missing.append("HTTP_HOST")
    if missing:
        raise ValueError(f"缺少必要配置: {', '.join(missing)}")

    if TELEGRAM_ENABLED and not ALLOWED_USERS:
        import logging
        logging.getLogger(__name__).warning(
            "BOT_TOKEN 已设置但 ALLOWED_USERS 为空，Bot 将拒绝所有用户"
        )
