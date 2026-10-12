"""
Utilidades para pruebas sintéticas: convierten una hoja generada en algo
parecido a una foto (rasterizada, contestada, deformada) sin necesidad de
imprimir ni de usar una cámara.
"""

import cv2
import numpy as np
import pypdfium2 as pdfium

from core.layout import SheetLayout

RASTER_DPI = 150


def rasterize(pdf_bytes: bytes, dpi: int = RASTER_DPI) -> np.ndarray:
    """PDF -> imagen BGR, como si fuera un escaneo perfecto."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    rgb = np.array(page.render(scale=dpi / 72).to_pil().convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def fill_bubbles(image: np.ndarray, layout: SheetLayout, bubbles, dpi: int = RASTER_DPI,
                 gray: int = 40, size: float = 0.85) -> None:
    """Rellena burbujas (en mm de página) sobre la imagen rasterizada,
    simulando a un alumno con lápiz. `gray` alto = lápiz tenue (170 ≈ marca
    muy suave o borrada); `size` < 0.85 = rellena a medias."""
    px_per_mm = dpi / 25.4
    for b in bubbles:
        center = (round(b.cx * px_per_mm), round(b.cy * px_per_mm))
        cv2.circle(image, center, round(b.r * size * px_per_mm), (gray, gray, gray), -1)


def cross_bubbles(image: np.ndarray, bubbles, dpi: int = RASTER_DPI) -> None:
    """Tacha burbujas con una X en vez de rellenarlas."""
    px_per_mm = dpi / 25.4
    for b in bubbles:
        cx, cy = round(b.cx * px_per_mm), round(b.cy * px_per_mm)
        d = round(b.r * 0.7 * px_per_mm)
        cv2.line(image, (cx - d, cy - d), (cx + d, cy + d), (40, 40, 40), 2)
        cv2.line(image, (cx - d, cy + d), (cx + d, cy - d), (40, 40, 40), 2)


def simulate_photo(image: np.ndarray, seed: int = 0) -> np.ndarray:
    """Pone la hoja sobre un fondo, con perspectiva, rotación, sombra, blur
    y ruido, como una foto de celular."""
    rng = np.random.default_rng(seed)
    h, w = image.shape[:2]
    out_w, out_h = int(w * 1.3), int(h * 1.3)

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jitter = rng.uniform(-0.06, 0.06, size=(4, 2)) * [w, h]
    dst = src + [w * 0.15, h * 0.15] + jitter
    angle = rng.uniform(-12, 12)
    rot = cv2.getRotationMatrix2D((out_w / 2, out_h / 2), angle, 1.0)
    dst = cv2.transform(dst[None], rot)[0].astype(np.float32)

    transform = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(
        image, transform, (out_w, out_h), borderValue=(90, 110, 130)
    )

    # Iluminación despareja: una esquina más oscura que la opuesta
    gradient = np.linspace(0.7, 1.05, out_w)[None, :, None]
    photo = np.clip(photo * gradient, 0, 255)
    photo = cv2.GaussianBlur(photo.astype(np.uint8), (3, 3), 0)
    noise = rng.normal(0, 6, photo.shape)
    return np.clip(photo + noise, 0, 255).astype(np.uint8)


def darkness(aligned_gray: np.ndarray, layout: SheetLayout, bubble) -> float:
    """Oscuridad promedio (0 = blanco, 1 = negro) dentro de una burbuja en
    la imagen alineada. Medida simple para validar la alineación; la
    detección real va en core/detection.py (fase 2)."""
    cx, cy, r = layout.bubble_px(bubble)
    mask = np.zeros(aligned_gray.shape, np.uint8)
    cv2.circle(mask, (round(cx), round(cy)), round(r * 0.6), 255, -1)
    return 1 - cv2.mean(aligned_gray, mask=mask)[0] / 255
