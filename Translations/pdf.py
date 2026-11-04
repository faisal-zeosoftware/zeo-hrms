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


_MIRROR = dict(zip('()[]{}<>«»‹›', ')(][}{><»«›‹'))


def _visual(line, base):
    """Visual order for reportlab, WITH the mirrored brackets of the right-to-left parts (v1.62.0, pending item 10:
    python-bidi 0.6 reorders but does not mirror, so "(21 days)" showed as ")21 days(" in Arabic PDFs).
    The levels come from python-bidi itself (debug output); the reorder below (rule L2) must give exactly what
    get_display gives – if it does not (another python-bidi version), get_display's own output is used."""
    import re
    from bidi import get_display
    plain = get_display(line, base_dir=base)
    if not any(c in _MIRROR for c in line):
        return plain
    try:
        info = get_display(line, base_dir=base, debug=True)
        m = re.search(r'levels:\s*\[(.*?)\]\s*,\s*paragraphs', str(info), re.S)
        byte_lv = [int(x) for x in re.findall(r'Level\(\s*(\d+)\s*,?\s*\)', m.group(1))]
        lv, i = [], 0
        for ch in line:
            lv.append(byte_lv[i]); i += len(ch.encode('utf-8'))
        if i != len(byte_lv):
            return plain
        base_lv = 1 if base == 'R' else 0
        # rule L1: trailing whitespace goes back to the paragraph level
        j = len(line) - 1
        while j >= 0 and line[j].isspace():
            lv[j] = base_lv; j -= 1
        chars = [(_MIRROR.get(c, c) if lv[k] % 2 else c, c) for k, c in enumerate(line)]
        order = list(range(len(line)))
        for level in range(max(lv), 0, -1):          # rule L2
            k = 0
            while k < len(order):
                if lv[order[k]] >= level:
                    e = k
                    while e < len(order) and lv[order[e]] >= level:
                        e += 1
                    order[k:e] = order[k:e][::-1]
                    k = e
                else:
                    k += 1
        if ''.join(chars[k][1] for k in order) != plain:
            return plain
        return ''.join(chars[k][0] for k in order)
    except Exception:
        return plain


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
            out.append(_visual(arabic_reshaper.reshape(line), 'R' if rtl else 'L'))
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
