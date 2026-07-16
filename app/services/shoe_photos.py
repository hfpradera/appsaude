from __future__ import annotations

import uuid
from pathlib import Path

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_PHOTO_BYTES = 5_000_000


def save_shoe_photo(
    upload_dir: Path,
    shoe_id: int,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> Path:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Formato de imagem nao suportado. Use JPEG, PNG, WEBP ou GIF.")
    if len(content) > MAX_PHOTO_BYTES:
        raise ValueError("Imagem maior que o limite de 5MB.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }[content_type]
    photos_dir = upload_dir / "shoe_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    destination = photos_dir / f"shoe_{shoe_id}_{uuid.uuid4().hex[:8]}{ext}"
    destination.write_bytes(content)
    return destination


def delete_shoe_photo_file(path_str: str | None) -> None:
    if not path_str:
        return
    try:
        Path(path_str).unlink()
    except FileNotFoundError:
        pass
