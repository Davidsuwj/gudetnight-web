"""共用工具：JSON 解析、CONTEXT 檢查、stdout 編碼。"""
import sys, json
from .config import CONTEXT_KEYS, JSON_OUTPUT


def setup_console():
    """讓 Windows Python 從 WSL/PowerShell 啟動時 log 不亂碼。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def parse_json_blob(text):
    """從一段文字盡力解析出 JSON dict，失敗回傳 None。"""
    if not text:
        return None
    text = text.strip()
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            d = json.loads(text[start:end + 1])
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return None


def check_contexts(data):
    """檢查 data 是否含 CONTEXT1/2/3 且皆有內容。回傳 (ok, missing_keys)。"""
    if not isinstance(data, dict):
        return False, list(CONTEXT_KEYS)
    missing = [k for k in CONTEXT_KEYS if not str(data.get(k, "")).strip()]
    return len(missing) == 0, missing


def save_json(data, path=JSON_OUTPUT):
    """把 data 寫入 path 並回傳路徑。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[SUCCESS] Saved to {path}")
    return path
