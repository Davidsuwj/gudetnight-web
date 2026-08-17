"""
股得 Night — Podcast 一鍵流程（精簡編排層）
============================================
STEP 1  ChatGPT 取得最新 podcast JSON（檢查 / 修正 CONTEXT1/2/3）
STEP 2  VoAI 逐段生成 MP3
STEP 3  ffmpeg 合併成完整 MP3，再用 logo.jpg 合成 MP4

用法：
  python run_podcast.py              # 完整流程
  python run_podcast.py --skip-fetch # 跳過 ChatGPT，用最新現有 JSON
  python run_podcast.py --json PATH  # 指定 JSON
  python run_podcast.py --no-video   # 不產生 MP4
"""
import asyncio, os, re, time, json, argparse

from podcast.config import DOWNLOAD_DIR, MP3_OUTPUT_DIR, CONTEXT_KEYS
from podcast.utils import setup_console
from podcast import browser as br
from podcast.chatgpt import fetch_latest_json, find_latest_json
from podcast.voai import open_voai, generate_speech
from podcast.media import merge_mp3s, make_video


def _load_contexts(json_path):
    """讀 JSON，回傳 (contexts_dict, topic)。"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    contexts = {k: data[k] for k in CONTEXT_KEYS if str(data.get(k, "")).strip()}
    if not contexts and str(data.get("CONTEXT", "")).strip():
        contexts = {"CONTEXT": data["CONTEXT"]}   # 單段格式相容
    return contexts, data.get("TOPIC", "N/A")


async def run_tts_and_media(browser, json_path, make_mp4=True):
    """STEP 2 + STEP 3：逐段 TTS → 合併 MP3 → 合成 MP4。"""
    print("\n" + "=" * 60)
    print(f"STEP 2 — VoAI TTS (來源: {os.path.basename(json_path)})")
    print("=" * 60)

    contexts, topic = _load_contexts(json_path)
    if not contexts:
        print("[FATAL] JSON 沒有 CONTEXT 內容")
        return False
    print(f"[OK] {len(contexts)} contexts | TOPIC: {topic}")

    page = await open_voai(browser)
    if page is None:
        return False

    # 從 JSON 檔名取日期（如 ..._20260605.json），沒有就用今天
    m = re.search(r"(20\d{6})", os.path.basename(json_path))
    today = m.group(1) if m else time.strftime("%Y%m%d")

    for idx, text in enumerate(contexts.values(), 1):
        name = f"{today}_{idx:02d}"
        print(f"\n{'='*50}\nCONTEXT {idx}/{len(contexts)} → {name}.mp3\n{'='*50}")
        if not await generate_speech(page, text, name):
            print(f"[FAIL] Context {idx} failed!")
            return False
        if idx < len(contexts):
            print("[INFO] Waiting 8s before next context...")
            await asyncio.sleep(8)

    # 收集本日的分段 MP3
    pattern = re.compile(rf"{today}_\d{{2}}\.mp3$")
    mp3_files = [os.path.join(MP3_OUTPUT_DIR, f)
                 for f in sorted(os.listdir(MP3_OUTPUT_DIR)) if pattern.match(f)]
    if not mp3_files:
        print("[WARN] No MP3 files to merge")
        return False

    # STEP 3a：合併成完整 MP3
    print("\n" + "=" * 60)
    print("STEP 3 — 合併 MP3 + 合成 MP4")
    print("=" * 60)
    merged_mp3 = os.path.join(MP3_OUTPUT_DIR, f"{today}_full.mp3")
    if not merge_mp3s(mp3_files, merged_mp3):
        return False
    print(f"[RESULT] 完整語音 → {merged_mp3}")

    # STEP 3b：合成 MP4（logo + 完整語音）
    if make_mp4:
        mp4_path = os.path.join(MP3_OUTPUT_DIR, f"{today}_full.mp4")
        if make_video(merged_mp3, mp4_path):
            print(f"[RESULT] 影片 → {mp4_path}")
    return True


async def run_pipeline(skip_fetch=False, json_override=None, make_mp4=True):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await br.connect(p)

        if json_override:
            json_path = json_override
            print(f"[INFO] 使用指定 JSON: {json_path}")
        elif skip_fetch:
            json_path = find_latest_json()
            print(f"[INFO] 跳過 ChatGPT，使用最新現有 JSON: {json_path}")
        else:
            json_path = await fetch_latest_json(browser)
            if not json_path:
                print("[WARN] ChatGPT 取得失敗，改用最新現有 JSON")
                json_path = find_latest_json()

        if not json_path or not os.path.exists(json_path):
            print("[FATAL] 找不到可用的 JSON，流程中止")
            return

        await run_tts_and_media(browser, json_path, make_mp4=make_mp4)


def parse_args():
    ap = argparse.ArgumentParser(description="股得 Night 一鍵 Podcast 流程")
    ap.add_argument("--skip-fetch", action="store_true", help="跳過 ChatGPT，直接用最新現有 JSON")
    ap.add_argument("--json", dest="json_path", default=None, help="指定 JSON 檔路徑")
    ap.add_argument("--no-video", action="store_true", help="不產生 MP4")
    return ap.parse_args()


if __name__ == "__main__":
    setup_console()
    args = parse_args()
    asyncio.run(run_pipeline(
        skip_fetch=args.skip_fetch,
        json_override=args.json_path,
        make_mp4=not args.no_video,
    ))
