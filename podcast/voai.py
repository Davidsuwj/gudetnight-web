"""STEP 2 — VoAI：逐段把 CONTEXT 文字轉成 MP3。"""
import asyncio, os, time
from .config import (MP3_OUTPUT_DIR, VOICE_NAME, PITCH, SPEED, shot)
from .browser import find_or_create_page


async def setup_voice(page):
    """一次性設定：選擇語音（國賢）並嘗試調整進階音調 / 語速。"""
    print("\n=== VOICE SETUP ===")

    # 若目標語音已是預設（VoAI 現在預設就是國賢），直接跳過選擇器互動。
    already = page.locator(f'text={VOICE_NAME}').first
    if await already.count() > 0:
        print(f"[VOICE] {VOICE_NAME} already selected, skip selector")
    else:
        # 語音選擇器不一定是 <button>，放寬條件：pill / gear / 「更多角色」皆可開啟面板。
        voice_btn = None
        for selector in (
            f'text={VOICE_NAME}',
            'text=更多角色',
            'button:has-text("Neo")',
            ':is(button,div,span)[class*="voice" i]',
            'text=Neo',
        ):
            el = page.locator(selector).first
            if await el.count() > 0:
                voice_btn = el
                break
        if voice_btn is None:
            print("[ERROR] Voice selector not found")
            await page.screenshot(path=shot("voai_err_voice.png"))
            return False
        await voice_btn.click()
        await asyncio.sleep(2)

        target_voice = page.locator(f'text={VOICE_NAME}').first
        if await target_voice.count() == 0:
            print(f"[ERROR] {VOICE_NAME} not found")
            await page.screenshot(path=shot("voai_err_voice2.png"))
            return False
        await target_voice.click()
        await asyncio.sleep(1.5)
        print(f"[VOICE] {VOICE_NAME} selected")

    # 關閉語音面板
    await page.keyboard.press("Escape")
    await asyncio.sleep(1)
    aside = page.locator('aside').first
    if await aside.count() > 0:
        try:
            if await aside.is_visible():
                main_area = page.locator('main, [class*="content"]').first
                if await main_area.count() > 0:
                    await main_area.click(position={"x": 10, "y": 10})
                await asyncio.sleep(1)
        except Exception:
            pass

    # 進階設定（音調 / 語速）— 找不到也不致命
    adv_btn = None
    for selector in ['button:has-text("進階設定")', 'button:has-text("進階")', 'text=進階設定']:
        el = page.locator(selector).first
        if await el.count() > 0:
            adv_btn = el
            break
    if adv_btn:
        await adv_btn.click()
        await asyncio.sleep(1.5)
        ranges = page.locator('input[type="range"]')
        pitch_set = speed_set = False
        for i in range(await ranges.count()):
            try:
                r = ranges.nth(i)
                lo = await r.get_attribute("min") or ""
                hi = await r.get_attribute("max") or ""
                aria = (await r.get_attribute("aria-label") or "").lower()
                if not pitch_set and ('音調' in aria or 'pitch' in aria or 'tone' in aria
                                      or (lo and hi and float(lo) <= float(PITCH) <= float(hi))):
                    await r.fill(PITCH); pitch_set = True
                    print(f"[ADV] pitch = {PITCH}")
                elif not speed_set and ('語速' in aria or 'speed' in aria or 'rate' in aria
                                        or (lo and hi and float(lo) <= float(SPEED) <= float(hi))):
                    await r.fill(SPEED); speed_set = True
                    print(f"[ADV] speed = {SPEED}")
            except Exception:
                pass
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
    else:
        print("[WARN] 進階設定 not found, using defaults")

    print("[VOICE] Setup complete")
    return True


async def _close_voice_panel(page):
    """關閉左側語音面板，避免擋住生成按鈕。"""
    aside = page.locator('aside').first
    if await aside.count() > 0:
        x_btn = aside.locator('button').first
        if await x_btn.count() > 0:
            try:
                await x_btn.click()
                await asyncio.sleep(0.5)
            except Exception:
                pass
        try:
            if await aside.is_visible():
                await page.evaluate("const a=document.querySelector('aside'); if(a) a.style.display='none';")
        except Exception:
            pass
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.3)


async def _paste_text(page, text):
    """清空輸入框並貼上 text。"""
    # VoAI 頁面常以 skeleton 佔位符先載入，輸入框稍後才出現；輪詢等待最多 40 秒。
    textarea = None
    for _ in range(40):
        for sel in ('textarea', '[contenteditable="true"]', 'input[type="text"]'):
            cand = page.locator(sel).first
            if await cand.count() > 0:
                try:
                    if await cand.is_visible():
                        textarea = cand
                        break
                except Exception:
                    pass
        if textarea is not None:
            break
        await asyncio.sleep(1)
    if textarea is None:
        print("[ERROR] Textarea not found!")
        await page.screenshot(path=shot("voai_err_textarea.png"))
        return False
    await textarea.click()
    await asyncio.sleep(0.3)
    await textarea.fill("")
    await asyncio.sleep(0.2)
    for i in range(0, len(text), 500):
        await textarea.type(text[i:i + 500], delay=5)
        await asyncio.sleep(0.2)
    print("  [INPUT] Text pasted")
    return True


async def _wait_generated(page):
    """等待生成完成（下載 icon 出現）。"""
    max_wait, waited = 360, 0
    while waited < max_wait:
        await asyncio.sleep(5)
        waited += 5
        if await page.locator('svg[aria-label="下載"]').first.count() > 0:
            print(f"  [READY] Generated after {waited}s")
            return True
        if waited % 30 == 0:
            print(f"  [WAIT] Still generating... ({waited}s)")
    print("[ERROR] Generation timed out!")
    await page.screenshot(path=shot("voai_err_timeout.png"))
    return False


async def _download_mp3(page, output_name):
    """開啟下載選單選 mp3，存成 {output_name}.mp3。"""
    print("  [DL] Opening download menu...")
    marker = time.time()
    save_path = None

    dl_icon = page.locator('svg[aria-label="下載"]').last
    if await dl_icon.count() == 0:
        print("[ERROR] Download icon not found!")
        return False

    try:
        await dl_icon.locator('..').click(force=True)
        await asyncio.sleep(0.8)
        mp3_btn = page.locator('div.flex.items-center.gap-1:has-text("mp3")').last
        if await mp3_btn.count() == 0:
            mp3_btn = page.locator('text=mp3').last
        if await mp3_btn.count() == 0:
            mp3_btn = page.locator('div:has-text("mp3")').last
        if await mp3_btn.count() > 0:
            try:
                async with page.expect_download(timeout=15000) as info:
                    await mp3_btn.click(force=True)
                download = await info.value
                save_path = os.path.join(MP3_OUTPUT_DIR, download.suggested_filename)
                await download.save_as(save_path)
                print(f"  [DOWNLOAD] {download.suggested_filename}")
            except Exception as e:
                print(f"  [WARN] Download capture missed: {e}")
    except Exception as e:
        print(f"[ERROR] Download menu click failed: {e}")
        return False

    target_path = os.path.join(MP3_OUTPUT_DIR, f"{output_name}.mp3")
    if not save_path:
        await asyncio.sleep(5)
        candidates = [(os.path.join(MP3_OUTPUT_DIR, f), os.path.getmtime(os.path.join(MP3_OUTPUT_DIR, f)))
                      for f in os.listdir(MP3_OUTPUT_DIR)
                      if f.endswith('.mp3') and os.path.getmtime(os.path.join(MP3_OUTPUT_DIR, f)) > marker]
        if not candidates:
            print("[ERROR] No new MP3 found after download!")
            return False
        candidates.sort(key=lambda x: x[1], reverse=True)
        save_path = candidates[0][0]
        print(f"  [FALLBACK] Matched {os.path.basename(save_path)}")

    if os.path.exists(target_path):
        os.remove(target_path)
    os.rename(save_path, target_path)
    print(f"  [SUCCESS] Saved: {target_path}")
    return True


async def generate_speech(page, text, output_name):
    """清空輸入 → 貼文字 → 生成 → 下載成 {output_name}.mp3。"""
    print(f"\n=== GENERATING: {output_name} ({len(text)} chars) ===")
    if not await _paste_text(page, text):
        return False
    await asyncio.sleep(1)
    await _close_voice_panel(page)

    gen_btn = page.locator('button:has-text("開始生成")').first
    if await gen_btn.count() == 0:
        gen_btn = page.locator('button:has-text("生成")').first
    if await gen_btn.count() == 0:
        print("[ERROR] 開始生成 button not found!")
        await page.screenshot(path=shot("voai_err_generate.png"))
        return False
    await gen_btn.click(force=True)
    print("  [GEN] Clicked 開始生成")
    await asyncio.sleep(2)

    if not await _wait_generated(page):
        return False
    return await _download_mp3(page, output_name)


async def open_voai(browser):
    """開啟 VoAI 建立頁並完成語音設定，回傳 page；失敗回傳 None。"""
    page = await find_or_create_page(browser, "voai.ai")
    await page.goto("https://app.voai.ai/create", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)
    # 等待頁面實際渲染完成（skeleton 消失、輸入框出現）再進行語音設定。
    for _ in range(40):
        try:
            if await page.locator('textarea, [contenteditable="true"]').first.count() > 0:
                break
        except Exception:
            pass
        await asyncio.sleep(1)
    print(f"[OK] VoAI loaded: {await page.title()}")
    if not await setup_voice(page):
        print("[FATAL] Voice setup failed")
        await page.screenshot(path=shot("voai_fatal.png"))
        return None
    return page
