from pathlib import Path

from worker import has_partial_shorts_images


def test_partial_image_resume_detects_existing_card(tmp_path: Path):
    assert has_partial_shorts_images(tmp_path) is False
    (tmp_path / "shorts_image_05.jpg").write_bytes(b"existing")
    assert has_partial_shorts_images(tmp_path) is True


def test_partial_image_resume_ignores_raw_and_debug_files(tmp_path: Path):
    (tmp_path / "raw_05.png").write_bytes(b"raw")
    (tmp_path / "debug_image_06.png").write_bytes(b"debug")
    assert has_partial_shorts_images(tmp_path) is False