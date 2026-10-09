// Photo compression on the phone (docs/02-architecture.md §8): longest
// side 1280 px, JPEG, stepping the quality down until it fits ~200 KB.
// The server re-encodes it again and strips EXIF, so this is about data
// cost, not trust.
(function () {
  "use strict";

  const MAX_SIDE = 1280;
  const TARGET_BYTES = 200 * 1024;

  function loadImage(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file).catch(() => loadViaElement(file));
    }
    return loadViaElement(file);
  }

  function loadViaElement(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("not an image")); };
      img.src = url;
    });
  }

  function toBlob(canvas, quality) {
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  }

  async function compress(file) {
    const img = await loadImage(file);
    let scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
    let blob = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      for (const q of [0.75, 0.6, 0.45]) {
        blob = await toBlob(canvas, q);
        if (blob && blob.size <= TARGET_BYTES) return blob;
      }
      scale *= 0.75;
    }
    return blob;
  }

  window.WFPhoto = { compress: compress };
})();
