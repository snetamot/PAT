"""
Locator strategies for finding UI elements on screen.

find_button()  -> template-match search, walking a spiral or raster pattern
find_text()    -> OCR-based search using Tesseract, fuzzy text match

Both return (x, y) screen coordinates of the match center, or None.
"""

import difflib
import os
import shutil

import cv2
import numpy as np
import mss

try:
    import pytesseract
except ImportError:
    pytesseract = None


def _configure_tesseract_path():
    """
    pytesseract only wraps the Tesseract binary — it doesn't install it.
    If it's not already discoverable on PATH, point pytesseract at the
    default Windows install location (or TESSERACT_CMD if the user set it).
    """
    if pytesseract is None:
        return

    if os.environ.get("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
        return

    if shutil.which("tesseract"):
        return  # already on PATH, nothing to do

    default_windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.name == "nt" and os.path.exists(default_windows_path):
        pytesseract.pytesseract.tesseract_cmd = default_windows_path


_configure_tesseract_path()


def _grab_region(x, y, w, h):
    """Capture a region of the screen as a BGR numpy array."""
    with mss.mss() as sct:
        monitor = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
        shot = sct.grab(monitor)
        img = np.array(shot)  # BGRA
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _grab_full_screen(bounds=None):
    """
    Capture the search area as (image, origin_x, origin_y).

    `bounds` optionally restricts capture to a sub-region of the screen:
    {"left": int, "top": int, "width": int, "height": int}. The returned
    origin is what gets added back to a match's local coordinates to make
    them absolute screen coordinates, so everything downstream still gets
    real click targets regardless of the region used.

    With no bounds, the primary monitor is captured.
    """
    with mss.mss() as sct:
        if bounds:
            monitor = {
                "left": int(bounds["left"]),
                "top": int(bounds["top"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
        else:
            monitor = sct.monitors[1]  # primary monitor
        shot = sct.grab(monitor)
        img = np.array(shot)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR), monitor["left"], monitor["top"]


def _spiral_offsets(max_radius, step=20, clockwise=True):
    """Yield (dx, dy) offsets from center in an expanding spiral."""
    dx, dy = (step, 0) if clockwise else (0, step)
    x, y = 0, 0
    segment_length = 1
    segments_completed = 0

    yield (0, 0)

    while max(abs(x), abs(y)) <= max_radius:
        for _ in range(segment_length):
            x += dx
            y += dy
            yield (x, y)
        dx, dy = (-dy, dx) if clockwise else (dy, -dx)
        segments_completed += 1
        if segments_completed % 2 == 0:
            segment_length += 1


def _raster_offsets(max_radius, step=20):
    """Yield (dx, dy) offsets in a simple row-by-row scan of the search box."""
    for dy in range(-max_radius, max_radius + 1, step):
        for dx in range(-max_radius, max_radius + 1, step):
            yield (dx, dy)


def find_button(template_path, search="spiral_cw", start_x=None, start_y=None,
                 radius=400, step=20, crop_size=60, threshold=0.8, bounds=None):
    """
    Search for a template image around (start_x, start_y) using the given
    search pattern, returning the matched center (x, y) in screen coordinates.

    search: "spiral_cw" | "spiral_ccw" | "raster"
    bounds: optional {"left","top","width","height"} region to search within
    """
    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(f"template image not found: {template_path}")
    th, tw = template.shape[:2]

    full_img, origin_x, origin_y = _grab_full_screen(bounds)
    # start_x/start_y arrive as absolute screen coordinates, but the captured
    # image is local to the bounds region — shift them into that local space.
    if start_x is not None:
        start_x -= origin_x
    if start_y is not None:
        start_y -= origin_y
    if start_x is None or start_y is None:
        start_x, start_y = full_img.shape[1] // 2, full_img.shape[0] // 2

    if search == "raster":
        offsets = _raster_offsets(radius, step)
    else:
        offsets = _spiral_offsets(radius, step, clockwise=(search != "spiral_ccw"))

    best_match = None
    best_score = 0.0

    for dx, dy in offsets:
        cx, cy = start_x + dx, start_y + dy
        x0 = max(cx - crop_size, 0)
        y0 = max(cy - crop_size, 0)
        x1 = min(cx + crop_size + tw, full_img.shape[1])
        y1 = min(cy + crop_size + th, full_img.shape[0])
        if x1 - x0 < tw or y1 - y0 < th:
            continue

        crop = full_img[y0:y1, x0:x1]
        result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_score:
            best_score = max_val
            match_x = x0 + max_loc[0] + tw // 2
            match_y = y0 + max_loc[1] + th // 2
            best_match = (match_x + origin_x, match_y + origin_y)

        if best_score >= threshold:
            return best_match, best_score

    return (best_match, best_score) if best_score >= threshold else (None, best_score)


def find_text(text, fuzzy_threshold=0.75, bounds=None):
    """
    OCR the search area with Tesseract and return the center coordinates of
    the best fuzzy match for `text`, or None if nothing crosses the threshold.

    bounds: optional {"left","top","width","height"} region to search within.
    Restricting this is usually a big accuracy and speed win for OCR, since
    Tesseract has far less irrelevant text to wade through.
    """
    if pytesseract is None:
        raise RuntimeError("pytesseract is not installed (pip install pytesseract), "
                            "and the Tesseract binary must also be installed separately")

    full_img, origin_x, origin_y = _grab_full_screen(bounds)
    gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
    # simple preprocessing: upscale + threshold tends to help OCR accuracy
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)

    best_ratio = 0.0
    best_center = None
    n = len(data["text"])
    for i in range(n):
        candidate = data["text"][i].strip()
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, candidate.lower(), text.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            cx = (data["left"][i] + data["width"][i] / 2) / 1.5
            cy = (data["top"][i] + data["height"][i] / 2) / 1.5
            best_center = (int(cx) + origin_x, int(cy) + origin_y)

    if best_ratio >= fuzzy_threshold:
        return best_center, best_ratio
    return None, best_ratio
