"""Decode and normalize optional catalog images before private storage."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import ValidationError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}
MAX_IMAGE_SIDE = 8_000
MAX_IMAGE_PIXELS = 24_000_000
DISPLAY_MAX_SIDE = 1_200
THUMBNAIL_MAX_SIDE = 320

# Pillow emits a warning above this threshold. The processing function turns it
# into an error, so compressed image bombs never reach full decoding.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass(frozen=True, slots=True)
class CatalogImageVariants:
    display: bytes
    thumbnail: bytes
    width: int
    height: int
    sha256: str


def _validated_mime(image_format: str | None, declared_content_type: str | None) -> str:
    if image_format not in ALLOWED_FORMATS:
        raise ValidationError("Поддерживаются только изображения JPG и PNG")
    actual = ALLOWED_FORMATS[image_format]
    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared and declared != actual:
        raise ValidationError("Тип файла не соответствует содержимому изображения")
    return actual


def _prepare_pixels(image: Image.Image) -> Image.Image:
    oriented = ImageOps.exif_transpose(image)
    width, height = oriented.size
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_SIDE
        or height > MAX_IMAGE_SIDE
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValidationError("Изображение слишком большое — уменьшите его и попробуйте снова")
    has_alpha = oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info
    return oriented.convert("RGBA" if has_alpha else "RGB")


def _webp(image: Image.Image, *, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=quality, method=4, exact=True)
    return output.getvalue()


def process_catalog_image(data: bytes, declared_content_type: str | None) -> CatalogImageVariants:
    """Fully decode, orient, resize and re-encode an untrusted JPEG/PNG."""
    if not data:
        raise ValidationError("Выберите непустой файл изображения")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                _validated_mime(probe.format, declared_content_type)
                if getattr(probe, "is_animated", False) or getattr(probe, "n_frames", 1) != 1:
                    raise ValidationError("Анимированные изображения не поддерживаются")
                probe.verify()

            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
                prepared = _prepare_pixels(decoded)
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("Изображение слишком большое — уменьшите его") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ValidationError("Файл повреждён или не является изображением JPG/PNG") from exc

    display = prepared.copy()
    display.thumbnail((DISPLAY_MAX_SIDE, DISPLAY_MAX_SIDE), Image.Resampling.LANCZOS)
    thumbnail = display.copy()
    thumbnail.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE), Image.Resampling.LANCZOS)
    display_bytes = _webp(display, quality=84)
    thumbnail_bytes = _webp(thumbnail, quality=80)
    return CatalogImageVariants(
        display=display_bytes,
        thumbnail=thumbnail_bytes,
        width=display.width,
        height=display.height,
        sha256=hashlib.sha256(display_bytes).hexdigest(),
    )
