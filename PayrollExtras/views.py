"""v1.68.0 – Payroll › Payroll extras (API under /payroll-plus/extras/api/ – inside the payroll-plus prefix the web server already sends to the backend).

Rights: payroll people (view payslips / payroll runs) see everything here; changing needs "change payroll run" or the extras right
(manage_payrollextras); company administrators always. Branch access applies to the employees listed and chosen.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import ctx
from Translations.i18n import tr

from . import services as S
from .models import (BulkAdvance, CommissionEntry, CommissionPlan, EmiratisationProfile, EmiratisationSettings, GCCPensionRate, ILOERecord, PayBasis,
                     RetentionBonus, ServiceChargePool)

M = apps.get_model
VIEW = {'view_payslip', 'view_payrollrun', 'change_payrollrun', 'manage_payrollextras'}
MANAGE = {'change_payrollrun', 'add_payrollrun', 'manage_payrollextras'}


def d(v):
    return v.isoformat() if v else ''


class Base(APIView):
    permission_classes = [IsAuthenticated]
    zeo_access = False
    zeo_scope = False
    need = VIEW

    def initial(self, request, *a, **kw):
        super().initial(request, *a, **kw)
        self.c = ctx(request)
        need = MANAGE if request.method not in ('GET', 'HEAD', 'OPTIONS') and self.need is VIEW else self.need
        if not (self.c.admin or set(need) & self.c.codes):
            raise exceptions.PermissionDenied(tr('You do not have the payroll rights for this.'))

    def handle_exception(self, exc):
        if isinstance(exc, S.Refused):
            body = {'detail': exc.message}
            if exc.problems:
                body['errors'] = exc.problems
            return Response(body, status=exc.status)
        return super().handle_exception(exc)

    def emps(self):
        qs = M('EmpManagement', 'emp_master').objects.filter(is_active=True).select_related('emp_branch_id', 'emp_desgntn_id')
        if self.c.branches is not None:
            qs = qs.filter(emp_branch_id_id__in=self.c.branches)
        return qs

    def employee(self, v, field='employee'):
        try:
            eid = int(v)
        except (TypeError, ValueError):
            raise S.Refused(tr('Choose the employee.'), problems={field: tr('Required.')})
        e = M('EmpManagement', 'emp_master').objects.select_related('emp_branch_id').filter(pk=eid).first()
        if e is None or (self.c.branches is not None and e.emp_branch_id_id not in self.c.branches):
            raise S.Refused(tr('Employee not found.'), status=404, problems={field: tr('Invalid.')})
        return e

    def branch_ok(self, bid):
        if self.c.branches is not None and bid not in self.c.branches:
            raise S.Refused(tr('This branch is not one of yours.'), status=403)


# ====================================================================== 29 commission
def plan_dict(p):
    return {'id': p.pk, 'name': p.name, 'basis': p.basis, 'mode': p.mode, 'tiers': p.tiers, 'cap': float(p.cap) if p.cap is not None else None,
            'component_code': p.component_code, 'active': p.active, 'note': p.note}


def entry_dict(x, names):
    return {'id': x.pk, 'plan': x.plan_id, 'plan_name': x.plan.name, 'employee_id': x.employee_id, 'employee': names.get(x.employee_id, ''),
            'month': d(x.month), 'sales': float(x.sales), 'target': float(x.target) if x.target is not None else None, 'commission': float(x.commission),
            'breakdown': x.breakdown, 'status': x.status, 'note': x.note}


class PlansView(Base):
    def get(self, request):
        return Response({'rows': [plan_dict(p) for p in CommissionPlan.objects.all()], 'bases': CommissionPlan.BASIS, 'modes': CommissionPlan.MODE})

    def post(self, request):
        dta = request.data
        p = CommissionPlan.objects.filter(pk=dta.get('id')).first() if dta.get('id') else CommissionPlan()
        if dta.get('id') and p is None:
            raise S.Refused(tr('Not found.'), status=404)
        name = str(dta.get('name') or p.name or '').strip()[:120]
        if not name:
            raise S.Refused(tr('Give the plan a name.'), problems={'name': tr('Required.')})
        if CommissionPlan.objects.filter(name__iexact=name).exclude(pk=p.pk).exists():
            raise S.Refused(tr('A plan with this name exists.'), problems={'name': tr('Already used.')})
        basis = dta.get('basis', p.basis or 'sales')
        mode = dta.get('mode', p.mode or 'slab')
        if basis not in dict(CommissionPlan.BASIS) or mode not in dict(CommissionPlan.MODE):
            raise S.Refused(tr('Choose how the commission is worked out.'), problems={'mode': tr('Invalid.')})
        p.name, p.basis, p.mode = name, basis, mode
        p.tiers = S.check_tiers(dta.get('tiers', p.tiers), mode, basis)
        p.cap = S.q2(dta['cap']) if dta.get('cap') not in (None, '') else None
        p.component_code = (str(dta.get('component_code') or p.component_code or 'ZCOMM').strip().upper())[:20]
        p.active = dta.get('active', p.active) in (True, 'true', 1, '1')
        p.note = str(dta.get('note') or '')[:255]
        p.save()
        return Response(plan_dict(p), status=201)


class EntriesView(Base):
    def get(self, request):
        m = S.first_of(request.query_params.get('month') or _zt_today())
        allowed = set(self.emps().values_list('pk', flat=True))
        rows = [x for x in CommissionEntry.objects.filter(month=m).select_related('plan') if x.employee_id in allowed]
        names = {e.pk: S.emp_name(e) for e in self.emps()}
        return Response({'month': d(m), 'rows': [entry_dict(x, names) for x in rows], 'total': float(sum((x.commission for x in rows), Decimal(0)))})

    def post(self, request):
        """{plan, month, rows: [{employee, sales, target}]} – works out and keeps the drafts (a sent one is not changed)"""
        dta = request.data
        plan = CommissionPlan.objects.filter(pk=dta.get('plan'), active=True).first()
        if plan is None:
            raise S.Refused(tr('Choose an active plan.'), problems={'plan': tr('Required.')})
        m = S.first_of(dta.get('month'))
        out, errors = [], {}
        for i, r in enumerate(dta.get('rows') or []):
            try:
                e = self.employee(r.get('employee'))
                sales = S.q2(r.get('sales'))
                if sales < 0:
                    raise S.Refused(tr('The sales cannot be below zero.'))
                target = S.q2(r['target']) if r.get('target') not in (None, '') else None
                amt, br = S.commission_of(plan, sales, target)
            except S.Refused as ex:
                errors[str(r.get('employee') or i)] = ex.message
                continue
            x, _ = CommissionEntry.objects.get_or_create(plan=plan, employee_id=e.pk, month=m)
            if x.status == 'sent':
                errors[str(e.pk)] = tr('Already sent to payroll.')
                continue
            x.sales, x.target, x.commission, x.breakdown, x.status = sales, target, amt, br, 'draft'
            x.note = str(r.get('note') or '')[:255]
            x.save()
            out.append(x.pk)
        if errors and not out:
            raise S.Refused(tr('Nothing was saved – check the marked rows.'), problems=errors)
        return Response({'saved': len(out), 'errors': errors}, status=201)


class CommissionSendView(Base):
    need = MANAGE

    def post(self, request):
        """{month, ids?} – approve the drafts and send them to Payroll › Monthly inputs"""
        m = S.first_of(request.data.get('month'))
        allowed = set(self.emps().values_list('pk', flat=True))
        qs = CommissionEntry.objects.filter(month=m, status__in=('draft', 'approved')).select_related('plan')
        if request.data.get('ids'):
            qs = qs.filter(pk__in=[int(i) for i in request.data['ids'] if str(i).isdigit()])
        xs = [x for x in qs if x.employee_id in allowed and x.commission > 0]
        if not xs:
            raise S.Refused(tr('No commission to send for this month.'))
        amounts = {}
        for x in xs:
            k = (x.employee_id, x.plan.component_code)
            amounts[k] = amounts.get(k, Decimal(0)) + x.commission
        r = S.send_amounts(m, amounts, request.user)
        now = timezone.now()
        CommissionEntry.objects.filter(pk__in=[x.pk for x in xs]).update(status='sent', sent_at=now, approved_by_id=request.user.pk)
        return Response({'sent': len(xs), **r})


# ====================================================================== 31 retention bonus
def rb_dict(r, names):
    return {'id': r.pk, 'employee_id': r.employee_id, 'employee': names.get(r.employee_id, ''), 'amount': float(r.amount), 'award_date': d(r.award_date),
            'pay_month': d(r.pay_month), 'clawback_months': r.clawback_months, 'clawback_basis': r.clawback_basis, 'status': r.status,
            'reason': r.reason, 'settlement_id': r.settlement_id, 'clawed_amount': float(r.clawed_amount), 'history': r.history,
            'clawback_until': d(S.month_end(_add_months(r.pay_month, r.clawback_months)))}


def _add_months(m, n):
    y, mo = divmod(m.month - 1 + n, 12)
    return date(m.year + y, mo + 1, 1)


class RetentionView(Base):
    def get(self, request):
        allowed = {e.pk: S.emp_name(e) for e in self.emps()}
        rows = [r for r in RetentionBonus.objects.all() if r.employee_id in allowed]
        return Response({'rows': [rb_dict(r, allowed) for r in rows]})

    def post(self, request):
        dta = request.data
        e = self.employee(dta.get('employee'))
        amt = S.q2(dta.get('amount'))
        if amt <= 0:
            raise S.Refused(tr('Enter an amount above zero.'), problems={'amount': tr('Required.')})
        try:
            award = date.fromisoformat(str(dta.get('award_date')))
        except ValueError:
            raise S.Refused(tr('Enter the award date.'), problems={'award_date': tr('Required.')})
        pay = S.first_of(dta.get('pay_month'))
        if pay < award.replace(day=1):
            raise S.Refused(tr('It is paid on or after the award date.'), problems={'pay_month': tr('Invalid.')})
        months = int(dta.get('clawback_months') or 0)
        if months < 0 or months > 60:
            raise S.Refused(tr('The clawback period is 0 – 60 months.'), problems={'clawback_months': tr('Invalid.')})
        basis = dta.get('clawback_basis') or 'pro_rata'
        if basis not in dict(RetentionBonus.BASIS):
            raise S.Refused(tr('Choose how much is repaid.'), problems={'clawback_basis': tr('Invalid.')})
        r = RetentionBonus.objects.create(employee_id=e.pk, amount=amt, award_date=award, pay_month=pay, clawback_months=months, clawback_basis=basis,
                                          reason=str(dta.get('reason') or '')[:255], created_by_id=request.user.pk,
                                          history=[{'action': 'Awarded', 'by': request.user.username, 'at': timezone.now().isoformat()}])
        return Response(rb_dict(r, {e.pk: S.emp_name(e)}), status=201)


class RetentionActionView(Base):
    def get(self, request, pk, action):
        """clawback?last_day=YYYY-MM-DD – what would be repaid"""
        r = RetentionBonus.objects.filter(pk=pk).first()
        if r is None or action != 'clawback':
            raise S.Refused(tr('Not found.'), status=404)
        self.employee(r.employee_id)
        try:
            day = date.fromisoformat(request.query_params.get('last_day') or '')
        except ValueError:
            day = _zt_today()
        amt, served = S.clawback_of(r, day)
        return Response({'amount': float(amt), 'months_served': served, 'last_day': d(day)})

    def post(self, request, pk, action):
        r = RetentionBonus.objects.filter(pk=pk).first()
        if r is None:
            raise S.Refused(tr('Not found.'), status=404)
        e = self.employee(r.employee_id)
        if action == 'send':
            if r.status != 'awarded':
                raise S.Refused(tr('Only an awarded bonus can be sent to payroll.'), status=409)
            out = S.send_amounts(r.pay_month, {(e.pk, r.component_code): r.amount}, request.user)
            r.status = 'sent'
            r.history = (r.history or []) + [{'action': f'Sent to payroll {r.pay_month:%m/%Y}', 'by': request.user.username, 'at': timezone.now().isoformat()}]
            r.save(update_fields=['status', 'history'])
            return Response({**rb_dict(r, {e.pk: S.emp_name(e)}), **out})
        if action == 'cancel':
            if r.status not in ('awarded',):
                raise S.Refused(tr('A bonus sent to payroll cannot be cancelled here – remove the monthly input first.'), status=409)
            r.status = 'cancelled'
            r.history = (r.history or []) + [{'action': 'Cancelled', 'by': request.user.username, 'at': timezone.now().isoformat()}]
            r.save(update_fields=['status', 'history'])
            return Response(rb_dict(r, {e.pk: S.emp_name(e)}))
        raise S.Refused(tr('Unknown action.'))


# ====================================================================== 32 service charge
def pool_dict(p, bnames):
    return {'id': p.pk, 'month': d(p.month), 'branch': p.branch_id, 'branch_name': bnames.get(p.branch_id, ''), 'amount': float(p.amount),
            'method': p.method, 'component_code': p.component_code, 'points': p.points, 'shares': p.shares, 'excluded': p.excluded,
            'status': p.status, 'note': p.note}


class ServiceView(Base):
    def get(self, request):
        bnames = dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', 'branch_name'))
        qs = ServiceChargePool.objects.all()
        if request.query_params.get('month'):
            qs = qs.filter(month=S.first_of(request.query_params['month']))
        rows = [p for p in qs if self.c.branches is None or p.branch_id in self.c.branches]
        return Response({'rows': [pool_dict(p, bnames) for p in rows], 'methods': ServiceChargePool.METHOD,
                         'branches': [{'id': k, 'name': v} for k, v in sorted(bnames.items(), key=lambda x: x[1]) if self.c.branches is None or k in self.c.branches]})

    def post(self, request):
        """{month, branch, amount, method, points, excluded, note} – works out the shares (draft)"""
        dta = request.data
        m = S.first_of(dta.get('month'))
        try:
            bid = int(dta.get('branch'))
        except (TypeError, ValueError):
            raise S.Refused(tr('Choose the branch.'), problems={'branch': tr('Required.')})
        if not M('OrganisationManager', 'brnch_mstr').objects.filter(pk=bid).exists():
            raise S.Refused(tr('Choose the branch.'), problems={'branch': tr('Invalid.')})
        self.branch_ok(bid)
        amt = S.q2(dta.get('amount'))
        if amt <= 0:
            raise S.Refused(tr('Enter the service charge collected (above zero).'), problems={'amount': tr('Required.')})
        method = dta.get('method') or 'points'
        if method not in dict(ServiceChargePool.METHOD):
            raise S.Refused(tr('Choose how it is shared.'), problems={'method': tr('Invalid.')})
        p = ServiceChargePool.objects.filter(month=m, branch_id=bid).first() or ServiceChargePool(month=m, branch_id=bid)
        if p.status == 'sent':
            raise S.Refused(tr('This month\'s pool is already sent to payroll.'), status=409)
        p.amount, p.method = amt, method
        pts = dta.get('points') or {}
        for k, v in pts.items():
            if S.q2(v) < 0:
                raise S.Refused(tr('Points cannot be below zero.'), problems={'points': tr('Invalid.')})
        p.points = {str(k): float(S.q2(v)) for k, v in pts.items()}
        p.excluded = [int(x) for x in (dta.get('excluded') or []) if str(x).isdigit()]
        p.note = str(dta.get('note') or '')[:255]
        p.component_code = (str(dta.get('component_code') or p.component_code or 'ZSVC').strip().upper())[:20]
        S.preview_pool(p)
        p.save()
        bnames = dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', 'branch_name'))
        return Response(pool_dict(p, bnames), status=201)


class ServiceSendView(Base):
    need = MANAGE

    def post(self, request, pk):
        p = ServiceChargePool.objects.filter(pk=pk).first()
        if p is None:
            raise S.Refused(tr('Not found.'), status=404)
        self.branch_ok(p.branch_id)
        if p.status == 'sent':
            raise S.Refused(tr('Already sent to payroll.'), status=409)
        S.preview_pool(p)   # worked out again: who is active now
        out = S.send_amounts(p.month, {(s['employee_id'], p.component_code): Decimal(str(s['amount'])) for s in p.shares if s['amount'] > 0}, request.user)
        p.status, p.sent_at = 'sent', timezone.now()
        p.save()
        return Response({'sent': len(p.shares), **out})


# ====================================================================== 33 hourly / daily
class PayBasisView(Base):
    def get(self, request):
        names = {e.pk: S.emp_name(e) for e in self.emps()}
        rows = [{'employee_id': b.employee_id, 'employee': names[b.employee_id], 'basis': b.basis, 'hourly_rate': float(b.hourly_rate),
                 'daily_rate': float(b.daily_rate), 'contract_hours_week': float(b.contract_hours_week) if b.contract_hours_week is not None else None}
                for b in PayBasis.objects.all() if b.employee_id in names]
        return Response({'rows': rows, 'bases': PayBasis.BASIS, 'formula_help': tr(
            'In a salary component formula: worked_hours * hourly_rate (hours from attendance), present_days * daily_rate; is_hourly / is_daily are 1 for '
            'those employees, else 0.')})

    def post(self, request):
        dta = request.data
        e = self.employee(dta.get('employee'))
        basis = dta.get('basis') or 'monthly'
        if basis not in dict(PayBasis.BASIS):
            raise S.Refused(tr('Choose how the employee is paid.'), problems={'basis': tr('Invalid.')})
        hr, dr = S.q2(dta.get('hourly_rate')), S.q2(dta.get('daily_rate'))
        if hr < 0 or dr < 0:
            raise S.Refused(tr('A rate cannot be below zero.'))
        if basis == 'hourly' and hr <= 0:
            raise S.Refused(tr('Enter the hourly rate.'), problems={'hourly_rate': tr('Required.')})
        if basis == 'daily' and dr <= 0:
            raise S.Refused(tr('Enter the daily rate.'), problems={'daily_rate': tr('Required.')})
        hw = S.q2(dta['contract_hours_week']) if dta.get('contract_hours_week') not in (None, '') else None
        if hw is not None and (hw <= 0 or hw > 84):
            raise S.Refused(tr('Contracted hours a week: 1 – 84.'), problems={'contract_hours_week': tr('Invalid.')})
        b, _ = PayBasis.objects.update_or_create(employee_id=e.pk, defaults={'basis': basis, 'hourly_rate': hr, 'daily_rate': dr, 'contract_hours_week': hw})
        return Response({'employee_id': e.pk, 'basis': b.basis, 'hourly_rate': float(b.hourly_rate), 'daily_rate': float(b.daily_rate)}, status=201)


# ====================================================================== 34 GCC pension
def gcc_dict(r):
    return {'id': r.pk, 'country': r.country, 'name': r.name, 'employee_rate': float(r.employee_rate), 'employer_rate': float(r.employer_rate), 'base': r.base,
            'max_base_aed': float(r.max_base_aed) if r.max_base_aed is not None else None, 'active': r.active, 'confirmed': r.confirmed, 'source': r.source,
            'effective_from': d(r.effective_from)}


class GCCView(Base):
    def get(self, request):
        S.gcc_seed()
        emps = [e for e in self.emps() if S.gcc_country(e)]
        return Response({'rows': [gcc_dict(r) for r in GCCPensionRate.objects.all()], 'bases': GCCPensionRate.BASES,
                         'employees': [{'employee_id': e.pk, 'employee': S.emp_name(e), 'country': S.gcc_country(e), **{k: float(v) for k, v in S.gcc_vars(e).items()}}
                                       for e in emps]})

    def post(self, request):
        dta = request.data
        r = GCCPensionRate.objects.filter(country=str(dta.get('country') or '').upper()).first()
        if r is None:
            raise S.Refused(tr('Choose the country.'), problems={'country': tr('Invalid.')})
        for f in ('employee_rate', 'employer_rate'):
            if f in dta:
                v = S.q2(dta[f])
                if v < 0 or v > 50:
                    raise S.Refused(tr('A rate is between 0 and 50 %.'), problems={f: tr('Invalid.')})
                setattr(r, f, v)
        if 'base' in dta:
            if dta['base'] not in dict(GCCPensionRate.BASES):
                raise S.Refused(tr('Choose the wage base.'), problems={'base': tr('Invalid.')})
            r.base = dta['base']
        if 'max_base_aed' in dta:
            r.max_base_aed = S.q2(dta['max_base_aed']) if dta['max_base_aed'] not in (None, '') else None
        if 'confirmed' in dta:
            r.confirmed = dta['confirmed'] in (True, 'true', 1, '1')
        if 'active' in dta:
            on = dta['active'] in (True, 'true', 1, '1')
            if on and not r.confirmed:
                raise S.Refused(tr('Tick "rates checked with GPSSA" first – the payroll then deducts these rates.'), problems={'active': tr('Invalid.')})
            r.active = on
        r.save()
        return Response(gcc_dict(r))


# ====================================================================== 35 ILOE
class ILOEView(Base):
    def get(self, request):
        recs = {r.employee_id: r for r in ILOERecord.objects.all()}
        rows = [S.iloe_row(e, recs.get(e.pk)) for e in self.emps()]
        summ = {k: sum(1 for r in rows if r['flag'] == k) for k in ('missing', 'expired', 'expiring')}
        summ.update({'subscribed': sum(1 for r in rows if r['status'] == 'subscribed'), 'exempt': sum(1 for r in rows if r['status'] == 'exempt'),
                     'employees': len(rows)})
        return Response({'rows': sorted(rows, key=lambda r: ({'missing': 0, 'expired': 1, 'expiring': 2}.get(r['flag'], 3), r['employee'])), 'summary': summ,
                         'statuses': ILOERecord.STATUS, 'exempt_reasons': ILOERecord._meta.get_field('exempt_reason').choices,
                         'note': tr('ILOE: category A for a basic salary up to AED 16,000 (AED 5 a month), B above (AED 10). Not subscribing: AED 400 fine; '
                                    'premiums unpaid: AED 200. The employee subscribes and pays unless the company pays for them.')})

    def post(self, request):
        dta = request.data
        e = self.employee(dta.get('employee'))
        st = dta.get('status') or 'subscribed'
        if st not in dict(ILOERecord.STATUS):
            raise S.Refused(tr('Choose the status.'), problems={'status': tr('Invalid.')})
        reason = dta.get('exempt_reason') or ''
        if st == 'exempt' and not reason:
            raise S.Refused(tr('Choose why the employee is exempt.'), problems={'exempt_reason': tr('Required.')})
        if reason and reason not in dict(ILOERecord._meta.get_field('exempt_reason').choices):
            raise S.Refused(tr('Choose why the employee is exempt.'), problems={'exempt_reason': tr('Invalid.')})
        def dt(k):
            v = dta.get(k)
            if v in (None, ''):
                return None
            try:
                return date.fromisoformat(str(v))
            except ValueError:
                raise S.Refused(tr('Enter a valid date.'), problems={k: tr('Invalid.')})
        sd, ed = dt('start_date'), dt('expiry_date')
        if st == 'subscribed' and not (dta.get('policy_no') or '').strip():
            raise S.Refused(tr('Enter the ILOE policy / certificate number.'), problems={'policy_no': tr('Required.')})
        if sd and ed and ed <= sd:
            raise S.Refused(tr('The expiry is after the start.'), problems={'expiry_date': tr('Invalid.')})
        cat = str(dta.get('category') or '').upper()
        if cat not in ('', 'A', 'B'):
            raise S.Refused(tr('Category A or B.'), problems={'category': tr('Invalid.')})
        r, _ = ILOERecord.objects.update_or_create(employee_id=e.pk, defaults={
            'status': st, 'exempt_reason': reason if st == 'exempt' else '', 'category': cat, 'policy_no': str(dta.get('policy_no') or '')[:60],
            'start_date': sd, 'expiry_date': ed, 'paid_by': dta.get('paid_by') if dta.get('paid_by') in ('employee', 'employer') else 'employee',
            'payment': dta.get('payment') if dta.get('payment') in dict(ILOERecord._meta.get_field('payment').choices) else '',
            'note': str(dta.get('note') or '')[:255]})
        return Response(S.iloe_row(e, r), status=201)


# ====================================================================== 36 Emiratisation / Nafis
class EmiratisationView(Base):
    def get(self, request):
        emps = list(self.emps())
        P = {p.employee_id: p for p in EmiratisationProfile.objects.all()}
        rows = []
        for e in emps:
            p = P.get(e.pk)
            nat = S.is_uae_national(e)
            rows.append({'employee_id': e.pk, 'employee': S.emp_name(e), 'national': nat, 'skill_level': p.skill_level if p else 0,
                         'nafis_registered': bool(p and p.nafis_registered), 'nafis_no': p.nafis_no if p else '', 'salary_support': bool(p and p.salary_support),
                         'support_until': d(p.support_until) if p else '', 'counts_for_quota': p.counts_for_quota if p else True})
        st = EmiratisationSettings.objects.order_by('id').first()
        from .nationalisation import programme
        prog = programme()
        prog = {**prog, 'programme': tr(prog['programme']), 'national': tr(prog['national']), 'nationals': tr(prog['nationals']),
                'air_ticket': {'law': prog['air_ticket']['law'], 'text': tr(prog['air_ticket']['text'])} if prog.get('air_ticket') else None}
        return Response({'programme': prog, 'quota': S.quota(emps if self.c.branches is not None else None), 'rows': sorted(rows, key=lambda r: (not r['national'], r['employee'])),
                         'settings': {'target_pct': float(st.target_pct) if st else 0, 'period_label': st.period_label if st else '',
                                      'deadline': d(st.deadline) if st else '', 'fine_per_head_month': float(st.fine_per_head_month) if st else 0,
                                      'min_nationals': st.min_nationals if st else 0, 'skilled_levels': st.skilled_levels if st else '1,2,3',
                                      'note': st.note if st else ''},
                         'help': tr(programme()['help'])})

    def put(self, request):
        if not (self.c.admin or MANAGE & self.c.codes):
            raise exceptions.PermissionDenied()
        dta = request.data
        st = EmiratisationSettings.objects.order_by('id').first() or EmiratisationSettings()
        if 'target_pct' in dta:
            v = S.q2(dta['target_pct'])
            if v < 0 or v > 100:
                raise S.Refused(tr('The target is 0 – 100 %.'), problems={'target_pct': tr('Invalid.')})
            st.target_pct = v
        if 'fine_per_head_month' in dta:
            v = S.q2(dta['fine_per_head_month'])
            if v < 0:
                raise S.Refused(tr('The amount cannot be below zero.'), problems={'fine_per_head_month': tr('Invalid.')})
            st.fine_per_head_month = v
        if 'deadline' in dta:
            try:
                st.deadline = date.fromisoformat(str(dta['deadline'])) if dta['deadline'] else None
            except ValueError:
                raise S.Refused(tr('Enter a valid date.'), problems={'deadline': tr('Invalid.')})
        if 'skilled_levels' in dta:
            lv = [x for x in str(dta['skilled_levels']).replace(' ', '').split(',') if x]
            if not lv or any(not x.isdigit() or int(x) not in (1, 2, 3, 4, 5) for x in lv):
                raise S.Refused(tr('Skill levels: numbers 1 – 5 separated by commas.'), problems={'skilled_levels': tr('Invalid.')})
            st.skilled_levels = ','.join(lv)
        for f in ('period_label', 'note'):
            if f in dta:
                setattr(st, f, str(dta[f] or '')[:40 if f == 'period_label' else 2000])
        if 'min_nationals' in dta:
            st.min_nationals = max(int(dta['min_nationals'] or 0), 0)
        st.save()
        return self.get(request)

    def post(self, request):
        """the profile of one employee"""
        dta = request.data
        e = self.employee(dta.get('employee'))
        lvl = int(dta.get('skill_level') or 0)
        if lvl not in range(0, 6):
            raise S.Refused(tr('Skill level 1 – 5.'), problems={'skill_level': tr('Invalid.')})
        reg = dta.get('nafis_registered') in (True, 'true', 1, '1')
        sup = dta.get('salary_support') in (True, 'true', 1, '1')
        from .nationalisation import pack as _pack
        if (reg or sup) and _pack() != 'AE':
            raise S.Refused(tr('Nafis is for UAE companies only.'), problems={'nafis_registered': tr('Invalid.')})
        if (reg or sup) and not S.is_uae_national(e):
            raise S.Refused(tr('Nafis is for UAE nationals only.'), problems={'nafis_registered': tr('Invalid.')})
        if sup and not reg:
            raise S.Refused(tr('Salary support needs the Nafis registration.'), problems={'salary_support': tr('Invalid.')})
        try:
            until = date.fromisoformat(str(dta['support_until'])) if dta.get('support_until') else None
        except ValueError:
            raise S.Refused(tr('Enter a valid date.'), problems={'support_until': tr('Invalid.')})
        p, _ = EmiratisationProfile.objects.update_or_create(employee_id=e.pk, defaults={
            'skill_level': lvl, 'nafis_registered': reg, 'nafis_no': str(dta.get('nafis_no') or '')[:40], 'salary_support': sup, 'support_until': until,
            'counts_for_quota': dta.get('counts_for_quota', True) in (True, 'true', 1, '1'), 'note': str(dta.get('note') or '')[:255]})
        return Response({'employee_id': e.pk, 'skill_level': p.skill_level, 'nafis_registered': p.nafis_registered, 'salary_support': p.salary_support},
                        status=201)


# ====================================================================== 37 bulk advance
def ba_dict(b):
    return {'id': b.pk, 'name': b.name, 'mode': b.mode, 'value': float(b.value), 'instalments': b.instalments,
            'max_amount': float(b.max_amount) if b.max_amount is not None else None, 'employee_ids': b.employee_ids, 'lines': b.lines, 'status': b.status,
            'created_at': b.created_at.isoformat() if b.created_at else '',
            'total': float(sum((Decimal(str(x['amount'])) for x in b.lines or [] if not x.get('skipped')), Decimal(0)))}


class AdvancesView(Base):
    def get(self, request):
        return Response({'rows': [ba_dict(b) for b in BulkAdvance.objects.all()[:50]], 'modes': BulkAdvance.MODE})

    def post(self, request):
        """{name, mode, value, instalments, max_amount, employee_ids | all: true | branch} – the draft with the amounts per employee"""
        dta = request.data
        name = str(dta.get('name') or '').strip()[:120]
        if not name:
            raise S.Refused(tr('Give it a name, e.g. Ramadan advance 2027.'), problems={'name': tr('Required.')})
        mode = dta.get('mode') or 'percent'
        if mode not in dict(BulkAdvance.MODE):
            raise S.Refused(tr('Choose how the amount is worked out.'), problems={'mode': tr('Invalid.')})
        val = S.q2(dta.get('value'))
        if val <= 0 or (mode != 'fixed' and val > 100):
            raise S.Refused(tr('Enter the amount, or a % between 0 and 100.'), problems={'value': tr('Invalid.')})
        try:
            n = int(dta.get('instalments')) if dta.get('instalments') not in (None, '') else 1
        except (TypeError, ValueError):
            n = 0
        try:
            from PayrollPlus.lending import settings as lend_settings
            mx = int(lend_settings()['advances'].get('max_instalments') or 24)
        except Exception:   # noqa: BLE001
            mx = 24
        if n < 1 or n > mx:
            raise S.Refused(tr('Instalments: 1 – {0} (Payroll › Loan and advance settings).', mx), problems={'instalments': tr('Invalid.')})
        qs = self.emps()
        if dta.get('branch'):
            qs = qs.filter(emp_branch_id_id=int(dta['branch']))
        if dta.get('all') or dta.get('branch'):
            ids = list(qs.values_list('pk', flat=True))
        else:
            allowed = set(qs.values_list('pk', flat=True))
            ids = [int(x) for x in (dta.get('employee_ids') or []) if str(x).isdigit() and int(x) in allowed]
        b = BulkAdvance(name=name, mode=mode, value=val, instalments=n, max_amount=S.q2(dta['max_amount']) if dta.get('max_amount') not in (None, '') else None,
                        employee_ids=ids, created_by_id=request.user.pk)
        b.lines = S.preview_bulk(b)
        b.save()
        return Response(ba_dict(b), status=201)


class AdvanceActionView(Base):
    need = MANAGE

    def post(self, request, pk, action):
        b = BulkAdvance.objects.filter(pk=pk).first()
        if b is None:
            raise S.Refused(tr('Not found.'), status=404)
        if action == 'make':
            n = S.create_bulk(b, request.user)
            return Response({**ba_dict(b), 'made': n})
        if action == 'cancel':
            if b.status != 'draft':
                raise S.Refused(tr('The advances are made – cancel each one in Payroll › Advance salary.'), status=409)
            b.status = 'cancelled'
            b.save(update_fields=['status'])
            return Response(ba_dict(b))
        raise S.Refused(tr('Unknown action.'))


# ====================================================================== 40 cost to company
class CTCView(Base):
    def get(self, request, emp_id):
        e = self.employee(emp_id)
        return Response(S.ctc(e))


class EmployeesView(Base):
    def get(self, request):
        return Response({'rows': [{'id': e.pk, 'name': S.emp_name(e), 'branch': e.emp_branch_id_id} for e in self.emps().order_by('emp_first_name')]})


# ====================================================================== v1.73.7 payroll categories
CAT_VIEW = VIEW | {'view_salarycomponent', 'view_payrollcategory', 'manage_payrollcategory'}
CAT_MANAGE = {'manage_payrollcategory', 'change_salarycomponent', 'add_salarycomponent'}


def cat_dict(c, used):
    from .categories import FLAGS
    return {'id': c.pk, 'key': c.key, 'name': c.name, 'name_ar': c.name_ar, 'header': c.header, 'header_ar': c.header_ar, 'order': c.order,
            'kind': c.kind, 'system': c.system, 'like': c.like, 'active': c.active, 'items': used.get(c.key, 0),
            **{f: getattr(c, f) for f in FLAGS}}


class CategoriesView(Base):
    """GET the categories (with how many salary items use each); POST a company category."""
    need = CAT_VIEW

    def initial(self, request, *a, **kw):
        APIView.initial(self, request, *a, **kw)
        self.c = ctx(request)
        need = CAT_MANAGE if request.method not in ('GET', 'HEAD', 'OPTIONS') else CAT_VIEW
        if not (self.c.admin or set(need) & self.c.codes):
            raise exceptions.PermissionDenied(tr('You do not have the payroll rights for this.'))

    def get(self, request):
        from django.db.models import Count
        from .categories import ensure, FLAGS
        from .models import PayrollCategory
        ensure()
        used = dict(M('PayrollManagement', 'SalaryComponent').objects.values('payroll_category').annotate(n=Count('id')).values_list('payroll_category', 'n'))
        return Response({'categories': [cat_dict(c, used) for c in PayrollCategory.objects.all()], 'flags': list(FLAGS)})

    def post(self, request):
        import re
        from .categories import SYSTEM, FLAGS, ensure
        from .models import PayrollCategory
        ensure()
        b = request.data
        name = ' '.join(str(b.get('name') or '').split())[:80]
        probs = {}
        if not name:
            probs['name'] = tr('Required.')
        like = str(b.get('like') or '')
        if like not in SYSTEM:
            probs['like'] = tr('Choose the system category it behaves like.')
        key = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:40] or 'category'
        if key in SYSTEM or PayrollCategory.objects.filter(key=key).exists():
            if key in SYSTEM:
                probs['name'] = tr('This is the name of a system category – rename that one instead.')
            else:
                probs['name'] = tr('There is a category with this name already.')
        if PayrollCategory.objects.filter(name__iexact=name).exists():
            probs['name'] = tr('There is a category with this name already.')
        if probs:
            raise S.Refused(tr('Check the marked fields.'), problems=probs)
        base = PayrollCategory.objects.filter(key=like).first()
        c = PayrollCategory(key=key, name=name, name_ar=str(b.get('name_ar') or '')[:80], header=str(b.get('header') or '')[:80],
                            header_ar=str(b.get('header_ar') or '')[:80], order=int(b.get('order') or (base.order + 1 if base else 100)),
                            kind=base.kind if base else SYSTEM[like][1], system=False, like=like, active=True, updated_by_id=request.user.id)
        for f in FLAGS:   # the ticks of the category it behaves like, unless given
            setattr(c, f, bool(b[f]) if f in b else bool(getattr(base, f, False)))
        c.save()
        return Response(cat_dict(c, {}), status=201)


class CategoryOneView(CategoriesView):
    """PATCH names, report header, order, ticks, active (a system category keeps its key and kind); DELETE an unused company one."""

    def _get(self, pk):
        from .models import PayrollCategory
        c = PayrollCategory.objects.filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), status=404)
        return c

    def patch(self, request, pk):
        from .categories import FLAGS
        c = self._get(pk)
        b = request.data
        probs = {}
        if 'name' in b:
            name = ' '.join(str(b.get('name') or '').split())[:80]
            if not name:
                probs['name'] = tr('Required.')
            elif type(c).objects.filter(name__iexact=name).exclude(pk=c.pk).exists():
                probs['name'] = tr('There is a category with this name already.')
            else:
                c.name = name
        for f in ('name_ar', 'header', 'header_ar'):
            if f in b:
                setattr(c, f, str(b.get(f) or '').strip()[:80])
        if 'order' in b:
            try:
                c.order = max(0, min(9999, int(b.get('order'))))
            except (TypeError, ValueError):
                probs['order'] = tr('A whole number.')
        if 'active' in b:
            on = str(b.get('active')).lower() in ('1', 'true')
            if not on and c.key == 'basic':
                probs['active'] = tr('Basic is used by gratuity, leave salary and the payroll – it stays active.')
            else:
                c.active = on
        for f in FLAGS:
            if f in b:
                setattr(c, f, str(b.get(f)).lower() in ('1', 'true'))
        if 'key' in b and b.get('key') != c.key:
            probs['key'] = tr('The key of a category does not change.')
        if 'kind' in b and b.get('kind') != c.kind and c.system:
            probs['kind'] = tr('The type of a system category does not change.')
        if probs:
            raise S.Refused(tr('Check the marked fields.'), problems=probs)
        c.updated_by_id = request.user.id
        c.save()
        n = M('PayrollManagement', 'SalaryComponent').objects.filter(payroll_category=c.key).count()
        return Response(cat_dict(c, {c.key: n}))

    def delete(self, request, pk):
        c = self._get(pk)
        if c.system:
            raise S.Refused(tr('A system category is not deleted – make it inactive instead.'), status=409)
        n = M('PayrollManagement', 'SalaryComponent').objects.filter(payroll_category=c.key).count()
        if n:
            raise S.Refused(tr('{0} salary items use this category – move them to another category first.', n), status=409)
        c.delete()
        return Response(status=204)


class HealthView(Base):
    """v1.73.7 GET ?run=<id> – the payroll health check of the user's employees (and the employees a run left out)"""

    def get(self, request):
        from .health import check
        run = request.query_params.get('run')
        issues = check(list(self.emps().select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id')), int(run) if str(run or '').isdigit() else None)
        counts = {}
        for i in issues:
            counts[i['code']] = counts.get(i['code'], 0) + 1
        return Response({'issues': issues, 'counts': counts, 'employees': self.emps().count()})


class TemplateDefaultsView(Base):
    """v1.73.7 GET / PUT the defaults of a salary template: {defaults: [{component, amount, percent, of_category}]}"""
    need = CAT_VIEW

    def initial(self, request, *a, **kw):
        APIView.initial(self, request, *a, **kw)
        self.c = ctx(request)
        need = {'change_salarycomponent', 'add_salarycomponent', 'change_salarystructure', 'add_salarystructure', 'manage_payrollcategory'} \
            if request.method not in ('GET', 'HEAD', 'OPTIONS') else CAT_VIEW | {'view_salarystructure'}
        if not (self.c.admin or set(need) & self.c.codes):
            raise exceptions.PermissionDenied(tr('You do not have the payroll rights for this.'))

    def get(self, request, pk):
        from .models import TemplateDefault
        return Response({'defaults': [{'component': d.component_id, 'amount': float(d.amount) if d.amount is not None else None,
                                       'percent': float(d.percent) if d.percent is not None else None, 'of_category': d.of_category}
                                      for d in TemplateDefault.objects.filter(structure_id=pk)]})

    def put(self, request, pk):
        from .models import TemplateDefault
        SS = M('PayrollManagement', 'SalaryStructure')
        st = SS.objects.filter(pk=pk).first()
        if st is None:
            raise S.Refused(tr('Not found.'), status=404)
        comps = set(st.components.values_list('pk', flat=True))
        rows, probs = [], {}
        for d in request.data.get('defaults') or []:
            try:
                cid = int(d.get('component'))
            except (TypeError, ValueError):
                continue
            if cid not in comps:
                continue
            amt, pct = d.get('amount'), d.get('percent')
            try:
                amt = Decimal(str(amt)) if amt not in (None, '') else None
                pct = Decimal(str(pct)) if pct not in (None, '') else None
            except Exception:   # noqa: BLE001
                probs[str(cid)] = tr('A number.')
                continue
            if (amt is not None and amt < 0) or (pct is not None and not 0 <= pct <= 1000):
                probs[str(cid)] = tr('0 or more (a percentage up to 1000).')
                continue
            if amt is None and pct is None:
                continue
            rows.append(TemplateDefault(structure_id=st.pk, component_id=cid, amount=None if pct is not None else amt, percent=pct,
                                        of_category=str(d.get('of_category') or 'basic')[:50]))
        if probs:
            raise S.Refused(tr('Check the marked fields.'), problems=probs)
        TemplateDefault.objects.filter(structure_id=st.pk).delete()
        TemplateDefault.objects.bulk_create(rows)
        from .templates import fill   # the template's employees get the defaults in their empty (0) amounts
        fill(st, list(st.employees.values_list('pk', flat=True)))
        return self.get(request, pk)


class RulesCoverageView(Base):
    """v1.73.7 GET – each payroll rule (pay structure) with the employees it applies to (the most specific match), and the
    employees no rule matches"""

    def get(self, request):
        from PayrollManagement.signals import structure_for
        PS = M('PayrollManagement', 'PayStructure')
        by, none = {}, []
        for e in self.emps().select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id'):
            r = structure_for(e)
            name = f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip() + f' ({e.emp_code})'
            if r is None:
                none.append(name)
            else:
                by.setdefault(r.pk, []).append(name)
        rows = []
        for ps in PS.objects.prefetch_related('branch', 'department', 'category', 'designation').order_by('pk'):
            who = [x.branch_name for x in ps.branch.all()] + [x.dept_name for x in ps.department.all()] + \
                  [x.ctgry_title for x in ps.category.all()] + [x.desgntn_job_title for x in ps.designation.all()]
            cyc = f'{ps.cycle_start_day} – {ps.cycle_end_day}' if ps.attendance_cycle_type == 'CUSTOM' and ps.cycle_start_day else tr('Calendar month')
            rows.append({'id': ps.pk, 'for': ', '.join(who) or tr('Every employee'), 'cycle': cyc, 'payday': ps.payday,
                         'days': ps.get_salary_calculation_type_display() + (f' ({ps.fixed_working_days})' if ps.fixed_working_days else ''),
                         'employees': len(by.get(ps.pk, [])), 'names': sorted(by.get(ps.pk, []))[:200]})
        return Response({'rules': rows, 'no_rule': sorted(none)[:500], 'no_rule_count': len(none)})


class EmployeeSalarySetupView(Base):
    """v1.73.8 – New employee › Salary: GET ?template=<id> the template's items with their defaults; POST {employee, template,
    amounts: {component id: amount}} gives the employee the template (its items, the defaults in empty amounts) and the amounts entered."""

    def initial(self, request, *a, **kw):
        APIView.initial(self, request, *a, **kw)
        self.c = ctx(request)
        need = {'add_employeesalarystructure', 'change_employeesalarystructure'} if request.method not in ('GET', 'HEAD', 'OPTIONS') \
            else {'view_employeesalarystructure', 'add_employeesalarystructure', 'add_emp_master'}
        if not (self.c.admin or set(need) & self.c.codes):
            raise exceptions.PermissionDenied(tr('You do not have the payroll rights for this.'))

    def get(self, request):
        from .models import TemplateDefault
        SS = M('PayrollManagement', 'SalaryStructure')
        out = [{'id': s.pk, 'name': s.name} for s in SS.objects.filter(is_active=True).order_by('name')]
        items = []
        tid = request.query_params.get('template')
        if str(tid or '').isdigit():
            st = SS.objects.filter(pk=int(tid)).first()
            if st:
                ds = {d.component_id: d for d in TemplateDefault.objects.filter(structure_id=st.pk)}
                for c in st.components.order_by('component_type', 'name'):
                    d = ds.get(c.pk)
                    items.append({'id': c.pk, 'name': c.name, 'code': c.code, 'type': c.component_type, 'category': c.payroll_category,
                                  'formula': bool((c.formula or '').strip()) and c.component_value_type == 'variable',
                                  'amount': float(d.amount) if d and d.amount is not None else None,
                                  'percent': float(d.percent) if d and d.percent is not None else None})
        return Response({'templates': out, 'items': items})

    def post(self, request):
        from decimal import Decimal, InvalidOperation
        from .templates import fill
        SS = M('PayrollManagement', 'SalaryStructure')
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        b = request.data
        e = self.employee(b.get('employee'))
        st = SS.objects.filter(pk=b.get('template')).first() if str(b.get('template') or '').isdigit() else None
        if st is None:
            raise S.Refused(tr('Choose the salary template.'), problems={'template': tr('Required.')})
        comps = set(st.components.values_list('pk', flat=True))
        amounts, probs = {}, {}
        for k, v in (b.get('amounts') or {}).items():
            if v in (None, ''):
                continue
            try:
                cid, amt = int(k), Decimal(str(v))
            except (TypeError, ValueError, InvalidOperation):
                probs[str(k)] = tr('A number.')
                continue
            if cid not in comps:
                continue
            if amt < 0:
                probs[str(k)] = tr('0 or more.')
                continue
            amounts[cid] = amt.quantize(Decimal('0.01'))
        if probs:
            raise S.Refused(tr('Check the marked fields.'), problems=probs)
        for cid, amt in amounts.items():   # the amounts entered first, so the % defaults are worked out on the Basic entered
            ESS.objects.update_or_create(employee=e, component_id=cid, defaults={'amount': amt, 'is_active': True})
        st.employees.add(e)   # the template's other items (0) – then its defaults fill the empty ones
        fill(st, [e.pk])
        rows = ESS.objects.filter(employee=e, is_active=True).select_related('component')
        gross = sum((Decimal(str(r.amount or 0)) for r in rows if r.component.component_type == 'addition'), Decimal('0'))
        return Response({'employee': e.pk, 'template': st.name, 'gross': float(gross),
                         'lines': [{'item': r.component.name, 'amount': float(r.amount or 0)} for r in rows]}, status=201)
