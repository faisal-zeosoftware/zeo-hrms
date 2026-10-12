"""Tenant / owner / buyer portal (/zeo-property/api/portal/...). Portal users are ZEO users linked to an Owner or a
tenant / buyer (Party) record, with no group rights; the portal guard (middleware.py) keeps them out of every other API.
Maintenance requests and owner approvals go to ZEO Maintenance when it is installed and activated."""
import secrets
import string

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
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
            'renewal_status': x.renewal_status, 'days_left': (x.end - today()).days,
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
        leases = Lease.objects.filter(party=p).exclude(status__in=('draft', 'cancelled')).select_related('unit__building').order_by('-start')[:10]
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
