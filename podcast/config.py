"""集中管理所有路徑與設定。"""
import os

WORKSPACE      = os.environ.get("GUDETNIGHT_WORKSPACE", os.path.dirname(os.path.dirname(__file__)))
PROMPT_FILE    = os.environ.get("GUDETNIGHT_PROMPT_FILE", os.path.join(WORKSPACE, "podcast_spec.txt"))
JSON_OUTPUT    = os.environ.get("GUDETNIGHT_JSON_OUTPUT", os.path.join(WORKSPACE, "podcast_output.json"))
DOWNLOAD_DIR   = os.environ.get("GUDETNIGHT_DOWNLOADS_DIR", os.path.join(WORKSPACE, "Downloads"))
MP3_OUTPUT_DIR = DOWNLOAD_DIR
SCREENSHOT_DIR = os.environ.get("GUDETNIGHT_SCREENSHOT_DIR", os.path.join(WORKSPACE, "screenshots"))
LOGO_FILE      = os.environ.get("GUDETNIGHT_LOGO_PATH", os.path.join(WORKSPACE, "logo.jpg"))

CDP_URL = os.environ.get("GUDETNIGHT_CDP_URL", "http://[::1]:9222")
FFMPEG  = os.environ.get("FFMPEG_PATH", "ffmpeg")

CONTEXT_KEYS       = ("CONTEXT1", "CONTEXT2", "CONTEXT3")
MAX_FETCH_ATTEMPTS = 3   # ChatGPT 取得 JSON 的最大重試次數（含修正請求）

# 語音參數
VOICE_NAME = "國賢"
PITCH      = "-0.6"
SPEED      = "1.15"


def shot(name):
    """回傳 SCREENSHOT_DIR 內的截圖路徑。"""
    return os.path.join(SCREENSHOT_DIR, name)
