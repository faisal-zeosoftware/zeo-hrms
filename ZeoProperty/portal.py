"""Tenant / owner / buyer portal (/zeo-property/api/portal/...). Portal users are ZEO users linked to an Owner or a
tenant / buyer (Party) record, with no group rights; the portal guard (middleware.py) keeps them out of every other API.
Maintenance requests and owner approvals go to ZEO Maintenance when it is installed and activated."""
import secrets
import string

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import F, Q
from django.http import FileResponse, Http404
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from zeo.appkit.engine import AppError, In, call, d, guard, jsonable, linked, today, user_name
from zeo.appkit.views import Base

from .app import PAGE, app, cur
from .models import Bill, Cheque, Lease, Owner, OwnerStatement, Party, PropertyFile, SaleContract, Unit


def _password():
    alphabet = string.ascii_letters + string.digits
    while True:
        p = ''.join(secrets.choice(alphabet) for _ in range(10)) + secrets.choice('!@#$%*')
        if any(c.isdigit() for c in p) and any(c.isupper() for c in p) and any(c.islower() for c in p):
            return p


def cache_key(schema, uid):
    return f'zprop-portal:{schema}:{uid}'


def is_portal_user(schema, uid):
    key = cache_key(schema, uid)
    v = cache.get(key)
    if v is None:
        v = Owner.objects.filter(portal_user_id=uid).exists() or Party.objects.filter(portal_user_id=uid).exists()
        cache.set(key, v, 300)
    return v


def invite(person, schema, reset=False):
    """Create (or reset) the portal login of an Owner or a Party. Returns (username, password)."""
    from tenant_users.tenants.models import UserTenantPermissions
    from UserManagement.models import CustomUser, company
    email = (person.email or '').strip().lower()
    if not email:
        raise AppError('An e-mail address is needed for a portal login.', 'email')
    if not person.is_active:
        raise AppError('The record is switched off.')
    tenant = company.objects.filter(schema_name=schema).first()
    if tenant is None:
        raise AppError('Company not found.')
    password = _password()
    with transaction.atomic():
        user = person.portal_user
        if user is None:
            clash = CustomUser.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).first()
            if clash is not None:
                if not is_portal_user(schema, clash.pk):
                    raise AppError('A ZEO login with this e-mail already exists (perhaps an employee). Use another e-mail.', 'email')
                user = clash   # the same person is owner and tenant / buyer: one login for both
                password = ''
            else:
                user = CustomUser.objects.create_user(username=email, email=email, password=password, is_ess=False)
                user.must_change_password = False
                user.save()
                user.tenants.add(tenant)
            person.portal_user = user
            person.save(update_fields=['portal_user'])
        else:
            if not reset and user.is_active:
                raise AppError('There is already a portal login. Tick "Reset the password" to send a new one.')
            user.set_password(password)
            user.is_active = True
            user.save()
            if not user.tenants.filter(pk=tenant.pk).exists():
                user.tenants.add(tenant)
        UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': False, 'is_staff': False})
    cache.delete(cache_key(schema, user.pk))
    s = app.settings()
    app.send_mail([email], 'Your portal login',
                  f'Dear {person.name},\n\nYou can now see your leases, payments, statements and maintenance requests in our portal.\n\n'
                  f'Address: {s.portal_url or "the portal address given by our team"}\nCompany code: {schema}\nUser name: {user.username}\n'
                  + (f'Password: {password}\n' if password else 'Password: the one you already use for this portal\n') + '\nPlease keep it safe.\n')
    return user.username, password


def invite_action(o, request, f):
    username, password = invite(o, connection.schema_name, f.b('reset'))
    app.event('owner' if isinstance(o, Owner) else 'party', o.pk, f'Portal login sent to {username} by {user_name(request.user)}', request.user)
    return {'message': f'Portal login sent to {username}.', 'username': username, 'password': password}


class PortalMember(BasePermission):
    message = 'This page is for portal users.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = connection.schema_name
        if not sch or sch == 'public' or not u.tenants.filter(schema_name=sch).exists():
            self.message = 'You do not have access to this portal.'
            return False
        if not app.activated():
            self.message = 'The portal is not open yet.'
            return False
        s = app.settings()
        request.pp_owner = Owner.objects.filter(portal_user=u, is_active=True).first() if s.portal_owner else None
        party = Party.objects.filter(portal_user=u, is_active=True).first()
        request.pp_party = party if party and (s.portal_tenant or s.portal_buyer) else None
        if request.pp_owner is None and request.pp_party is None:
            self.message = 'Your portal login is not active.'
            return False
        return True


class PortalBase(Base):
    permission_classes = [PortalMember]
    needs_activation = False
    app = app


def files_of(model, ids):
    return [{'id': f.pk, 'name': f.name, 'at': d(f.uploaded_at), 'object_id': f.object_id}
            for f in PropertyFile.objects.filter(model=model, object_id__in=list(ids), portal=True)]


def bill_row(b):
    return {'id': b.pk, 'number': b.number, 'description': b.description or b.charge.name, 'due_date': d(b.due_date), 'total': float(b.total),
            'paid': float(b.paid), 'balance': float(b.balance), 'status': b.get_status_display(), 'invoice': b.external_ref,
            'overdue': b.status in ('issued', 'part_paid') and b.due_date < today()}


def lease_row(x):
    return {'id': x.pk, 'number': x.number, 'unit': x.unit.label, 'unit_id': x.unit_id, 'start': d(x.start), 'end': d(x.end),
            'rent': float(x.annual_rent), 'deposit': float(x.deposit), 'payments': x.payments, 'status': x.get_status_display(), 'status_code': x.status,
            'renewal_status': x.renewal_status, 'days_left': (x.end - today()).days, 'accepted_on': d(x.accepted_on) if x.accepted_on else '',   # v1.40.0
            'bills': [bill_row(b) for b in x.bills.exclude(status__in=('void', 'draft')).select_related('charge')],
            'cheques': [{'cheque_no': c.cheque_no, 'bank': c.bank, 'amount': float(c.amount), 'date': d(c.cheque_date), 'status': c.get_status_display()}
                        for c in x.cheques.all()]}


class MeView(PortalBase):
    @guard
    def get(self, request):
        s = app.settings()
        p, o = request.pp_party, request.pp_owner
        roles = []
        if p is not None:
            if s.portal_tenant and Lease.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).exists():
                roles.append('tenant')
            if s.portal_buyer and SaleContract.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).exists():
                roles.append('buyer')
            if not roles:
                roles.append('tenant' if p.kind != 'buyer' else 'buyer')
        if o is not None:
            roles.append('owner')
        from UserManagement.models import company
        t = company.objects.filter(schema_name=connection.schema_name).first()
        return Response({'name': (p or o).name, 'roles': roles, 'company': t.name if t else '', 'currency': cur(),
                         'maintenance': linked(app, 'ZeoMaintenance')})


class TenantView(PortalBase):
    @guard
    def get(self, request):
        p = request.pp_party
        if p is None:
            raise Http404
        leases = Lease.objects.filter(party=p).exclude(status='cancelled').select_related('unit__building').order_by('-start')[:10]   # v1.40.0 drafts shown for e-acceptance
        units = [x.unit_id for x in leases if x.status in ('active', 'notice')]
        reqs = call(app, 'ZeoMaintenance', 'requests_for', unit_ids=units, email=p.email) or []
        return Response(jsonable({'leases': [lease_row(x) for x in leases], 'requests': reqs, 'maintenance': linked(app, 'ZeoMaintenance'),
                                  'files': files_of('lease', [x.pk for x in leases])}))


class RequestView(PortalBase):
    @guard
    def post(self, request):
        """Tenant or buyer raises a maintenance / snag request for one of their units."""
        p = request.pp_party
        if p is None:
            raise Http404
        if not linked(app, 'ZeoMaintenance'):
            raise AppError('Maintenance requests are not available in the portal. Please call the office.')
        f = In(request)
        unit = f.obj('unit', Unit, True, 'Unit')
        mine = Lease.objects.filter(party=p, unit=unit, status__in=('active', 'notice')).exists() or \
            SaleContract.objects.filter(party=p, unit=unit, status__in=('signed', 'handed_over')).exists()
        if not mine:
            raise AppError('Choose one of your units.', 'unit')
        if not f.s('title'):
            raise AppError('Say what the problem is.', 'title')
        res = call(app, 'ZeoMaintenance', 'request', title=f.s('title'), description=f.s('description'), unit_id=unit.pk, requester=p.name,
                   email=p.email, phone=p.phone, source='buyer_portal' if f.s('kind') == 'snag' else 'tenant_portal', ref=f'Portal {p.number}',
                   user=request.user, files=f.filelist('files'), snag=f.s('kind') == 'snag', access=f.s('access'))
        if not res:
            raise AppError('The request could not be sent. Please call the office.')
        return Response({'message': f'Request {res["number"]} sent. We will contact you.', 'number': res['number']})


class RenewalAnswerView(PortalBase):
    @guard
    def post(self, request):
        p = request.pp_party
        f = In(request)
        lease = Lease.objects.filter(party=p, pk=f.i('lease', True), status='active').first() if p else None
        if lease is None:
            raise Http404
        ans = f.s('answer')
        if ans not in ('renew', 'leave'):
            raise AppError('Choose renew or leave.', 'answer')
        lease.renewal_status = 'renewing' if ans == 'renew' else 'leaving'
        lease.save(update_fields=['renewal_status'])
        text = f'Tenant answered in the portal: {"wants to renew" if ans == "renew" else "will not renew"}' + (f' – {f.s("note")}' if f.s('note') else '')
        app.event('lease', lease.pk, text, request.user)
        app.tell(app.users_with_role('prop_leasing'), f'Renewal answer: {lease.number}', text, 'lease', lease.pk, lease.number,
                 PAGE + f'r/lease/{lease.pk}', summary=f'Renewal answer {lease.number}: {ans}')
        if ans == 'leave':
            call(app, 'ZeoCRM', 'renewal_closed', lease.pk, False, '', request.user)
        return Response({'message': 'Thank you – our leasing team will contact you.'})


class OwnerView(PortalBase):
    @guard
    def get(self, request):
        o = request.pp_owner
        if o is None:
            raise Http404
        units = Unit.objects.filter(owner=o).select_related('building', 'unit_type')
        rows = []
        for u in units:
            lease = u.leases.filter(status__in=('active', 'notice')).select_related('party').first()
            rows.append({'id': u.pk, 'unit': u.label, 'type': u.unit_type.name if u.unit_type_id else '', 'status': u.get_status_display(),
                         'tenant': lease.party.name if lease else '', 'rent': float(lease.annual_rent) if lease else None, 'lease_end': d(lease.end) if lease else None})
        sts = OwnerStatement.objects.filter(owner=o).exclude(status='draft')[:24]
        pending = call(app, 'ZeoMaintenance', 'owner_pending', unit_ids=[u.pk for u in units]) or []
        return Response(jsonable({'units': rows, 'statements': [{'id': s.pk, 'number': s.number, 'from': d(s.period_from), 'to': d(s.period_to),
                                                                  'collected': float(s.collected), 'expenses': float(s.expenses), 'fees': float(s.fees),
                                                                  'net': float(s.net), 'status': s.get_status_display(), 'lines': s.lines} for s in sts],
                                  'approvals': pending, 'bills': [bill_row(b) for b in Bill.objects.filter(owner=o).exclude(status__in=('void', 'draft'))[:50]],
                                  'requests': call(app, 'ZeoMaintenance', 'requests_for', unit_ids=[u.pk for u in units]) or []}))


class OwnerDecisionView(PortalBase):
    @guard
    def post(self, request):
        o = request.pp_owner
        if o is None:
            raise Http404
        f = In(request)
        units = list(Unit.objects.filter(owner=o).values_list('pk', flat=True))
        res = call(app, 'ZeoMaintenance', 'owner_decision', work_order_id=f.i('work_order', True), approve=f.b('approve'), note=f.s('note'),
                   by=f'{o.name} (owner portal)', unit_ids=units)
        if res is None:
            raise AppError('The approval could not be recorded.')
        return Response({'message': 'Thank you – your decision was sent.' , 'status': res})


class BuyerView(PortalBase):
    @guard
    def get(self, request):
        p = request.pp_party
        if p is None:
            raise Http404
        sales = SaleContract.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).select_related('unit__building')
        out = []
        for x in sales:
            out.append({'id': x.pk, 'number': x.number, 'unit': x.unit.label, 'unit_id': x.unit_id, 'price': float(x.price), 'status': x.get_status_display(),
                        'signed_on': d(x.signed_on), 'handover_due': d(x.handover_due), 'handed_over_on': d(x.handed_over_on), 'checklist': x.handover,
                        'bills': [bill_row(b) for b in x.bills.exclude(status__in=('void', 'draft')).select_related('charge')]})
        reqs = call(app, 'ZeoMaintenance', 'requests_for', unit_ids=[x.unit_id for x in sales], email=p.email) or []
        return Response(jsonable({'sales': out, 'requests': reqs, 'maintenance': linked(app, 'ZeoMaintenance'),
                                  'files': files_of('sale', [x.pk for x in sales])}))


class PortalFileView(PortalBase):
    @guard
    def get(self, request, pk):
        x = PropertyFile.objects.filter(pk=pk, portal=True).first()
        if x is None:
            raise Http404
        p, o = request.pp_party, request.pp_owner
        ok = (x.model == 'lease' and p and Lease.objects.filter(pk=x.object_id, party=p).exists()) or \
             (x.model == 'sale' and p and SaleContract.objects.filter(pk=x.object_id, party=p).exists()) or \
             (x.model == 'statement' and o and OwnerStatement.objects.filter(pk=x.object_id, owner=o).exists())
        if not ok:
            raise Http404
        return FileResponse(x.file.open('rb'), as_attachment=True, filename=x.name)


# ================================================================== v1.40.0 – portal additions, public listings and feeds
def pay_link(b):
    """The company's own payment-gateway link for a bill (Settings → online payment link; {amount}, {ref}, {name})."""
    s = app.settings()
    if not s.payment_link or b.balance <= 0:
        return ''
    who = b.party or b.owner
    from urllib.parse import quote
    return s.payment_link.replace('{amount}', f'{float(b.balance):.2f}').replace('{ref}', quote(b.number)).replace('{name}', quote(who.name if who else ''))


_bill_row = bill_row


def bill_row(b):   # noqa: F811 – adds the pay link and the kind
    r = _bill_row(b)
    r.update({'pay': pay_link(b), 'credit': b.is_credit, 'disputed': b.disputed, 'kind': b.charge.kind, 'promise_date': d(b.promise_date) if b.promise_date else ''})
    return r


class AnnouncementsView(PortalBase):
    @guard
    def get(self, request):
        from .models import Announcement
        p, o = request.pp_party, request.pp_owner
        t = today()
        qs = Announcement.objects.filter(status='published', show_from__lte=t).filter(Q(show_to__isnull=True) | Q(show_to__gte=t))
        aud = ['all']
        if p is not None:
            aud += ['tenants', 'buyers']
        if o is not None:
            aud.append('owners')
        qs = qs.filter(audience__in=aud)
        bld = set()
        if p is not None:
            bld |= set(Lease.objects.filter(party=p, status__in=('active', 'notice')).values_list('unit__building_id', flat=True))
            bld |= set(SaleContract.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).values_list('unit__building_id', flat=True))
        if o is not None:
            bld |= set(Unit.objects.filter(owner=o).values_list('building_id', flat=True))
        qs = qs.filter(Q(building__isnull=True) | Q(building_id__in=bld))
        return Response([{'id': a.pk, 'title': a.title, 'body': a.body, 'from': d(a.show_from), 'building': a.building.name if a.building_id else ''} for a in qs[:30]])


class PortalRequestsView(PortalBase):
    """Move-in / move-out dates, profile changes, NOC and parking requests from the portal."""
    @guard
    def get(self, request):
        from .models import PortalRequest
        p, o = request.pp_party, request.pp_owner
        qs = PortalRequest.objects.filter(party=p) if p is not None else PortalRequest.objects.filter(owner=o)
        return Response([{'id': r.pk, 'number': r.number, 'kind': r.get_kind_display(), 'wanted_on': d(r.wanted_on) if r.wanted_on else '', 'text': r.text, 'status': r.get_status_display(),
                          'answer': r.answer, 'at': d(r.created_at)} for r in qs[:30]])

    @guard
    def post(self, request):
        from .models import PortalRequest
        p, o = request.pp_party, request.pp_owner
        f = In(request)
        kind = f.choice('kind', PortalRequest._meta.get_field('kind').choices, 'other')
        lease = None
        if p is not None and f.i('lease'):
            lease = Lease.objects.filter(party=p, pk=f.i('lease')).first()
        data = {}
        if kind == 'profile':
            data = {k: f.s(k) for k in ('name', 'email', 'phone', 'address', 'emergency_contact', 'nationality') if f.s(k)}
            if not data:
                raise AppError('Enter what should change.', 'name')
        if kind in ('move_in', 'move_out') and not f.dt('wanted_on'):
            raise AppError('Choose the date.', 'wanted_on')
        r = PortalRequest.objects.create(number=app.next_number('portal_request', 'REQ'), kind=kind, party=p, owner=o, lease=lease, unit=lease.unit if lease else None,
                                         wanted_on=f.dt('wanted_on'), text=f.s('text')[:2000], data=data)
        who = (p or o).name
        app.event('portal_request', r.pk, f'Sent from the portal by {who}', request.user)
        app.tell(app.users_with_role('prop_leasing'), f'Portal request {r.number}: {r.get_kind_display()}', f'{who}' + (f' – {d(r.wanted_on)}' if r.wanted_on else '') + (f': {r.text[:120]}' if r.text else ''),
                 'portal_request', r.pk, r.number, PAGE + f'r/portal_request/{r.pk}', summary=f'{r.get_kind_display()} request from {who}')
        return Response({'message': f'Request {r.number} sent. We will answer in the portal.', 'number': r.number})


class DepositView(PortalBase):
    """The tenant's deposits: held, deductions, refunds."""
    @guard
    def get(self, request):
        from .models import Refund
        p = request.pp_party
        if p is None:
            raise Http404
        out = []
        for l in Lease.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).select_related('unit__building').order_by('-start')[:10]:
            dep = sum((b.paid for b in l.bills.filter(charge__kind='deposit').exclude(status='void')), 0)
            mo = l.moveouts.exclude(status='cancelled').order_by('-id').first()
            out.append({'lease': l.number, 'unit': l.unit.label, 'held': float(dep), 'status': l.get_status_display(),
                        'moveout': {'number': mo.number, 'status': mo.get_status_display(), 'deductions': float(mo.deductions), 'unpaid': float(mo.unpaid), 'refund': float(mo.refund),
                                    'lines': mo.deduction_lines, 'pdf': mo.status == 'settled', 'id': mo.pk} if mo else None,
                        'refunds': [{'number': r.number, 'amount': float(r.amount), 'status': r.get_status_display(), 'paid_on': d(r.paid_on) if r.paid_on else '', 'reference': r.reference}
                                    for r in Refund.objects.filter(party=p, lease=l)]})
        return Response(jsonable(out))


class AcceptLeaseView(PortalBase):
    """E-acceptance of a draft lease by the tenant (date, time and address recorded; printed on the contract)."""
    @guard
    def post(self, request):
        p = request.pp_party
        f = In(request)
        lease = Lease.objects.filter(party=p, pk=f.i('lease', True), status='draft').first() if p else None
        if lease is None:
            raise Http404
        if not f.b('accept'):
            raise AppError('Tick "I accept the lease terms".', 'accept')
        from zeo.appkit.engine import now
        lease.accepted_on = now()
        lease.accepted_ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))[:45]
        lease.save(update_fields=['accepted_on', 'accepted_ip'])
        app.event('lease', lease.pk, f'Accepted online by {p.name} from {lease.accepted_ip}', request.user)
        app.tell(app.users_with_role('prop_leasing'), f'Lease {lease.number} accepted online', f'{p.name} accepted the terms – activate the lease', 'lease', lease.pk, lease.number,
                 PAGE + f'r/lease/{lease.pk}', summary=f'Activate lease {lease.number}')
        return Response({'message': 'Thank you – the lease terms are accepted. The leasing team will activate the lease.'})


class PayLinkView(PortalBase):
    """The payment link of a bill and the record of an online payment reference typed by the tenant (the receipt is posted by finance)."""
    @guard
    def get(self, request):
        p, o = request.pp_party, request.pp_owner
        b = Bill.objects.filter(pk=request.GET.get('bill')).first()
        if b is None or not ((p and b.party_id == p.pk) or (o and b.owner_id == o.pk)):
            raise Http404
        return Response({'pay': pay_link(b), 'balance': float(b.balance)})

    @guard
    def post(self, request):
        p, o = request.pp_party, request.pp_owner
        f = In(request)
        b = Bill.objects.filter(pk=f.i('bill', True)).first()
        if b is None or not ((p and b.party_id == p.pk) or (o and b.owner_id == o.pk)):
            raise Http404
        ref = f.s('reference')
        if not ref:
            raise AppError('Enter the payment reference.', 'reference')
        from .models import Receipt
        r = Receipt.objects.create(number=app.next_number('receipt', 'RCT'), party=p, owner=o, received_on=today(), amount=f.n('amount') or b.balance, method='online', reference=ref[:60],
                                   note=f'Paid online for {b.number} (portal)')
        app.event('receipt', r.pk, f'Online payment reported in the portal by {(p or o).name} for {b.number} – to be confirmed by finance', request.user)
        app.tell(app.users_with_role('prop_finance'), f'Online payment to confirm: {r.number}', f'{(p or o).name} paid {cur()} {r.amount} for {b.number} – ref. {ref}', 'receipt', r.pk, r.number,
                 PAGE + f'r/receipt/{r.pk}', summary=f'Confirm online payment {r.number}')
        return Response({'message': f'Thank you – payment {r.number} recorded; it shows as paid once finance confirms it.'})


class PortalPdfView(PortalBase):
    """Lease, invoice, receipt, deposit statement and owner statement PDFs for the portal user's own records."""
    @guard
    def get(self, request, kind, pk):
        from .models import MoveOut, Receipt
        from .pdf import build
        p, o = request.pp_party, request.pp_owner
        obj = None
        if kind == 'lease' and p:
            obj = Lease.objects.filter(pk=pk, party=p).exclude(status='cancelled').first()
        elif kind == 'sale' and p:
            obj = SaleContract.objects.filter(pk=pk, party=p).exclude(status='cancelled').first()
        elif kind == 'bill':
            obj = Bill.objects.filter(pk=pk).filter(Q(party=p) if p else Q(owner=o)).exclude(status='draft').first()
        elif kind == 'receipt':
            obj = Receipt.objects.filter(pk=pk, status='posted').filter(Q(party=p) if p else Q(owner=o)).first()
        elif kind == 'moveout' and p:
            obj = MoveOut.objects.filter(pk=pk, lease__party=p, status='settled').first()
        elif kind == 'statement' and o:
            obj = OwnerStatement.objects.filter(pk=pk, owner=o).exclude(status__in=('draft', 'approval')).first()
        if obj is None:
            raise Http404
        from django.http import HttpResponse
        resp = HttpResponse(build(kind, obj, app), content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{getattr(obj, "number", kind)}.pdf"'
        return resp


# ------------------------------------------------------------------ public listings (no login): JSON, HTML page, Bayut / Property Finder XML feed
def _public_rows(request):
    from .models import Listing
    s = app.settings()
    if not s.listing_site:
        return None
    t = today()
    qs = Listing.objects.filter(status='published').filter(Q(publish_to__isnull=True) | Q(publish_to__gte=t)).select_related('unit__building', 'unit__unit_type', 'agent')
    q = (request.GET.get('q') or '').strip()
    kind = request.GET.get('kind') or ''
    if kind in ('rent', 'sale'):
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(unit__building__name__icontains=q) | Q(unit__building__city__icontains=q) | Q(description__icontains=q))
    if request.GET.get('beds'):
        qs = qs.filter(unit__bedrooms__gte=request.GET['beds'])
    if request.GET.get('max'):
        qs = qs.filter(price__lte=request.GET['max'])
    rows = []
    for l in qs[:200]:
        u = l.unit
        rows.append({'id': l.pk, 'ref': l.number, 'title': l.title, 'kind': l.kind, 'price': float(l.price), 'currency': cur(), 'description': l.description, 'building': u.building.name,
                     'city': u.building.city, 'type': u.unit_type.name if u.unit_type_id else '', 'bedrooms': u.bedrooms, 'bathrooms': u.bathrooms, 'area': float(u.area or 0),
                     'furnishing': u.get_furnishing_display() if u.furnishing else '', 'parking': u.parking, 'amenities': (u.amenities or []) + (u.building.amenities or []),
                     'permit': l.permit_no, 'agent': user_name(l.agent.users) if l.agent_id and l.agent.users_id else '', 'gps': u.gps or u.building.gps, 'from': d(l.publish_from),
                     'agency': s.listing_agency, 'photos': [f'/zeo-property/api/public/listings/photo/{x.pk}/' for x in PropertyFile.objects.filter(model='listing', object_id=l.pk, portal=True)[:8]]})
    return rows


class PublicListingsView(Base):
    permission_classes = []
    authentication_classes = []
    needs_activation = False
    app = app

    def get(self, request):
        from django.conf import settings as dj
        if getattr(request, 'tenant', None) is None or not app.activated():
            raise Http404
        rows = _public_rows(request)
        if rows is None:
            raise Http404
        if request.GET.get('count'):
            from .models import Listing
            Listing.objects.filter(pk=request.GET['count']).update(views=F('views') + 1)
        return Response({'agency': app.settings().listing_agency, 'currency': cur(), 'listings': rows})


class ListingFeedView(Base):
    """XML feed in the common portal format (Bayut / Property Finder / dubizzle accept it after mapping): /zeo-property/api/public/listings/feed.xml?key=…"""
    permission_classes = []
    authentication_classes = []
    needs_activation = False
    app = app

    def get(self, request):
        from xml.sax.saxutils import escape
        from django.http import HttpResponse
        s = app.settings()
        if getattr(request, 'tenant', None) is None or not app.activated() or not s.listing_feed_key or request.GET.get('key') != s.listing_feed_key:
            raise Http404
        rows = _public_rows(request) or []
        out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<list last_update="{today().isoformat()}" listing_count="{len(rows)}">']
        for r in rows:
            out.append('<property>')
            for k, v in (('reference_number', r['ref']), ('offering_type', 'RR' if r['kind'] == 'rent' else 'RS'), ('property_type', r['type']), ('price', f'{r["price"]:.0f}'), ('price_currency', r['currency']),
                         ('city', r['city']), ('community', r['building']), ('title_en', r['title']), ('description_en', r['description']), ('bedroom', r['bedrooms'] if r['bedrooms'] is not None else ''),
                         ('bathroom', r['bathrooms'] if r['bathrooms'] is not None else ''), ('size', f'{r["area"]:.0f}' if r['area'] else ''), ('furnished', r['furnishing']), ('parking', r['parking']),
                         ('permit_number', r['permit']), ('agent_name', r['agent']), ('agency', r['agency']), ('geopoints', r['gps']), ('last_update', r['from']), ('rent_frequency', 'Y' if r['kind'] == 'rent' else '')):
                if v not in ('', None):
                    out.append(f'<{k}><![CDATA[{v}]]></{k}>' if k.endswith('_en') else f'<{k}>{escape(str(v))}</{k}>')
            if r['amenities']:
                out.append('<private_amenities>' + escape(', '.join(str(x) for x in r['amenities'])) + '</private_amenities>')
            if r['photos']:
                out.append('<photo>' + ''.join(f'<url>{request.build_absolute_uri(p)}</url>' for p in r['photos']) + '</photo>')
            out.append('</property>')
        out.append('</list>')
        return HttpResponse('\n'.join(out), content_type='application/xml; charset=utf-8')


def public_listings_page(request):
    """Server-rendered public page (no login): search, filter, cards. Embed it in your website or link to it."""
    from django.http import HttpResponse
    from django.utils.html import escape as e
    if getattr(request, 'tenant', None) is None or not app.activated():
        raise Http404
    rows = _public_rows(request)
    if rows is None:
        raise Http404
    s = app.settings()
    q, kind = e(request.GET.get('q', '')), request.GET.get('kind', '')
    cards = []
    for r in rows:
        meta = ' · '.join(x for x in ((f'{r["bedrooms"]} bed' if r['bedrooms'] else ''), (f'{r["bathrooms"]} bath' if r['bathrooms'] else ''), (f'{r["area"]:,.0f} sq ft' if r['area'] else ''), r['furnishing'], r['type']) if x)
        cards.append(f'''<article class="c"><div class="ph">{'<img src="' + e(r['photos'][0]) + '" alt="">' if r['photos'] else '<span>' + e(r['building'][:1]) + '</span>'}</div>
          <div class="b"><div class="k">{'For rent' if r['kind'] == 'rent' else 'For sale'} · {e(r['ref'])}</div><h3>{e(r['title'])}</h3>
          <div class="p">{e(r['currency'])} {r['price']:,.0f}{' / year' if r['kind'] == 'rent' else ''}</div><div class="m">{e(r['building'])}{', ' + e(r['city']) if r['city'] else ''}</div><div class="m">{e(meta)}</div>
          {('<p>' + e(r['description'][:220]) + ('…' if len(r['description']) > 220 else '') + '</p>') if r['description'] else ''}
          {('<div class="m">Permit ' + e(r['permit']) + '</div>') if r['permit'] else ''}</div></article>''')
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(s.listing_agency or 'Properties')} – listings</title>
<style>:root{{--ink:#1f2433;--mut:#6b7185;--line:#dcdfea;--acc:#0f4c81;--bg:#f4f5fa}}*{{box-sizing:border-box}}body{{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
header{{background:#fff;border-bottom:1px solid var(--line);padding:18px 16px}}header h1{{margin:0 0 10px;font-size:22px}}form{{display:flex;flex-wrap:wrap;gap:8px}}input,select{{height:38px;border:1px solid var(--line);border-radius:8px;padding:0 10px;font:inherit}}
button{{height:38px;border:0;border-radius:8px;background:var(--acc);color:#fff;padding:0 16px;font:inherit;cursor:pointer}}main{{max-width:1100px;margin:0 auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
.c{{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}}.ph{{height:150px;background:#e8ebf5;display:flex;align-items:center;justify-content:center;font-size:48px;color:var(--acc)}}.ph img{{width:100%;height:100%;object-fit:cover}}
.b{{padding:12px 14px}}.k{{font-size:12px;color:var(--mut)}}h3{{margin:2px 0 4px;font-size:16px}}.p{{font-weight:700;color:var(--acc);font-size:17px}}.m{{font-size:13px;color:var(--mut)}}p{{font-size:13px;margin:6px 0 0}}.e{{grid-column:1/-1;text-align:center;color:var(--mut);padding:40px}}</style></head>
<body><header><h1>{e(s.listing_agency or 'Properties')} · {len(rows)} listing{'s' if len(rows) != 1 else ''}</h1>
<form method="get"><input name="q" value="{q}" placeholder="Building, area, keyword"><select name="kind"><option value="">Rent and sale</option><option value="rent"{' selected' if kind == 'rent' else ''}>For rent</option><option value="sale"{' selected' if kind == 'sale' else ''}>For sale</option></select>
<input name="beds" type="number" min="0" placeholder="Min bedrooms" value="{e(request.GET.get('beds', ''))}" style="width:130px"><input name="max" type="number" min="0" placeholder="Max price" value="{e(request.GET.get('max', ''))}" style="width:130px"><button>Search</button></form></header>
<main>{''.join(cards) or '<div class="e">No listing matches your search.</div>'}</main></body></html>'''
    return HttpResponse(html)


class PublicPhotoView(Base):
    """A listing photo (a file attached to the listing and marked for the portal)."""
    permission_classes = []
    authentication_classes = []
    needs_activation = False
    app = app

    def get(self, request, pk):
        if getattr(request, 'tenant', None) is None or not app.settings().listing_site:
            raise Http404
        x = PropertyFile.objects.filter(pk=pk, model='listing', portal=True).first()
        if x is None:
            raise Http404
        return FileResponse(x.file.open('rb'), filename=x.name)
