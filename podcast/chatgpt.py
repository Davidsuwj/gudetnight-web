"""STEP 1 — ChatGPT：取得最新 podcast JSON，並檢查 / 修正 CONTEXT1/2/3。"""
import asyncio, os, json
from .config import (PROMPT_FILE, JSON_OUTPUT, DOWNLOAD_DIR, CONTEXT_KEYS,
                     MAX_FETCH_ATTEMPTS, shot)
from .utils import parse_json_blob, check_contexts, save_json
from .browser import find_or_create_page


async def _safe_screenshot(page, path):
    """Capture diagnostics without allowing a hung page to mask the real error."""
    try:
        await asyncio.wait_for(page.screenshot(path=path), timeout=15)
        return True
    except Exception as exc:
        print(f"[WARN] Screenshot skipped: {type(exc).__name__}: {exc}")
        return False


async def _find_prompt_locator(page):
    """Return the first visible ChatGPT composer across current UI variants."""
    selectors = (
        '#prompt-textarea[contenteditable="true"]',
        '#prompt-textarea',
        '[contenteditable="true"][data-lexical-editor="true"]',
        'textarea[data-testid="prompt-textarea"]',
    )
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if await locator.count() == 0:
                continue
            candidate = locator.first if hasattr(locator, "first") else locator
            if hasattr(candidate, "is_visible") and not await candidate.is_visible():
                continue
            return candidate
        except Exception:
            continue
    return None


async def _send_message(page, text, fresh=False, ready_timeout=30):
    """Send text only after a visible ChatGPT composer is ready."""
    if fresh:
        print("[INFO] Starting new chat...")
        try:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
        except Exception as exc:
            print(f"[ERROR] ChatGPT navigation failed: {exc}")
            await _safe_screenshot(page, shot("chatgpt_debug.png"))
            return False

    textarea = None
    deadline = asyncio.get_running_loop().time() + max(0, ready_timeout)
    while asyncio.get_running_loop().time() < deadline:
        textarea = await _find_prompt_locator(page)
        if textarea is not None:
            break
        await asyncio.sleep(1)
    if textarea is None:
        print("[ERROR] ChatGPT prompt not ready - login or Turnstile may be blocking")
        await _safe_screenshot(page, shot("chatgpt_debug.png"))
        return False

    try:
        await textarea.click()
        await textarea.fill("")
        await textarea.fill(text)
        await asyncio.sleep(1)
        send_btn = page.locator('button[data-testid="send-button"]:visible')
        if await send_btn.count() == 0:
            send_btn = page.locator('[data-testid="send-button"]')
        if await send_btn.count() == 0:
            send_btn = page.locator('button:has(svg)').last
        await send_btn.click()
    except Exception as exc:
        print(f"[ERROR] ChatGPT send failed: {exc}")
        await _safe_screenshot(page, shot("chatgpt_send_error.png"))
        return False
    print("[INFO] Prompt sent! Waiting for response...")
    return True


def _is_complete_response_text(text):
    """Return True once the latest assistant response contains the required JSON.

    ChatGPT can leave a stale stop button mounted after generation has completed.  The
    parsed response is a stronger completion signal for this JSON-only pipeline.
    """
    data = parse_json_blob(text or "")
    return isinstance(data, dict) and all(str(data.get(k, "")).strip() for k in ("TOPIC", *CONTEXT_KEYS))


async def _response_snapshot(page):
    """Read completion state in one bounded CDP call.

    Individual Playwright locator calls can occasionally wait forever on a live
    ChatGPT tab even though raw CDP still responds.  A single evaluated snapshot,
    guarded by asyncio.wait_for, keeps the six-minute outer deadline effective.
    """
    script = """() => {
      const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
      return {
        latestText: messages.length ? (messages[messages.length - 1].innerText || '') : '',
        hasStop: !!document.querySelector('[data-testid="stop-button"]'),
        hasMarkdown: !!document.querySelector('.markdown')
      };
    }"""
    return await asyncio.wait_for(page.evaluate(script), timeout=10)


async def _wait_for_response(page):
    """等待 ChatGPT 回應完成（最長 6 分鐘）。"""
    max_wait, poll, waited = 360, 5, 0
    while waited < max_wait:
        await asyncio.sleep(poll)
        waited += poll

        try:
            snapshot = await _response_snapshot(page)
        except Exception as exc:
            if waited % 30 == 0:
                print(f"[DEBUG] Response snapshot unavailable ({type(exc).__name__}); waited {waited}s")
            continue

        # Prefer a fully parseable assistant JSON response over UI chrome. Newer
        # ChatGPT builds sometimes keep a stale stop button after generation.
        if _is_complete_response_text(snapshot.get("latestText", "")):
            print(f"[INFO] Complete JSON response detected after {waited}s")
            break
        if not snapshot.get("hasStop") and snapshot.get("hasMarkdown"):
            print(f"[INFO] Response complete after {waited}s")
            break
        if waited % 30 == 0:
            print(f"[DEBUG] Waiting... ({waited}s)")
    else:
        print("[WARN] Timeout waiting for response")
    await asyncio.sleep(3)


async def _extract_json(page, download_box):
    """從最新回應抽取 JSON dict（inline / code block / 下載），失敗回傳 None。"""
    download_box["path"] = None
    messages = page.locator('[data-message-author-role="assistant"]')
    msg_count = await messages.count()

    full_text = await messages.nth(msg_count - 1).inner_text() if msg_count else ""
    print(f"[INFO] Response text: {len(full_text)} chars")

    data = parse_json_blob(full_text)
    if data is not None:
        print(f"[SUCCESS] Inline JSON parsed! TOPIC: {data.get('TOPIC', 'N/A')}")
        return data

    code_blocks = page.locator('code')
    for i in range(await code_blocks.count()):
        try:
            code_text = await code_blocks.nth(i).inner_text()
            if '"TOPIC"' in code_text or '"CONTEXT' in code_text:
                d = parse_json_blob(code_text)
                if d is not None:
                    print(f"[SUCCESS] JSON in code block #{i}!")
                    return d
        except Exception:
            pass

    print("[INFO] No inline JSON, trying download button...")
    clicked = False
    for selector in ['a:has-text("下載")', 'button:has-text("下載")', 'a[download]',
                     '[data-testid="download-button"]']:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.click()
                clicked = True
                await asyncio.sleep(3)
                break
        except Exception:
            pass

    if not clicked and msg_count:
        links = messages.nth(msg_count - 1).locator('a')
        for i in range(await links.count()):
            try:
                href = (await links.nth(i).get_attribute("href") or "").lower()
                if "json" in href or "download" in href or "backend-api" in href:
                    await links.nth(i).click()
                    clicked = True
                    await asyncio.sleep(3)
                    break
            except Exception:
                pass

    if clicked:
        await asyncio.sleep(5)
        if download_box["path"]:
            try:
                with open(download_box["path"], "r", encoding="utf-8") as f:
                    d = parse_json_blob(f.read())
                if d is not None:
                    return d
            except Exception as e:
                print(f"[ERROR] Reading download: {e}")

    # 最後備援：掃 Downloads 內最新 JSON
    json_files = [(os.path.join(DOWNLOAD_DIR, f), os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)))
                  for f in os.listdir(DOWNLOAD_DIR)
                  if f.endswith('.json') and 'podcast' not in f.lower()]
    if json_files:
        json_files.sort(key=lambda x: x[1], reverse=True)
        try:
            with open(json_files[0][0], "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and ("TOPIC" in d or any(k in d for k in CONTEXT_KEYS)):
                return d
        except Exception:
            pass

    with open(JSON_OUTPUT.replace('.json', '_raw.txt'), 'w', encoding='utf-8') as f:
        f.write(full_text)
    await page.screenshot(path=shot("chatgpt_result.png"))
    return None


def _correction_prompt(missing):
    missing_str = "、".join(missing) if missing else "CONTEXT1、CONTEXT2、CONTEXT3"
    return (
        f"你剛剛輸出的 JSON 不符合要求，缺少或留空了：{missing_str}。\n"
        "請重新輸出『一個』完整的 JSON 物件，必須同時包含 TOPIC、CONTEXT1、CONTEXT2、CONTEXT3 四個欄位，"
        "每個 CONTEXT 都要有完整、可直接朗讀的逐字稿內容，不可省略、不可留空、不可用佔位文字。\n"
        "只輸出 JSON 本身，不要加上任何說明或程式碼以外的文字。"
    )


async def fetch_latest_json(browser, max_attempts=MAX_FETCH_ATTEMPTS):
    """
    送出 prompt 取得 podcast JSON，檢查 CONTEXT1/2/3；缺少則請 ChatGPT 修正，
    最多重試 max_attempts 次。成功回傳 JSON 路徑，失敗回傳 None。
    """
    print("\n" + "=" * 60)
    print("STEP 1 — 取得最新 Podcast JSON (ChatGPT)")
    print("=" * 60)

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        prompt = f.read().strip()
    print(f"[OK] Prompt loaded: {len(prompt)} chars")

    page = await find_or_create_page(browser, "chatgpt.com")

    download_box = {"path": None}

    async def handle_download(download):
        save_path = os.path.join(DOWNLOAD_DIR, download.suggested_filename)
        await download.save_as(save_path)
        print(f"[DOWNLOAD] Saved: {save_path}")
        download_box["path"] = save_path

    page.on("download", handle_download)

    missing = list(CONTEXT_KEYS)
    for attempt in range(1, max_attempts + 1):
        if attempt == 1:
            print(f"\n[ATTEMPT {attempt}/{max_attempts}] 送出原始 prompt")
            ok = await _send_message(page, prompt, fresh=True)
        else:
            print(f"\n[ATTEMPT {attempt}/{max_attempts}] 要求修正（缺少: {', '.join(missing)}）")
            ok = await _send_message(page, _correction_prompt(missing), fresh=False)
        if not ok:
            return None

        await _wait_for_response(page)
        data = await _extract_json(page, download_box)

        if data is None:
            print("[WARN] 未取得可解析 JSON，將請 ChatGPT 重做")
            missing = list(CONTEXT_KEYS)
            continue

        ok_ctx, missing = check_contexts(data)
        if ok_ctx:
            print(f"[CHECK] 通過：含 CONTEXT1/2/3，TOPIC: {data.get('TOPIC', 'N/A')}")
            return save_json(data)
        print(f"[CHECK] 未通過：缺少 {', '.join(missing)} → 請 ChatGPT 重做")

    print(f"[FATAL] 連續 {max_attempts} 次仍未取得完整 CONTEXT1/2/3，放棄")
    return None


def find_latest_json():
    """掃描工作區與 Downloads，回傳含 CONTEXT、最新修改的 JSON 路徑。"""
    from .config import WORKSPACE
    candidates, seen = [], set()
    for d in (WORKSPACE, DOWNLOAD_DIR):
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.endswith(".json"):
                continue
            fp = os.path.join(d, fname)
            if fp in seen:
                continue
            seen.add(fp)
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if any(k in data for k in CONTEXT_KEYS) or "CONTEXT" in data:
                candidates.append((fp, os.path.getmtime(fp)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]
