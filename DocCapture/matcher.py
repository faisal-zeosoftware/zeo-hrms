"""Matching what a document says to ZEO's records: the customer / supplier, each item and its price.
Order of trust: what the user taught before (alias) → exact codes (supplier's code, SKU, barcode, part no.) → e-mail / TRN / phone → names."""
import re
from decimal import Decimal
from difflib import SequenceMatcher

from django.db.models import Q

from .models import CaptureAlias

SALES = ('sales_quote', 'sales_order')


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def words(s):
    return {w for w in re.findall(r'[a-z0-9]+', (s or '').lower()) if len(w) > 1 or w.isdigit()}


def nums(s):
    """the sizes in a text: '2 ton' → {'2'}, '3/8"' → {'3/8'}, '2.5' → {'2.5'}, '20x20' → {'20'}"""
    return set(re.findall(r'\d+(?:[./]\d+)?', (s or '').lower()))


def sim(a, b):
    """0..1 – character similarity and shared words; different sizes (2 ton / 2.5 ton, 1/2" / 3/8") count against"""
    base = _sim(a, b)
    na, nb = nums(a), nums(b)
    if na and nb and not (na & nb):
        base *= 0.7
    elif na and nb and not na <= nb:
        base *= 0.9
    return base


def _sim(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = SequenceMatcher(None, na, nb).ratio()
    wa, wb = words(a), words(b)
    j = len(wa & wb) / max(1, len(wa | wb)) if wa and wb else 0
    cover = len(wa & wb) / max(1, len(wa)) if wa else 0   # the document's words found in the item name
    return max(r, (j + cover) / 2 * 1.02)


# ------------------------------------------------------------------ partner
def _pinfo(p):
    return {'id': p.pk, 'label': p.name, 'number': p.number, 'email': p.email or '', 'trn': getattr(p, 'tax_id', '') or ''}


def match_partner(info, kind):
    from ZeoMasters.models import Partner
    info = info or {}
    qs = Partner.objects.exclude(status='archived') if hasattr(Partner, 'status') else Partner.objects.all()
    name, mail = (info.get('name') or '').strip(), (info.get('email') or '').strip().lower()
    trn, phone = re.sub(r'\D', '', info.get('trn') or ''), re.sub(r'\D', '', info.get('phone') or '')
    if name:
        a = CaptureAlias.objects.filter(kind='partner', text=norm(name)[:200]).first()
        if a and qs.filter(pk=a.target_id).exists():
            return {'status': 'matched', 'by': 'learnt before', 'score': 1, **_pinfo(qs.get(pk=a.target_id)), 'options': []}
    if trn and len(trn) >= 10:
        p = qs.filter(tax_id__icontains=trn).first()
        if p:
            return {'status': 'matched', 'by': 'TRN', 'score': 1, **_pinfo(p), 'options': []}
    if mail:
        p = qs.filter(email__iexact=mail).first()
        if p is None:
            from_contact = qs.filter(children__email__iexact=mail).first() if hasattr(Partner, 'children') else None
            p = from_contact
        if p:
            p = p.parent if getattr(p, 'parent_id', None) else p
            return {'status': 'matched', 'by': 'e-mail', 'score': 1, **_pinfo(p), 'options': []}
    if phone and len(phone) >= 8:
        tail = phone[-9:]
        p = qs.filter(Q(phone__endswith=tail) | Q(mobile__endswith=tail)).first()
        if p:
            p = p.parent if getattr(p, 'parent_id', None) else p
            return {'status': 'matched', 'by': 'phone', 'score': 0.95, **_pinfo(p), 'options': []}
    cands = []
    if name:
        ws = [w for w in words(name) if w not in ('llc', 'l', 'fze', 'fzco', 'co', 'est', 'trading', 'general', 'the', 'and', 'company', 'ltd', 'llp')]
        pre = qs.filter(Q(*[Q(name__icontains=w) for w in ws[:4]], _connector=Q.OR)) if ws else qs.none()
        for p in pre[:400]:
            cands.append((max(sim(name, p.name), sim(name, getattr(p, 'legal_name', '') or '')), p))
    if mail and '@' in mail and not cands:
        dom = mail.split('@', 1)[1]
        if dom not in ('gmail.com', 'hotmail.com', 'yahoo.com', 'outlook.com', 'icloud.com', 'live.com'):
            for p in qs.filter(email__iendswith='@' + dom)[:20]:
                cands.append((0.85, p.parent if getattr(p, 'parent_id', None) else p))
    cands.sort(key=lambda x: -x[0])
    opts = [{**_pinfo(p), 'score': round(s, 2)} for s, p in cands[:5]]
    if cands and cands[0][0] >= 0.9:
        return {'status': 'matched', 'by': 'name', 'score': round(cands[0][0], 2), **_pinfo(cands[0][1]), 'options': opts[1:]}
    if cands and cands[0][0] >= 0.6:
        return {'status': 'check', 'by': 'similar name', 'score': round(cands[0][0], 2), **_pinfo(cands[0][1]), 'options': opts[1:]}
    return {'status': 'new', 'by': '', 'score': 0, 'id': None, 'label': '', 'options': opts}


# ------------------------------------------------------------------ items
def _iinfo(p):
    return {'id': p.pk, 'label': f'{p.code or p.number} {p.name}'.strip(), 'code': p.code or p.number, 'name': p.name,
            'unit': getattr(p.uom, 'name', '') if p.uom_id else ''}


def _scored(qs, code, desc):
    """items by name similarity, best first – spaces, dashes and case do not matter ('HP 26 A' = 'hp-26a')"""
    text = f'{code} {desc}'.strip()
    ws = [w for w in words(text) if len(w) > 2 or w.isdigit()][:8]
    nt = norm(text)
    conds = [Q(name__icontains=w) | Q(description__icontains=w) | Q(code__icontains=w) for w in ws if not w.isdigit()]
    if nt[:5]:
        conds.append(Q(code__icontains=nt[:5]) | Q(manufacturer_no__icontains=nt[:5]))
    pre = qs.filter(Q(*conds, _connector=Q.OR)) if conds else qs.none()
    return sorted(((max(sim(text, p.name), sim(desc, p.name), sim(text, f'{p.code} {p.name}'), sim(text, f'{p.manufacturer_no} {p.name}')), p)
                   for p in pre.select_related('uom')[:800]), key=lambda x: -x[0])


def _name_cands(qs, code, desc):
    return [{**_iinfo(p), 'score': round(s, 2)} for s, p in _scored(qs, code, desc)[:6] if s >= 0.3]


def match_item(line, partner_id, kind):
    from ZeoMasters.models import Product
    qs = Product.objects.filter(is_active=True)
    qs = qs.filter(is_sold=True) if kind in SALES else qs.filter(is_purchased=True)
    code, desc = (line.get('code') or '').strip(), (line.get('description') or '').strip()
    key = norm(code or desc)[:200]
    if key:
        a = (CaptureAlias.objects.filter(kind='product', partner_id=partner_id, text=key).first() or
             CaptureAlias.objects.filter(kind='product', partner_id=None, text=key).first())
        if a and qs.filter(pk=a.target_id).exists():
            return {'status': 'matched', 'by': 'learnt before', 'score': 1, **_iinfo(qs.get(pk=a.target_id)),
                    'options': [o for o in _name_cands(qs, code, desc) if o['id'] != a.target_id][:5]}
    tokens = [code] if code else []
    tokens += [t for t in re.findall(r'[A-Za-z0-9][A-Za-z0-9\-/.]{2,}', desc) if re.search(r'\d', t) and re.search(r'[A-Za-z]', t)][:3]
    for t in tokens:
        nt = norm(t)
        if not nt:
            continue
        if partner_id and kind not in SALES:
            try:
                from ZeoPurchase.models import SupplierPrice
                sp = SupplierPrice.objects.filter(supplier_id=partner_id, supplier_code__iexact=t, product__in=qs).select_related('product').first()
                if sp:
                    return {'status': 'matched', 'by': "supplier's code", 'score': 1, **_iinfo(sp.product),
                            'options': [o for o in _name_cands(qs, '', desc) if o['id'] != sp.product_id][:5]}
            except Exception:   # noqa: BLE001
                pass
        p = qs.filter(Q(code__iexact=t) | Q(number__iexact=t) | Q(barcode__iexact=t) | Q(manufacturer_no__iexact=t) | Q(supplier_code__iexact=t)).first()
        if p is None:   # codes written with other separators: HP-26A = HP26A = hp 26a
            for p2 in qs.filter(Q(code__icontains=nt[:4]) | Q(manufacturer_no__icontains=nt[:4]) | Q(supplier_code__icontains=nt[:4]))[:300]:
                if nt in (norm(p2.code), norm(p2.manufacturer_no), norm(p2.supplier_code), norm(p2.barcode)) or \
                        nt in {norm(w) for w in f'{p2.manufacturer_no} {p2.supplier_code}'.split() if len(norm(w)) >= 4}:
                    p = p2
                    break
        if p:
            return {'status': 'matched', 'by': 'code' if t == code else 'code in the text', 'score': 1, **_iinfo(p),
                    'options': [o for o in _name_cands(qs, code, desc) if o['id'] != p.pk][:5]}
    cands = _scored(qs, code, desc)
    opts = [{**_iinfo(p), 'score': round(s, 2)} for s, p in cands[:6]]
    if cands and cands[0][0] >= 0.88 and (len(cands) == 1 or cands[0][0] - cands[1][0] > 0.04):
        return {'status': 'matched', 'by': 'name', 'score': round(cands[0][0], 2), **_iinfo(cands[0][1]), 'options': opts[1:]}
    if cands and cands[0][0] >= 0.55:
        return {'status': 'check', 'by': 'similar name', 'score': round(cands[0][0], 2), **_iinfo(cands[0][1]), 'options': opts[1:]}
    return {'status': 'none', 'by': '', 'score': 0, 'id': None, 'label': '', 'options': opts}


# ------------------------------------------------------------------ prices
def system_price(kind, product_id, partner_id, qty):
    """(price, what) – sales: the list / price-book price; purchase: the supplier's price list, else the last purchase price"""
    from ZeoMasters.models import Partner, Product
    p = Product.objects.filter(pk=product_id).first()
    if p is None:
        return None, ''
    q = Decimal(str(qty or 1))
    if kind in SALES:
        try:
            from ZeoSales import services as SS
            from ZeoSales.models import Order
            from django.utils import timezone
            o = Order(partner=Partner.objects.filter(pk=partner_id).first() if partner_id else None, date=timezone.localdate())
            if o.partner is not None and getattr(o.partner, 'price_book_id', None):
                o.price_book_id = o.partner.price_book_id
            return Decimal(SS.list_price(o, p, q) or 0), 'price list'
        except Exception:   # noqa: BLE001
            return Decimal(p.sale_price or 0), 'list price'
    if partner_id:
        try:
            from ZeoPurchase import services as PS
            price, row = PS.best_price(Partner.objects.get(pk=partner_id), p, q)
            if price and row is not None:
                return Decimal(price), "supplier's price list"
        except Exception:   # noqa: BLE001
            pass
    if p.purchase_price:
        return Decimal(p.purchase_price), 'last purchase price'
    return Decimal(p.cost_price or 0), 'cost'


def check_line(kind, ln, partner_id):
    """notes for the reviewer: price differences, units"""
    notes = []
    it = ln.get('item') or {}
    if not it.get('id'):
        return notes
    sp, what = system_price(kind, it['id'], partner_id, ln.get('qty'))
    ln['system_price'] = float(sp) if sp is not None else None
    ln['system_price_from'] = what
    dp = ln.get('price')
    if dp in (None, '') and sp:
        ln['price_from'] = what
    elif dp not in (None, '') and sp:
        d = float(dp) - float(sp)
        if abs(d) > max(0.01, float(sp) * 0.005):
            pct = d / float(sp) * 100 if sp else 0
            notes.append(f'Price {float(dp):,.2f} is {abs(pct):.1f} % {"above" if d > 0 else "below"} the {what} ({float(sp):,.2f}).')
    u, iu = norm(ln.get('unit')), norm(it.get('unit'))
    if u and iu and u != iu and not (u.rstrip('s') == iu.rstrip('s')):
        notes.append(f'The document says "{ln.get("unit")}", the item is kept in "{it.get("unit")}" – check the quantity.')
    return notes


def propose(kind, ex):
    """the proposal the review screen shows"""
    ex = ex or {}
    partner = match_partner(ex.get('partner') or {}, kind)
    pid = partner.get('id') if partner.get('status') == 'matched' else partner.get('id')
    lines = []
    for i, l in enumerate(ex.get('lines') or []):
        qty = l.get('qty')
        try:
            qty = float(qty) if qty not in (None, '') else 1.0
        except (TypeError, ValueError):
            qty = 1.0
        price = l.get('price')
        try:
            price = float(price) if price not in (None, '') else None
        except (TypeError, ValueError):
            price = None
        ln = {'n': i + 1, 'code': str(l.get('code') or '')[:60], 'description': str(l.get('description') or '')[:255], 'qty': qty,
              'unit': str(l.get('unit') or '')[:20], 'price': price, 'discount_pct': float(l.get('discount_pct') or 0), 'amount': l.get('amount'),
              'min_qty': float(l.get('min_qty')) if str(l.get('min_qty') or '').replace('.', '', 1).isdigit() else 0.0}
        ln['item'] = match_item(ln, pid, kind)
        ln['notes'] = check_line(kind, ln, pid)
        lines.append(ln)
    mark_images(lines)
    total = sum((l['qty'] * (l['price'] if l['price'] is not None else (l.get('system_price') or 0)) * (1 - l['discount_pct'] / 100)) for l in lines)
    st = {'matched': sum(1 for l in lines if l['item']['status'] == 'matched'), 'check': sum(1 for l in lines if l['item']['status'] == 'check'),
          'none': sum(1 for l in lines if l['item']['status'] == 'none')}
    doc_total = (ex.get('totals') or {}).get('net')
    warn = []
    try:
        if doc_total and abs(float(doc_total) - total) > max(1, float(doc_total) * 0.01) and all(l['price'] is not None for l in lines):
            warn.append(f'The lines add up to {total:,.2f}; the document says {float(doc_total):,.2f} before VAT – a line may be missing.')
    except (TypeError, ValueError):
        pass
    return {'partner': partner, 'partner_text': ex.get('partner') or {}, 'reference': str(ex.get('reference') or '')[:60], 'date': ex.get('date') or '',
            'valid_until': ex.get('valid_until') or '', 'valid_from': ex.get('valid_from') or '', 'delivery_date': ex.get('delivery_date') or '', 'currency': (ex.get('currency') or '')[:3].upper(),
            'notes': str(ex.get('notes') or '')[:1000], 'lead_days': ex.get('lead_days'), 'lines': lines, 'counts': st, 'estimated_net': round(total, 2), 'warnings': warn}


def learn(user, kind, partner_text, partner_id, lines):
    """remember the user's choices: the sender's name → partner; the document's item text → item (for that partner)"""
    n = 0
    nm = norm((partner_text or {}).get('name'))[:200]
    if nm and partner_id:
        CaptureAlias.objects.update_or_create(kind='partner', partner_id=None, text=nm, defaults={'target_id': partner_id, 'created_by': user})
    for ln in lines:
        key = norm(ln.get('code') or ln.get('description'))[:200]
        pid = ln.get('product')
        if key and pid and ln.get('changed'):
            a, made = CaptureAlias.objects.get_or_create(kind='product', partner_id=partner_id, text=key, defaults={'target_id': pid, 'created_by': user})
            if not made:
                a.target_id, a.uses = pid, a.uses + 1
                a.save(update_fields=['target_id', 'uses', 'updated_at'])
            n += 1
    return n


def ai_pick(request, kind, prop):
    """the company's AI chooses among ZEO's own candidates for the lines the matcher was unsure of (only item codes and names are sent)"""
    todo = [l for l in prop.get('lines') or [] if l['item']['status'] in ('check', 'none') and (l['item'].get('id') or l['item'].get('options'))]
    if not todo:
        return 0
    import json
    import re as _re
    from AISettings.service import chat
    ask = []
    for l in todo:
        c = ([{'id': l['item']['id'], 'item': l['item']['label']}] if l['item'].get('id') else []) + \
            [{'id': o['id'], 'item': o['label']} for o in l['item'].get('options') or [] if o['id'] != l['item'].get('id')]
        ask.append({'line': l['n'], 'document_says': f"{l['code']} {l['description']} ({l['unit']})".strip(), 'candidates': c[:6]})
    system = ('You match lines of a business document to items of an ERP item list. For each line answer the id of the candidate that is the same '
              'product (same type AND same size / capacity / model), or null when none is clearly the same, with how sure you are (0-100). '
              'Answer ONLY JSON: {"<line>": [id, percent] or null}.')
    try:
        r = chat(request, 'documents', system, [{'role': 'user', 'content': json.dumps(ask)}], max_tokens=600)
        t = '\n'.join(b.get('text', '') for b in (r or {}).get('content') or [] if b.get('type') == 'text')
        m = _re.search(r'\{.*\}', t, _re.S)
        picks = json.loads(m.group(0)) if m else {}
    except Exception:   # noqa: BLE001
        return 0
    n = 0
    for l in todo:
        pick = picks.get(str(l['n']))
        pid, conf = (pick[0], pick[1]) if isinstance(pick, list) and len(pick) == 2 else (pick, None)
        if not pid:
            continue
        cand = [l['item']] + list(l['item'].get('options') or [])
        hit = next((c for c in cand if c.get('id') == pid), None)
        if hit is None:
            continue
        others = [o for o in cand if o.get('id') and o['id'] != pid]
        sc = round(max(0, min(100, float(conf))) / 100, 2) if isinstance(conf, (int, float)) else hit.get('score', 0)
        if sc < 0.6:
            continue   # the AI is not sure either – the user chooses
        l['item'] = {**{k: hit[k] for k in ('id', 'label', 'code', 'name', 'unit') if k in hit}, 'status': 'matched' if sc >= 0.8 else 'check',
                     'by': 'AI choice', 'score': sc,
                     'options': [{k: o.get(k) for k in ('id', 'label', 'code', 'name', 'unit', 'score')} for o in others][:4]}
        l['notes'] = check_line(kind, l, (prop.get('partner') or {}).get('id'))
        n += 1
    prop['counts'] = {'matched': sum(1 for l in prop['lines'] if l['item']['status'] == 'matched'),
                      'check': sum(1 for l in prop['lines'] if l['item']['status'] == 'check'),
                      'none': sum(1 for l in prop['lines'] if l['item']['status'] == 'none')}
    return n



def mark_images(lines):
    """has_image on every candidate whose item has a picture (shown as a thumbnail on the review screen)"""
    from .images import item_images
    ids = set()
    for l in lines:
        it = l.get('item') or {}
        ids |= {it.get('id')} | {o.get('id') for o in it.get('options') or []}
    pics = item_images([i for i in ids if i])
    for l in lines:
        it = l.get('item') or {}
        if it.get('id'):
            it['has_image'] = it['id'] in pics
        for o in it.get('options') or []:
            o['has_image'] = o.get('id') in pics
