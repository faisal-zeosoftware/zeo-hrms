"""Asset Management rules. Custody, transfers, disposal and verification go through the HR asset services (AssetPlus),
so HR → Assets, payroll recovery, exit clearance and HSE keep seeing one register."""
import calendar
import contextvars
import logging
import re
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps as djapps
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from zeo.appkit.engine import AppError, emp_name, tables

from .models import AssetCard, AuditLine, Category, Location, Movement, Reservation

log = logging.getLogger(__name__)
C2 = Decimal('0.01')
ZERO = Decimal('0')


def money(v):
    return Decimal(str(v or 0)).quantize(C2, ROUND_HALF_UP)


def S():
    """The HR asset services."""
    from AssetPlus import services
    return services


def HRAsset():
    return djapps.get_model('OrganisationManager', 'Asset')


def Profile():
    return djapps.get_model('AssetPlus', 'AssetProfile')


def today():
    return timezone.localdate()


@contextmanager
def hr_errors():
    """AssetPlus raises AssetError – show it as a normal message on the screen."""
    try:
        yield
    except AppError:
        raise
    except Exception as e:
        if type(e).__name__ == 'AssetError':
            raise AppError(getattr(e, 'message', str(e)), getattr(e, 'field', None))
        raise


# ------------------------------------------------------------------ keeping the card and the HR asset in step
_busy = contextvars.ContextVar('zeoassets_busy', default=False)


@contextmanager
def busy():
    tok = _busy.set(True)
    try:
        yield
    finally:
        _busy.reset(tok)


def is_busy():
    return _busy.get()


def ready():
    """The app's tables exist in this company and the app is activated."""
    try:
        if connection.schema_name == 'public' or AssetCard._meta.db_table.lower() not in tables():
            return False
        from .app import app
        return app.activated()
    except Exception:
        return False


def ensure_hr_type(cat):
    """Each category is one HR asset type (HR → Assets uses the type)."""
    AssetType = djapps.get_model('OrganisationManager', 'AssetType')
    if cat.hr_type_id is None:
        t = AssetType.objects.filter(name__iexact=cat.name).first() or AssetType.objects.create(name=cat.name[:255], description=f'Asset Management category {cat.code}')
        Category.objects.filter(pk=cat.pk).update(hr_type=t)
        cat.hr_type = t
    cfg = S().type_config(cat.hr_type_id)
    cfg.code_prefix = (cat.code_prefix or cfg.code_prefix or cat.code)[:12].upper()
    cfg.depreciation_method = cat.method
    cfg.useful_life_months = cat.life_months or 36
    cfg.salvage_percent = cat.residual_percent or 0
    cfg.save()
    return cat.hr_type


def category_for_type(asset_type):
    c = Category.objects.filter(hr_type=asset_type).first()
    if c:
        return c
    c = Category.objects.filter(hr_type__isnull=True, name__iexact=asset_type.name).first()
    if c:
        Category.objects.filter(pk=c.pk).update(hr_type=asset_type)
        return c
    base = re.sub(r'[^A-Z0-9]', '', asset_type.name.upper())[:10] or 'CAT'
    code, n = base, 1
    while Category.objects.filter(code=code).exists():
        n += 1
        code = f'{base[:8]}{n}'
    cfg = S().type_config(asset_type.pk)
    return Category.objects.create(code=code, name=asset_type.name[:100], hr_type=asset_type, code_prefix=cfg.code_prefix,
                                   method=cfg.depreciation_method, life_months=cfg.useful_life_months or 36,
                                   residual_percent=cfg.salvage_percent or 0)


def hr_state(asset):
    """What HR knows about the asset now: status, holder, due date, profile."""
    s = S()
    p = Profile().objects.filter(asset_id=asset.pk).first()
    h = s.holder(asset.pk) or {}
    st = s.effective_status(asset, p)
    emp_id = h.get('employee_id') or h.get('custodian_employee_id')
    return p, {'status': st, 'condition': asset.condition, 'holder': (h.get('name') or '')[:150], 'holder_employee_id': emp_id if h.get('type') == 'employee' else None,
               'due_back': h.get('expected_return_date')}


def refresh(card=None, asset_id=None):
    """Copy the HR side onto the card (queryset update: no signals, no loops)."""
    if card is None:
        card = AssetCard.objects.filter(hr_asset_id=asset_id).first()
        if card is None:
            asset = HRAsset().objects.filter(pk=asset_id).first()
            return import_asset(asset) if asset is not None else None
    asset = HRAsset().objects.filter(pk=card.hr_asset_id).first()
    if asset is None:
        return card
    p, h = hr_state(asset)
    upd = {'name': asset.name, 'model_no': asset.model or '', 'purchase_date': asset.purchase_date, 'condition': asset.condition,
           'holder': h['holder'], 'holder_employee_id': h['holder_employee_id'], 'due_back': h['due_back']}
    sn = asset.serial_number or ''
    code = p.asset_code if p is not None else ''
    upd['serial_number'] = '' if (sn.startswith('NOSN-') or (code and sn == code)) else sn
    st = h['status']
    upd['status'] = 'cwip' if card.status == 'cwip' and st == 'available' and not card.capitalised_on else st
    if p is not None:
        upd.update(code=p.asset_code, purchase_cost=p.purchase_cost, supplier=p.vendor or card.supplier, invoice_no=p.invoice_no or card.invoice_no,
                   purchase_order=p.purchase_order or card.purchase_order, warranty_end=p.warranty_end or card.warranty_end,
                   barcode=card.barcode or p.barcode or p.asset_code)
        if p.branch_id:
            upd['branch_id'] = p.branch_id
        if p.last_verified_on and (not card.last_audited_on or p.last_verified_on > card.last_audited_on):
            upd['last_audited_on'] = p.last_verified_on
        cost = money(p.purchase_cost)
        upd['book_value'] = max(cost - money(card.accumulated), ZERO) if card.method != 'none' else cost
    AssetCard.objects.filter(pk=card.pk).update(**upd)
    card.refresh_from_db()
    return card


def import_asset(asset):
    """Make the card of an HR asset (assets created in HR → Assets appear here by themselves)."""
    if asset is None:
        return None
    card = AssetCard.objects.filter(hr_asset=asset).first()
    if card:
        return refresh(card)
    s = S()
    p = s.ensure_profile(asset)
    cat = category_for_type(asset.asset_type)
    loc = Location.objects.filter(name__iexact=p.location).first() if p.location else None
    with busy():
        card = AssetCard.objects.create(hr_asset=asset, code=p.asset_code, name=asset.name, category=cat, purchase_date=asset.purchase_date,
                                        branch_id=p.branch_id, location=loc, method=p.depreciation_method, life_months=p.useful_life_months or 36,
                                        residual_value=p.salvage_value or 0, declining_rate=p.declining_rate, depreciation_start=p.depreciation_start,
                                        capitalised_on=asset.purchase_date)
    return refresh(card)


def import_all():
    n = 0
    have = set(AssetCard.objects.values_list('hr_asset_id', flat=True))
    for a in HRAsset().objects.exclude(pk__in=have).select_related('asset_type').order_by('pk'):
        try:
            with transaction.atomic():
                import_asset(a)
                n += 1
        except Exception:
            log.exception('import of HR asset %s failed', a.pk)
    for c in AssetCard.objects.all():
        refresh(c)
    return n


def write_through(card, user=None):
    """Save the card's shared fields onto the HR asset and its profile (create them for a new card)."""
    s = S()
    cat = card.category
    ensure_hr_type(cat)
    A = HRAsset()
    serial = (card.serial_number or '').strip()
    if cat.requires_serial and not serial:
        raise AppError('This category needs the serial number.', 'serial_number')
    clash = A.objects.filter(serial_number__iexact=serial).exclude(pk=card.hr_asset_id).exists() if serial else False
    if clash:
        raise AppError('Another asset already has this serial number.', 'serial_number')
    with busy(), s.quiet():
        if card.hr_asset_id:
            a = A.objects.get(pk=card.hr_asset_id)
            if a.asset_type_id != cat.hr_type_id:
                a.asset_type_id = cat.hr_type_id
            a.name, a.model, a.purchase_date, a.condition = card.name, card.model_no or '', card.purchase_date, card.condition
            if serial:
                a.serial_number = serial
            a.save()
            p = s.ensure_profile(a, user)
            created = False
        else:
            tmp = f'NOSN-{uuid.uuid4().hex[:12]}'
            a = A(asset_type_id=cat.hr_type_id, name=card.name, serial_number=serial or tmp, model=card.model_no or '',
                  purchase_date=card.purchase_date, condition=card.condition)
            a.save()
            p = s.ensure_profile(a, user)
            created = True
            if not serial:   # no serial number: HR needs a unique one – the asset code is used (shown empty here)
                A.objects.filter(pk=a.pk).update(serial_number=p.asset_code)
                a.serial_number = p.asset_code
                Ev = djapps.get_model('AssetPlus', 'AssetEvent')
                for ev in Ev.objects.filter(asset_id=a.pk, summary__contains=tmp):
                    Ev.objects.filter(pk=ev.pk).update(summary=ev.summary.replace(f'serial {tmp}', 'no serial number'))
        p.purchase_cost, p.vendor, p.invoice_no, p.purchase_order = money(card.purchase_cost), card.supplier, card.invoice_no, card.purchase_order
        p.warranty_end = card.warranty_end
        p.branch_id = card.branch_id or p.branch_id
        p.location = (card.location.path() if card.location_id else p.location)[:150]
        p.depreciation_method, p.useful_life_months = card.method, card.life_months or 36
        p.salvage_value, p.declining_rate = money(card.residual_value), card.declining_rate
        p.depreciation_start = card.depreciation_start or card.capitalised_on
        p.barcode = card.barcode or p.asset_code
        p.save()
    card.hr_asset = a
    card.code = p.asset_code
    if not card.barcode:
        card.barcode = p.asset_code
    if created:
        s.log_event(a.pk, 'created', f'Registered in Asset Management as {p.asset_code}', user)
    return card


def apply_category_defaults(card):
    c = card.category
    card.method, card.life_months, card.declining_rate = c.method, c.life_months, c.declining_rate
    card.residual_value = money(Decimal(card.purchase_cost or 0) * Decimal(c.residual_percent or 0) / 100)
    if not c.capitalise:
        card.method = 'none'


# ------------------------------------------------------------------ movements and custody (through HR)
def log_move(card, kind, user, *, frm='', to='', from_location=None, to_location=None, employee=None, due_back=None, condition='', note='', ref=''):
    return Movement.objects.create(card=card, branch_id=card.branch_id, kind=kind, from_text=frm[:200], to_text=to[:200], from_location=from_location,
                                   to_location=to_location, employee=employee, due_back=due_back, condition=condition, note=note[:255], hr_ref=ref[:40],
                                   by=user if (user and user.is_authenticated) else None)


def where(card):
    return card.holder or (card.location.path() if card.location_id else 'Store')


def check_out(card, user, *, to='employee', employee=None, department_id=None, location=None, due_back=None, note='', reservation=None):
    if card.status == 'cwip':
        raise AppError('This asset is still under construction. Capitalise it first.')
    if due_back and due_back < today():
        raise AppError('The due-back date is in the past.', 'due_back')
    asset = card.hr_asset
    before = where(card)
    with hr_errors():
        if to == 'employee':
            if employee is None:
                raise AppError('Choose the employee.', 'employee')
            res = S().allocate(asset, user, employee_id=employee.pk, expected_return_date=due_back, handover_note=note)
        elif to == 'department':
            if not department_id:
                raise AppError('Choose the department.', 'department')
            res = S().allocate(asset, user, target_type='department', department_id=department_id, branch_id=card.branch_id,
                               custodian_employee_id=getattr(employee, 'pk', None), expected_return_date=due_back, handover_note=note)
        else:
            if location is None:
                raise AppError('Choose the location.', 'location')
            res = S().allocate(asset, user, target_type='location', branch_id=location.branch_id or card.branch_id, location=location.path(),
                               custodian_employee_id=getattr(employee, 'pk', None) or getattr(location, 'custodian_id', None),
                               expected_return_date=due_back, handover_note=note)
            AssetCard.objects.filter(pk=card.pk).update(location=location)
    card = refresh(card)
    log_move(card, 'check_out', user, frm=before, to=card.holder, to_location=location, employee=employee if to == 'employee' else None,
             due_back=due_back, note=note, ref=f'{type(res).__name__} {res.pk}')
    if reservation is not None:
        Reservation.objects.filter(pk=reservation.pk).update(status='checked_out')
    return card


def check_in(card, user, *, condition='healthy', location=None, note='', missing_items=''):
    before = card.holder
    with hr_errors():
        S().complete_return(card.hr_asset, user, condition=condition, accessories_returned=not missing_items, missing_items=missing_items, notes=note)
    if location is not None:
        AssetCard.objects.filter(pk=card.pk).update(location=location)
        Profile().objects.filter(asset_id=card.hr_asset_id).update(location=location.path()[:150])
    card = refresh(card)
    log_move(card, 'check_in', user, frm=before, to=card.location.path() if card.location_id else 'Store', to_location=location, condition=condition, note=note)
    Reservation.objects.filter(card=card, status='checked_out').update(status='returned')
    return card


def move(card, user, location, note=''):
    if location is None:
        raise AppError('Choose the new location.', 'location')
    if location.pk == card.location_id:
        raise AppError('The asset is already there.', 'location')
    old = card.location
    AssetCard.objects.filter(pk=card.pk).update(location=location, plan_x=None, plan_y=None, branch_id=location.branch_id or card.branch_id)
    Profile().objects.filter(asset_id=card.hr_asset_id).update(location=location.path()[:150], **({'branch_id': location.branch_id} if location.branch_id else {}))
    S().log_event(card.hr_asset_id, 'moved', f'Moved {old.path() if old else "(no location)"} → {location.path()}' + (f' – {note}' if note else ''), user)
    card.refresh_from_db()
    log_move(card, 'move', user, frm=old.path() if old else '', to=location.path(), from_location=old, to_location=location, note=note)
    return card


# ------------------------------------------------------------------ depreciation
def start_of(card, s):
    start = card.depreciation_start or card.capitalised_on or card.purchase_date
    if start is None:
        return None
    if s.convention == 'full_month':
        return start.replace(day=1)
    if s.convention == 'next_month' and start.day > 1:
        y, m = divmod(start.month, 12)
        return date(start.year + y, m + 1, 1)
    return start


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def months_between(a, b, daily):
    """Months from a to b (both included)."""
    if b < a:
        return ZERO
    if daily:
        return (Decimal((b - a).days + 1) / Decimal('30.4375')).quantize(Decimal('0.01'))
    return Decimal((b.year - a.year) * 12 + b.month - a.month + 1)


def depreciable(card, s):
    return (card.method != 'none' and card.status not in ('cwip', 'disposed') and card.category.capitalise
            and money(card.purchase_cost) >= money(s.capitalise_threshold) and card.life_months)


def depreciation_for(card, frm, to, s):
    """(months, opening, amount, closing) for the period, after what was already posted."""
    cost = money(card.purchase_cost)
    residual = min(money(card.residual_value), cost)
    acc = money(card.accumulated)
    opening = cost - acc
    start = start_of(card, s)
    if not depreciable(card, s) or start is None or opening <= residual:
        return ZERO, opening, ZERO, opening
    # Never depreciated in Asset Management and no opening balance entered: the first run catches up from the start
    # (enter the opening "accumulated depreciation" and "depreciated up to" of migrated assets to avoid the catch-up).
    a = max(start, card.depreciated_to + timedelta(days=1)) if card.depreciated_to else (start if not acc else max(frm, start))
    if a > to:
        return ZERO, opening, ZERO, opening
    daily = s.convention == 'daily'
    months = months_between(a, to, daily)
    used_before = months_between(start, a - timedelta(days=1), daily) if a > start else ZERO
    left_life = max(Decimal(card.life_months) - used_before, ZERO)
    months = min(months, left_life) if left_life > 0 else ZERO
    if months <= 0:
        amount = opening - residual if used_before >= card.life_months else ZERO
        return months, opening, money(amount), opening - money(amount)
    if card.method == 'straight_line':
        amount = (cost - residual) / Decimal(card.life_months) * months
    else:
        rate = (Decimal(card.declining_rate) / 100) if card.declining_rate else (Decimal(24) / Decimal(card.life_months))
        nbv, whole = opening, int(months)
        for _ in range(whole):
            nbv -= nbv * rate / 12
        nbv -= nbv * rate / 12 * (months - whole)
        amount = opening - nbv
        if used_before + months >= card.life_months:
            amount = opening - residual
    amount = money(min(max(amount, ZERO), opening - residual))
    return months, opening, amount, opening - amount


def verify_from_audit(card, user, result, location=None, note=''):
    m = {'found': 'found', 'damaged': 'damaged', 'missing': 'not_found', 'misplaced': 'wrong_location', 'unexpected': 'wrong_location'}
    with hr_errors():
        S().verify_asset(card.hr_asset, user, result=m[result], location=location.path() if location is not None else '', note=note)


def find(code):
    """A card from a scanned QR / barcode / RFID / typed code. HR labels (ASSET:<code>|SN:<serial>|ID:<id>) work too."""
    raw = (code or '').strip()
    if not raw:
        return None
    hr_id = None
    if raw.upper().startswith('ASSET:') or '|' in raw:
        parts = dict(p.split(':', 1) for p in raw.split('|') if ':' in p)
        parts = {k.strip().upper(): v.strip() for k, v in parts.items()}
        raw = parts.get('ASSET') or raw
        hr_id = parts.get('ID')
    for f in ('code', 'barcode', 'rfid_tag', 'serial_number'):
        c = AssetCard.objects.filter(**{f'{f}__iexact': raw}).first()
        if c:
            return c
    if hr_id and str(hr_id).isdigit():
        c = AssetCard.objects.filter(hr_asset_id=int(hr_id)).first()
        if c:
            return c
    p = Profile().objects.filter(asset_code__iexact=raw).first() or Profile().objects.filter(barcode__iexact=raw).first()
    if p:
        return refresh(asset_id=p.asset_id)
    a = HRAsset().objects.filter(serial_number__iexact=raw).first()
    return refresh(asset_id=a.pk) if a else None


def holder_name(card):
    return card.holder or (emp_name(card.holder_employee) if card.holder_employee_id else '')


def audit_counts(audit):
    rows = dict(AuditLine.objects.filter(audit=audit).values_list('result').annotate(n=Count('id')))
    audit.expected = AuditLine.objects.filter(audit=audit).exclude(result='unexpected').count()
    audit.found = rows.get('found', 0) + rows.get('damaged', 0) + rows.get('misplaced', 0)
    audit.missing = rows.get('missing', 0) + (rows.get('pending', 0) if audit.status in ('review', 'closed') else 0)
    audit.misplaced, audit.unexpected, audit.damaged = rows.get('misplaced', 0), rows.get('unexpected', 0), rows.get('damaged', 0)
    audit.save(update_fields=['expected', 'found', 'missing', 'misplaced', 'unexpected', 'damaged'])
    return audit
