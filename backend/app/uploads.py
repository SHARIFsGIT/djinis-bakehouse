"""Photo uploads from the admin console and profile page.

Files land under backend/static/uploads/… which the existing /static mount
serves. Client filenames are never used on disk — each file gets a generated
name, so a hostile upload can't traverse or collide with anything.
"""

import secrets
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from .config import get_settings

# Content types we accept, mapped to the extension we save.
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_PRODUCT_PHOTO_BYTES = 6 * 1024 * 1024  # 6 MB before the resize on the client
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB

UPLOADS_DIR = "uploads"

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _uploads_root() -> Path:
    override = get_settings().uploads_dir
    if override:
        root = Path(override)
    else:
        static_dir = Path(get_settings().static_dir)
        if not static_dir.is_absolute():
            static_dir = _BACKEND_DIR / static_dir
        root = static_dir / UPLOADS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


async def save_photo(upload: UploadFile, folder: str, max_bytes: int) -> str:
    """Stream an upload to disk and return its /static URL.

    Reads in chunks so an oversized file is cut off mid-stream instead of
    being buffered whole first.
    """
    extension = IMAGE_TYPES.get(upload.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Please upload a JPEG, PNG or WebP image.",
        )

    target_dir = _uploads_root() / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}{extension}"
    path = target_dir / name

    size = 0
    with path.open("wb") as out:
        while chunk := await upload.read(64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="That image is too large — try one under "
                    f"{max_bytes // (1024 * 1024)} MB.",
                )
            out.write(chunk)
    await upload.close()
    return f"/static/{UPLOADS_DIR}/{folder}/{name}"


def delete_photo(url: str | None) -> None:
    """Remove an uploaded file that a record no longer points at."""
    if not url or not url.startswith(f"/static/{UPLOADS_DIR}/"):
        return
    relative = url[len(f"/static/{UPLOADS_DIR}/"):]
    candidate = (_uploads_root() / relative).resolve()
    if _uploads_root().resolve() in candidate.parents and candidate.is_file():
        candidate.unlink()
