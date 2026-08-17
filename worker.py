# -*- coding: utf-8 -*-
"""Worker for 股得Night manual frontend.
Runs one stage at a time so manual approval can pause between stages.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
JOBS = BASE / "jobs"
G_ROOT = Path(os.environ.get("GUDETNIGHT_WORKSPACE", str(BASE)))
DOWNLOADS = Path(os.environ.get("GUDETNIGHT_DOWNLOADS_DIR", str(G_ROOT / "Downloads")))
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
LOGO = Path(os.environ.get("GUDETNIGHT_LOGO_PATH", str(G_ROOT / "logo.jpg")))
TARGET_EMAIL = os.environ.get("YOUTUBE_ACCOUNT_EMAIL", "").strip()
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
YOUTUBE_UPLOADER_PATH = Path(
    os.environ.get("GUDETNIGHT_YOUTUBE_UPLOADER", str(BASE / "youtube_uploader.py"))
)
AMAZON_DISCLOSURE = (
    os.environ.get(
        "AMAZON_ASSOCIATES_DISCLOSURE",
        "As an Amazon Associate I earn from qualifying purchases.",
    ).strip()
    or "As an Amazon Associate I earn from qualifying purchases."
)

# CDP Chrome (the logged-in Windows browser the worker drives via Playwright).
CDP_PORT = 9222
# On this host the visible Windows Chrome listens on IPv6 ::1, while an unrelated
# headless Chrome listens on IPv4 127.0.0.1 using the same port.
CDP_CANDIDATES = (
    f"http://[::1]:{CDP_PORT}",
    f"http://127.0.0.1:{CDP_PORT}",
    "http://127.0.0.1:9224",
)
CDP_URL = CDP_CANDIDATES[0]
# Dedicated persistent profile so the login state is kept forever and does not
# clash with the user's everyday Chrome. Chrome 136+ refuses remote debugging on
# the default profile, so a separate user-data-dir is required.
CDP_PROFILE = Path(os.path.expanduser(r"~\chrome-cdp-profile"))
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# Import existing 股得Night modules without modifying cron or the original scripts.
sys.path.insert(0, str(G_ROOT))


def _cdp_endpoint_identity(url: str) -> dict | None:
    """Return metadata only when *url* is the dedicated visible CDP browser.

    IPv4 port 9222 is sometimes occupied by an unrelated headless browser on this
    host. Chrome 150 currently binds the dedicated profile to IPv4 instead of IPv6,
    so endpoint family alone is no longer a safe identity check. Reject headless
    user agents and require one of this pipeline's authenticated app tabs before
    allowing the IPv4 fallback.
    """
    try:
        with urllib.request.urlopen(f"{url}/json/version", timeout=2) as resp:
            version = json.loads(resp.read().decode("utf-8", errors="replace"))
        if "HeadlessChrome" in str(version.get("User-Agent", "")):
            return None
        with urllib.request.urlopen(f"{url}/json/list", timeout=2) as resp:
            targets = json.loads(resp.read().decode("utf-8", errors="replace"))
        urls = [str(t.get("url", "")) for t in targets if t.get("type") == "page"]
        if url.startswith("http://127.0.0.1") and not any(
            host in page_url
            for host in ("chatgpt.com", "voai.ai", "studio.youtube.com", "creator.line.me")
            for page_url in urls
        ):
            return None
        return {"url": url, "version": version, "page_urls": urls}
    except Exception:
        return None


def validated_cdp_url() -> str | None:
    for url in CDP_CANDIDATES:
        if _cdp_endpoint_identity(url):
            return url
    return None


def _cdp_alive() -> bool:
    return validated_cdp_url() is not None


def ensure_cdp_chrome(wait_seconds: int = 30):
    """Make sure the logged-in CDP Chrome is running; launch it if it isn't.

    This removes the manual "open Chrome first" step before each run.
    """
    global CDP_URL
    live_url = validated_cdp_url()
    if live_url:
        CDP_URL = live_url
        return

    chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not chrome:
        raise FileNotFoundError(
            "Chrome executable not found. Checked: " + " | ".join(CHROME_CANDIDATES)
        )

    CDP_PROFILE.mkdir(parents=True, exist_ok=True)
    log(f"CDP Chrome not running; launching with profile {CDP_PROFILE}")
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CDP_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        live_url = validated_cdp_url()
        if live_url:
            CDP_URL = live_url
            log(f"CDP Chrome is up at validated endpoint {CDP_URL}")
            return
        time.sleep(1)
    raise RuntimeError(
        f"Launched Chrome but no validated visible CDP endpoint became ready within {wait_seconds}s"
    )


def jdir(job_id: str) -> Path:
    return JOBS / job_id


def spath(job_id: str) -> Path:
    return jdir(job_id) / "state.json"


def load(job_id: str) -> dict:
    with open(spath(job_id), "r", encoding="utf-8") as f:
        return json.load(f)


def save(job_id: str, st: dict):
    st["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(spath(job_id), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def mark_done(job_id: str, st: dict, stage: str):
    done = st.setdefault("completed_stages", [])
    if stage not in done:
        done.append(stage)
    st["running"] = False
    st["pid"] = None
    st["current_stage"] = stage


def flow_for(st: dict) -> list[str]:
    """Return the stage flow; affiliate comments apply only to product Shorts."""
    flow = ["script", "audio", "video", "upload"]
    if st.get("target_type") == "shorts" and st.get("product"):
        flow.append("comment")
    return flow


def next_after(st: dict, stage: str):
    flow = flow_for(st)
    idx = flow.index(stage)
    if idx + 1 >= len(flow):
        return None
    return flow[idx + 1]


def finish_stage(job_id: str, st: dict, stage: str):
    mark_done(job_id, st, stage)
    nxt = next_after(st, stage)
    if nxt:
        if st.get("approval_mode") == "auto":
            st["next_stage"] = nxt
            st["status"] = f"auto_continue_{nxt}"
            save(job_id, st)
            run_stage(job_id, nxt)
            return
        st["next_stage"] = nxt
        st["status"] = f"needs_review_{stage}"
    else:
        st["next_stage"] = None
        st["status"] = "uploaded" if st.get("youtube_url") else "done"
    save(job_id, st)


def log(msg: str):
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    try:
        print(f"[{datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')}] {msg}", flush=True)
    except OSError:
        with open(JOBS / "worker_stderr.log", "a", encoding="utf-8") as _lf:
            _lf.write(f"[{datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')}] {msg}\n")


def compact_text(s: str, n=140):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def safe_page_screenshot(page, path, timeout_ms=5000):
    """Capture diagnostics without allowing screenshot failures to abort a stage."""
    try:
        page.screenshot(path=str(path), timeout=timeout_ms)
        return True
    except Exception as exc:
        log(f"WARN diagnostic screenshot failed: {path} error={exc!r}")
        return False


def build_shorts_mode_prompt(prompt: str, product: dict | None = None) -> str:
    product_block = ""
    output_shape = '{"TOPIC":"短而有張力的標題","CONTEXT1":"開場鉤子與今天重點","CONTEXT2":"核心數據/事件與影響","CONTEXT3":"結尾觀察與風險提醒"}'
    if product:
        from affiliate_product import normalize_product
        p = normalize_product(product)
        product_block = f"""

片尾商品推薦（已由 Amazon 官網與 SiteStripe 驗證）：
- 商品：{p['name']}
- 價格：{p['price'] or '不要在文案中提價格'}
- 熱門依據：{p['popularity_evidence']}
- 與本集的關聯：{p['relevance_reason']}

請額外產出 PRODUCT_CONTEXT：只能 7至10秒，先從本集觀眾的實際需求自然銜接到商品，不能突然硬轉、不能宣稱保證獲利，不能捏造折扣、功能、配件或效果。最後一句必須逐字使用「請參考留言處商品連結」，不得在這句後面加任何文字；不要朗讀網址或聯盟揭露，也不要使用「有需要可以看看」、「想了解更多」等其他 CTA。
"""
        output_shape = '{"TOPIC":"短而有張力的標題","CONTEXT1":"開場鉤子與今天重點","CONTEXT2":"核心數據/事件與影響","CONTEXT3":"結尾觀察與風險提醒","PRODUCT_CONTEXT":"自然銜接商品用途，最後一句固定為請參考留言處商品連結"}'
    return f"""
你是股得Night短影音編劇。請根據使用者需求，產出 YouTube Shorts 用的繁體中文文案。
限制：新聞正文總長盡量 35-55 秒；CONTEXT1/2/3 每段 70-100 字，適合 TTS 朗讀；語氣專業但口語、像台股朋友提醒，不要投資建議承諾。
{product_block}
請只輸出 JSON，不要 markdown，不要額外說明：
{output_shape}

使用者需求：
{prompt}
""".strip()


PRODUCT_COMMENT_CTA = "請參考留言處商品連結"


def normalize_product_context_cta(value: str) -> str:
    """Keep the useful product bridge and enforce one exact, non-redundant CTA."""
    text = compact_text(value, n=180)
    if not text:
        return ""
    if PRODUCT_COMMENT_CTA in text:
        text = text.split(PRODUCT_COMMENT_CTA, 1)[0]
    text = re.sub(
        r"[；;，,。]?\s*(?:有需要|如果有需要|想了解更多|更多資訊|商品詳情|可以再看看|歡迎查看|請查看|請參考).*$",
        "",
        text,
    ).rstrip("。；;，,、 ")
    return f"{text}。{PRODUCT_COMMENT_CTA}" if text else PRODUCT_COMMENT_CTA


def attach_product_context(data: dict) -> dict:
    """Preserve the news ending and append the short product narration for TTS."""
    result = dict(data)
    product_context = normalize_product_context_cta(result.get("PRODUCT_CONTEXT", ""))
    if not product_context:
        return result
    news_context3 = str(result.get("NEWS_CONTEXT3") or result.get("CONTEXT3") or "").strip()
    result["NEWS_CONTEXT3"] = news_context3
    result["PRODUCT_CONTEXT"] = product_context
    result["CONTEXT3"] = "\n\n".join(x for x in [news_context3, product_context] if x)
    return result


async def send_chatgpt_prompt(prompt: str, target_type: str, product: dict | None = None) -> dict:
    """Use the logged-in Chrome/ChatGPT CDP flow from existing project, but with a custom prompt."""
    from playwright.async_api import async_playwright
    from podcast import browser as br
    from podcast.chatgpt import _send_message, _wait_for_response, _extract_json, _correction_prompt
    from podcast.utils import check_contexts
    from podcast.config import CONTEXT_KEYS

    if target_type == "shorts":
        mode_prompt = build_shorts_mode_prompt(prompt, product)
    else:
        # Keep the normal YouTube/podcast structure, but make it custom-prompt driven.
        try:
            spec = (G_ROOT / "podcast_spec.txt").read_text(encoding="utf-8", errors="ignore")
        except Exception:
            spec = "請產出股得Night podcast JSON，含 TOPIC、CONTEXT1、CONTEXT2、CONTEXT3。"
        mode_prompt = f"""
以下是股得Night原本 Podcast 規格，請維持一般 YouTube 長影片/podcast 的結構與口吻，但主題必須依照使用者輸入。

--- 原規格 ---
{spec}

--- 使用者這次的 INPUT PROMPT ---
{prompt}

請只輸出 JSON，不要 markdown，不要額外說明，格式必須為：
{{"TOPIC":"","CONTEXT1":"","CONTEXT2":"","CONTEXT3":""}}
""".strip()

    async with async_playwright() as p:
        secure_url = validated_cdp_url()
        if not secure_url:
            raise RuntimeError("No validated visible Chrome CDP endpoint for script stage")
        br.CDP_URL = secure_url
        browser = await br.connect(p)
        page = await br.find_or_create_page(browser, "chatgpt.com")
        download_box = {"path": None}
        missing = list(CONTEXT_KEYS)
        for attempt in range(1, 4):
            if attempt == 1:
                log("Sending custom prompt to ChatGPT")
                try:
                    ok = await _send_message(page, mode_prompt, fresh=True)
                except Exception as exc:
                    # ChatGPT keeps a hidden fallback textarea in the DOM. The legacy
                    # helper can resolve it before the visible ProseMirror composer.
                    log(f"WARN legacy ChatGPT composer failed; trying visible editor: {exc!r}")
                    ok = await _send_visible_chatgpt_message(page, mode_prompt)
            else:
                log(f"Asking ChatGPT to repair missing fields: {missing}")
                ok = await _send_message(page, _correction_prompt(missing), fresh=False)
            if not ok:
                raise RuntimeError("ChatGPT prompt textarea not available")
            await _wait_for_response(page)
            data = await _extract_json(page, download_box)
            if data:
                ok_ctx, missing = check_contexts(data)
                if ok_ctx:
                    result = {k: str(data.get(k, "")).strip() for k in ["TOPIC", "CONTEXT1", "CONTEXT2", "CONTEXT3"]}
                    if product:
                        product_context = str(data.get("PRODUCT_CONTEXT", "")).strip()
                        if not product_context:
                            missing = ["PRODUCT_CONTEXT"]
                            continue
                        result["PRODUCT_CONTEXT"] = product_context
                    return result
        raise RuntimeError("ChatGPT did not return complete TOPIC/CONTEXT1/2/3 JSON")


async def _send_visible_chatgpt_message(page, message: str) -> bool:
    """Fill the first visible ChatGPT composer, ignoring hidden fallback textareas."""
    selectors = (
        '#prompt-textarea[contenteditable="true"]',
        '[contenteditable="true"][data-lexical-editor="true"]',
        'div.ProseMirror[contenteditable="true"]',
        'textarea[name="prompt-textarea"]',
        'textarea[placeholder]',
    )
    for selector in selectors:
        locators = page.locator(selector)
        for index in range(await locators.count()):
            locator = locators.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                await locator.click(timeout=5000)
                await locator.fill(message, timeout=15000)
                send = page.locator('button[data-testid="send-button"]:visible').first
                if await send.count() and await send.is_enabled():
                    await send.click(timeout=10000)
                else:
                    await locator.press("Enter")
                log(f"INFO prompt sent through visible ChatGPT editor selector={selector}")
                return True
            except Exception as exc:
                log(f"WARN visible editor candidate failed selector={selector} index={index}: {exc!r}")
    return False


def stage_script(job_id: str, st: dict):
    data = asyncio.run(send_chatgpt_prompt(st["prompt"], st["target_type"], st.get("product")))
    if st.get("product") and st.get("target_type") == "shorts":
        data = attach_product_context(data)
    out = jdir(job_id) / "podcast_output.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    st["script_json"] = data
    st["title"] = data.get("TOPIC") or job_id
    st.setdefault("artifacts", {})["script_json"] = str(out)
    log(f"Script ready: {st['title']}")
    finish_stage(job_id, st, "script")


def stage_audio(job_id: str, st: dict):
    from playwright.async_api import async_playwright
    from podcast import browser as br
    from run_podcast import run_tts_and_media

    json_path = str(jdir(job_id) / "podcast_output.json")
    today = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    # Remove same-day segmented files to avoid merging stale segments from other manual runs.
    DOWNLOADS.mkdir(exist_ok=True)
    for p in DOWNLOADS.glob(f"{today}_*.mp3"):
        try:
            p.unlink()
        except Exception:
            pass
    async def _run():
        async with async_playwright() as p:
            secure_url = validated_cdp_url()
            if not secure_url:
                raise RuntimeError("No validated visible Chrome CDP endpoint for audio stage")
            br.CDP_URL = secure_url
            browser = await br.connect(p)
            await run_tts_and_media(browser, json_path, make_mp4=False)
    asyncio.run(_run())
    src = DOWNLOADS / f"{today}_full.mp3"
    if not src.exists():
        raise FileNotFoundError(src)
    dst = jdir(job_id) / "audio_full.mp3"
    shutil.copy2(src, dst)
    st.setdefault("artifacts", {})["audio"] = str(dst)
    log(f"Audio ready: {dst}")
    finish_stage(job_id, st, "audio")


def find_font():
    candidates = [
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\NotoSansCJK-Regular.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def wrap_text(draw, text, font, max_width):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            test = cur + ch
            if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


def make_card(path: Path, title: str, body: str, idx: int, total: int):
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (9, 16, 32))
    draw = ImageDraw.Draw(img)
    # simple gradient
    for y in range(H):
        r = 9 + int(28 * y / H)
        g = 16 + int(18 * y / H)
        b = 32 + int(65 * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    font_path = find_font()
    title_font = ImageFont.truetype(font_path, 72) if font_path else ImageFont.load_default()
    body_font = ImageFont.truetype(font_path, 48) if font_path else ImageFont.load_default()
    small_font = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default()
    draw.rounded_rectangle((70, 90, 1010, 1830), radius=46, outline=(122, 92, 255), width=4, fill=(14, 22, 43))
    draw.text((95, 130), "股得Night Shorts", fill=(180, 195, 255), font=small_font)
    draw.text((95, 185), f"{idx}/{total}", fill=(255, 202, 97), font=small_font)
    ty = 300
    for line in wrap_text(draw, title, title_font, 900)[:4]:
        draw.text((95, ty), line, fill=(255, 255, 255), font=title_font)
        ty += 86
    y = max(650, ty + 60)
    for line in wrap_text(draw, body, body_font, 880)[:14]:
        draw.text((100, y), line, fill=(224, 235, 255), font=body_font)
        y += 66
    draw.text((95, 1740), "明天看盤，晚上說盤。", fill=(255, 202, 97), font=small_font)
    img.save(path, quality=95)


def split_contexts_into_six(data: dict) -> list[dict]:
    """Split the three news contexts into two visual chunks each = 6 images."""
    chunks = []
    for ci, key in enumerate(["CONTEXT1", "CONTEXT2", "CONTEXT3"], 1):
        source_key = "NEWS_CONTEXT3" if key == "CONTEXT3" and data.get("NEWS_CONTEXT3") else key
        text = re.sub(r"\s+", " ", str(data.get(source_key, "")).strip())
        if not text:
            continue
        # Prefer punctuation boundary near the middle, otherwise split by character count.
        mid = len(text) // 2
        candidates = [m.end() for m in re.finditer(r"[。！？；，,;!?]", text)]
        cut = min(candidates, key=lambda x: abs(x - mid)) if candidates else mid
        if cut < len(text) * 0.25 or cut > len(text) * 0.75:
            cut = mid
        parts = [text[:cut].strip(), text[cut:].strip()]
        for pi, part in enumerate(parts, 1):
            if part:
                chunks.append({"kind": "news", "context": key, "part": pi, "text": part, "index": len(chunks) + 1})
    return chunks[:6]


def build_visual_chunks(data: dict, product: dict | None = None) -> list[dict]:
    """Keep six news visuals and append one dedicated product outro visual."""
    chunks = split_contexts_into_six(data)
    if product and str(data.get("PRODUCT_CONTEXT", "")).strip():
        from affiliate_product import normalize_product
        p = normalize_product(product)
        chunks.append({
            "kind": "product",
            "context": "PRODUCT_CONTEXT",
            "part": 1,
            "text": str(data.get("PRODUCT_CONTEXT", "")).strip(),
            "product_name": p["name"],
            "product_reason": p["relevance_reason"],
            "index": len(chunks) + 1,
        })
    return chunks


def normalize_vertical_image(src: Path, dst: Path, caption: str, idx: int, total: int, title: str = ""):
    """Crop GPT image to 9:16 and add subtle lower caption for Shorts readability.
    If idx==1, also draws a glowing silver bold title overlay at the top."""
    W, H = 1080, 1920
    img = Image.open(src).convert("RGB")
    sw, sh = img.size
    target_ratio = W / H
    if sw / sh > target_ratio:
        nw = int(sh * target_ratio)
        left = (sw - nw) // 2
        img = img.crop((left, 0, left + nw, sh))
    else:
        nh = int(sw / target_ratio)
        top = max(0, (sh - nh) // 2)
        img = img.crop((0, top, sw, top + nh))
    img = img.resize((W, H), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    font_path = find_font()
    if font_path is None:
        raise RuntimeError("no CJK font found on this system")
    body_font = ImageFont.truetype(font_path, 44)
    small_font = ImageFont.truetype(font_path, 30)

    # ── Title overlay on ALL images: glowing gold bold title, center-vertical, full-image mist ──
    if title:
        title_font = ImageFont.truetype(font_path, 72)  # bold & readable
        lines = wrap_text(draw, title, title_font, 960)[:3]  # max 3 lines

        # measure total height of the text block
        line_heights = []
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=title_font)
            line_heights.append(bb[3] - bb[1])
        total_text_h = sum(line_heights) + (len(lines) - 1) * 20
        y2 = (H - total_text_h) // 2  # center vertically

        # ── full-image mist-black overlay ──
        mist = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        mist_draw = ImageDraw.Draw(mist)
        mist_draw.rectangle((0, 0, W, H), fill=(0, 0, 0, 100))  # 半透明霧黑全圖
        img = Image.alpha_composite(img.convert("RGBA"), mist).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")  # recreate draw after composite

        for line in lines:
            # Thick black outline
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    if dx * dx + dy * dy <= 9:
                        draw.text((95 + dx, y2 + dy), line, fill=(0, 0, 0, 220), font=title_font)
            # Glowing egg-yolk gold glow behind text
            glow_color = (255, 180, 0, 35)
            for dx, dy in [(2, 0), (-2, 0), (0, 2), (0, -2)]:
                draw.text((95 + dx, y2 + dy), line, fill=glow_color, font=title_font)
            # Core bright egg-yolk gold text
            draw.text((95, y2), line, fill=(255, 180, 0, 250), font=title_font)
            y2 += line_heights[0] + 20

    # ── Lower caption panel (all images, including first) ──
    panel_top = 1380
    draw.rounded_rectangle((55, panel_top, 1025, 1815), radius=36, fill=(0, 0, 0, 150))
    draw.text((85, panel_top + 28), f"股得Night Shorts  {idx}/{total}", fill=(255, 210, 95, 255), font=small_font)
    y = panel_top + 82
    for line in wrap_text(draw, caption, body_font, 880)[:5]:
        draw.text((85, y), line, fill=(255, 255, 255, 245), font=body_font)
        y += 58
    img.save(dst, quality=95)


def has_partial_shorts_images(out_dir: Path) -> bool:
    """Return whether a retried video stage already has completed GPT/fallback cards."""
    return any(out_dir.glob("shorts_image_*.jpg"))


def build_image_prompt(title: str, chunk: dict, total: int, user_style: str = "") -> str:
    idx = int(chunk.get("index", 1))
    text = str(chunk.get("text", "")).strip()
    if chunk.get("kind") == "product":
        base_prompt = f"""
請生成一張 YouTube Shorts 直式 9:16 的 Amazon 商品推薦片尾行銷圖。
商品名稱：{chunk.get('product_name', '')}
與本集觀眾的需求關聯：{chunk.get('product_reason', '')}
片尾文案：{text}
畫面：以商品類別為主體的高質感 lifestyle hero shot，專業、可信、乾淨，讓觀眾感覺是前面新聞內容自然延伸出的實用工具，而不是突兀廣告。
限制：不得捏造價格、折扣、評價、配件或保證效果；圖片內不要放中文字、英文字、數字、logo、水印；不要假裝是 Amazon 官方廣告；商品實際規格以連結頁為準。
""".strip()
    else:
        base_prompt = f"""
請生成一張 YouTube Shorts 直式 9:16 圖片。
主題：{title}
第 {idx}/{total} 張，必須和其他張明顯不同構圖與畫面內容。
根據這段文案設計畫面：{text}
風格：台灣財經短影音封面感、專業現代、電影級光影、股市/科技/國際金融視覺元素、深色高質感、可有人物剪影但不要真實名人臉。
限制：圖片內不要放中文字、英文字、數字、logo、水印；不要投資建議字樣；直式構圖，適合手機全螢幕。
""".strip()
    return f"{user_style}\n\n{base_prompt}" if user_style else base_prompt


def generate_chatgpt_shorts_images(job_id: str, data: dict) -> list[Path]:
    """Ask ChatGPT for six news visuals plus an optional product outro visual."""
    from playwright.sync_api import sync_playwright

    st = load(job_id)
    chunks = build_visual_chunks(data, st.get("product"))
    if len(chunks) < 6:
        log(f"WARN only {len(chunks)} chunks generated from contexts; continuing")
    out_dir = jdir(job_id) / "gpt_images"
    out_dir.mkdir(exist_ok=True)
    # A previous Playwright/CDP call can stall after some images were persisted.  On a
    # retry, reuse those images and create the pipeline's normal fallback cards for
    # only the missing slots instead of risking another unbounded provider wait.
    resuming_partial = has_partial_shorts_images(out_dir)
    final_paths = []
    title = data.get("TOPIC", "股得Night")
    user_img_style = st.get("img_prompt", "").strip()

    def current_image_srcs(page):
        return set(page.evaluate("""
        () => Array.from(document.querySelectorAll('img')).map(img => ({
            src: img.src || '', w: img.naturalWidth || img.width || 0, h: img.naturalHeight || img.height || 0
        })).filter(x => x.w > 100 && x.h > 100 && (
            x.src.includes('estuary/content') || x.src.includes('oaidalleapi') || x.src.includes('files.oaiusercontent')
        )).map(x => x.src)
        """))

    with sync_playwright() as p:
        log("Connecting Chrome CDP for GPT image generation")
        browser = None
        last_err = None
        secure_url = validated_cdp_url()
        for cdp_url in ([secure_url] if secure_url else []):
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
                log(f"Connected CDP at {cdp_url}")
                break
            except Exception as e:
                last_err = e
        if browser is None:
            raise RuntimeError(
                "無法連線 Chrome CDP（9222/9224 皆拒絕連線）。請先執行 start_chrome_cdp.bat 啟動 CDP Chrome 再重試。"
            ) from last_err
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        page = page or context.new_page()
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        for chunk in chunks:
            idx = chunk["index"]
            text = chunk["text"]
            final_path = out_dir / f"shorts_image_{idx:02d}.jpg"
            raw_path = out_dir / f"raw_{idx:02d}.png"
            if final_path.exists():
                final_paths.append(final_path)
                log(f"GPT image exists, reuse: {final_path}")
                continue
            card_title = str(chunk.get("product_name") or title) if chunk.get("kind") == "product" else str(title)
            if resuming_partial:
                log(f"WARN resumed partial video stage; fallback card used for missing image {idx}/{len(chunks)}")
                make_card(final_path, card_title, text, idx, len(chunks))
                final_paths.append(final_path)
                continue
            img_prompt = build_image_prompt(title, chunk, len(chunks), user_img_style)
            log(f"Generating GPT image {idx}/{len(chunks)} kind={chunk.get('kind')} from {chunk['context']} part {chunk['part']}")
            img_src = None
            for attempt in range(1, 4):
                # ChatGPT can display an account/rate-limit modal over an otherwise
                # visible editor. Playwright then aborts the video stage before the
                # pipeline's normal fallback-card path can run. Detect it first and
                # handle it as an ordinary provider-generation failure.
                if page.locator('[data-testid="modal-conversation-history-rate-limit"]:visible').count():
                    log(
                        f"WARN GPT image provider blocked by conversation-history rate-limit modal "
                        f"for image {idx}/{len(chunks)}; fallback card used"
                    )
                    break
                before = current_image_srcs(page)
                before_text_len = page.evaluate("() => document.body.innerText.length")
                prompt_to_send = img_prompt if attempt == 1 else (img_prompt + f"\n\n剛剛生成失敗，請重新嘗試第 {idx}/{len(chunks)} 張；可以簡化畫面，但一定要直接產生圖片。")
                area = page.locator('#prompt-textarea[contenteditable="true"]:visible')
                area.wait_for(state="visible", timeout=30000)
                area.click()
                area.fill("")
                area.fill(prompt_to_send)
                page.wait_for_timeout(800)
                sent = page.evaluate("""() => {
                    const buttons=[...document.querySelectorAll('button')];
                    const btn=buttons.find(b=>{
                      const s=((b.getAttribute('data-testid')||'')+' '+(b.getAttribute('aria-label')||'')+' '+(b.innerText||b.textContent||''));
                      const dis=b.disabled||b.getAttribute('aria-disabled')==='true';
                      return !dis && /send|submit|傳送|送出/i.test(s);
                    });
                    if(btn){ btn.click(); return true; }
                    return false;
                }""")
                if not sent:
                    page.keyboard.press("Enter")
                try:
                    page.wait_for_function("""() => {
                        const el=document.querySelector('#prompt-textarea');
                        return el && !(el.innerText||el.textContent||'').trim();
                    }""", timeout=8000)
                except Exception:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1500)
                for _ in range(120):  # up to 4 minutes per attempt
                    page.wait_for_timeout(2000)
                    after = current_image_srcs(page)
                    new = [s for s in after if s not in before]
                    if new:
                        img_src = new[-1]
                        break
                    body_len = page.evaluate("() => document.body.innerText.length")
                    tail = page.evaluate("() => document.body.innerText.slice(-1200)")
                    if body_len > before_text_len + 50 and any(s in tail for s in ["無法成功生成", "再發一次請求", "couldn't generate", "unable to generate"]):
                        log(f"GPT image {idx} attempt {attempt} returned generation failure; retrying")
                        break
                if img_src:
                    break
            if not img_src:
                shot = out_dir / f"debug_image_{idx:02d}.png"
                shot_ok = safe_page_screenshot(page, shot)
                log(
                    f"WARN GPT image {idx} failed after retries; fallback card used. "
                    f"screenshot={shot if shot_ok else 'unavailable'}"
                )
                make_card(final_path, card_title, text, idx, len(chunks))
                final_paths.append(final_path)
                continue
            b64 = page.evaluate("""
            async (url) => {
                const resp = await fetch(url);
                const blob = await resp.blob();
                const reader = new FileReader();
                return new Promise(resolve => { reader.onloadend = () => resolve(reader.result.split(',')[1]); reader.readAsDataURL(blob); });
            }
            """, img_src)
            raw_path.write_bytes(base64.b64decode(b64))
            normalize_vertical_image(raw_path, final_path, text, idx, len(chunks), title=card_title)
            final_paths.append(final_path)
            log(f"GPT image saved: {final_path}")
        browser.close()
    return final_paths


def stage_video(job_id: str, st: dict):
    audio = Path(st.get("artifacts", {}).get("audio", ""))
    if not audio.exists():
        raise FileNotFoundError("audio artifact missing")
    if st["target_type"] == "shorts":
        data = st.get("script_json", {})
        # New Shorts behavior: CONTEXT1/2/3 -> split each into 2 parts -> ask ChatGPT for 6 distinct images.
        image_paths = generate_chatgpt_shorts_images(job_id, data)
        if not image_paths:
            raise RuntimeError("no GPT images generated for Shorts")
        # Duration split equally by audio duration.
        probe = subprocess.run([FFMPEG, "-i", str(audio), "-f", "null", "-"], capture_output=True, text=True)
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr or "")
        dur = 45.0
        if m:
            dur = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
        per = max(1.0, dur / len(image_paths))
        list_file = jdir(job_id) / "slides.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in image_paths:
                f.write(f"file '{p.as_posix()}'\n")
                f.write(f"duration {per:.3f}\n")
            f.write(f"file '{image_paths[-1].as_posix()}'\n")
        silent_video = jdir(job_id) / "shorts_slides.mp4"
        out = jdir(job_id) / "shorts_final.mp4"
        subprocess.run([FFMPEG, "-f", "concat", "-safe", "0", "-i", str(list_file), "-vf", "scale=1080:1920,format=yuv420p", "-r", "30", "-y", str(silent_video)], check=True, timeout=600)
        subprocess.run([FFMPEG, "-i", str(silent_video), "-i", str(audio), "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", "-y", str(out)], check=True, timeout=600)
        st.setdefault("artifacts", {})["video"] = str(out)
        st["video_path"] = str(out)
        for idx, p in enumerate(image_paths, 1):
            st["artifacts"][f"gpt_image_{idx}"] = str(p)
        log(f"Shorts video ready with {len(image_paths)} GPT images: {out}")
    else:
        out = jdir(job_id) / "youtube_full.mp4"
        subprocess.run([
            FFMPEG, "-loop", "1", "-i", str(LOGO), "-i", str(audio),
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:a", "aac", "-b:a", "192k", "-shortest", "-y", str(out)
        ], check=True, timeout=600)
        st.setdefault("artifacts", {})["video"] = str(out)
        st["video_path"] = str(out)
        log(f"YouTube video ready: {out}")
    finish_stage(job_id, st, "video")


def affiliate_comment_already_done(st: dict, video_id: str, product: dict) -> bool:
    from affiliate_product import comment_fingerprint, normalize_product
    p = normalize_product(product)
    record = st.get("affiliate_comment") or {}
    expected = comment_fingerprint(video_id, p["affiliate_url"])
    return bool(record.get("verified") and record.get("fingerprint") == expected)


async def post_youtube_affiliate_comment(youtube_url: str, product: dict) -> dict:
    """Post one disclosed affiliate comment and verify it on the public watch page."""
    from affiliate_product import (
        affiliate_comment_text,
        comment_fingerprint,
        extract_video_id,
        normalize_product,
    )
    from playwright.async_api import async_playwright

    pinfo = normalize_product(product)
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise ValueError("cannot extract YouTube video ID for affiliate comment")
    text = affiliate_comment_text(pinfo)
    fingerprint = comment_fingerprint(video_id, pinfo["affiliate_url"])

    async with async_playwright() as pw:
        secure_url = validated_cdp_url()
        if not secure_url:
            raise RuntimeError("No validated visible Chrome CDP endpoint for YouTube comment")
        browser = await pw.chromium.connect_over_cdp(secure_url, timeout=30000)
        context = browser.contexts[0]
        page = await context.new_page()
        try:
            await page.goto(
                f"https://www.youtube.com/watch?v={video_id}",
                wait_until="commit",
                timeout=30000,
            )
            await page.wait_for_timeout(10000)
            for y in (700, 1100, 1500, 1900):
                await page.evaluate(f"window.scrollTo(0,{y})")
                await page.wait_for_timeout(4000)
                if await page.locator("ytd-comment-simplebox-renderer, #placeholder-area, #simplebox-placeholder").count():
                    break
            body = await page.locator("body").inner_text(timeout=20000)
            if any(marker in body for marker in ["留言功能已關閉", "Comments are turned off"]):
                raise RuntimeError("YouTube comments are turned off for this video")
            if any(marker in body for marker in ["登入即可留言", "Sign in to comment"]):
                raise RuntimeError("YouTube watch page is not signed in for commenting")

            # Crash-safe idempotency: verify the public page before posting again.
            existing = page.locator("ytd-comment-thread-renderer").filter(has_text=pinfo["affiliate_url"])
            if await existing.count():
                return {
                    "status": "existing",
                    "verified": True,
                    "video_id": video_id,
                    "fingerprint": fingerprint,
                    "affiliate_url": pinfo["affiliate_url"],
                }

            placeholder = page.locator("#placeholder-area:visible, #simplebox-placeholder:visible").first
            await placeholder.wait_for(state="visible", timeout=30000)
            await placeholder.click()
            editable = page.locator('#contenteditable-root[contenteditable="true"]:visible').first
            await editable.wait_for(state="visible", timeout=15000)
            await editable.fill(text)
            submit = page.locator("ytd-comment-simplebox-renderer #submit-button:visible").first
            await submit.wait_for(state="visible", timeout=15000)
            await submit.click()

            matched = page.locator("ytd-comment-thread-renderer").filter(has_text=pinfo["affiliate_url"])
            await matched.first.wait_for(state="visible", timeout=30000)
            matched_text = await matched.first.inner_text(timeout=10000)
            if pinfo["name"] not in matched_text or pinfo["affiliate_url"] not in matched_text:
                raise RuntimeError("affiliate comment appeared but exact product/link verification failed")
            hrefs = await matched.first.locator('a[href*="lc="]').evaluate_all(
                "els => els.map(e => e.href).filter(Boolean)"
            )
            return {
                "status": "posted",
                "verified": True,
                "video_id": video_id,
                "fingerprint": fingerprint,
                "affiliate_url": pinfo["affiliate_url"],
                "comment_url": hrefs[0] if hrefs else "",
            }
        finally:
            await page.close()


def stage_comment(job_id: str, st: dict):
    from affiliate_product import extract_video_id

    product = st.get("product")
    youtube_url = str(st.get("youtube_url") or "")
    if not product:
        raise RuntimeError("affiliate comment stage requires product data")
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise RuntimeError("affiliate comment stage requires a verified YouTube URL")
    if affiliate_comment_already_done(st, video_id, product):
        log(f"Affiliate comment already verified; skip duplicate video_id={video_id}")
        finish_stage(job_id, st, "comment")
        return
    record = asyncio.run(post_youtube_affiliate_comment(youtube_url, product))
    st["affiliate_comment"] = record
    log(f"Affiliate comment result: {json.dumps(record, ensure_ascii=False)}")
    finish_stage(job_id, st, "comment")


async def upload_youtube(title, desc, video):
    """Load a user-supplied uploader adapter without embedding credentials."""
    import importlib.util
    uploader_path = YOUTUBE_UPLOADER_PATH
    if not uploader_path.exists():
        raise FileNotFoundError(f"cron uploader not found: {uploader_path}")
    spec = importlib.util.spec_from_file_location("gudetnight_daily_youtube_pipeline", str(uploader_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load uploader spec: {uploader_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    secure_url = validated_cdp_url()
    if not secure_url:
        raise RuntimeError("No validated visible Chrome CDP endpoint for YouTube upload")
    setattr(mod, "YOUTUBE_CDP_URL", secure_url)
    return await mod.upload_youtube(title, desc, video)


async def find_latest_studio_video_url(title: str, shorts: bool = False):
    # YouTube Studio lists Shorts under /videos/short; the cron uploader verifier checks /videos/upload.
    # Scrape the current logged-in Studio via CDP and return a public watch/shorts URL if the uploaded title is visible.
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        log(f"Studio URL lookup skipped: playwright unavailable: {exc!r}")
        return None
    if not YOUTUBE_CHANNEL_ID:
        log("Studio URL lookup skipped: YOUTUBE_CHANNEL_ID is not configured")
        return None
    tab = "short" if shorts else "upload"
    async with async_playwright() as p:
        secure_url = validated_cdp_url()
        if not secure_url:
            return None
        browser = await p.chromium.connect_over_cdp(secure_url)
        context = browser.contexts[0]
        page = await context.new_page()
        await page.goto(
            f"https://studio.youtube.com/channel/{YOUTUBE_CHANNEL_ID}/videos/{tab}?filter=%5B%5D&sort=%7B%22columnType%22%3A%22date%22%2C%22sortOrder%22%3A%22DESCENDING%22%7D",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(10000)
        anchors = await page.evaluate("""() => [...document.querySelectorAll('a[href*=\"/video/\"]')]
            .map(a => ({text:(a.innerText||a.textContent||'').trim(), href:a.href}))""")
        title_key = (title or "").replace("#Shorts", "").strip()[:24]
        for a in anchors:
            href = a.get("href") or ""
            text = a.get("text") or ""
            if "/video/" in href and (not title_key or title_key in text or text in title):
                vid = href.split("/video/", 1)[1].split("/", 1)[0].split("?", 1)[0]
                if len(vid) == 11:
                    return f"https://youtube.com/shorts/{vid}" if shorts else f"https://youtu.be/{vid}"
        return None


async def ensure_studio_public(youtube_url: str):
    # If the reused uploader leaves a Shorts item as 不公開, set it to 公開 from Studio edit page.
    m = re.search(r"(?:shorts/|youtu\.be/|v=)([A-Za-z0-9_-]{11})", youtube_url or "")
    if not m:
        return False
    vid = m.group(1)
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        log(f"Visibility fix skipped: playwright unavailable: {exc!r}")
        return False
    async with async_playwright() as p:
        secure_url = validated_cdp_url()
        if not secure_url:
            return False
        browser = await p.chromium.connect_over_cdp(secure_url)
        page = await browser.contexts[0].new_page()
        auth_query = f"?authuser={TARGET_EMAIL}" if TARGET_EMAIL else ""
        await page.goto(f"https://studio.youtube.com/video/{vid}/edit{auth_query}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        body = await page.locator("body").inner_text(timeout=15000)
        if "瀏覽權限\n公開" in body or "瀏覽權限\r\n公開" in body:
            return True
        clicked = await page.evaluate("""() => {
            const els=[...document.querySelectorAll('*')].filter(e=>{
              const s=(e.innerText||e.textContent||'').trim(); const r=e.getBoundingClientRect();
              return (s==='不公開'||s==='私人') && r.width>0 && r.height>0;
            });
            const e=els[els.length-1]; if(e){ e.scrollIntoView({block:'center'}); e.click(); return true; }
            return false;
        }""")
        if not clicked:
            return False
        await page.wait_for_timeout(3000)
        picked = await page.evaluate("""() => {
            const radios=[...document.querySelectorAll('tp-yt-paper-radio-button')];
            const r=radios.find(e=>{const s=e.innerText||e.textContent||''; return s.includes('公開') && !s.includes('不公開') && !s.includes('私人');});
            if(r){ r.scrollIntoView({block:'center'}); r.click(); return true; }
            return false;
        }""")
        if not picked:
            return False
        await page.wait_for_timeout(1500)
        saved = await page.evaluate("""() => {
            const btns=[...document.querySelectorAll('ytcp-button,button,tp-yt-paper-button')];
            const c=btns.filter(e=>{const s=((e.innerText||e.textContent||'')+' '+(e.getAttribute('aria-label')||'')); const r=e.getBoundingClientRect(); const dis=e.disabled||e.hasAttribute('disabled')||e.getAttribute('aria-disabled')==='true'; return !dis && r.width>0 && r.height>0 && (s.includes('儲存')||s.includes('Save')||s.includes('完成')||s.includes('Done')||s.includes('發布')||s.includes('Publish'));});
            const e=c[c.length-1]; if(e){ e.click(); return true; }
            return false;
        }""")
        await page.wait_for_timeout(5000)
        top_saved = await page.evaluate("""() => {
            const btns=[...document.querySelectorAll('ytcp-button,button,tp-yt-paper-button')];
            const c=btns.filter(e=>{const s=((e.innerText||e.textContent||'')+' '+(e.getAttribute('aria-label')||'')); const r=e.getBoundingClientRect(); const dis=e.disabled||e.hasAttribute('disabled')||e.getAttribute('aria-disabled')==='true'; return !dis && r.width>0 && r.height>0 && (s.includes('儲存')||s.includes('Save'));});
            const e=c[c.length-1]; if(e){ e.click(); return true; }
            return false;
        }""")
        await page.wait_for_timeout(15000)
        return bool(saved or top_saved)


def stage_upload(job_id: str, st: dict):
    video = st.get("video_path") or st.get("artifacts", {}).get("video")
    if not video or not os.path.exists(video):
        raise FileNotFoundError("video artifact missing")
    data = st.get("script_json", {})
    title = data.get("TOPIC") or st.get("title") or "股得Night"
    if st.get("target_type") == "shorts":
        # 移除 title 內原有的任何 hashtag，用統一的
        clean_title = re.sub(r"\s*#[^\s#]*", "", title).strip()
        title = (clean_title[:80] + " #股市 #台股 #美股 #AI #產業 #國際 #投資 #Shorts")[:100]
        desc_tags = "#股市 #台股 #美股 #AI #產業 #國際 #投資"
    else:
        desc_tags = "#股得Night #台股"
    desc = "\n\n".join([data.get("CONTEXT1", ""), data.get("CONTEXT2", ""), data.get("CONTEXT3", "")]).strip()
    if st.get("product") and st.get("target_type") == "shorts":
        desc += f"\n\n{AMAZON_DISCLOSURE}"
    desc += f"\n\n{desc_tags}"
    log(f"DEBUG title={title[:80]!r} desc_suffix={desc[-120:]!r}")
    result = asyncio.run(upload_youtube(title[:100], desc[:4900], video))
    if not isinstance(result, dict):
        result = {"raw": result}
    youtube_url = result.get("youtube_url")
    if not youtube_url:
        youtube_url = asyncio.run(find_latest_studio_video_url(title, shorts=st.get("target_type") == "shorts"))
        if youtube_url:
            result["youtube_url"] = youtube_url
            result["studio_lookup_verified"] = True
    if youtube_url:
        public_ok = asyncio.run(ensure_studio_public(youtube_url))
        result["studio_public_verified_or_fixed"] = public_ok
    st["upload_result"] = result
    st["youtube_url"] = youtube_url
    st.pop("error", None)
    st.pop("traceback", None)
    log(f"Upload result: {json.dumps(result, ensure_ascii=False)}")
    if not youtube_url:
        raise RuntimeError("upload finished but YouTube URL could not be verified from Studio")
    finish_stage(job_id, st, "upload")


def run_stage(job_id: str, stage: str):
    st = load(job_id)
    st["running"] = True
    st["current_stage"] = stage
    st["status"] = f"running_{stage}"
    st["next_stage"] = None
    save(job_id, st)
    log(f"START stage={stage} job={job_id}")
    try:
        ensure_cdp_chrome()
        if stage == "script":
            stage_script(job_id, st)
        elif stage == "audio":
            stage_audio(job_id, st)
        elif stage == "video":
            stage_video(job_id, st)
        elif stage == "upload":
            stage_upload(job_id, st)
        elif stage == "comment":
            stage_comment(job_id, st)
        else:
            raise ValueError(stage)
    except Exception as e:
        st = load(job_id)
        st["running"] = False
        st["pid"] = None
        st["status"] = "failed"
        st["error"] = repr(e)
        st["traceback"] = traceback.format_exc()
        save(job_id, st)
        log(f"FAILED stage={stage}: {e!r}\n{traceback.format_exc()}")
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--stage", required=True, choices=["script", "audio", "video", "upload", "comment"])
    args = ap.parse_args()
    run_stage(args.job, args.stage)


if __name__ == "__main__":
    main()
