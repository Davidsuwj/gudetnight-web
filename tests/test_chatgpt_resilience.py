import asyncio
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from podcast import chatgpt
from podcast import browser as podcast_browser
from podcast import config as podcast_config
import worker


class FakeLocator:
    def __init__(self, count_value=0):
        self.count_value = count_value

    async def count(self):
        return self.count_value

    async def click(self):
        return None

    async def fill(self, _text):
        return None


class FakePage:
    def __init__(self, title='請稍候...', selectors=None, screenshot_error=None):
        self._title = title
        self.selectors = selectors or {}
        self.screenshot_error = screenshot_error
        self.url = 'https://chatgpt.com/'

    async def title(self):
        return self._title

    def locator(self, selector):
        return FakeLocator(self.selectors.get(selector, 0))

    async def screenshot(self, **_kwargs):
        if self.screenshot_error:
            raise self.screenshot_error
        return b'ok'


def test_safe_screenshot_does_not_raise_when_playwright_times_out():
    page = FakePage(screenshot_error=TimeoutError('screenshot timeout'))
    result = asyncio.run(chatgpt._safe_screenshot(page, 'debug.png'))
    assert result is False


def test_find_prompt_uses_contenteditable_fallback():
    page = FakePage(
        title='ChatGPT',
        selectors={'[contenteditable="true"][data-lexical-editor="true"]': 1},
    )
    locator = asyncio.run(chatgpt._find_prompt_locator(page))
    assert locator is not None


def test_send_message_returns_false_on_turnstile_even_if_screenshot_fails():
    page = FakePage(screenshot_error=TimeoutError('screenshot timeout'))
    result = asyncio.run(chatgpt._send_message(page, 'hello', fresh=False, ready_timeout=0))
    assert result is False


class FakeSyncPage:
    def screenshot(self, **_kwargs):
        raise TimeoutError('screenshot timeout')


def test_worker_debug_screenshot_timeout_is_best_effort(tmp_path):
    result = worker.safe_page_screenshot(FakeSyncPage(), tmp_path / 'debug.png')
    assert result is False


def test_visible_windows_chrome_ipv6_is_the_primary_cdp_endpoint():
    assert podcast_config.CDP_URL == 'http://[::1]:9222'
    assert worker.CDP_URL == 'http://[::1]:9222'
    assert worker.CDP_CANDIDATES[0] == 'http://[::1]:9222'


def test_cdp_readiness_does_not_accept_unrelated_ipv4_headless(monkeypatch):
    seen = []

    def fake_urlopen(url, timeout):
        seen.append(url)
        raise OSError('visible IPv6 Chrome is unavailable')

    monkeypatch.setattr(worker.urllib.request, 'urlopen', fake_urlopen)
    assert worker._cdp_alive() is False
    assert seen[0] == 'http://[::1]:9222/json/version'
    assert all(url.endswith('/json/version') for url in seen)


def test_uploader_adapter_path_is_configurable_and_not_user_specific():
    assert worker.YOUTUBE_UPLOADER_PATH == worker.BASE / 'youtube_uploader.py'


def test_browser_connect_cleans_stale_targets_and_retries_once(monkeypatch):
    class FakeChromium:
        def __init__(self):
            self.calls = 0

        async def connect_over_cdp(self, url, timeout):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError('CDP init timeout')
            return 'connected-browser'

    class FakePlaywright:
        chromium = FakeChromium()

    cleaned = []

    async def fake_cleanup():
        cleaned.append(True)
        return 2

    monkeypatch.setattr(podcast_browser, '_cleanup_unresponsive_safe_targets', fake_cleanup)
    result = asyncio.run(podcast_browser.connect(FakePlaywright()))
    assert result == 'connected-browser'
    assert FakePlaywright.chromium.calls == 2
    assert cleaned == [True]


def test_visible_chatgpt_sender_skips_hidden_fallback_textarea():
    events = []

    class Locator:
        def __init__(self, visible=False, count=1, name=''):
            self.visible, self.count_value, self.name = visible, count, name
        async def count(self): return self.count_value
        def nth(self, _index): return self
        @property
        def first(self): return self
        async def is_visible(self): return self.visible
        async def is_enabled(self): return True
        async def click(self, **_kwargs): events.append(('click', self.name))
        async def fill(self, text, **_kwargs): events.append(('fill', self.name, text))
        async def press(self, key): events.append(('press', self.name, key))

    class Page:
        def locator(self, selector):
            if selector == '#prompt-textarea[contenteditable="true"]':
                return Locator(visible=True, name='visible-editor')
            if selector == 'button[data-testid="send-button"]:visible':
                return Locator(visible=True, name='send')
            return Locator(visible=False, name='hidden-fallback')

    assert asyncio.run(worker._send_visible_chatgpt_message(Page(), 'hello')) is True
    assert ('fill', 'visible-editor', 'hello') in events
    assert ('click', 'send') in events
