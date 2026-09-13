r"""
fo_ocr.py
Part of: The File Organizer

Optical character recognition for pages and pictures that carry no text
layer -- a scanned PDF, a photographed letter, a TIFF from a copier -- and,
before and after each recognition, a judgement of how much the result can
be trusted.

The engine is Windows' own (Windows.Media.Ocr through the `winocr` package):
free, offline, already on the machine, a few tenths of a second a page. It
returns words and their positions but no confidence, so quality is judged
here, from two sides:

  * the picture, before recognition -- the scan's resolution, its contrast
    (how far the ink stands from the paper), its sharpness, its speckle, and
    how much of it is ink at all;
  * the words, after recognition -- how many of them look like words, and
    the skew the engine measured while reading.

Each page gets a score from 0 to 100 and, when something is off, a list of
reasons in plain words ("96 dpi", "skewed 3.4 degrees", "41% of the words
are not words"). A file whose worst page falls below the line is flagged
for a person to look at: `OcrReview = yes`, with the reasons. The flag is
deliberately quick to trip -- the cost of a false alarm is one glance; the
cost of a missed one is a document the search cannot find.

Nothing here writes to a source file. Pages are rendered in memory.
"""

import asyncio
import re

try:
    import numpy as np
except ImportError:                                            # pragma: no cover
    np = None

try:
    from PIL import Image, ImageOps
except ImportError:                                            # pragma: no cover
    Image = None

ENGINE = "Windows OCR"
RENDER_DPI = 300
#: Below this a page is treated as having no text layer worth keeping.
MIN_TEXT_CHARS = 20
#: A page scoring under this is flagged for review.
REVIEW_THRESHOLD = 70


def available():
    """Whether OCR can run here at all."""
    try:
        import winocr                                           # noqa: F401
        return np is not None and Image is not None
    except ImportError:
        return False


def _recognize(img, lang="en"):
    import winocr
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(winocr.recognize_pil(img, lang))
        finally:
            loop.close()
    except Exception as exc:                                   # noqa: BLE001
        raise RuntimeError("Windows OCR failed: %s" % exc)


# -- what the picture says about itself --------------------------------------------

def picture_metrics(img, source_dpi=None):
    r"""Measures of a page image that bear on how well OCR will read it.

    Returns a dict: dpi (source scan resolution when known), contrast (0-255:
    the gap between the paper's brightness and the ink's), sharpness (variance
    of the Laplacian, edges per pixel), speckle (share of the ink that is
    isolated single pixels -- noise, not letters), ink (share of the page
    that is dark), and blank (True when there is next to nothing on it).
    """
    gray = ImageOps.grayscale(img)
    if gray.width > 1600:
        gray = gray.resize((1600, max(1, int(gray.height * 1600 / gray.width))), Image.BILINEAR)
    a = np.asarray(gray, dtype=np.float32)
    if a.size == 0:
        return {"dpi": source_dpi, "contrast": 0.0, "sharpness": 0.0, "speckle": 0.0, "ink": 0.0, "blank": True}
    paper = float(np.percentile(a, 90))
    # Otsu's threshold splits ink from paper without assuming either level.
    hist = np.bincount(a.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    w = np.cumsum(hist)
    m = np.cumsum(hist * np.arange(256))
    mean_total = m[-1] / total if total else 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean_total * w - m) ** 2 / (w * (total - w))
    between[~np.isfinite(between)] = 0.0
    threshold = int(np.argmax(between)) if total else 128
    dark = a < threshold
    ink = float(dark.mean())
    # Contrast: how far the ink stands from the paper -- the ink's own mean,
    # not a percentile of the whole page, so a page with three lines on it
    # is judged by its three lines and not by its white.
    ink_level = float(a[dark].mean()) if dark.any() else paper
    contrast = max(0.0, paper - ink_level)
    # Speckle: dark pixels with no dark 4-neighbour.
    if dark.any():
        up = np.zeros_like(dark); up[1:, :] = dark[:-1, :]
        down = np.zeros_like(dark); down[:-1, :] = dark[1:, :]
        left = np.zeros_like(dark); left[:, 1:] = dark[:, :-1]
        right = np.zeros_like(dark); right[:, :-1] = dark[:, 1:]
        isolated = dark & ~(up | down | left | right)
        speckle = float(isolated.sum() / dark.sum())
    else:
        speckle = 0.0
    # Sharpness: the strength of the strongest edges (the 99th percentile
    # of the Laplacian's magnitude). Crisp letters make strong edges however
    # few of them there are; blur softens every edge on the page.
    lap = (-4.0 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    sharpness = float(np.percentile(np.abs(lap), 99)) if lap.size else 0.0
    return {"dpi": source_dpi, "contrast": round(contrast, 1), "sharpness": round(sharpness, 1),
            "speckle": round(speckle, 3), "ink": round(ink, 4), "blank": ink < 0.002}


# -- what the words say about themselves ----------------------------------------

_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")
_VOWELS = set("aeiouyAEIOUY")


def word_metrics(text):
    r"""How much of the recognised text looks like language.

    A token counts as a word when it is letters with a vowel in it (or a
    short common shape like "Mr", "St", "II"), a number, or a date-like
    thing; the rest -- "l|", "~~m", "rn.t" -- is what a bad scan reads as.
    """
    tokens = [t.strip(".,;:!?()[]{}\"") for t in text.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return {"words": 0, "wordlike": 0.0}
    good = 0
    for t in tokens:
        if _WORD.match(t):
            letters = t.replace("'", "").replace("-", "")
            if len(letters) <= 2 or any(c in _VOWELS for c in letters):
                good += 1
        elif re.match(r"^[$€£]?[\d][\d,./:%-]*$", t):
            good += 1
        elif re.match(r"^[A-Za-z]+\d+[A-Za-z\d-]*$|^\d+[A-Za-z]+$", t):
            good += 1                                           # A4, 3rd, 21st
    return {"words": len(tokens), "wordlike": round(good / len(tokens), 3)}


# -- the judgement ------------------------------------------------------------------

def judge(picture, words, angle):
    r"""(score 0-100, reasons) for one page. Reasons are plain words.

    The words carry most of the weight: a page that reads as 97% real words
    was read, whatever its dpi. The picture's measures add up when they are
    extreme, and one comparison catches the silent failure -- a page with
    plenty of ink and hardly any words found, which is text the engine
    could not see at all.
    """
    score = 100.0
    reasons = []
    dpi = picture.get("dpi")
    if dpi is not None and dpi < 150:
        reasons.append("%d dpi" % dpi)
        score -= 20 if dpi < 100 else 10
    if picture["contrast"] < 80:
        reasons.append("low contrast (%d of 255)" % picture["contrast"])
        score -= 15 if picture["contrast"] < 50 else 8
    if picture["sharpness"] < 90 and not picture["blank"]:
        reasons.append("soft focus")
        score -= 15 if picture["sharpness"] < 60 else 8
    if picture["speckle"] > 0.12:
        reasons.append("speckled (%d%% of the ink is noise)" % round(picture["speckle"] * 100))
        score -= 10
    if angle is not None and abs(angle) >= 2.0:
        reasons.append("skewed %.1f degrees" % abs(angle))
        score -= 20 if abs(angle) >= 4 else 8
    if words["words"] == 0:
        if not picture["blank"]:
            reasons.append("nothing recognised")
            score -= 60
    else:
        bad = 1.0 - words["wordlike"]
        if bad > 0.08:
            reasons.append("%d%% of the words are not words" % round(bad * 100))
            score -= min(60.0, bad * 150.0)
        # Ink with no words in it: a page of text at ordinary size holds
        # roughly 3-6 words per 1% of ink; far under that, the engine
        # skipped what it could not read.
        density = words["words"] / (picture["ink"] * 100.0) if picture["ink"] > 0 else 0.0
        if picture["ink"] > 0.02 and density < 1.0:
            reasons.append("much of the ink was not read (%d words)" % words["words"])
            score -= 30
        elif words["words"] < 8 and picture["ink"] > 0.01:
            reasons.append("only %d words found" % words["words"])
            score -= 15
    return max(0, int(round(score))), reasons


def ocr_image(img, source_dpi=None, lang="en"):
    r"""(text, page report) for one picture."""
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    picture = picture_metrics(img, source_dpi)
    if picture["blank"]:
        return "", {"score": 100, "reasons": [], "words": 0, "angle": 0.0, **picture}
    result = _recognize(img, lang)
    lines = [line.text for line in result.lines]
    text = "\n".join(lines)
    angle = None
    try:
        angle = float(result.text_angle) if result.text_angle is not None else None
    except (TypeError, ValueError):
        angle = None
    words = word_metrics(text)
    score, reasons = judge(picture, words, angle)
    return text, {"score": score, "reasons": reasons, "angle": angle, **picture, **words}


# -- PDFs ---------------------------------------------------------------------------

def _source_dpi(page):
    r"""The resolution of the scan inside a PDF page: the effective DPI of
    the largest picture on it (pixels over the space it is drawn in). None
    when the page has no picture."""
    try:
        best_area, best_dpi = 0.0, None
        for obj in page.get_objects(max_depth=3):
            if obj.type != 3:                                   # FPDF_PAGEOBJ_IMAGE
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
                area = abs(right - left) * abs(top - bottom)
                meta = obj.get_metadata()
                dpi = float(meta.horizontal_dpi or 0)
            except Exception:                                   # noqa: BLE001
                continue
            if dpi > 0 and area > best_area:
                best_area, best_dpi = area, dpi
        return int(round(best_dpi)) if best_dpi else None
    except Exception:                                          # noqa: BLE001
        return None


def ocr_pdf_pages(doc, page_indexes, lang="en"):
    r"""OCR the given pages of an open pypdfium2 document.

    Returns (texts by page index, reports by page index)."""
    texts, reports = {}, {}
    for index in page_indexes:
        page = doc[index]
        try:
            dpi = _source_dpi(page)
            bitmap = page.render(scale=RENDER_DPI / 72.0)
            try:
                img = bitmap.to_pil()
            finally:
                bitmap.close()
            text, report = ocr_image(img, dpi, lang)
            texts[index] = text
            reports[index] = report
        finally:
            page.close()
    return texts, reports


def summarize(reports):
    r"""File-level fields from per-page reports."""
    if not reports:
        return {}
    scores = [r["score"] for r in reports.values()]
    worst = min(scores)
    flagged = [(i, r) for i, r in sorted(reports.items()) if r["score"] < REVIEW_THRESHOLD]
    reasons = "; ".join("page %d: %s" % (i + 1, ", ".join(r["reasons"])) for i, r in flagged[:6])
    if len(flagged) > 6:
        reasons += "; and %d more pages" % (len(flagged) - 6)
    return {"OcrEngine": ENGINE, "OcrPages": len(reports), "OcrQuality": worst,
            "OcrReview": "yes" if flagged else "no", "OcrReviewReasons": reasons}


# -- pictures on their own ------------------------------------------------------------

def looks_like_document(img):
    r"""A quick gate for picture files: little colour, mostly one light
    "paper" tone, and some darker marks standing clearly apart from it. A
    photograph of a beach fails on colour; a photograph of a wall fails on
    having no marks; a photographed letter -- even a faded one -- passes."""
    small = img.copy()
    small.thumbnail((500, 500))
    rgb = np.asarray(small.convert("RGB"), dtype=np.float32)
    if rgb.size == 0:
        return False
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    saturation = float(((mx - mn) / np.maximum(mx, 1.0)).mean())
    if saturation >= 0.25:
        return False
    gray = rgb.mean(axis=2)
    hist = np.bincount(gray.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    w = np.cumsum(hist)
    m = np.cumsum(hist * np.arange(256))
    mean_total = m[-1] / total
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean_total * w - m) ** 2 / (w * (total - w))
    between[~np.isfinite(between)] = 0.0
    threshold = int(np.argmax(between))
    dark = gray < threshold
    ink = float(dark.mean())
    if not (0.0005 < ink < 0.6):
        return False
    paper = float(gray[~dark].mean()) if (~dark).any() else 0.0
    marks = float(gray[dark].mean()) if dark.any() else paper
    return paper > 140 and (paper - marks) > 15


def image_dpi(img):
    try:
        dpi = img.info.get("dpi")
        if dpi and dpi[0]:
            return int(round(dpi[0]))
    except Exception:                                          # noqa: BLE001
        pass
    return None
