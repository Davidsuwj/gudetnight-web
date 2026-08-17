from pathlib import Path

import worker


def test_image_generation_checks_rate_limit_modal_before_editor_click():
    """Regression: a blocking ChatGPT modal must route to fallback before click."""
    source = Path(worker.__file__).read_text(encoding="utf-8")
    modal_check = "page.locator('[data-testid=\"modal-conversation-history-rate-limit\"]:visible').count()"
    visible_editor = "page.locator('#prompt-textarea[contenteditable=\"true\"]:visible')"

    modal_pos = source.index(modal_check)
    editor_pos = source.index(visible_editor, modal_pos)
    between = source[modal_pos:editor_pos]

    assert modal_pos < editor_pos
    assert "fallback card used" in between
    assert "break" in between
