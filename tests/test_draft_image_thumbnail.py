from pathlib import Path

from PIL import Image

from api.services.draft_images import get_image_thumbnail


def test_image_thumbnail_is_resized_and_reused(tmp_path: Path):
    source = tmp_path / "data" / "images" / "1" / "large.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (2400, 1600), "navy").save(source)

    first = get_image_thumbnail(source, tmp_path, 512)
    second = get_image_thumbnail(source, tmp_path, 512)

    assert first == second
    assert first.exists()
    assert first.stat().st_size < source.stat().st_size
    with Image.open(first) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert max(thumbnail.size) == 512


def test_transparent_thumbnail_is_flattened_for_jpeg(tmp_path: Path):
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (800, 400), (255, 0, 0, 128)).save(source)

    thumbnail_path = get_image_thumbnail(source, tmp_path, 256)

    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.mode == "RGB"
        assert max(thumbnail.size) == 256
