// Shrink a picked photo in the browser before it leaves the device — a
// 12-megapixel phone shot shouldn't travel (or load) at full size.
export async function shrinkImage(file, maxEdge, quality = 0.85) {
  if (!file.type.startsWith("image/")) {
    throw new Error("Please pick an image file");
  }
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(maxEdge / bitmap.width, maxEdge / bitmap.height, 1);
    if (scale === 1 && file.size < 700_000) return file; // already small enough

    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } finally {
    bitmap.close();
  }
}
