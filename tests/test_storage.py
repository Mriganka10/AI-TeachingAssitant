from app.core.storage import safe_filename


def test_internal_storage_filename_is_bounded_and_keeps_extension() -> None:
    original = "A-very-long-generated-teaching-topic-" * 5 + ".mp4"

    filename = safe_filename(original)

    assert len(filename) <= 48
    assert filename.endswith(".mp4")
    assert filename == safe_filename(original)
