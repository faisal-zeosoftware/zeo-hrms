"""v1.79.5 item pictures: the item master picture (the first picture attached to the item – Masters › Items › Files, or the item
in CRM / Inventory), thumbnails for the review screen, and finding an item from a photo: the company's AI compares the photo with
the item pictures (vision), else a built-in picture comparison (shape + colours) gives a similarity percentage."""
import base64
import io
import json
import logging
import re

from django.apps import apps as django_apps

log = logging.getLogger(__name__)
IMG = re.compile(r'\.(jpe?g|png|webp|gif|bmp)$', re.I)


def _file_models():
    from zeo.appkit.models import FileBase
    return [m for m in django_apps.get_models() if issubclass(m, FileBase)]


def item_images(ids):
    """{product id: (file model, file row)} – the first picture attached to each item, in any app"""
    ids = [int(i) for i in ids if i]
    out = {}
    if not ids:
        return out
    for M in _file_models():
        try:
            rows = M.objects.filter(model__in=('item', 'product'), object_id__in=[i for i in ids if i not in out]).order_by('uploaded_at')
            for f in rows:
                if f.object_id not in out and IMG.search(f.name or f.file.name or ''):
                    out[f.object_id] = f
        except Exception:   # noqa: BLE001 – a file table of an app not migrated
            continue
        if len(out) == len(ids):
            break
    return out


def thumb(f, size=320):
    from PIL import Image, ImageOps
    with f.file.open('rb') as fh:
        im = Image.open(io.BytesIO(fh.read()))
        im = ImageOps.exif_transpose(im).convert('RGB')
    im.thumbnail((size, size))
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=82)
    return b.getvalue()


# ------------------------------------------------------------------ built-in picture comparison
def _bg(im):
    """the background colour: the median of the picture's border"""
    w, h = im.size
    px = im.load()
    border = [px[x, y] for x in range(0, w, max(1, w // 40)) for y in (0, h - 1)] + [px[x, y] for y in range(0, h, max(1, h // 40)) for x in (0, w - 1)]
    return tuple(sorted(c[k] for c in border)[len(border) // 2] for k in range(3))


def _fg(im, bg, tol=38):
    """a mask of the object: every pixel clearly different from the background"""
    from PIL import Image
    diff = Image.new('L', im.size)
    src, dst = im.load(), diff.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b = src[x, y]
            dst[x, y] = 255 if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > tol else 0
    return diff


def _prep(data):
    """the object alone: the background (a table, a wall, a sheet of paper) is trimmed – twice, for a product on a sheet on a table"""
    from PIL import Image, ImageFilter, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
    im.thumbnail((256, 256))
    mask = None
    for _ in range(2):
        soft = im.filter(ImageFilter.GaussianBlur(2))
        bg = _bg(soft)
        m = _fg(soft, bg, 48)
        box = m.getbbox()
        if not box or (box[2] - box[0]) * (box[3] - box[1]) < im.size[0] * im.size[1] * 0.03:
            break
        im, mask = im.crop(box), m.crop(box)
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.92 * (soft.size[0] * soft.size[1]):
            break   # nothing more to trim
    if mask is None:
        mask = _fg(im, _bg(im), 48)
    return im, mask


def _dhash(im, n=12):
    g = im.convert('L').resize((n + 1, n))
    px = list(g.getdata())
    return [px[r * (n + 1) + c] > px[r * (n + 1) + c + 1] for r in range(n) for c in range(n)]


def _hist(im, mask):
    """colours of the object only (the background left out), 8 levels per channel"""
    small, m = im.resize((64, 64)), mask.resize((64, 64))
    h = [0] * 512
    for (r, g, b), v in zip(list(small.getdata()), list(m.getdata())):
        if v > 127:
            h[(r // 32) * 64 + (g // 32) * 8 + b // 32] += 1
    t = float(sum(h)) or 1
    return [x / t for x in h]


def _shape(mask, n=12):
    m = mask.resize((n, n))
    return [v > 127 for v in m.getdata()]


def signature(data):
    im, mask = _prep(data)
    return {'d': _dhash(im), 'h': _hist(im, mask), 's': _shape(mask), 'ar': im.size[0] / max(1, im.size[1])}


def similarity(a, b):
    """0..1 – colours of the object, its outline and its proportions, its light / dark pattern"""
    d = sum(1 for x, y in zip(a['d'], b['d']) if x == y) / len(a['d'])
    sh = sum(1 for x, y in zip(a['s'], b['s']) if x == y) / len(a['s'])
    h = sum(min(x, y) for x, y in zip(a['h'], b['h']))
    ar = min(a['ar'], b['ar']) / max(a['ar'], b['ar'])
    return max(0.0, min(1.0, 0.45 * h + 0.25 * sh + 0.15 * ar + 0.15 * max(0.0, (d - 0.5) * 2)))


# ------------------------------------------------------------------ an item from a photo
def photo_match(request, data, kind, hint_ids=(), limit=8):
    """[{id, label, code, unit, score, by, has_image}] best first"""
    from ZeoMasters.models import Product

    from . import matcher as M
    qs = Product.objects.filter(is_active=True)
    qs = qs.filter(is_sold=True) if kind in M.SALES else qs.filter(is_purchased=True)
    ids = list(qs.values_list('pk', flat=True)[:3000])
    pics = item_images(ids)
    if not pics:
        return [], 'No item has a picture yet – attach pictures to the items (Masters › Items › Files) to match by photo.'
    q = signature(data)
    scored = []
    for pid, f in list(pics.items())[:600]:
        try:
            with f.file.open('rb') as fh:
                s = similarity(q, signature(fh.read()))
        except Exception:   # noqa: BLE001
            continue
        if pid in hint_ids:
            s = min(1.0, s + 0.08)   # the item the text matched gets a small lift
        scored.append((s, pid))
    scored.sort(reverse=True)
    top = scored[:max(limit, 6)]
    prods = {p.pk: p for p in Product.objects.filter(pk__in=[pid for _, pid in top]).select_related('uom')}
    out = [{**M._iinfo(prods[pid]), 'score': round(s, 2), 'by': 'picture', 'has_image': True} for s, pid in top if pid in prods]
    note = 'Built-in picture comparison (colours, outline, proportions) – a guide only; with the company AI on for documents the pictures are compared by the AI.'
    from . import reader as R
    if R.ai_ready(request):
        try:
            out, note = _ai_rank(request, data, out[:6], pics, out[6:])
        except Exception as e:   # noqa: BLE001
            log.warning('photo match AI failed: %s', e)
            note = 'The AI could not compare the pictures – the built-in comparison is shown.'
    return out[:limit], note


def _ai_rank(request, data, cands, pics, rest):
    from AISettings.service import chat, settings_row
    if settings_row().provider != 'anthropic' or not cands:
        return cands + rest, ''
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
    im.thumbnail((640, 640))
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=85)
    content = [{'type': 'text', 'text': 'PHOTO of the item we are looking for:'},
               {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(b.getvalue()).decode()}}]
    for c in cands:
        content += [{'type': 'text', 'text': f"Candidate id={c['id']}: {c['label']}"},
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(thumb(pics[c['id']], 384)).decode()}}]
    content.append({'type': 'text', 'text': 'For each candidate, how likely is it the SAME product as the photo (type, model, size, colour)? '
                                            'Answer ONLY JSON: {"<id>": percent 0-100, ...}'})
    r = chat(request, 'documents', 'You compare product photos with the pictures of an item list. Be strict: a similar product of another size or model is not the same.',
             [{'role': 'user', 'content': content}], max_tokens=300)
    t = '\n'.join(x.get('text', '') for x in (r or {}).get('content') or [] if x.get('type') == 'text')
    m = re.search(r'\{.*\}', t, re.S)
    pct = json.loads(m.group(0)) if m else {}
    for c in cands:
        v = pct.get(str(c['id']))
        if isinstance(v, (int, float)):
            c['score'], c['by'] = round(max(0, min(100, v)) / 100, 2), 'AI picture check'
    cands.sort(key=lambda c: -c['score'])
    return cands + rest, 'Compared by the company AI.'
