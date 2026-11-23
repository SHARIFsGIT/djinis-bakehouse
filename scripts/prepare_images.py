"""Prepare web-ready images from the raw photos in ../images.

Run from the project root after editing photos:

    venv/Scripts/python.exe scripts/prepare_images.py

Products are resized to a 900px long edge and re-compressed (the originals
are ~3000x4000 phone photos). The slug keys here must match the image URLs
seeded in backend/app/seed.py.
"""

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "images"
STATIC = ROOT / "backend" / "static"
PUBLIC = ROOT / "frontend" / "public"

PRODUCTS = {
    # breads
    "french-baguette": "Baguette.jpg",
    "fresh-bagels": "Bagelen.jpg",
    "beef-floss-bread": "Floss bread.jpg",
    "triple-cheese-bread": "Triple cheese bread.jpg",
    "blueberry-cheese-bread": "Blueberry cheese bread.jpg",
    "wool-roll-bread": "Woll Roll Bread.jpg",
    "rosemary-focaccia": "Foccacia.jpg",
    "garden-focaccia": "Foccacia Art.jpg",
    # pastries
    "butter-croissant": "Croissant.jpg",
    "croffle": "Croffle.jpg",
    "apple-strudel": "Apple Strudle.jpg",
    "beef-curry-puff": "Beef Curry Puff.jpg",
    "chocolate-cream-puff": "Choco Cream Puff.jpg",
    "fruit-cushion-pastry": "Fruit Cushion Pastry.jpg",
    "rhum-horn": "Rhum Horn.jpg",
    "vol-au-vent": "Val Au Vent.jpg",
    "pretzels-and-bagels": "Pretzel and Bagels.jpg",
    "cheese-straws": "Cheese Straw.jpg",
    # desserts
    "glazed-doughnuts": "Doughnuts.jpg",
    "maritozzo": "Maritozzo.jpg",
    "strawberry-maritozzo": "Strawberry Maritozzo.jpg",
    "strawberry-mille-feuille": "Strawberry Mille Fuille.jpg",
    "strawberry-puff": "Strawberry Puff.jpg",
    "double-chocolate-cream-bun": "Double Choco Cream.jpg",
    "christmas-stollen": "Christmas stollen.jpg",
}


def save_jpeg(image: Image.Image, path: Path, long_edge: int, quality: int) -> None:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((long_edge, long_edge), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    print(f"  {path.name:<28} {image.size[0]}x{image.size[1]}  {path.stat().st_size // 1024} KB")


def save_square(image: Image.Image, path: Path, size: int, quality: int) -> None:
    image = ImageOps.exif_transpose(image).convert("RGB")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    print(f"  {path.name:<28} {size}x{size}      {path.stat().st_size // 1024} KB")


def save_logo(image: Image.Image, path: Path, size: int) -> None:
    # Trim transparent padding so the badge sits tight wherever it is placed.
    alpha = image.getchannel("A")
    image = image.crop(alpha.getbbox())
    image = image.resize((size, size), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    print(f"  {path.name:<28} {size}x{size}      {path.stat().st_size // 1024} KB")


# The page's own paper (theme "bakery", --color-base-100) — what the shop's
# badge sits on when a phone puts it on a home screen.
ICON_BACKGROUND = (252, 246, 238)


def save_app_icon(image: Image.Image, path: Path, size: int, badge: float) -> None:
    """The badge on a cream square, for a phone's home screen. `badge` is
    the share of the square the mark fills: a "maskable" icon may be cut to
    a circle by the phone, so its mark stays inside the middle 60%."""
    alpha = image.getchannel("A")
    mark = image.crop(alpha.getbbox())
    side = round(size * badge)
    mark = mark.resize((side, side), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), ICON_BACKGROUND)
    offset = (size - side) // 2
    canvas.paste(mark, (offset, offset), mark)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, "PNG", optimize=True)
    print(f"  {path.name:<28} {size}x{size}      {path.stat().st_size // 1024} KB")


def save_app_icons() -> None:
    """Home-screen icons (frontend/public/manifest.json names these files)."""
    print("App icons -> frontend/public/icons/")
    logo = Image.open(SOURCE / "banners" / "Djini_s_Bakehouse_1_-removebg-preview.png")
    icons = PUBLIC / "icons"
    save_app_icon(logo, icons / "icon-192.png", 192, 0.8)
    save_app_icon(logo, icons / "icon-512.png", 512, 0.8)
    save_app_icon(logo, icons / "icon-maskable-512.png", 512, 0.6)
    save_app_icon(logo, icons / "apple-touch-icon.png", 180, 0.8)


def main() -> None:
    print("Product photos -> backend/static/products/")
    for slug, filename in PRODUCTS.items():
        save_jpeg(Image.open(SOURCE / "products" / filename), STATIC / "products" / f"{slug}.jpg", 900, 82)

    print("Site assets -> backend/static/site/")
    save_logo(Image.open(SOURCE / "banners" / "Djini_s_Bakehouse_1_-removebg-preview.png"), STATIC / "site" / "logo.png", 256)
    save_jpeg(Image.open(SOURCE / "banners" / "Banner photo 2.png"), STATIC / "site" / "baker.jpg", 1100, 88)
    save_square(Image.open(SOURCE / "client.png"), STATIC / "site" / "avatar-tanvir.jpg", 320, 85)
    save_square(Image.open(SOURCE / "client2.png"), STATIC / "site" / "avatar-rahman.jpg", 320, 85)

    print("Favicon -> frontend/public/")
    save_logo(Image.open(SOURCE / "banners" / "Djini_s_Bakehouse_1_-removebg-preview.png"), PUBLIC / "favicon.png", 64)

    save_app_icons()


if __name__ == "__main__":
    main()
