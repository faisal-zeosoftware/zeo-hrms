"""v1.67.13 (billing) – Billing & collections API: item stages, PIs and tax invoices (PDF, e-mail), payments, follow-ups.

Rights: billing_view to see, billing_edit to change. A project manager may also move a milestone of a project they
manage to "PI to create" (the work is finished and can be billed).
"""
import json
from decimal import Decimal, InvalidOperation

from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import billing as B
from . import services as S
from .models import (BILL_STATUS, CONTACT_METHODS, FOLLOWUP_KINDS, PAY_METHODS, VAT_TYPES, BillDocument, BillFollowUp, BillPayment,
                     Milestone)
from .views import Base, In, guard


def d(v):
    return str(v) if v else ''


def _list(v):
    if isinstance(v, str):
        try:
            v = json.loads(v or '[]')
        except ValueError:
            raise S.ProjectsError('Bad list.')
    return v if isinstance(v, list) else []


def _dec(v, field):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise S.ProjectsError('Enter a number.', field)


def doc_row(doc, full=False):
    r = {'id': doc.id, 'kind': doc.kind, 'kind_label': doc.get_kind_display(), 'number': doc.number, 'external': doc.external,
         'customer': doc.customer_id, 'customer_name': doc.customer.name, 'customer_email': doc.customer.email, 'customer_trn': doc.customer.trn,
         'date': d(doc.date), 'supply_date': d(doc.supply_date), 'due_date': d(doc.due_date), 'status': doc.status,
         'status_label': doc.get_status_display(), 'client_ref': doc.client_ref, 'currency': doc.currency, 'exchange_rate': str(doc.exchange_rate),
         'subtotal': str(doc.subtotal), 'vat_amount': str(doc.vat_amount), 'total': str(doc.total), 'paid_amount': str(doc.paid_amount),
         'balance': str(doc.balance), 'from_pi': doc.from_pi_id, 'from_pi_number': doc.from_pi.number if doc.from_pi_id else '',
         'sent_at': d(doc.sent_at), 'sent_to': doc.sent_to, 'cancel_reason': doc.cancel_reason,
         'sync_status': doc.sync_status, 'sync_ref': doc.sync_ref,   # v1.67.14 (billing connectors)
         'overdue': bool(doc.kind == 'invoice' and doc.status == 'issued' and doc.due_date and doc.due_date < S.local_today() and doc.balance > 0)}
    if full:
        r.update({'notes': doc.notes, 'terms': doc.terms,
                  'lines': [{'id': ln.id, 'description': ln.description, 'quantity': str(ln.quantity), 'unit_price': str(ln.unit_price),
                             'vat_type': ln.vat_type, 'vat_rate': str(ln.vat_rate), 'amount': str(ln.amount), 'vat_amount': str(ln.vat_amount),
                             'milestone': ln.milestone_id, 'amc_billing': ln.amc_billing_id} for ln in doc.lines.all()],
                  'payments': [pay_row(p) for p in doc.payments.all()],
                  'followups': [fu_row(f) for f in doc.followups.select_related('assigned_to')[:50]],
                  'invoices': [{'id': x.id, 'number': x.number, 'status': x.status} for x in doc.invoices.all()],
                  'editable': doc.status not in ('converted', 'cancelled') and not (doc.kind == 'invoice' and (doc.status != 'draft' or doc.paid_amount))})
    return r


def pay_row(p):
    return {'id': p.id, 'document': p.document_id, 'date': d(p.date), 'amount': str(p.amount), 'method': p.method,
            'method_label': p.get_method_display(), 'reference': p.reference, 'note': p.note, 'by': S.user_name(p.created_by) if p.created_by_id else ''}


def fu_row(f):
    return {'id': f.id, 'kind': f.kind, 'kind_label': f.get_kind_display(), 'date': d(f.date), 'method': f.method,
            'method_label': f.get_method_display(), 'contact': f.contact, 'note': f.note,
            'promised_amount': str(f.promised_amount) if f.promised_amount is not None else '', 'promised_date': d(f.promised_date),
            'next_date': d(f.next_date), 'assigned_to': f.assigned_to_id, 'assigned_name': S.user_name(f.assigned_to) if f.assigned_to_id else '',
            'is_open': f.is_open, 'document': f.document_id, 'by': S.user_name(f.created_by) if f.created_by_id else ''}


def _doc(request, pk):
    A.need(request, 'billing_view')
    doc = BillDocument.objects.select_related('customer', 'from_pi').filter(pk=pk).first()
    if doc is None:
        raise Http404
    return doc


def _can_stage(request, obj, stage):
    if A.can(request, 'billing_edit'):
        return True
    if isinstance(obj, Milestone) and stage == 'pi_to_create':
        from .views_projects import need_manage, project_for
        need_manage(request, project_for(request, obj.project_id))
        return True
    raise A.Denied('Only finance can change billing stages.')


class CollectionsView(Base):
    """GET – totals and one row per milestone / AMC billing line (filters stage, customer, project, contract, kind, q, days)."""

    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        out = B.collections(request, A, request.query_params)
        out['rights'] = {'billing_edit': A.can(request, 'billing_edit')}
        out['options'] = {'vat_types': VAT_TYPES, 'pay_methods': PAY_METHODS, 'contact_methods': CONTACT_METHODS,
                          'followup_kinds': FOLLOWUP_KINDS, 'stages': list(B.STAGE_LABEL.items()), 'doc_status': BILL_STATUS,
                          'default_vat_rate': str(S.settings().default_vat_rate)}
        return Response(out)


class ItemView(Base):
    """GET  billing/items/<kind>/<pk>/ – the item with its documents and follow-ups.
    POST billing/items/<kind>/<pk>/stage/ {stage}."""

    @guard
    def get(self, request, kind, pk):
        A.need(request, 'billing_view')
        o = B.item_get(kind, pk)
        r = B.item_row(o)
        docs = BillDocument.objects.filter(lines__milestone=o) if kind == 'milestone' else BillDocument.objects.filter(lines__amc_billing=o)
        r['documents'] = [doc_row(x) for x in docs.distinct().select_related('customer', 'from_pi')]
        r['followups'] = [fu_row(f) for f in B._fu_key_qs(o).select_related('assigned_to', 'created_by')[:100]]
        r['default_lines'] = B.default_lines([o])
        return Response(r)


class ItemStageView(Base):
    @guard
    def post(self, request, kind, pk):
        o = B.item_get(kind, pk)
        stage = In(request).s('stage')
        _can_stage(request, o, stage)
        B.set_stage(o, stage, request.user)
        return Response(B.item_row(o))


class DocumentListView(Base):
    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        q = request.query_params
        qs = BillDocument.objects.select_related('customer', 'from_pi')
        if q.get('kind'):
            qs = qs.filter(kind=q['kind'])
        if q.get('status'):
            qs = qs.filter(status__in=q['status'].split(','))
        if q.get('customer'):
            qs = qs.filter(customer_id=q['customer'])
        if q.get('q'):
            from django.db.models import Q
            qs = qs.filter(Q(number__icontains=q['q']) | Q(customer__name__icontains=q['q']) | Q(client_ref__icontains=q['q']))
        if q.get('open'):
            qs = qs.exclude(status__in=('cancelled', 'converted'))
        return Response([doc_row(x) for x in qs[:500]])

    @guard
    def post(self, request):
        """{kind: pi|invoice, items: [{kind, id}], lines?, pi?, date, supply_date, client_ref, notes, external_no, customer (free lines)}"""
        A.need(request, 'billing_edit')
        f = In(request)
        kind = f.s('kind') or 'pi'
        items = _list(f.d.get('items'))
        lines = _list(f.d.get('lines')) if f.has('lines') and f.d.get('lines') not in (None, '') else None
        if kind == 'pi':
            if items:
                doc = B.create_pi(items, request.user, lines=lines, date=f.dt('date'), client_ref=f.s('client_ref'), notes=f.s('notes'))
            else:
                if not lines:
                    raise S.ProjectsError('Add at least one line.', 'lines')
                doc = B.pi_create_customer(f.i('customer', True, 'Customer'), request.user, lines=lines, date=f.dt('date'),
                                           client_ref=f.s('client_ref'), notes=f.s('notes'))
        elif kind == 'invoice':
            pi = BillDocument.objects.filter(pk=f.i('pi'), kind='pi').first() if f.i('pi') else None
            if pi is None and not items:
                raise S.ProjectsError('Create the invoice from a PI or from milestones / AMC lines.')
            doc = B.create_invoice(request.user, pi=pi, refs=items, lines=lines, date=f.dt('date'), supply_date=f.dt('supply_date'),
                                   external_no=f.s('external_no'), client_ref=f.s('client_ref'), notes=f.s('notes'))
        else:
            raise S.ProjectsError('Unknown document kind.', 'kind')
        return Response(doc_row(doc, True), status=status.HTTP_201_CREATED)


class DocumentDetailView(Base):
    @guard
    def get(self, request, pk):
        return Response(doc_row(_doc(request, pk), True))

    @guard
    def patch(self, request, pk):
        doc = _doc(request, pk)
        A.need(request, 'billing_edit')
        f = In(request)
        B.edit_document(doc, request.user, lines=_list(f.d.get('lines')) if f.has('lines') else None,
                        client_ref=f.s('client_ref') if f.has('client_ref') else None, notes=f.s('notes') if f.has('notes') else None,
                        terms=f.s('terms') if f.has('terms') else None, date=f.dt('date'), supply_date=f.dt('supply_date'),
                        due_date=f.dt('due_date'))
        return Response(doc_row(BillDocument.objects.get(pk=doc.pk), True))


class DocumentActionView(Base):
    """POST billing/documents/<pk>/<action>/ – send {to, message} | mark-sent | accept {client_ref} | cancel {reason}."""

    @guard
    def post(self, request, pk, action):
        doc = _doc(request, pk)
        A.need(request, 'billing_edit')
        f = In(request)
        if action == 'send':
            B.send_document(doc, f.s('to'), f.s('message'), request.user)
        elif action == 'mark-sent':
            B.mark_sent(doc, request.user, f.s('to'))
        elif action == 'accept':
            B.accept_pi(doc, request.user, f.s('client_ref'))
        elif action == 'cancel':
            B.cancel_document(doc, request.user, f.s('reason'))
        elif action == 'accounting':   # v1.67.14 (billing connectors): (re)make its journal and send it when an app is connected
            B.send_to_accounting(doc, request.user)
        else:
            raise Http404
        return Response(doc_row(BillDocument.objects.get(pk=doc.pk), True))


class DocumentPdfView(Base):
    @guard
    def get(self, request, pk):
        doc = _doc(request, pk)
        from .billing_pdf import build
        resp = HttpResponse(build(doc), content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{doc.number}.pdf"'
        return resp


class PaymentView(Base):
    @guard
    def post(self, request, pk):
        doc = _doc(request, pk)
        A.need(request, 'billing_edit')
        f = In(request)
        B.add_payment(doc, _dec(f.d.get('amount'), 'amount') or 0, f.dt('date'), f.s('method') or 'transfer', f.s('reference'), f.s('note'), request.user)
        return Response(doc_row(BillDocument.objects.get(pk=doc.pk), True), status=status.HTTP_201_CREATED)


class PaymentDetailView(Base):
    @guard
    def delete(self, request, pk):
        A.need(request, 'billing_edit')
        p = BillPayment.objects.filter(pk=pk).first()
        if p is None:
            raise Http404
        doc = B.delete_payment(p, request.user)
        return Response(doc_row(BillDocument.objects.get(pk=doc.pk), True))


class FollowUpView(Base):
    """GET billing/followups/?mine=1&open=1 · POST {item_kind, item_id, kind, date, method, contact, note, promised_amount,
    promised_date, next_date, assigned_to, payment: {amount, date, method, reference}}"""

    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        q = request.query_params
        qs = BillFollowUp.objects.select_related('assigned_to', 'created_by', 'milestone', 'amc_billing', 'amc_billing__contract')
        if q.get('open'):
            qs = qs.filter(is_open=True)
        if q.get('mine'):
            qs = qs.filter(assigned_to=request.user)
        out = []
        for x in qs[:300]:
            r = fu_row(x)
            o = x.milestone or x.amc_billing
            r['item'] = {'kind': B.item_kind(o), 'id': o.pk, 'label': B.item_label(o)} if o else None
            out.append(r)
        return Response(out)

    @guard
    def post(self, request):
        A.need(request, 'billing_edit')
        f = In(request)
        o = B.item_get(f.s('item_kind'), f.i('item_id', True, 'Item'))
        pay = f.d.get('payment')
        if isinstance(pay, str):
            pay = json.loads(pay or '{}') if pay else None
        if pay:
            amt = _dec(pay.get('amount'), 'payment')
            from django.utils.dateparse import parse_date
            pay = {'amount': amt, 'date': parse_date(str(pay.get('date'))[:10]) if pay.get('date') else None, 'method': pay.get('method'),
                   'reference': pay.get('reference', '')} if amt else None
        assigned = None
        if f.i('assigned_to'):
            from django.contrib.auth import get_user_model
            assigned = get_user_model().objects.filter(pk=f.i('assigned_to')).first()
        fu = B.add_followup(o, request.user, kind=f.s('kind') or None, date=f.dt('date'), method=f.s('method') or 'call', contact=f.s('contact'),
                            note=f.s('note'), promised_amount=_dec(f.d.get('promised_amount'), 'promised_amount'), promised_date=f.dt('promised_date'),
                            next_date=f.dt('next_date'), assigned_to=assigned, payment=pay)
        return Response(fu_row(fu), status=status.HTTP_201_CREATED)


class BillingUsersView(Base):
    """Users who can be given billing follow-ups (people with billing rights, support managers, the user)."""

    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        users = {u.pk: u for u in S.manager_users()}
        users[request.user.pk] = request.user
        s = S.settings()
        if s.billing_user_id:
            users[s.billing_user_id] = s.billing_user
        try:
            from tenant_users.tenants.models import UserTenantPermissions
            from django.db.models import Q
            for utp in UserTenantPermissions.objects.filter(Q(groups__permissions__codename='change_amcbilling') |
                                                            Q(permissions__codename='change_amcbilling')).select_related('profile').distinct():
                if utp.profile and utp.profile.is_active:
                    users[utp.profile.pk] = utp.profile
        except Exception:   # noqa: BLE001
            pass
        return Response(sorted(({'id': u.pk, 'name': S.user_name(u)} for u in users.values()), key=lambda x: x['name'].lower()))

