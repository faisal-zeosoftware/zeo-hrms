"""v1.35.0 – Accounting integration API, mounted at /accounting/.

Inside ZEO (signed-in users; rights "Can see / build and post / set up accounting journals"):
GET  api/settings/                       posting rules, connector, accounts (secrets only as "set / not set")      PUT
POST api/settings/test/                  test the connection to Odoo / Zoho Books
GET  api/options/                        accounts and analytic accounts / tag options of the accounting app
GET  api/accounts/                       salary components, provisions, payables, settlement lines, expense categories
                                         with their account rows                                                  PUT {rows:[…]}
GET  api/dimensions/?kind=department     departments / employees / branches with their GL code, analytic, partner PUT {rows:[…]}
POST api/dimensions/auto/                {kind, create} – match (and create) the analytic accounts / tag options
POST api/partners/auto/                  {create} – match (and create) the employee partners / contacts
GET  api/sources/                        payroll runs and final settlements that can be posted, with their journal
                                         + "property": bills, receipts (and refunds R<id>), payouts, commissions, vendor bills (v1.40.0)
POST api/property/paid/                  v1.40.0 read the paid state of the posted Property invoices from Odoo → Property
GET  api/journals/?status=&source=       journals                                    POST api/journals/build/ {source, id | ids | as_of | from, to}
GET  api/journals/<id>/                  a journal with its lines and log
POST api/journals/<id>/<action>/         post | recheck | reverse | cancel | exported
GET  api/journals/<id>/missing/          accounts the journal uses that the accounting app does not have
POST api/accounts/create/                {codes:[…]} – create those accounts there (type of the base account)
GET  api/journals/export/?ids=1,2&type=xlsx|csv   Excel / CSV for Tally, Sage, QuickBooks, Xero or any other application
GET/POST api/clients/   DELETE api/clients/<id>/     applications that pull journals (client id + secret, shown once)
GET/POST api/hooks/     DELETE api/hooks/<id>/       webhooks: journal.ready, journal.posted, journal.exported

For other applications (ZEO API v1 – client credentials, no user):
POST v1/token/?schema=<company>          {client_id, client_secret} → {access_token, expires_in: 3600}
GET  v1/journals/?status=ready&since=    Authorization: Bearer <token>
GET  v1/journals/<id>/
POST v1/journals/<id>/ack/               {external_id, external_ref} – the application posted it: the journal is "posted"
"""
import secrets as pysecrets
from datetime import date

from django.apps import apps
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.db import connection
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import ctx

from . import secure
from . import services as S
from .connectors import ConnectorError
from .models import AccountingSettings, AccountMap, ApiClient, ApiClientHook, DimensionMap, JournalBatch

M = apps.get_model
VIEW = {'view_accounting', 'post_accounting', 'manage_accounting'}
POST = {'post_accounting', 'manage_accounting'}
MANAGE = {'manage_accounting'}

SETTING_FIELDS = ['connector', 'department_mode', 'employee_mode', 'branch_mode', 'dims_on', 'dept_gl_pattern', 'emp_gl_pattern', 'branch_gl_pattern',
                  'post_as', 'auto_build', 'auto_post', 'partner_lines', 'journal_code', 'currency', 'payable_account', 'rounding_account', 'gratuity_expense_account',
                  'gratuity_provision_account', 'leave_expense_account', 'leave_provision_account', 'settlement_payable_account',
                  'default_expense_account', 'default_deduction_account', 'odoo_url', 'odoo_db', 'odoo_login', 'odoo_api', 'odoo_company_id',
                  'odoo_dept_plan_id', 'odoo_emp_plan_id', 'odoo_branch_plan_id', 'zoho_api_domain', 'zoho_accounts_domain', 'zoho_org_id',
                  'zoho_client_id', 'zoho_dept_tag_id', 'zoho_emp_tag_id', 'zoho_branch_tag_id',
                  # v1.62.0 JV or invoices / payments / bills
                  'post_style', 'sales_journal_code', 'purchase_journal_code', 'bank_journal_code', 'sale_tax_ref', 'purchase_tax_ref']


def d2(v):
    return f'{v:.2f}'


class Base(APIView):
    permission_classes = [IsAuthenticated]
    zeo_access = False   # rights checked here (accounting rights), not by the model-permission layer
    zeo_scope = False
    need = VIEW

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.c = ctx(request)
        need = self.need_for(request)
        if need and not self.can(need):
            raise exceptions.PermissionDenied('You do not have the accounting rights for this.')

    def need_for(self, request):
        return self.need

    def can(self, codes):
        return self.c.admin or bool(set(codes) & self.c.codes)

    def handle_exception(self, exc):
        if isinstance(exc, S.Refused):
            body = {'detail': exc.message}
            if exc.problems:
                body['errors'] = exc.problems
            return Response(body, status=exc.status)
        if isinstance(exc, ConnectorError):
            return Response({'detail': str(exc)}, status=502)
        return super().handle_exception(exc)

    def rights(self):
        return {'can_post': self.can(POST), 'can_manage': self.can(MANAGE)}


def setting_dict(st):
    d = {f: getattr(st, f) for f in SETTING_FIELDS}
    d['secrets'] = secure.present(st)
    return d


class SettingsView(Base):
    def need_for(self, request):
        return MANAGE if request.method == 'PUT' else VIEW

    def get(self, request):
        st = S.settings()
        return Response({**setting_dict(st), **self.rights(), 'choices': {
            'connector': AccountingSettings._meta.get_field('connector').choices,
            'department_mode': AccountingSettings._meta.get_field('department_mode').choices,
            'employee_mode': AccountingSettings._meta.get_field('employee_mode').choices,
            'branch_mode': AccountingSettings._meta.get_field('branch_mode').choices,
            'dims_on': AccountingSettings._meta.get_field('dims_on').choices,
            'post_as': AccountingSettings._meta.get_field('post_as').choices,
            'post_style': AccountingSettings._meta.get_field('post_style').choices,   # v1.62.0
            'odoo_api': AccountingSettings._meta.get_field('odoo_api').choices}})

    def put(self, request):
        st = S.settings()
        d = request.data
        before = (st.connector, st.odoo_url, st.odoo_db, st.zoho_org_id)
        for f in SETTING_FIELDS:
            if f in d:
                fld = AccountingSettings._meta.get_field(f)
                v = d[f]
                if fld.get_internal_type() == 'IntegerField':
                    v = int(v) if v not in (None, '') else None
                elif fld.get_internal_type() == 'BooleanField':
                    v = bool(v)
                else:
                    v = (v or '').strip() if isinstance(v, str) or v is None else v
                    if fld.choices and v not in dict(fld.choices):
                        raise S.Refused(f'{fld.verbose_name}: "{v}" is not a choice.')
                setattr(st, f, v)
        for p in ('{account}',):
            for f in ('dept_gl_pattern', 'emp_gl_pattern', 'branch_gl_pattern'):
                if p not in getattr(st, f):
                    raise S.Refused(f'The pattern "{getattr(st, f)}" must contain {{account}}.')
        for n in ('odoo_api_key', 'zoho_client_secret', 'zoho_refresh_token'):
            if d.get(n):
                secure.put(st, n, d[n].strip())
                if n == 'zoho_refresh_token':
                    secure.put(st, 'zoho_access_token', '')
            if d.get(f'clear_{n}'):
                secure.put(st, n, '')
        st.save()
        after = (st.connector, st.odoo_url, st.odoo_db, st.zoho_org_id)
        if before != after and before[0] in ('odoo', 'zoho') and after[0] in ('odoo', 'zoho'):
            # another accounting application: its analytic accounts / tag options and partners are not the old one's
            DimensionMap.objects.update(analytic_ref='', analytic_name='', partner_ref='')
        S.logit(None, 'settings', True, 'posting rules / connector changed', request.user)
        return Response({**setting_dict(st), **self.rights()})


class TestView(Base):
    need = MANAGE

    def post(self, request):
        con = S.connector()
        if con is None:
            return Response({'ok': True, 'detail': 'Export only – nothing to connect.'})
        try:
            return Response(con.test())
        except ConnectorError as ex:
            return Response({'ok': False, 'detail': str(ex)})


class OptionsView(Base):
    def get(self, request):
        return Response(S.fetch_options())


# ------------------------------------------------------------------ account map
def account_items():
    """Everything that needs an account, grouped for the screen."""
    st = S.settings()
    comps = M('PayrollManagement', 'SalaryComponent').objects.select_related('branch').order_by('component_type', 'name')
    items = []
    for c in comps:
        side = {'addition': 'debit', 'deduction': 'credit', 'others': 'both'}[c.component_type] if c.component_type in ('addition', 'deduction', 'others') else 'debit'
        items.append({'kind': 'component', 'key': str(c.pk), 'label': c.name + (f' ({c.branch.branch_name})' if c.branch_id else ''), 'code': c.code or '',
                      'group': {'addition': 'Salary additions', 'deduction': 'Salary deductions'}.get(c.component_type, 'Employer contributions / others'),
                      'side': side, 'default_debit': st.default_expense_account if side != 'credit' else '',
                      'default_credit': st.default_deduction_account if side != 'debit' else ''})
    items.append({'kind': 'payable', 'key': 'net', 'label': 'Net salaries payable', 'group': 'Payables', 'side': 'credit', 'default_credit': st.payable_account})
    items.append({'kind': 'payable', 'key': 'settlement', 'label': 'Final settlement payable', 'group': 'Payables', 'side': 'credit',
                  'default_credit': st.settlement_payable_account})
    items.append({'kind': 'provision', 'key': 'gratuity', 'label': 'Gratuity (expense / provision)', 'group': 'Provisions', 'side': 'both',
                  'default_debit': st.gratuity_expense_account, 'default_credit': st.gratuity_provision_account})
    items.append({'kind': 'provision', 'key': 'leave', 'label': 'Leave salary (expense / provision)', 'group': 'Provisions', 'side': 'both',
                  'default_debit': st.leave_expense_account, 'default_credit': st.leave_provision_account})
    for key, label, side in (('salary', 'Salary due', 'debit'), ('notice', 'Pay in lieu of notice', 'debit'), ('overtime', 'Overtime', 'debit'),
                             ('air_ticket', 'Air ticket', 'debit'), ('unpaid', 'Unpaid leave', 'credit'), ('notice_short', 'Notice not served', 'credit'),
                             ('assets', 'Asset recovery', 'credit'), ('loan', 'Loan balance', 'credit'), ('advance', 'Salary advance', 'credit')):
        items.append({'kind': 'settlement', 'key': key, 'label': label, 'group': 'Final settlement', 'side': side,
                      'default_debit': st.default_expense_account if side == 'debit' else '', 'default_credit': st.default_deduction_account if side == 'credit' else ''})
    try:
        for c in M('ExpenseManagement', 'ExpenseCategory').objects.order_by('name'):
            items.append({'kind': 'expense', 'key': str(c.pk), 'label': c.name, 'group': 'Expense categories', 'side': 'debit',
                          'default_debit': c.gl_account or '', 'default_credit': ''})
    except LookupError:
        pass
    if S.property_app() is not None:   # v1.40.0 Property app accounts
        for key, label, side, default, group in S.PROPERTY_ACCOUNTS:
            items.append({'kind': 'property', 'key': key, 'label': label, 'group': group, 'side': side,
                          'default_debit': default if side == 'debit' else '', 'default_credit': default if side == 'credit' else ''})
    return items


class AccountsView(Base):
    def need_for(self, request):
        return MANAGE if request.method == 'PUT' else VIEW

    def get(self, request):
        rows = list(AccountMap.objects.values('id', 'kind', 'key', 'label', 'debit_account', 'credit_account', 'department_id', 'employee_id', 'branch_id'))
        return Response({'items': account_items(), 'rows': rows, **self.rights()})

    def put(self, request):
        keep = []
        for r in request.data.get('rows', []):
            if not (r.get('debit_account') or r.get('credit_account')):
                continue
            obj = AccountMap.objects.filter(pk=r.get('id')).first() if r.get('id') else None
            obj = obj or AccountMap()
            for f in ('kind', 'key', 'label', 'debit_account', 'credit_account'):
                setattr(obj, f, (str(r.get(f) or '')).strip())
            for f in ('department_id', 'employee_id', 'branch_id'):
                setattr(obj, f, int(r[f]) if r.get(f) else None)
            if obj.kind not in ('component', 'settlement', 'expense', 'payable', 'provision', 'property'):
                raise S.Refused(f'Unknown kind {obj.kind}.')
            obj.save()
            keep.append(obj.pk)
        AccountMap.objects.exclude(pk__in=keep).delete()
        return self.get(request)


class CreateAccountsView(Base):
    need = MANAGE

    def post(self, request):
        return Response({'created': S.create_accounts(request.data.get('codes') or [])})


# ------------------------------------------------------------------ dimensions
class DimensionsView(Base):
    def need_for(self, request):
        return MANAGE if request.method == 'PUT' else VIEW

    def get(self, request):
        kind = request.query_params.get('kind', 'department')
        maps = {m.local_id: m for m in DimensionMap.objects.filter(kind=kind)}
        if kind == 'department':
            items = [(d.pk, d.dept_name, d.dept_code or '', '') for d in M('OrganisationManager', 'dept_master').objects.order_by('dept_name')]
        elif kind == 'branch':
            items = [(b.pk, b.branch_name, b.branch_code or '', '') for b in M('OrganisationManager', 'brnch_mstr').objects.order_by('branch_name')]
        else:
            q = request.query_params.get('q', '').strip()
            qs = M('EmpManagement', 'emp_master').objects.filter(is_active=True).select_related('emp_dept_id').order_by('emp_code')
            if q:
                from django.db.models import Q
                qs = qs.filter(Q(emp_code__icontains=q) | Q(emp_first_name__icontains=q) | Q(emp_last_name__icontains=q))
            items = [(e.pk, S.emp_name(e), e.emp_code, e.emp_dept_id.dept_name if e.emp_dept_id_id else '') for e in qs[:500]]
        rows = []
        for pk, name, code, extra in items:
            m = maps.get(pk)
            rows.append({'local_id': pk, 'name': name, 'code': code, 'extra': extra, 'gl_code': m.gl_code if m else '',
                         'analytic_ref': m.analytic_ref if m else '', 'analytic_name': m.analytic_name if m else '', 'partner_ref': m.partner_ref if m else ''})
        return Response({'kind': kind, 'rows': rows, **self.rights()})

    def put(self, request):
        kind = request.data.get('kind')
        if kind not in ('department', 'employee', 'branch'):
            raise S.Refused('kind?')
        for r in request.data.get('rows', []):
            m, _ = DimensionMap.objects.get_or_create(kind=kind, local_id=int(r['local_id']))
            for f in ('gl_code', 'analytic_ref', 'analytic_name', 'partner_ref'):
                if f in r:
                    setattr(m, f, (r.get(f) or '').strip())
            m.label = r.get('name') or m.label
            m.save()
        return Response({'ok': True})


class AutoMapView(Base):
    need = MANAGE

    def post(self, request):
        return Response(S.auto_map(request.data.get('kind', 'department'), bool(request.data.get('create')), request.user))


class PartnersView(Base):
    need = MANAGE

    def post(self, request):
        if request.data.get('business'):   # v1.44.0 business partners (tenants, owners, customers, suppliers…)
            return Response(S.map_business_partners(bool(request.data.get('create'))))
        return Response(S.map_partners(bool(request.data.get('create'))))


# ------------------------------------------------------------------ journals
def batch_row(b):
    return {'id': b.id, 'number': b.number, 'source': b.source, 'source_name': b.get_source_display(), 'source_id': b.source_id, 'label': b.source_label,
            'date': b.date, 'reference': b.reference, 'status': b.status, 'status_name': b.get_status_display(), 'connector': b.connector,
            'debit': d2(b.total_debit), 'credit': d2(b.total_credit), 'lines': b.lines_count, 'external_id': b.external_id, 'external_ref': b.external_ref,
            'problems': b.problems, 'last_error': b.last_error, 'attempts': b.attempts, 'posted_at': b.posted_at,
            'reversal_of': b.reversal_of.number if b.reversal_of_id else '', 'rules': {k: v for k, v in (b.rules or {}).items()
                                                                                       if k in ('department', 'employee', 'branch', 'dims_on')}}


class SourcesView(Base):
    def get(self, request):
        have = {}
        for b in JournalBatch.objects.exclude(status='cancelled').order_by('id'):
            have[(b.source, b.source_id)] = {'id': b.id, 'number': b.number, 'status': b.status}
        runs = [{'id': r.pk, 'label': f'{r.get_month_display()} {r.year}' + (f' – {r.name}' if r.name else ''), 'status': r.status,
                 'journal': have.get(('payroll', str(r.pk)))} for r in M('PayrollManagement', 'PayrollRun').objects.filter(status__in=('approved', 'paid')).order_by('-year', '-month', '-id')[:36]]
        sets = []
        try:
            E = M('EmpManagement', 'emp_master')
            ss = list(M('PayrollPlus', 'ExitSettlement').objects.filter(status__in=('approved', 'paid')).order_by('-last_working_date')[:50])
            emps = {e.pk: e for e in E.objects.filter(pk__in=[s.employee_id for s in ss])}
            sets = [{'id': s.pk, 'label': f'{s.number or s.pk} – {S.emp_name(emps.get(s.employee_id))}', 'status': s.status, 'net': d2(s.net),
                     'journal': have.get(('settlement', str(s.pk)))} for s in ss]
        except LookupError:
            pass
        prov = [{'month': k[1], 'journal': v} for k, v in have.items() if k[0] == 'provision']
        out = {'payroll': runs, 'settlements': sets, 'provisions': prov}
        if S.property_app() is not None:   # v1.40.0 Property documents of the last 90 days (the hook's default), same shape as the payroll rows
            def prow(src, it, sid, label, party, amount):
                return {'id': sid, 'number': it['number'], 'date': it['date'], 'label': label, 'party': party, 'amount': d2(S.q2(amount)),
                        'status': it.get('external_ref') or it.get('status') or '', 'journal': have.get((src, str(sid)))}
            prop = {'bills': [prow('prop_bill', i, i['id'], f"{'Credit note' if i['credit'] else 'Invoice'} {i['number']} – {i['charge']}", i['party'], i['total'])
                              for i in S.property_items('bills')],
                    'receipts': [prow('prop_receipt', i, i['id'], f"Receipt {i['number']} – {i['method']}", i['party'], i['amount']) for i in S.property_items('receipts')]
                    + [prow('prop_receipt', i, f"R{i['id']}", f"Refund {i['number']} – {i['kind']}", i['party'], i['amount']) for i in S.property_items('refunds')],
                    'payouts': [prow('prop_payout', i, i['id'], f"Payout {i['number']}", i['owner'], i['net']) for i in S.property_items('payouts')],
                    'commissions': [prow('prop_commission', i, i['id'], f"Commission {i['number']}", i['agent'], i['amount']) for i in S.property_items('commissions')],
                    'vendor_bills': [prow('prop_vendor', i, i['id'], f"Vendor bill {i.get('invoice_no') or i['number']}", i['vendor'], i['amount'])
                                     for i in S.property_items('vendor_bills')]}
            out['property'] = {k: sorted(v, key=lambda r: (str(r['date']), str(r['number'])), reverse=True) for k, v in prop.items()}
        return Response(out)


class JournalsView(Base):
    def get(self, request):
        qs = JournalBatch.objects.select_related('reversal_of')
        p = request.query_params
        if p.get('status'):
            qs = qs.filter(status__in=p['status'].split(','))
        if p.get('source'):
            qs = qs.filter(source=p['source'])
        if p.get('from'):
            qs = qs.filter(date__gte=p['from'])
        if p.get('to'):
            qs = qs.filter(date__lte=p['to'])
        counts = {}
        for s in JournalBatch.objects.values_list('status', flat=True):
            counts[s] = counts.get(s, 0) + 1
        return Response({'results': [batch_row(b) for b in qs[:300]], 'counts': counts, 'connector': S.settings().connector, **self.rights()})


class BuildView(Base):
    need = POST

    def post(self, request):
        d = request.data
        src = d.get('source')
        if src == 'payroll':
            b = S.build_payroll(int(d['id']), request.user)
        elif src == 'settlement':
            b = S.build_settlement(int(d['id']), request.user)
        elif src == 'provision':
            b = S.build_provisions(date.fromisoformat(d['as_of']) if d.get('as_of') else None, request.user)
        elif src == 'expense':
            b = S.build_expenses(date.fromisoformat(d['from']), date.fromisoformat(d['to']), request.user)
        elif src in S.PROP_SOURCES:   # v1.40.0 {source, id} or {source, ids:[…]} – one journal per Property document
            ids = d.get('ids') or [d.get('id')]
            made, errors = [], []
            for pk in ids:
                try:
                    made.append(S.build_property(src, pk, request.user))
                except S.Refused as ex:
                    if len(ids) == 1:
                        raise
                    errors.append(f'{pk}: {ex.message}')
            for x in made:
                S.hooks_fire('journal.ready', x) if x.status == 'ready' else None
            if not made:
                raise S.Refused('; '.join(errors) or 'Nothing to post.')
            return Response({**batch_row(made[-1]), 'built': [batch_row(x) for x in made], 'errors': errors}, status=201)
        else:
            raise S.Refused('Choose what to post.')
        S.hooks_fire('journal.ready', b) if b.status == 'ready' else None
        return Response(batch_row(b), status=201)


class PropertyPaidView(Base):
    """v1.40.0 POST – reads the payment state of the posted Property invoices from Odoo and tells Property the paid ones."""
    need = POST

    def post(self, request):
        return Response(S.property_check_paid(request.user))


class JournalView(Base):
    def get(self, request, pk):
        b = JournalBatch.objects.filter(pk=pk).first()
        if b is None:
            return Response({'detail': 'Not found.'}, status=404)
        names = {(m.kind, m.analytic_ref): m.analytic_name or m.label for m in DimensionMap.objects.exclude(analytic_ref='')}
        lines = [{'seq': l.seq, 'account': l.account, 'base_account': l.base_account, 'label': l.label, 'debit': d2(l.debit), 'credit': d2(l.credit),
                  'department': l.department, 'employee': l.employee, 'branch': l.branch, 'analytics': l.analytics, 'partner_ref': l.partner_ref,
                  'analytic_names': [f"{k}: {names.get((k, str(v)), v)}" for k, v in (l.analytics or {}).items()]}
                 for l in b.lines.all()]
        logs = [{'at': x.at, 'action': x.action, 'ok': x.ok, 'detail': x.detail} for x in b.log.all()[:50]]
        return Response({**batch_row(b), 'lines': lines, 'log': logs, 'reversals': [r.number for r in b.reversals.all()], **self.rights()})


class JournalActionView(Base):
    need = POST

    def post(self, request, pk, action):
        b = JournalBatch.objects.filter(pk=pk).first()
        if b is None:
            return Response({'detail': 'Not found.'}, status=404)
        if action == 'post':
            b = S.post(b, request.user)
        elif action == 'recheck':
            b = S.recheck(b, request.user)
        elif action == 'reverse':
            r = S.reverse(b, request.user, date.fromisoformat(request.data['date']) if request.data.get('date') else None)
            return Response(batch_row(r))
        elif action == 'cancel':
            b = S.cancel(b, request.user)
        elif action == 'exported':
            if b.status not in ('ready', 'failed') and not (b.status == 'draft' and b.source == 'reversal'):
                raise S.Refused('Only a journal ready to post can be marked as exported.')
            S.mark_done(b, 'exported', request.user, external_ref=request.data.get('external_ref', ''))
            S.logit(b, 'exported', True, request.data.get('external_ref', ''), request.user)
        else:
            return Response({'detail': 'Unknown action.'}, status=400)
        b.refresh_from_db()
        return Response(batch_row(b))


class MissingView(Base):
    def get(self, request, pk):
        b = JournalBatch.objects.filter(pk=pk).first()
        return Response({'missing': S.accounts_check(b)})


class ExportView(Base):
    def get(self, request):
        ids = [int(x) for x in (request.query_params.get('ids') or '').split(',') if x.strip().isdigit()]
        qs = JournalBatch.objects.filter(pk__in=ids).order_by('date', 'id') if ids else JournalBatch.objects.filter(status='ready').order_by('date', 'id')
        bs = list(qs)
        if not bs:
            raise S.Refused('No journals to export.')
        if request.query_params.get('type') == 'csv':
            resp = HttpResponse(S.export_csv(bs), content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="zeo-journals-{date.today():%Y%m%d}.csv"'
        else:
            resp = HttpResponse(S.export_xlsx(bs), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="zeo-journals-{date.today():%Y%m%d}.xlsx"'
        for b in bs:
            S.logit(b, 'export', True, request.query_params.get('type', 'xlsx'), request.user)
        return resp


# ------------------------------------------------------------------ API clients and webhooks (set up in ZEO)
class ClientsView(Base):
    need = MANAGE

    def get(self, request):
        return Response({'clients': [{'id': c.id, 'name': c.name, 'client_id': c.client_id, 'can_ack': c.can_ack, 'is_active': c.is_active,
                                      'last_used': c.last_used} for c in ApiClient.objects.order_by('name')],
                         'hooks': [{'id': h.id, 'name': h.name, 'url': h.url, 'events': h.events, 'is_active': h.is_active, 'last_status': h.last_status}
                                   for h in ApiClientHook.objects.order_by('name')], 'schema': connection.schema_name})

    def post(self, request):
        name = (request.data.get('name') or '').strip()
        if not name:
            raise S.Refused('Name the application.')
        secret = pysecrets.token_urlsafe(32)
        c = ApiClient.objects.create(name=name, client_id='zac_' + pysecrets.token_hex(8), secret_hash=make_password(secret),
                                     can_ack=bool(request.data.get('can_ack', True)))
        return Response({'id': c.id, 'name': c.name, 'client_id': c.client_id, 'client_secret': secret,
                         'note': 'Copy the secret now – ZEO keeps only a hash of it.'}, status=201)


class ClientView(Base):
    need = MANAGE

    def delete(self, request, pk):
        ApiClient.objects.filter(pk=pk).delete()
        return Response(status=204)


class HooksView(Base):
    need = MANAGE

    def post(self, request):
        d = request.data
        url = (d.get('url') or '').strip()
        if not url.startswith('https://') and not url.startswith('http://'):
            raise S.Refused('Give the webhook URL (https://…).')
        ev = [e for e in (d.get('events') or ['journal.ready', 'journal.posted']) if e in ('journal.ready', 'journal.posted', 'journal.exported')]
        h = ApiClientHook.objects.create(name=(d.get('name') or url)[:100], url=url, events=ev, secret=(d.get('secret') or pysecrets.token_hex(16))[:100])
        return Response({'id': h.id, 'secret': h.secret}, status=201)


class HookView(Base):
    need = MANAGE

    def delete(self, request, pk):
        ApiClientHook.objects.filter(pk=pk).delete()
        return Response(status=204)


# ------------------------------------------------------------------ ZEO API v1 for other applications
SALT = 'zeo-accounting-api'


class ClientAuth(authentication.BaseAuthentication):
    def authenticate(self, request):
        h = request.META.get('HTTP_AUTHORIZATION', '')
        if not h.startswith('Bearer '):
            raise exceptions.NotAuthenticated('Bearer token needed (POST v1/token/).')
        try:
            data = signing.loads(h[7:].strip(), salt=SALT, max_age=3600)
        except signing.SignatureExpired:
            raise exceptions.AuthenticationFailed('Token expired – get a new one.')
        except signing.BadSignature:
            raise exceptions.AuthenticationFailed('Invalid token.')
        if data.get('s') != connection.schema_name:
            raise exceptions.AuthenticationFailed('The token is for another company.')
        c = ApiClient.objects.filter(pk=data.get('c'), is_active=True).first()
        if c is None:
            raise exceptions.AuthenticationFailed('Application switched off.')
        ApiClient.objects.filter(pk=c.pk).update(last_used=timezone.now())
        return (c, None)

    def authenticate_header(self, request):
        return 'Bearer'


class PublicBase(APIView):
    authentication_classes = [ClientAuth]
    permission_classes = []
    zeo_access = False
    zeo_scope = False

    def handle_exception(self, exc):
        if isinstance(exc, S.Refused):
            return Response({'detail': exc.message}, status=exc.status)
        return super().handle_exception(exc)


class TokenView(APIView):
    authentication_classes = []
    permission_classes = []
    zeo_access = False
    zeo_scope = False

    def post(self, request):
        cid = request.data.get('client_id') or ''
        c = ApiClient.objects.filter(client_id=cid, is_active=True).first()
        if c is None or not check_password(request.data.get('client_secret') or '', c.secret_hash):
            return Response({'error': 'invalid_client'}, status=401)
        return Response({'access_token': signing.dumps({'c': c.pk, 's': connection.schema_name}, salt=SALT), 'token_type': 'Bearer', 'expires_in': 3600})


def public_journal(b, lines=False):
    d = {'id': b.id, 'number': b.number, 'source': b.source, 'source_id': b.source_id, 'description': b.source_label, 'date': b.date.isoformat(),
         'reference': b.reference, 'status': b.status, 'currency': S.settings().currency, 'total_debit': d2(b.total_debit),
         'total_credit': d2(b.total_credit), 'reversal_of': b.reversal_of_id, 'external_id': b.external_id, 'external_ref': b.external_ref,
         'posting_rules': {k: v for k, v in (b.rules or {}).items() if k in ('department', 'employee', 'branch', 'dims_on')},
         'updated': (b.posted_at or b.created_at).isoformat()}
    if lines:
        d['lines'] = [{'line': l.seq, 'account': l.account, 'base_account': l.base_account, 'description': l.label, 'debit': d2(l.debit),
                       'credit': d2(l.credit), 'department': {'id': l.department_id, 'name': l.department} if l.department_id else None,
                       'employee': {'id': l.employee_id, 'name': l.employee} if l.employee_id else None,
                       'branch': {'id': l.branch_id, 'name': l.branch} if l.branch_id else None,
                       'analytics': l.analytics, 'partner': l.partner_ref} for l in b.lines.all()]
    return d


class PublicJournals(PublicBase):
    def get(self, request):
        qs = JournalBatch.objects.exclude(status__in=('draft', 'cancelled', 'posting')).order_by('id')
        st = request.query_params.get('status', 'ready')
        if st != 'all':
            qs = qs.filter(status__in=st.split(','))
        if request.query_params.get('since'):
            qs = qs.filter(id__gt=int(request.query_params['since']))
        bs = list(qs[:100])
        return Response({'results': [public_journal(b, lines=request.query_params.get('lines') == '1') for b in bs],
                         'next_since': bs[-1].id if bs else None})


def public_partner(p):
    """v1.44.0 – a business partner for the accounting application (customer / supplier master)."""
    return {'id': p.pk, 'number': p.number, 'name': p.name, 'legal_name': p.legal_name, 'arabic_name': p.arabic_name, 'kind': p.kind,
            'company': {'id': p.parent_id, 'number': p.parent.number} if p.parent_id else None, 'roles': p.roles_text, 'status': p.status,
            'tax_id': p.tax_id, 'licence_no': p.licence_no, 'email': p.email, 'phone': p.phone, 'mobile': p.mobile,
            'address': {'street': p.street, 'building_no': p.building_no, 'additional_no': p.additional_no, 'district': p.district, 'city': p.city,
                        'postal_code': p.postal_code, 'state': p.state, 'country': p.country, 'country_code': p.country_code, 'text': p.address},
            'einvoice': {'channel': p.einvoice_channel, 'endpoint_id': p.einvoice_id, 'scheme': p.einvoice_scheme, 'buyer_id_type': p.buyer_id_type,
                         'buyer_id': p.buyer_id},
            'payment_term': p.payment_term.name if p.payment_term_id else '', 'currency': p.currency, 'credit_limit': d2(p.credit_limit) if p.credit_limit is not None else None,
            'credit_hold': p.credit_hold, 'balance': {'receivable': d2(p.balance_receivable), 'payable': d2(p.balance_payable), 'overdue': d2(p.balance_overdue)},
            'banks': [{'bank': b.bank, 'iban': b.iban, 'account_no': b.account_no, 'swift': b.swift, 'currency': b.currency, 'default': b.is_default} for b in p.banks.all()],
            'accounting_ref': p.accounting_ref, 'updated': p.updated_at.isoformat() if p.updated_at else None}


def public_item(i):
    return {'id': i.pk, 'number': i.number, 'code': i.code, 'name': i.name, 'arabic_name': i.arabic_name, 'kind': i.kind, 'active': i.is_active,
            'uom': {'code': i.uom.code, 'unece': i.uom.unece_code} if i.uom_id else None, 'barcode': i.barcode, 'hs_code': i.hs_code,
            'sale_price': d2(i.sale_price), 'tax': {'code': i.tax.code, 'rate': d2(i.tax.rate)} if i.tax_id else None, 'tax_category': i.tax_category,
            'exemption_reason': i.exemption_reason, 'valuation': i.valuation, 'standard_cost': d2(i.standard_cost) if i.standard_cost is not None else None,
            'stock_qty': d2(i.stock_qty), 'stock_value': d2(i.stock_value),
            'accounts': {'income': i.income_account, 'expense': i.expense_account, 'stock': i.stock_account},
            'updated': i.updated_at.isoformat() if i.updated_at else None}


def _since(request, qs):
    v = request.query_params.get('updated_since')
    if v:
        from django.utils.dateparse import parse_datetime, parse_date
        dt = parse_datetime(v) or (parse_date(v) and timezone.make_aware(timezone.datetime.combine(parse_date(v), timezone.datetime.min.time())))
        if dt:
            qs = qs.filter(updated_at__gte=dt)
    return qs


class PublicPartners(PublicBase):
    """GET /accounting/v1/partners/?updated_since=2026-10-01&page=1 – 200 per page."""
    def get(self, request):
        from ZeoMasters.models import Partner
        qs = _since(request, Partner.objects.filter(merged_into__isnull=True).select_related('parent', 'payment_term').prefetch_related('banks').order_by('id'))
        page = max(int(request.query_params.get('page') or 1), 1)
        rows = list(qs[(page - 1) * 200: page * 200 + 1])
        return Response({'results': [public_partner(p) for p in rows[:200]], 'next_page': page + 1 if len(rows) > 200 else None})


class PublicPartnerRef(PublicBase):
    """POST {"accounting_ref": "42"} – the accounting application tells ZEO its id of the partner."""
    def post(self, request, pk):
        from ZeoMasters.models import Partner
        if not request.user.can_ack:
            return Response({'detail': 'This application may only read.'}, status=403)
        ref = str(request.data.get('accounting_ref') or '')[:60]
        n = Partner.objects.filter(pk=pk).update(accounting_ref=ref)
        if not n:
            return Response({'detail': 'Not found.'}, status=404)
        return Response({'id': pk, 'accounting_ref': ref})


class PublicItems(PublicBase):
    def get(self, request):
        from ZeoMasters.models import Product
        qs = _since(request, Product.objects.select_related('uom', 'tax').order_by('id'))
        page = max(int(request.query_params.get('page') or 1), 1)
        rows = list(qs[(page - 1) * 200: page * 200 + 1])
        return Response({'results': [public_item(i) for i in rows[:200]], 'next_page': page + 1 if len(rows) > 200 else None})


class PublicJournal(PublicBase):
    def get(self, request, pk):
        b = JournalBatch.objects.exclude(status__in=('draft', 'cancelled')).filter(pk=pk).first()
        if b is None:
            return Response({'detail': 'Not found.'}, status=404)
        return Response(public_journal(b, lines=True))


class PublicAck(PublicBase):
    def post(self, request, pk):
        if not request.user.can_ack:
            return Response({'detail': 'This application may only read.'}, status=403)
        b = JournalBatch.objects.filter(pk=pk).first()
        if b is None:
            return Response({'detail': 'Not found.'}, status=404)
        if b.status == 'posted':
            return Response(public_journal(b))
        if b.status not in ('ready', 'failed'):
            return Response({'detail': f'The journal is {b.status}.'}, status=409)
        S.mark_done(b, 'posted', None, request.data.get('external_id', ''), request.data.get('external_ref', ''))
        S.logit(b, 'ack', True, f'{request.user.name}: {request.data.get("external_ref", "")}')
        return Response(public_journal(b))
