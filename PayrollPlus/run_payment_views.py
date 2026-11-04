# v1.62.11 (item 227, scope additions 03/11/2026): new module – API of Payroll run › Payments and Payroll › Payments › File layouts
"""v1.62.11 (item 227) – mounted at /payroll-plus/.

GET    api/run-payments/<run>/                 the run's net pay by method (totals per method and per paying account, the lines)
POST   api/run-payments/<run>/file/            {method, account, branch, kind (file|pdf|xlsx), cheque_start, cheque_end, preview} → the file
POST   api/run-payments/<run>/mark/            {method, payslips | all, account, branch, paid_on, reference, unmark}
GET    api/payment-methods/                    built-in and company methods        POST {name, employees}  PATCH / DELETE <code>/
GET    api/payment-layouts/                    built-in + company layouts, field catalogue    POST {copy_of | layout}
GET    api/payment-layouts/<code>/             PATCH / DELETE (company layouts only)
POST   api/payment-layouts/preview/            {run, layout (code or the layout being designed)} → first lines, totals, checks
POST   api/payment-layouts/check-sample/       multipart {file, layout (code) | layout_json} → the differences
"""
import base64
import datetime
import json
import re

from django.apps import apps
from rest_framework.response import Response

from AccessControl.access import run_in_branches
from Translations.i18n import tr

from . import bankfiles as B
from . import layouts as LY
from . import run_payments as RP
from . import services as S
from .bankfile_views import READ, SET, FILE, _B

M = apps.get_model
MARK = {'change_payrollrun', 'add_wps', 'change_wps', 'pay_exitsettlement'}
KIND_NAMES = {'header': 'Header', 'detail': 'Detail (one per employee)', 'trailer': 'Trailer / control'}


class _R(_B):
    def run(self, pk):
        r = M('PayrollManagement', 'PayrollRun').objects.filter(pk=pk).first()
        if r is None or not run_in_branches(self.c.branches, r):
            raise S.Refused(tr('Not found.'), status=404)
        return r

    def br(self):
        return None if self.every_branch() else list(self.c.branches)


def _lay_public(l):
    x = dict(l)
    meta = B.LAYOUTS.get(l.get('code'))
    x['builtin'] = bool(l.get('builtin'))
    x['state'] = 'company' if not x['builtin'] else ('to_confirm' if l.get('code') in ('generic_csv',) else (meta['state'] if meta else 'base'))
    x['state_text'] = tr({'verified': 'Verified', 'to_confirm': 'To be confirmed', 'company': 'Company layout', 'base': 'Built-in list'}[x['state']])
    x['sources'] = meta['sources'] if meta else (RP.TRANSFER_NOTES.get('cbd', ('', []))[1] if l.get('code') == 'generic_csv' else [])
    x['method_name'] = tr(dict(RP.BUILTIN).get(l.get('method'), 'Other method'))
    x['name_t'] = tr(l.get('name') or '')
    x['records_n'] = len(l.get('records') or [])
    return x


class RunPaymentsView(_R):
    def get(self, request, pk):
        self.need(READ, 'You do not have the right to see the bank files.')
        run = self.run(pk)
        s = RP.summary(run, self.br())
        names = {m['code']: tr(m['name']) for m in RP.all_methods()}
        for g in s['methods']:
            g['name'] = names.get(g['method'], g['method'])
        for a in s['accounts']:
            a['name'] = tr(a['name']); a['agent'] = tr(a['agent'])
        layouts = [{'code': l['code'], 'name': tr(l['name']), 'method': l['method'], 'file_type': l.get('file_type')} for l in LY.all_layouts()]
        PR = M('PayrollManagement', 'PayrollRun')
        runs = [{'id': r.pk, 'name': r.name or f'{r.month:02d}/{r.year}', 'month': r.month, 'year': r.year, 'status': r.status}
                for r in PR.objects.order_by('-year', '-month', '-pk')[:40] if run_in_branches(self.c.branches, r)][:24]
        branches = sorted({(l['branch_id'], l['branch']) for l in s['lines'] if l['branch_id']}, key=lambda x: x[1])
        return Response(dict(s, run={'id': run.pk, 'name': run.name, 'month': run.month, 'year': run.year, 'status': run.status,
                                     'payment_date': run.payment_date}, all_methods=[dict(m, name=tr(m['name'])) for m in RP.all_methods()],
                             layouts=layouts, runs=runs, branches=[{'id': b, 'name': n} for b, n in branches],
                             can_mark=self.can(MARK), can_file=self.can(FILE), split_note=tr('A salary split over two methods (the employee’s Bank tab › Salary split) shows as two lines: the main method and the second method.')))   # v1.63.0 (item 27)


def _sel(self, run, d):
    method = str(d.get('method') or '')
    if method not in {m['code'] for m in RP.all_methods()}:
        raise S.Refused(tr('Choose the payment method.'), problems={'method': tr('Invalid.')})
    acc = d.get('account')
    acc = int(acc) if str(acc or '').isdigit() else None
    branch = int(d['branch']) if str(d.get('branch') or '').isdigit() else None
    return method, acc, branch


class RunPaymentFileView(_R):
    def post(self, request, pk):
        self.need(FILE, 'You do not have the right to make the WPS file.')
        run = self.run(pk)
        d = request.data or {}
        method, acc, branch = _sel(self, run, d)
        preview = d.get('preview') in (True, 'true', '1', 1)
        rows = B.profiles()
        prof = next((p for p in rows if p['id'] == acc), None) if acc else None
        if acc and (prof is None or not self.mine(prof)):
            raise S.Refused(tr('Not found.'), status=404)
        ls = RP.method_lines(run, method, self.br(), account=acc if method in ('wps', 'bank_transfer') else None, branch=branch)
        mname = next((tr(m['name']) for m in RP.all_methods() if m['code'] == method), method)
        out = {'method': method, 'method_name': mname, 'employees': len(ls), 'note': ''}
        if method == 'wps':
            br = list((prof or {}).get('branch_ids') or [])
            if not br:   # the company default / no set-up pays the branches no other WPS set-up pays
                others = {b for p in rows if p.get('active', True) and (p.get('method') or 'wps') == 'wps' and p['id'] != acc for b in p.get('branch_ids') or []}
                if others:
                    br = [b for b in M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', flat=True) if b not in others]
            if self.br() is not None:
                br = [b for b in (br or self.br()) if b in set(self.br())]
            all_in = RP.summary(run, br or None)['lines']
            wps_emp = [l['employee_id'] for l in all_in if l['method'] == 'wps']
            emp = None if len(wps_emp) == len(all_in) else wps_emp
            res = B.make(run, prof or {'id': None, 'agent': 'cbuae'}, branch_ids=br, employee_ids=emp, justifications=d.get('justifications') or {})
            content = res['content'] if isinstance(res['content'], bytes) else res['content'].encode('utf-8')
            out.update(file_name=res['file_name'], mime=res['mime'], preview_lines=(res.get('preview_lines') or [])[:300], checks=res['checks'],
                       note=res['note'], state=res['state'], layout_name=tr(res['layout_name']), warnings=res.get('warnings'),
                       skipped=[dict(x, reason=tr(x.get('reason') or '')) for x in res.get('skipped') or []], summary=res.get('summary'))
            if not preview:
                B.log_file({'profile': acc, 'agent': res['agent'], 'layout': res['layout'], 'run': run.pk, 'file_name': res['file_name'],
                            'employees': (res.get('summary') or {}).get('employees'), 'total': (res.get('summary') or {}).get('total'),
                            'accounts': res.get('accounts') or {}, 'by': request.user.username, 'at': datetime.datetime.now().isoformat(timespec='seconds')})
        else:
            if not ls:
                raise S.Refused(tr('No employee to pay by this method in the run.'))
            nums = {}
            if method == 'cheque':
                st = str(d.get('cheque_start') or '').strip()
                if preview and st.isdigit():   # a preview shows the numbers without keeping them
                    nums = {l['payslip']: str(int(st) + i).zfill(len(st)) for i, l in enumerate(ls)}
                else:
                    nums = RP.cheque_numbers(run, ls, None if preview else d.get('cheque_start'), d.get('cheque_end'))
            bname = next((l['branch'] for l in ls if l['branch_id'] == branch), '') if branch else ''
            kind = 'xlsx' if d.get('kind') == 'xlsx' else None
            layout = d.get('layout') if method in ('cash',) and d.get('layout') in LY.BUILTIN else None
            name, content, mime, prev, checks, lay = RP.payment_file(run, method, ls, prof, layout=layout, branch_name=bname, method_name=mname, nums=nums, kind=kind)
            out.update(file_name=name, mime=mime, preview_lines=prev[:300], checks=checks, layout_name=tr(lay['name']),
                       state='company' if not lay.get('builtin') else ('to_confirm' if lay.get('code') == 'generic_csv' else 'base'),
                       summary={'employees': len(ls), 'total': f"{sum(float(l['amount']) for l in ls):.2f}", 'currency': 'AED'})
            if method == 'bank_transfer' and (lay.get('code') == 'generic_csv'):
                out['note'] = tr(RP.TRANSFER_TBC)
        if not preview:
            out['content_b64'] = base64.b64encode(content).decode('ascii')
        return Response(out)


class RunPaymentMarkView(_R):
    def post(self, request, pk):
        self.need(MARK, 'You do not have the right to mark salaries paid.')
        run = self.run(pk)
        d = request.data or {}
        method, acc, branch = _sel(self, run, d)
        ls = RP.method_lines(run, method, self.br(), account=acc if method in ('wps', 'bank_transfer') and 'account' in d else None, branch=branch)
        if not d.get('all'):
            want = {int(x) for x in d.get('payslips') or [] if str(x).isdigit()}
            ls = [l for l in ls if l['payslip'] in want]
        if not ls:
            raise S.Refused(tr('Choose the employees.'), problems={'payslips': tr('Required.')})
        unmark = d.get('unmark') in (True, 'true', 1, '1')
        on = str(d.get('paid_on') or '')[:10]
        if not unmark:
            try:
                datetime.date.fromisoformat(on)
            except ValueError:
                raise S.Refused(tr('Enter the date paid.'), problems={'paid_on': tr('Required.')})
        n = RP.mark(run, [(l['payslip'], method) for l in ls], on, re.sub(r'[\r\n\t]+', ' ', str(d.get('reference') or ''))[:60], request.user, unmark=unmark)
        return Response({'marked': n, 'unmark': unmark})


# ------------------------------------------------------------------ company methods
class PaymentMethodsView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see the bank files.')
        return Response([dict(m, name=tr(m['name'])) for m in RP.all_methods()])

    def post(self, request):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        d = request.data or {}
        name = str(d.get('name') or '').strip()[:40]
        if not name:
            raise S.Refused(tr('Check the marked fields.'), problems={'name': tr('Enter a name.')})

        def add(rows, v):
            code = 'x_' + (re.sub(r'[^a-z0-9]+', '', name.lower())[:8] or 'm') + str(int(v.get('next') or 0) + 1)
            v['next'] = int(v.get('next') or 0) + 1
            if any(r['name'].lower() == name.lower() for r in rows) or name.lower() in {n.lower() for _c, n in RP.BUILTIN}:
                raise S.Refused(tr('Check the marked fields.'), problems={'name': tr('This method exists already.')})
            m = {'code': code[:14], 'name': name, 'employees': _emps(self, d.get('employees')), 'active': True}
            rows.append(m)
            return rows, m
        return Response(RP.save_methods(add), status=201)


def _emps(view, raw):
    ids = [int(x) for x in (raw if isinstance(raw, list) else str(raw or '').split(',')) if str(x).strip().isdigit()]
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.filter(pk__in=ids)
    if not view.every_branch():
        qs = qs.filter(emp_branch_id__in=view.c.branches)
    return sorted(qs.values_list('pk', flat=True))


class PaymentMethodView(_B):
    def get(self, request, code):
        self.need(READ, 'You do not have the right to see the bank files.')
        m = next((x for x in RP.methods() if x['code'] == code), None)
        if m is None:
            raise S.Refused(tr('Not found.'), status=404)
        return Response(m)

    def patch(self, request, code):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        d = request.data or {}

        def upd(rows, v):
            m = next((x for x in rows if x['code'] == code), None)
            if m is None:
                raise S.Refused(tr('Not found.'), status=404)
            if 'name' in d and str(d['name']).strip():
                m['name'] = str(d['name']).strip()[:40]
            if 'employees' in d:
                m['employees'] = _emps(self, d.get('employees'))
            if 'active' in d:
                m['active'] = bool(d['active'])
            return rows, m
        return Response(RP.save_methods(upd))

    def delete(self, request, code):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        RP.save_methods(lambda rows, v: ([r for r in rows if r['code'] != code], None))
        return Response(status=204)


# ------------------------------------------------------------------ layouts
class PaymentLayoutsView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see the bank files.')
        cat = [{'key': k, 'label': tr(lbl), 'level': lvl, 'type': typ} for k, lbl, lvl, typ in LY.CATALOGUE]
        return Response({'layouts': [_lay_public(l) for l in LY.all_layouts()], 'catalogue': cat,
                         'methods': [{'code': c, 'name': tr(n)} for c, n in RP.BUILTIN] + [{'code': 'other', 'name': tr('Other method')}],
                         'file_types': [{'code': c, 'name': tr(n)} for c, n in (('delimited', 'Delimited text'), ('fixed', 'Fixed-width text'), ('csv', 'CSV'),
                                                                                   ('xlsx', 'Excel'), ('pdf', 'PDF list'))],
                         'kinds': [{'code': k, 'name': tr(v)} for k, v in KIND_NAMES.items()],
                         'placeholders': ['{company_code}', '{company}', '{employer_id}', '{bank}', '{account}', '{month}', '{year}', '{mmyyyy}',
                                          '{yyyymm}', '{run}', '{date:%Y%m%d}', '{now:%y%m%d%H%M%S}', '{branch}', '{estb_id}', '{method}'],
                         'can_set': self.can(SET)})

    def post(self, request):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        d = request.data or {}
        if d.get('copy_of'):
            base = LY.copy_of(str(d['copy_of']), d.get('name'))
            if base is None:
                raise S.Refused(tr('Not found.'), status=404)
            body = base
        else:
            body = d.get('layout') or d
        v, errs = LY.clean(body)
        if errs:
            raise S.Refused(tr('Check the marked fields.'), problems=errs)

        def add(rows, o):
            o['next'] = int(o.get('next') or 0) + 1
            v['code'] = f"c{o['next']}_" + (re.sub(r'[^a-z0-9]+', '_', v['name'].lower()).strip('_')[:20] or 'layout')
            v['created_by'] = request.user.username
            v['created_at'] = datetime.datetime.now().isoformat(timespec='seconds')
            rows.append(v)
            return rows, v
        return Response(_lay_public(LY.save_layouts(add)), status=201)


class PaymentLayoutView(_B):
    def get(self, request, code):
        self.need(READ, 'You do not have the right to see the bank files.')
        l = LY.get(code)
        if l is None:
            raise S.Refused(tr('Not found.'), status=404)
        return Response(_lay_public(l))

    def patch(self, request, code):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        if code in LY.BUILTIN:
            raise S.Refused(tr('A built-in layout cannot be changed – make a copy.'), status=400)
        d = request.data or {}

        def upd(rows, o):
            old = next((r for r in rows if r['code'] == code), None)
            if old is None:
                raise S.Refused(tr('Not found.'), status=404)
            v, errs = LY.clean(d.get('layout') or d, old)
            if errs:
                raise S.Refused(tr('Check the marked fields.'), problems=errs)
            v['code'] = code
            v['changed_by'] = request.user.username
            return [v if r['code'] == code else r for r in rows], v
        return Response(_lay_public(LY.save_layouts(upd)))

    put = patch

    def delete(self, request, code):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        if code in LY.BUILTIN:
            raise S.Refused(tr('A built-in layout cannot be deleted.'), status=400)
        used = [p['name'] for p in B.profiles() if p.get('layout') == code]
        if used:
            raise S.Refused(tr('The layout is used by the set-up "{0}".', used[0]))
        LY.save_layouts(lambda rows, o: ([r for r in rows if r['code'] != code], None))
        return Response(status=204)


def _layout_from(d):
    raw = d.get('layout_json') or d.get('layout')
    if isinstance(raw, str) and raw.strip().startswith('{'):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        v, errs = LY.clean(raw)
        if errs:
            raise S.Refused(tr('Check the marked fields.'), problems=errs)
        v['code'] = raw.get('code') or 'draft'
        return v
    lay = LY.get(str(raw or ''))
    if lay is None:
        raise S.Refused(tr('Choose a file layout from the list.'), problems={'layout': tr('Required.')})
    return lay


class PaymentLayoutPreviewView(_R):
    def post(self, request):
        self.need(FILE, 'You do not have the right to make the WPS file.')
        d = request.data or {}
        lay = _layout_from(d)
        run = self.run(d.get('run') if str(d.get('run') or '').isdigit() else 0)
        note = ''
        if lay.get('source') in ('wps', 'ksa'):
            res = B.make(run, {'id': None, 'agent': 'cbuae', 'options': {'evp': True}}, layout=lay if not lay.get('builtin') else lay['code'],
                         branch_ids=self.br() or [])
            prev, checks, summ = res.get('preview_lines') or [], res['checks'], res.get('summary') or {}
            fname, warn = res['file_name'], res.get('warnings') or []
        else:
            method = lay.get('method')
            all_l = RP.summary(run, self.br())['lines']
            ls = [l for l in all_l if l['state'] == 'ready' and (l['method'] == method or (method == 'other' and l['method'].startswith('x_')))]
            if not ls:
                ls = [l for l in all_l if l['state'] == 'ready']
                note = tr('No employee is paid by this method in the run – the preview uses all employees of the run as an example.')
            fname, _c, _m, prev, checks, _l = RP.payment_file(run, method, ls, None, layout=lay, method_name=tr(lay.get('name') or ''))
            summ = {'employees': len(ls), 'total': f"{sum(float(l['amount']) for l in ls):.2f}", 'currency': 'AED'}
            warn = []
        return Response({'file_name': fname, 'preview_lines': prev[:60], 'lines': len(prev), 'checks': checks, 'summary': summ, 'note': note, 'warnings': warn})


class PaymentLayoutSampleView(_B):
    def post(self, request):
        self.need(READ, 'You do not have the right to see the bank files.')
        f = request.FILES.get('file')
        if f is None:
            raise S.Refused(tr('Choose the bank’s sample file.'), problems={'file': tr('Required.')})
        if f.size > 5 * 1024 * 1024:
            raise S.Refused(tr('The sample file is larger than 5 MB.'))
        lay = _layout_from(request.data)
        res = LY.compare(lay, f.read(), f.name)
        res['file'] = f.name
        return Response(res)
