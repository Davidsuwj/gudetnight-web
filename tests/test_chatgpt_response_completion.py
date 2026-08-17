import asyncio
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from podcast.chatgpt import _is_complete_response_text, _response_snapshot


def test_complete_json_wins_even_when_ui_stop_button_is_stale():
    text = '{"TOPIC":"題目","CONTEXT1":"一","CONTEXT2":"二","CONTEXT3":"三"}'
    assert _is_complete_response_text(text) is True


def test_partial_or_non_json_response_is_not_complete():
    assert _is_complete_response_text('{"TOPIC":"題目","CONTEXT1":"一"}') is False
    assert _is_complete_response_text('仍在產生內容') is False


def test_response_snapshot_uses_single_page_evaluation():
    expected = {"latestText": "done", "hasStop": True, "hasMarkdown": True}

    class Page:
        calls = 0

        async def evaluate(self, script):
            self.calls += 1
            assert "data-message-author-role" in script
            return expected

    page = Page()
    assert asyncio.run(_response_snapshot(page)) == expected
    assert page.calls == 1


def test_response_snapshot_times_out_hung_cdp_call(monkeypatch):
    class Page:
        async def evaluate(self, script):
            await asyncio.sleep(1)

    real_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        assert timeout == 10
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr("podcast.chatgpt.asyncio.wait_for", fast_wait_for)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_response_snapshot(Page()))
