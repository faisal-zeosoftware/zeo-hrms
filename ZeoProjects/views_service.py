"""v1.67.13 (service) – customer sites, customer equipment, equipment categories, AMC types and industry packs.

Rights: sites and equipment follow the customer rights (customer_view / customer_edit); categories, AMC types and
industry packs follow Projects set-up (setup). Branch users see the sites (and their equipment) of their branches.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import packs as PK
from . import rows as RW
from . import services as S
from .models import (COVER_SCOPE, EQUIPMENT_STATUS, PARTS_COVER, SITE_TYPES, AMCType, Contact, Customer, CustomerEquipment,
                     CustomerSite, EquipmentCategory, EquipmentFile, SLAPolicy, SupportTeam)
from .views import Base, In, bad, emp, guard, page


# ------------------------------------------------------------------ plumbing
def _sites(request):
    """Sites the user may see: branch users – sites of their branches or of customers of their branches."""
    qs = CustomerSite.objects.select_related('customer', 'branch', 'contact', 'default_team', 'default_technician')
    b = A.branch_ids(request)
    if b is not None:
        qs = qs.filter(Q(branch_id__in=b) | Q(branch__isnull=True, customer__branch_id__in=b) |
                       Q(branch__isnull=True, customer__branch__isnull=True))
    return qs


def _equipment(request):
    qs = CustomerEquipment.objects.select_related('customer', 'site', 'site__branch', 'category', 'item')
    b = A.branch_ids(request)
    if b is not None:
        qs = qs.filter(site__in=_sites(request))
    return qs


def _plain(request, qs, row, default_limit=100):
    """page() without the branch filter of page() (the scope above already applies the branches)."""
    q = request.query_params
    try:
        limit = max(1, min(2000, int(q.get('limit') or default_limit)))
        offset = max(0, int(q.get('offset') or 0))
    except ValueError:
        limit, offset = default_limit, 0
    return Response({'count': qs.count(), 'results': [row(x) for x in qs[offset:offset + limit]]})


def _one(qs, pk):
    o = qs.filter(pk=pk).first()
    if o is None:
        raise S.ProjectsError('Not found.', None, 404)
    return o


# ------------------------------------------------------------------ sites
def save_site(x, f, user, request):
    creating = not x.pk
    if creating or f.has('customer'):
        c = f.obj('customer', Customer, required=creating)
        if c is not None:
            if x.pk and x.customer_id != c.pk and (x.tickets.exists() or x.equipment.exists()):
                raise S.ProjectsError('The customer cannot change once the site has equipment or tickets.', 'customer')
            x.customer = c
    for k in ('name', 'address', 'area', 'city', 'emirate', 'country', 'contact_name', 'contact_phone', 'access_notes', 'working_hours'):
        if f.has(k):
            setattr(x, k, f.s(k))
    if not x.name:
        raise S.ProjectsError('Site name is required.', 'name')
    if CustomerSite.objects.filter(customer=x.customer, name__iexact=x.name).exclude(pk=x.pk).exists():
        raise S.ProjectsError(f'{x.customer.name} already has a site named "{x.name}".', 'name')
    if f.has('site_type'):
        x.site_type = f.choice('site_type', SITE_TYPES, 'office')
    for k in ('latitude', 'longitude'):
        if f.has(k):
            v = f.d.get(k)
            if v in (None, ''):
                setattr(x, k, None)
            else:
                try:
                    v = Decimal(str(v))
                except Exception:   # noqa: BLE001
                    raise S.ProjectsError(f'{k.capitalize()} must be a number.', k)
                lim = 90 if k == 'latitude' else 180
                if abs(v) > lim:
                    raise S.ProjectsError(f'{k.capitalize()} must be between -{lim} and {lim}.', k)
                setattr(x, k, v.quantize(Decimal('0.000001')))
    if f.has('contact'):
        ct = f.obj('contact', Contact)
        if ct is not None and ct.customer_id != x.customer_id:
            raise S.ProjectsError('The contact belongs to another customer.', 'contact')
        x.contact = ct
    if f.has('default_team'):
        x.default_team = f.obj('default_team', SupportTeam)
    if f.has('default_technician'):
        x.default_technician = emp(f.i('default_technician'))
    from OrganisationManager.models import brnch_mstr
    allowed = A.branch_ids(request)
    if f.has('branch'):
        bid = f.i('branch')
        if bid and allowed is not None and bid not in allowed:
            raise S.ProjectsError('Choose one of your branches.', 'branch')
        x.branch = brnch_mstr.objects.filter(pk=bid).first() if bid else None
    if creating and not x.branch_id:
        x.branch = x.customer.branch
    if not x.country:
        x.country = x.customer.country or ''
    if f.has('is_active'):
        x.is_active = f.b('is_active', True)
    if creating:
        x.code = S.next_number('site')
        x.created_by = user
    x.save()
    return x


class SiteListView(Base):
    zeo_model = CustomerSite

    @guard
    def get(self, request):
        A.need(request, 'customer_view')
        q = request.query_params
        qs = _sites(request)
        if q.get('customer'):
            qs = qs.filter(customer_id=q['customer'])
        if q.get('emirate'):
            qs = qs.filter(emirate__iexact=q['emirate'])
        if q.get('branch'):
            qs = qs.filter(branch_id=q['branch'])
        if q.get('q'):
            s = q['q']
            qs = qs.filter(Q(name__icontains=s) | Q(code__icontains=s) | Q(address__icontains=s) | Q(customer__name__icontains=s))
        act = q.get('active', '1')
        if act == '1':
            qs = qs.filter(is_active=True)
        elif act == '0':
            qs = qs.filter(is_active=False)
        return _plain(request, qs, lambda x: RW.site_row(x, counts=True))

    @guard
    def post(self, request):
        A.need(request, 'customer_add')
        with transaction.atomic():
            x = save_site(CustomerSite(), In(request), request.user, request)
        return Response(RW.site_row(x, True), status=status.HTTP_201_CREATED)


class SiteDetailView(Base):
    zeo_model = CustomerSite

    @guard
    def get(self, request, pk):
        A.need(request, 'customer_view')
        x = _one(_sites(request), pk)
        r = RW.site_row(x, True)
        r['equipment'] = [RW.equipment_row(e) for e in _equipment(request).filter(site=x)]
        return Response(r)

    @guard
    def patch(self, request, pk):
        A.need(request, 'customer_change')
        x = _one(_sites(request), pk)
        with transaction.atomic():
            save_site(x, In(request), request.user, request)
        return Response(RW.site_row(x, True))

    put = patch

    @guard
    def delete(self, request, pk):
        A.need(request, 'customer_change')
        x = _one(_sites(request), pk)
        if x.tickets.exists() or x.equipment.exists() or x.contracts.exists():
            x.is_active = False
            x.save(update_fields=['is_active'])
            return Response({'detail': 'In use – made inactive instead of deleted.', 'deactivated': True})
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ equipment
def _date(f, k):
    return f.dt(k) if f.has(k) else None


def save_equipment(x, f, user, request):
    creating = not x.pk
    if creating or f.has('site'):
        st = f.obj('site', CustomerSite, required=creating)
        if st is not None:
            if not _sites(request).filter(pk=st.pk).exists():
                raise S.ProjectsError('Choose one of your sites.', 'site')
            if x.pk and x.site_id != st.pk and st.customer_id != x.customer_id and x.tickets.exists():
                raise S.ProjectsError('The equipment has tickets – it can only move to another site of the same customer.', 'site')
            x.site = st
            x.customer_id = st.customer_id
    if f.has('customer') and f.i('customer') and f.i('customer') != x.customer_id:
        raise S.ProjectsError('The site belongs to another customer.', 'site')
    if creating or f.has('category'):
        cat = f.obj('category', EquipmentCategory, required=creating)
        if cat is not None:
            x.category = cat
    for k in ('name', 'make', 'model_no', 'serial_no', 'location_in_site', 'warranty_by', 'capacity_unit', 'notes'):
        if f.has(k):
            setattr(x, k, f.s(k))
    if not x.name:
        raise S.ProjectsError('Name / tag is required.', 'name')
    if x.serial_no and CustomerEquipment.objects.filter(customer_id=x.customer_id, serial_no__iexact=x.serial_no).exclude(pk=x.pk).exists():
        raise S.ProjectsError(f'Serial no. {x.serial_no} is already registered for this customer.', 'serial_no')
    for k in ('install_date', 'warranty_start', 'warranty_end'):
        if f.has(k):
            setattr(x, k, f.dt(k))
    if x.warranty_start and x.warranty_end and x.warranty_end < x.warranty_start:
        raise S.ProjectsError('Warranty end is before warranty start.', 'warranty_end')
    if f.has('capacity'):
        x.capacity = f.n('capacity')
    if f.has('supplied_by_us'):
        x.supplied_by_us = f.b('supplied_by_us')
    if f.has('status'):
        x.status = f.choice('status', EQUIPMENT_STATUS, 'in_service')
    if f.has('item'):
        from ZeoMasters.models import Product
        x.item = f.obj('item', Product)
    if f.has('extra'):
        v = f.d.get('extra')
        if isinstance(v, str):
            import json
            try:
                v = json.loads(v or '{}')
            except ValueError:
                raise S.ProjectsError('Extra fields must be an object.', 'extra')
        if not isinstance(v, dict):
            raise S.ProjectsError('Extra fields must be an object.', 'extra')
        keys = {e.get('key') for e in (x.category.extra_fields or []) if isinstance(e, dict)}
        x.extra = {k: v[k] for k in v if k in keys}
    if f.has('is_active'):
        x.is_active = f.b('is_active', True)
    if creating:
        x.number = S.next_number('eqp')
        x.created_by = user
    x.save()
    for fl in f.filelist('files'):
        EquipmentFile.objects.create(equipment=x, file=fl, name=getattr(fl, 'name', 'file')[:255], size=getattr(fl, 'size', 0) or 0,
                                     uploaded_by=user)
    return x


def _filter_equipment(request, qs):
    q = request.query_params
    for k in ('customer', 'site', 'category'):
        if q.get(k):
            qs = qs.filter(**{f'{k}_id': q[k]})
    if q.get('status'):
        qs = qs.filter(status=q['status'])
    on = S.local_today()
    w = q.get('warranty')
    if w == 'in_warranty':
        qs = qs.filter(warranty_end__gte=on)
    elif w == 'expiring':
        from datetime import timedelta
        qs = qs.filter(warranty_end__gte=on, warranty_end__lte=on + timedelta(days=30))
    elif w == 'expired':
        qs = qs.filter(warranty_end__lt=on)
    if q.get('q'):
        s = q['q']
        qs = qs.filter(Q(name__icontains=s) | Q(number__icontains=s) | Q(serial_no__icontains=s) | Q(make__icontains=s) |
                       Q(model_no__icontains=s) | Q(customer__name__icontains=s) | Q(site__name__icontains=s))
    act = q.get('active', '1')
    if act == '1':
        qs = qs.filter(is_active=True)
    elif act == '0':
        qs = qs.filter(is_active=False)
    amc = q.get('amc')
    if amc in ('covered', 'not_covered'):
        ids = [x.pk for x in qs if bool(S.equipment_contracts(x)) == (amc == 'covered')]
        qs = qs.filter(pk__in=ids)
    return qs


class EquipmentListView(Base):
    zeo_model = CustomerEquipment

    @guard
    def get(self, request):
        A.need(request, 'customer_view')
        return _plain(request, _filter_equipment(request, _equipment(request)), RW.equipment_row)

    @guard
    def post(self, request):
        A.need(request, 'customer_add')
        with transaction.atomic():
            x = save_equipment(CustomerEquipment(), In(request), request.user, request)
        return Response(RW.equipment_row(x, True), status=status.HTTP_201_CREATED)


class EquipmentDetailView(Base):
    zeo_model = CustomerEquipment

    @guard
    def get(self, request, pk):
        A.need(request, 'customer_view')
        return Response(RW.equipment_row(_one(_equipment(request), pk), True))

    @guard
    def patch(self, request, pk):
        A.need(request, 'customer_change')
        x = _one(_equipment(request), pk)
        with transaction.atomic():
            save_equipment(x, In(request), request.user, request)
        return Response(RW.equipment_row(x, True))

    put = patch

    @guard
    def delete(self, request, pk):
        A.need(request, 'customer_change')
        x = _one(_equipment(request), pk)
        if x.tickets.exists() or x.contracts.exists():
            x.is_active = False
            x.save(update_fields=['is_active'])
            return Response({'detail': 'In use – made inactive instead of deleted.', 'deactivated': True})
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EquipmentImportView(Base):
    """POST {"rows": [{customer, site, category, name, make, model_no, serial_no, location_in_site, install_date,
    warranty_start, warranty_end, warranty_by, capacity, capacity_unit, status}]} – customer / site / category by
    code or name. Dry run unless "apply": true; returns each row's result."""

    @guard
    def post(self, request):
        A.need(request, 'customer_add')
        data = request.data if isinstance(request.data, dict) else {}
        rows = data.get('rows') or []
        apply = bool(data.get('apply'))
        out, ok = [], 0
        sites = _sites(request)
        for i, r in enumerate(rows, start=1):
            try:
                cust = Customer.objects.filter(Q(code__iexact=str(r.get('customer') or '').strip()) |
                                               Q(name__iexact=str(r.get('customer') or '').strip())).first()
                if cust is None:
                    raise S.ProjectsError('Customer not found.', 'customer')
                site = sites.filter(customer=cust).filter(Q(code__iexact=str(r.get('site') or '').strip()) |
                                                          Q(name__iexact=str(r.get('site') or '').strip())).first()
                if site is None:
                    raise S.ProjectsError('Site not found for this customer.', 'site')
                cat = EquipmentCategory.objects.filter(Q(code__iexact=str(r.get('category') or '').strip()) |
                                                       Q(name__iexact=str(r.get('category') or '').strip())).first()
                if cat is None:
                    raise S.ProjectsError('Category not found.', 'category')
                payload = {k: r.get(k) for k in ('name', 'make', 'model_no', 'serial_no', 'location_in_site', 'install_date',
                                                  'warranty_start', 'warranty_end', 'warranty_by', 'capacity', 'capacity_unit',
                                                  'status') if r.get(k) not in (None, '')}
                payload.update(site=site.pk, category=cat.pk)

                class _F(In):
                    def __init__(self, d):
                        self.d, self.files = d, {}
                with transaction.atomic():
                    sid = transaction.savepoint()
                    x = save_equipment(CustomerEquipment(), _F(payload), request.user, request)
                    if apply:
                        transaction.savepoint_commit(sid)
                    else:
                        transaction.savepoint_rollback(sid)
                out.append({'row': i, 'ok': True, 'number': x.number if apply else ''})
                ok += 1
            except S.ProjectsError as e:
                out.append({'row': i, 'ok': False, 'error': e.message})
        return Response({'applied': apply, 'ok': ok, 'failed': len(rows) - ok, 'rows': out})


# ------------------------------------------------------------------ set-up: equipment categories, AMC types
class EquipmentCategoryListView(Base):
    zeo_model = EquipmentCategory   # v1.67.15 (checklist scan): extra fields / record panel
    @guard
    def get(self, request):
        qs = EquipmentCategory.objects.select_related('parent', 'default_team')
        act = request.query_params.get('active', '')
        if act == '1':
            qs = qs.filter(is_active=True)
        elif act == '0':
            qs = qs.filter(is_active=False)
        return Response([RW.equipment_category_row(x) for x in qs])

    @guard
    def post(self, request):
        A.need(request, 'setup')
        return Response(RW.equipment_category_row(save_category(EquipmentCategory(), In(request))), status=status.HTTP_201_CREATED)


def save_category(x, f):
    for k in ('name',):
        if f.has(k):
            setattr(x, k, f.s(k))
    if not x.name:
        raise S.ProjectsError('Name is required.', 'name')
    if f.has('code') and f.s('code'):
        x.code = f.s('code').upper()[:40]
    if not x.code:
        base = ''.join(ch for ch in x.name.upper() if ch.isalnum())[:30] or 'CAT'
        code, n = base, 1
        while EquipmentCategory.objects.filter(code=code).exclude(pk=x.pk).exists():
            n += 1
            code = f'{base}{n}'
        x.code = code
    if EquipmentCategory.objects.filter(code=x.code).exclude(pk=x.pk).exists():
        raise S.ProjectsError(f'Code {x.code} is used by another category.', 'code')
    if f.has('parent'):
        p = f.obj('parent', EquipmentCategory)
        if p is not None and p.pk == x.pk:
            raise S.ProjectsError('A category cannot be its own parent.', 'parent')
        x.parent = p
    if f.has('pm_months'):
        x.pm_months = f.i('pm_months')
    if f.has('default_team'):
        x.default_team = f.obj('default_team', SupportTeam)
    if f.has('extra_fields'):
        v = f.d.get('extra_fields')
        if isinstance(v, str):
            import json
            try:
                v = json.loads(v or '[]')
            except ValueError:
                raise S.ProjectsError('Extra fields must be a list.', 'extra_fields')
        if not isinstance(v, list):
            raise S.ProjectsError('Extra fields must be a list.', 'extra_fields')
        clean = []
        for e in v:
            if isinstance(e, dict) and str(e.get('label') or '').strip():
                key = str(e.get('key') or '').strip() or ''.join(ch if ch.isalnum() else '_' for ch in str(e['label']).lower()).strip('_')
                clean.append({'key': key[:40], 'label': str(e['label']).strip()[:80],
                              'type': e.get('type') if e.get('type') in ('text', 'number', 'date', 'choice', 'yes_no') else 'text',
                              'options': [str(o)[:60] for o in (e.get('options') or [])][:30]})
        x.extra_fields = clean
    if f.has('to_review'):
        x.to_review = f.b('to_review')
    if f.has('is_active'):
        x.is_active = f.b('is_active', True)
    edited = False
    if f.has('checklist'):   # v1.67.23 (pending 271): the service checklist set on screen – one line per item
        v = f.d.get('checklist')
        if isinstance(v, str):
            v = v.splitlines()
        if not isinstance(v, list):
            raise S.ProjectsError('The checklist must be a list of lines.', 'checklist')
        items, seen = [], set()
        for it in v:
            t = ' '.join(str((it.get('item') if isinstance(it, dict) else it) or '').split())
            if not t:
                continue
            if len(t) > 120:
                raise S.ProjectsError(f'Checklist line too long (120 characters at most): {t[:40]}…', 'checklist')
            if t.lower() in seen:
                raise S.ProjectsError(f'"{t}" is in the checklist twice.', 'checklist')
            seen.add(t.lower())
            items.append(t)
        if len(items) > 60:
            raise S.ProjectsError('At most 60 checklist lines.', 'checklist')
        x.checklist = items
        edited = True
    x.save()
    if edited:
        from .packs import mark_checklist_edited
        mark_checklist_edited(x.pk)
    return x


class EquipmentCategoryDetailView(Base):
    @guard
    def patch(self, request, pk):
        A.need(request, 'setup')
        return Response(RW.equipment_category_row(save_category(_one(EquipmentCategory.objects.all(), pk), In(request))))

    put = patch

    @guard
    def delete(self, request, pk):
        A.need(request, 'setup')
        x = _one(EquipmentCategory.objects.all(), pk)
        if x.equipment.exists() or x.contracts.exists() or x.children.exists():
            x.is_active = False
            x.save(update_fields=['is_active'])
            return Response({'detail': 'In use – made inactive instead of deleted.', 'deactivated': True})
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def save_amc_type(x, f):
    if f.has('name'):
        x.name = f.s('name')
    if not x.name:
        raise S.ProjectsError('Name is required.', 'name')
    if f.has('code') and f.s('code'):
        x.code = f.s('code').upper()[:40]
    if not x.code:
        base = ''.join(ch for ch in x.name.upper() if ch.isalnum())[:30] or 'AMC'
        code, n = base, 1
        while AMCType.objects.filter(code=code).exclude(pk=x.pk).exists():
            n += 1
            code = f'{base}{n}'
        x.code = code
    if AMCType.objects.filter(code=x.code).exclude(pk=x.pk).exists():
        raise S.ProjectsError(f'Code {x.code} is used by another AMC type.', 'code')
    if f.has('parts'):
        x.parts = f.choice('parts', PARTS_COVER, 'included')
    if f.has('labour_included'):
        x.labour_included = f.b('labour_included', True)
    if f.has('visits_per_year'):
        x.visits_per_year = f.i('visits_per_year') or 0
    if f.has('callouts_per_year'):
        x.callouts_per_year = f.i('callouts_per_year')
    if f.has('sla_policy'):
        x.sla_policy = f.obj('sla_policy', SLAPolicy)
    if f.has('terms'):
        x.terms = f.s('terms')
    if f.has('to_review'):
        x.to_review = f.b('to_review')
    if f.has('is_active'):
        x.is_active = f.b('is_active', True)
    x.save()
    return x


class AMCTypeListView(Base):
    zeo_model = AMCType   # v1.67.15 (checklist scan): extra fields / record panel
    @guard
    def get(self, request):
        qs = AMCType.objects.select_related('sla_policy')
        act = request.query_params.get('active', '')
        if act == '1':
            qs = qs.filter(is_active=True)
        elif act == '0':
            qs = qs.filter(is_active=False)
        return Response([RW.amc_type_row(x) for x in qs])

    @guard
    def post(self, request):
        A.need(request, 'setup')
        return Response(RW.amc_type_row(save_amc_type(AMCType(), In(request))), status=status.HTTP_201_CREATED)


class AMCTypeDetailView(Base):
    @guard
    def patch(self, request, pk):
        A.need(request, 'setup')
        return Response(RW.amc_type_row(save_amc_type(_one(AMCType.objects.all(), pk), In(request))))

    put = patch

    @guard
    def delete(self, request, pk):
        A.need(request, 'setup')
        x = _one(AMCType.objects.all(), pk)
        if x.contracts.exists():
            x.is_active = False
            x.save(update_fields=['is_active'])
            return Response({'detail': 'In use – made inactive instead of deleted.', 'deactivated': True})
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ industry packs
def _industries_payload():
    from ZeoApps.models import AppState
    st = AppState.objects.filter(app='projects').first()
    ticked = PK.ticked()
    have = {r['code'] for r in ticked}
    return {'main': st.industry if st is not None else '', 'ticked': ticked,
            'available': [{'code': c, 'label': l, 'has_content': c in PK.WITH_CONTENT} for c, l in PK.INDUSTRIES if c not in have],
            'all': [{'code': c, 'label': l, 'has_content': c in PK.WITH_CONTENT} for c, l in PK.INDUSTRIES],
            'to_review': {'equipment_categories': EquipmentCategory.objects.filter(to_review=True).count(),
                          'amc_types': AMCType.objects.filter(to_review=True).count()}}


class IndustryView(Base):
    """GET the ticked industries; POST {"codes": [...]} ticks more (packs only add); DELETE ?code= unticks one."""

    @guard
    def get(self, request):
        return Response(_industries_payload())

    @guard
    def post(self, request):
        A.need(request, 'setup')
        data = request.data if isinstance(request.data, dict) else {}
        codes = data.get('codes') or ([data['code']] if data.get('code') else [])
        if not codes:
            return bad('Tick at least one industry.', field='codes')
        added = []
        for c in codes:
            if c not in PK.LABELS:
                return bad(f'Unknown industry "{c}".', field='codes')
        for c in codes:
            added += PK.install(c, request.user)
        r = _industries_payload()
        r['added'] = added
        return Response(r)

    @guard
    def delete(self, request):
        A.need(request, 'setup')
        code = request.query_params.get('code', '')
        try:
            PK.remove(code)
        except ValueError as e:
            return bad(str(e), field='code')
        return Response(_industries_payload())


def lookups():
    """Choice lists for the screens."""
    return {'site_types': [{'value': k, 'label': v} for k, v in SITE_TYPES],
            'equipment_status': [{'value': k, 'label': v} for k, v in EQUIPMENT_STATUS],
            'parts_cover': [{'value': k, 'label': v} for k, v in PARTS_COVER],
            'cover_scope': [{'value': k, 'label': v} for k, v in COVER_SCOPE],
            'ticket_kinds': [{'value': 'request', 'label': 'Request'}, {'value': 'callout', 'label': 'Emergency call-out'},
                             {'value': 'visit', 'label': 'Preventive visit'}]}
