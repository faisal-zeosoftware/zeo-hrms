"""v1.13.0 – minimal Arabic shaping + right-to-left ordering for reportlab (no extra packages needed).

reportlab draws characters as they come: Arabic letters must be replaced by their joined presentation forms
(isolated / final / initial / medial) and a line must be laid out right to left. This covers the Arabic letters,
lam-alef ligatures and harakat, which is enough for HR letters. Latin words and numbers inside an Arabic line
keep their own left-to-right order.
"""
import re

# letter: (isolated, final, initial, medial) – None = the letter does not join to the next one
FORMS = {
    0x0621: (0xFE80, None, None, None), 0x0622: (0xFE81, 0xFE82, None, None), 0x0623: (0xFE83, 0xFE84, None, None),
    0x0624: (0xFE85, 0xFE86, None, None), 0x0625: (0xFE87, 0xFE88, None, None), 0x0626: (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),
    0x0627: (0xFE8D, 0xFE8E, None, None), 0x0628: (0xFE8F, 0xFE90, 0xFE91, 0xFE92), 0x0629: (0xFE93, 0xFE94, None, None),
    0x062A: (0xFE95, 0xFE96, 0xFE97, 0xFE98), 0x062B: (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C), 0x062C: (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),
    0x062D: (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4), 0x062E: (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8), 0x062F: (0xFEA9, 0xFEAA, None, None),
    0x0630: (0xFEAB, 0xFEAC, None, None), 0x0631: (0xFEAD, 0xFEAE, None, None), 0x0632: (0xFEAF, 0xFEB0, None, None),
    0x0633: (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4), 0x0634: (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8), 0x0635: (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),
    0x0636: (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0), 0x0637: (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4), 0x0638: (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    0x0639: (0xFEC9, 0xFECA, 0xFECB, 0xFECC), 0x063A: (0xFECD, 0xFECE, 0xFECF, 0xFED0), 0x0641: (0xFED1, 0xFED2, 0xFED3, 0xFED4),
    0x0642: (0xFED5, 0xFED6, 0xFED7, 0xFED8), 0x0643: (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC), 0x0644: (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    0x0645: (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4), 0x0646: (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8), 0x0647: (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),
    0x0648: (0xFEED, 0xFEEE, None, None), 0x0649: (0xFEEF, 0xFEF0, None, None), 0x064A: (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
    0x0640: (0x0640, 0x0640, 0x0640, 0x0640),
}
LAM_ALEF = {0x0622: (0xFEF5, 0xFEF6), 0x0623: (0xFEF7, 0xFEF8), 0x0625: (0xFEF9, 0xFEFA), 0x0627: (0xFEFB, 0xFEFC)}
ARABIC = re.compile('[؀-ۿﭐ-﷿ﹰ-﻿]')
SEGMENT = re.compile('[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]+|[^\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]+')


def _harakah(c):
    return 0x064B <= ord(c) <= 0x0652 or ord(c) == 0x0670


def _joins_next(c):
    f = FORMS.get(ord(c))
    return bool(f and f[2] is not None)


def shape(word):
    """Logical-order Arabic word -> presentation forms (still logical order)."""
    chars = list(word)
    out = []
    i = 0
    n = len(chars)

    def prev_letter(k):
        k -= 1
        while k >= 0 and _harakah(chars[k]):
            k -= 1
        return chars[k] if k >= 0 else None

    def next_letter(k):
        k += 1
        while k < n and _harakah(chars[k]):
            k += 1
        return (chars[k], k) if k < n else (None, k)

    while i < n:
        c = chars[i]
        code = ord(c)
        if code not in FORMS:
            out.append(c)
            i += 1
            continue
        p = prev_letter(i)
        joined_before = bool(p and _joins_next(p))
        nxt, j = next_letter(i)
        if code == 0x0644 and nxt is not None and ord(nxt) in LAM_ALEF:
            iso, fin = LAM_ALEF[ord(nxt)]
            out.append(chr(fin if joined_before else iso))
            out.extend(chars[i + 1:j])  # harakat between lam and alef
            i = j + 1
            continue
        joins_after = _joins_next(c) and nxt is not None and ord(nxt) in FORMS
        iso, fin, ini, med = FORMS[code]
        if joined_before and joins_after:
            form = med
        elif joined_before:
            form = fin or iso
        elif joins_after:
            form = ini or iso
        else:
            form = iso
        out.append(chr(form))
        i += 1
    return ''.join(out)


def visual_line(line):
    """Logical-order text of one line -> characters in drawing order (left to right) for a right-to-left line.
    Simplified bidi: Arabic letters are right-to-left, Latin letters and digits left-to-right, spaces and punctuation
    take the direction of their neighbours (left-to-right only between two left-to-right characters)."""
    text = shape(line)
    kinds = []
    for ch in text:
        if ARABIC.match(ch):
            kinds.append('R')
        elif ch.isalnum():
            kinds.append('L')
        else:
            kinds.append('N')
    n = len(text)
    for i in range(n):
        if kinds[i] != 'N':
            continue
        j = i - 1
        while j >= 0 and kinds[j] == 'N':
            j -= 1
        k = i + 1
        while k < n and kinds[k] == 'N':
            k += 1
        before = kinds[j] if j >= 0 else 'R'
        after = kinds[k] if k < n else 'R'
        kinds[i] = 'L' if (before == 'L' and after == 'L') else 'r'
    runs = []
    for ch, kd in zip(text, kinds):
        kd = 'R' if kd == 'r' else kd
        if runs and runs[-1][0] == kd:
            runs[-1][1].append(ch)
        else:
            runs.append([kd, [ch]])
    out = []
    for kd, chars in reversed(runs):
        out.append(''.join(reversed(chars)) if kd == 'R' else ''.join(chars))
    return ''.join(out)


def has_arabic(s):
    return bool(ARABIC.search(s or ''))
