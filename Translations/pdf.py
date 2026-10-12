"""v1.15.0 – PDFs in the reader's language (reportlab).

* Font: Noto Naskh Arabic (SIL Open Font License 1.1, fonts/OFL.txt) – Arabic letters + Latin + digits, embedded in the PDF.
* Shaping: reportlab draws code points as they come, so Arabic text is shaped into its joined presentation forms
  (arabic-reshaper) and put in visual order (python-bidi). Without those packages the built-in shaper of SelfService is used.
* English PDFs do not use anything from here (they keep their fonts and layout).
"""
import logging
import os
import re

log = logging.getLogger(__name__)
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
REGULAR, BOLD = 'ZeoNaskh', 'ZeoNaskh-Bold'
ARABIC_RE = re.compile('[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]')
_REGISTERED = None


def fonts():
    """(regular, bold) font names registered with reportlab; Helvetica if the font files are missing."""
    global _REGISTERED
    if _REGISTERED is not None:
        return _REGISTERED
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.fonts import addMapping
        reg = os.path.join(FONT_DIR, 'NotoNaskhArabic-Regular.ttf')
        bold = os.path.join(FONT_DIR, 'NotoNaskhArabic-Bold.ttf')
        pdfmetrics.registerFont(TTFont(REGULAR, reg))
        pdfmetrics.registerFont(TTFont(BOLD, bold if os.path.exists(bold) else reg))
        addMapping(REGULAR, 0, 0, REGULAR)
        addMapping(REGULAR, 1, 0, BOLD)
        addMapping(REGULAR, 0, 1, REGULAR)
        addMapping(REGULAR, 1, 1, BOLD)
        _REGISTERED = (REGULAR, BOLD)
    except Exception:
        log.exception('Arabic PDF font could not be registered')
        _REGISTERED = ('Helvetica', 'Helvetica-Bold')
    return _REGISTERED


def has_arabic(s):
    return bool(s) and bool(ARABIC_RE.search(str(s)))


def shape(text, rtl=True):
    """Logical-order text → what reportlab must draw (joined Arabic letters, right-to-left order). Lines without Arabic
    are returned unchanged."""
    if text is None:
        return ''
    s = str(text).replace('\u2192', '\u203a').replace('\u2190', '\u203a')  # the font has no → / ← ; › shows as ‹ in right-to-left text
    if not has_arabic(s):
        return s
    out = []
    for line in s.split('\n'):
        if not has_arabic(line):
            out.append(line)
            continue
        try:
            import arabic_reshaper
            from bidi import get_display
            out.append(get_display(arabic_reshaper.reshape(line), base_dir='R' if rtl else 'L'))
        except Exception:
            try:
                from SelfService.arabic import visual_line
                out.append(visual_line(line))
            except Exception:
                out.append(line)
    return '\n'.join(out)


def para_text(text, rtl=True):
    """Text for a reportlab Paragraph (XML escaped, shaped)."""
    from xml.sax.saxutils import escape
    return escape(shape(text, rtl))
