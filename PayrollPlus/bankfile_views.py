# v1.62.11 (item 227): new module – API of Payroll › Bank file layouts
"""v1.62.11 (item 227) – bank salary file layouts, mounted at /payroll-plus/.

GET    api/bank-files/                      layouts, banks / exchange houses with their state and sources, the company's set-ups,
                                            branches, recent payroll runs
GET    api/bank-files/profiles/             the set-ups                                  POST {name, agent, branch_ids, employer_reference,
GET    api/bank-files/profiles/<id>/        one set-up                                         account, active, evp, mohre_columns,
PATCH  api/bank-files/profiles/<id>/                                                           resident_default, dest_bank, estb_id, mol_estb_id}
DELETE api/bank-files/profiles/<id>/
POST   api/bank-files/preview/              {run, profile | agent, justifications} → the file's lines, summary, warnings, checks
POST   api/bank-files/download/             the same + file_name, mime, content_b64 (logged on the run like the WPS screen)
"""
import base64
import datetime
import re

from django.apps import apps
from rest_framework.response import Response

from AccessControl.access import run_in_branches
from Translations.i18n import tr

from . import bankfiles as B
from . import services as S
from .views import Base

M = apps.get_model
READ = {'view_wps', 'export_wps', 'add_wps', 'change_wps', 'view_payrollrun', 'change_payrollrun', 'change_payrollplussettings'}
SET = {'change_payrollplussettings', 'change_payrollrun', 'add_wps', 'change_wps'}
FILE = {'view_wps', 'export_wps', 'add_wps', 'change_payrollrun'}
FLAT = ('evp', 'mohre_columns', 'resident_default', 'dest_bank', 'estb_id', 'mol_estb_id', 'non_residents')


class _B(Base):
    def need(self, codes, msg):
        if not self.can(codes):
            raise S.Refused(tr(msg), status=403)

    def handle_exception(self, exc):
        if isinstance(exc, B.FileError):
            return Response({'detail': tr(exc.message), 'errors': exc.problems} if exc.problems else {'detail': tr(exc.message)}, status=400)
        return super().handle_exception(exc)

    def branches(self):
        Br = M('OrganisationManager', 'brnch_mstr')
        qs = Br.objects.all().order_by('branch_name')
        if self.c.branches is not None:
            qs = qs.filter(pk__in=self.c.branches)
        return [{'id': b.pk, 'name': b.branch_name, 'country': B.branch_country(b.pk) or B.company_country()} for b in qs]

    def every_branch(self):
        """unrestricted, or the user's branches are all the company's branches"""
        if self.c.branches is None:
            return True
        return set(M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', flat=True)) <= set(self.c.branches)

    def mine(self, p):
        """a set-up the user may see: all its branches are the user's (a company default only for a user of every branch)"""
        if self.every_branch():
            return True
        br = p.get('branch_ids') or []
        return bool(br) and set(br) <= set(self.c.branches)


def _tl(d, keys):
    """the display texts of a registry entry in the reader's language (the source titles stay as published)"""
    x = dict(d)
    for k in keys:
        v = x.get(k)
        if isinstance(v, str) and v:
            x[k] = tr(v)
        elif isinstance(v, list):
            x[k] = [tr(i) if isinstance(i, str) else i for i in v]
    if isinstance(x.get('records'), list):
        x['records'] = [_tl(r, ('type', 'per', 'fields')) for r in x['records']]
    return x


LAYOUT_TEXT = ('name', 'summary', 'format', 'file_name', 'header', 'trailer', 'open', 'separator')
AGENT_TEXT = ('name', 'note', 'state_text', 'layout_name', 'fallback')


def flat(p):
    x = {k: v for k, v in p.items() if k != 'options'}
    for k in FLAT:
        x[k] = (p.get('options') or {}).get(k, {'evp': False, 'mohre_columns': True, 'resident_default': 'Yes', 'non_residents': []}.get(k, ''))
    from . import layouts as LY, run_payments as RP
    x['method'] = p.get('method') or 'wps'
    x['method_name'] = tr(next((m['name'] for m in RP.all_methods() if m['code'] == x['method']), x['method']))
    x['layout_choice'] = p.get('layout') or ''
    a = B.AGENT.get(p.get('agent')) or B.AGENT['cbuae']
    if x['method'] != 'wps' or p.get('layout'):
        code = p.get('layout') or LY.DEFAULT_FOR.get(x['method'] if x['method'] in LY.DEFAULT_FOR else 'other')
        lay = LY.get(code) or {}
        meta = B.LAYOUTS.get(code)
        st = (a['state'] if code == a['layout'] else meta['state']) if meta else 'company'
        if x['method'] == 'bank_transfer' and code == 'generic_csv':
            st = 'to_confirm'
        x.update(agent_name=tr(a['name']) if p.get('agent') else '–', layout=code, layout_name=tr(lay.get('name') or code), state=st,
                 state_text=tr({'verified': 'Verified', 'company': 'Company layout'}.get(st, 'Layout to be confirmed')))
        x.update({k: x[k] for k in ()})
        Br = M('OrganisationManager', 'brnch_mstr')
        names = dict(Br.objects.filter(pk__in=p.get('branch_ids') or []).values_list('pk', 'branch_name'))
        x['branch_names'] = [names.get(b, str(b)) for b in p.get('branch_ids') or []]
        return x
    x.update(agent_name=tr(a['name']), layout=a['layout'], layout_name=tr(B.LAYOUTS[a['layout']]['name']), state=a['state'],
             state_text=tr('Verified' if a['state'] == 'verified' else 'Own layout to be confirmed' if a['layout'] == 'cbuae_sif' else 'Layout to be confirmed'))
    Br = M('OrganisationManager', 'brnch_mstr')
    names = dict(Br.objects.filter(pk__in=p.get('branch_ids') or []).values_list('pk', 'branch_name'))
    x['branch_names'] = [names.get(b, str(b)) for b in p.get('branch_ids') or []]
    return x


class BankFilesView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see the bank files.')
        PR = M('PayrollManagement', 'PayrollRun')
        runs = []
        for r in PR.objects.order_by('-year', '-month', '-pk').prefetch_related('branch')[:60]:
            if not run_in_branches(self.c.branches, r):
                continue
            runs.append({'id': r.pk, 'name': r.name or f'{r.month:02d}/{r.year}', 'month': r.month, 'year': r.year, 'status': r.status,
                         'payslips': r.payslips.count()})
            if len(runs) >= 24:
                break
        return Response({
            'layouts': [_tl(B.layout_public(k), LAYOUT_TEXT) for k in B.LAYOUTS],
            'agents': [_tl(B.agent_public(a), AGENT_TEXT) for a in B.AGENTS],
            'all_agents': _tl(B.ALL_AGENTS_NOTE, ('text',)), 'fallback_note': tr(B.FALLBACK_NOTE),
            'profiles': [flat(p) for p in B.profiles() if self.mine(p)],
            'branches': self.branches(), 'runs': runs, 'company_country': B.company_country(),
            'can_set': self.can(SET), 'can_file': self.can(FILE), 'checked': B.CHECKED,
            'methods': [{'code': m['code'], 'name': tr(m['name'])} for m in __import__('PayrollPlus.run_payments', fromlist=['x']).all_methods()],
            'payment_layouts': [{'code': l['code'], 'name': tr(l['name']), 'method': l['method'], 'builtin': bool(l.get('builtin'))}
                                for l in __import__('PayrollPlus.layouts', fromlist=['x']).all_layouts()],
        })


def _clean_profile(view, d, rows, pid=None):
    errs = {}
    old = next((p for p in rows if p['id'] == pid), {}) if pid else {}
    v = dict(old)
    v['options'] = dict(old.get('options') or {})
    if 'name' in d or not pid:
        v['name'] = str(d.get('name') or '').strip()[:80]
        if not v['name']:
            errs['name'] = tr('Enter a name.')
    if 'method' in d or not pid:   # v1.62.11 (item 227 scope addition): the payment method the set-up pays
        from . import run_payments as RP
        v['method'] = str(d.get('method') or 'wps')
        if v['method'] not in {m['code'] for m in RP.all_methods()}:
            errs['method'] = tr('Choose the payment method.')
    v.setdefault('method', 'wps')
    if 'agent' in d or not pid:
        v['agent'] = str(d.get('agent') or ('cbuae' if v['method'] == 'wps' else ''))
        if v['method'] in ('wps', 'bank_transfer') and v['agent'] not in B.AGENT:
            errs['agent'] = tr('Choose the bank or exchange house.')
        if v['agent'] and v['agent'] not in B.AGENT:
            errs['agent'] = tr('Choose the bank or exchange house.')
    if 'layout' in d or not pid:   # the file layout of this paying account (empty = the bank's verified one / the method's default)
        from . import layouts as LY
        lc = str(d.get('layout') or '')
        lay = LY.get(lc) if lc else None
        if lc and lay is None:
            errs['layout'] = tr('Choose a file layout from the list.')
        elif lay is not None and lay.get('method') != ('other' if v['method'] not in ('wps', 'bank_transfer', 'cheque', 'cash') else v['method']):
            errs['layout'] = tr('This file layout is for another payment method.')
        v['layout'] = lc
    if 'branch_ids' in d or not pid:
        raw = d.get('branch_ids') or []
        try:
            br = sorted({int(x) for x in (raw if isinstance(raw, list) else [raw])})
        except (TypeError, ValueError):
            br, errs['branch_ids'] = [], tr('Invalid.')
        ok = set(M('OrganisationManager', 'brnch_mstr').objects.filter(pk__in=br).values_list('pk', flat=True))
        if set(br) - ok:
            errs['branch_ids'] = tr('Invalid.')
        if not view.every_branch() and (not br or set(br) - set(view.c.branches)):
            errs['branch_ids'] = tr('Choose your own branches.')
        v['branch_ids'] = br
    for k in ('employer_reference', 'account'):
        if k in d or not pid:
            v[k] = re.sub(r'[,\r\n\t"]+', ' ', str(d.get(k) or '')).strip()[:35 if k == 'employer_reference' else 34]
    if 'active' in d or not pid:
        v['active'] = d.get('active', True) not in (False, 'false', 0, '0')
    o = v['options']
    if 'evp' in d:
        o['evp'] = bool(d.get('evp')) and d.get('evp') not in ('false', '0')
    if 'mohre_columns' in d:
        o['mohre_columns'] = bool(d.get('mohre_columns')) and d.get('mohre_columns') not in ('false', '0')
    if 'resident_default' in d:
        o['resident_default'] = 'No' if str(d.get('resident_default')) == 'No' else 'Yes'
    if 'non_residents' in d:
        nr = d.get('non_residents') or []
        o['non_residents'] = [str(x).strip() for x in (nr if isinstance(nr, list) else str(nr).split(',')) if str(x).strip()][:500]
    for k, rx, msg in (('dest_bank', r'[A-Za-z]{4}', 'Four letters (SARIE bank ID), e.g. RJHI.'), ('estb_id', r'\d{1,10}', 'Up to 10 digits.'),
                       ('mol_estb_id', r'[0-9]{1,3}-?[0-9]{1,12}', 'Labour office and number, e.g. 1-123456.')):
        if k in d:
            val = str(d.get(k) or '').strip()
            if val and not re.fullmatch(rx, val):
                errs[k] = tr(msg)
            o[k] = val.upper() if k == 'dest_bank' else val
    a = B.AGENT.get(v.get('agent')) if v.get('method', 'wps') == 'wps' else None
    if a and not errs.get('branch_ids'):
        want = B.LAYOUTS[a['layout']]['country']
        cc = [B.branch_country(b) or B.company_country() for b in v['branch_ids']] or [B.company_country()]
        if any(c and c != want for c in cc):
            errs['agent'] = tr('This layout is for {0} – the chosen branches are elsewhere.', 'Saudi Arabia' if want == 'SA' else 'the UAE')
        if a['layout'] == 'mudad_wages' and v.get('account') and not re.fullmatch(r'SA[0-9A-Z]{22}', v['account'].replace(' ', '').upper()):
            errs['account'] = tr('A Saudi IBAN: SA and 22 more characters.')
    if v.get('active') and not errs:
        for p in rows:
            if p['id'] == pid or not p.get('active', True) or (p.get('method') or 'wps') != v.get('method', 'wps'):
                continue
            both = set(p.get('branch_ids') or []) & set(v['branch_ids'])
            if both or (not p.get('branch_ids') and not v['branch_ids']):
                errs['branch_ids'] = tr('Already in the set-up "{0}".', p.get('name')) if both else tr('There is already a company default set-up ("{0}").', p.get('name'))
                break
    if errs:
        raise S.Refused(tr('Check the marked fields.'), problems=errs)
    return v


class BankFileProfilesView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see the bank files.')
        return Response([flat(p) for p in B.profiles() if self.mine(p)])

    def post(self, request):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        d = request.data or {}
        u = request.user

        def add(rows, v):
            p = _clean_profile(self, d, rows)
            v['next_id'] = int(v.get('next_id') or (max([r['id'] for r in rows] or [0]))) + 1
            p.update(id=v['next_id'], created_by=getattr(u, 'username', ''), created_at=datetime.datetime.now().isoformat(timespec='seconds'))
            rows.append(p)
            return rows, p
        return Response(flat(B.save_profiles(add)), status=201)


class BankFileProfileView(_B):
    def _get(self, pk):
        p = next((x for x in B.profiles() if x['id'] == pk), None)
        if p is None or not self.mine(p):
            raise S.Refused(tr('Not found.'), status=404)
        return p

    def get(self, request, pk):
        self.need(READ, 'You do not have the right to see the bank files.')
        return Response(flat(self._get(pk)))

    def patch(self, request, pk):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        self._get(pk)
        d = request.data or {}

        def upd(rows, v):
            p = _clean_profile(self, d, rows, pid=pk)
            p.update(changed_by=getattr(request.user, 'username', ''), changed_at=datetime.datetime.now().isoformat(timespec='seconds'))
            return [p if r['id'] == pk else r for r in rows], p
        return Response(flat(B.save_profiles(upd)))

    put = patch

    def delete(self, request, pk):
        self.need(SET, 'You do not have the right to change the bank file set-ups.')
        self._get(pk)
        B.save_profiles(lambda rows, v: ([r for r in rows if r['id'] != pk], None))
        return Response(status=204)


class _FileBase(_B):
    def made(self, request):
        self.need(FILE, 'You do not have the right to make the WPS file.')
        d = request.data or {}
        PR = M('PayrollManagement', 'PayrollRun')
        run = PR.objects.filter(pk=d.get('run')).first() if str(d.get('run') or '').isdigit() else None
        if run is None or not run_in_branches(self.c.branches, run):
            raise S.Refused(tr('Choose the payroll run.'), problems={'run': tr('Required.')})
        rows = B.profiles()
        prof = None
        if d.get('profile'):
            prof = next((p for p in rows if str(p['id']) == str(d.get('profile'))), None)
            if prof is None or not self.mine(prof):
                raise S.Refused(tr('Not found.'), status=404)
        else:
            ag = str(d.get('agent') or 'cbuae')
            if ag not in B.AGENT:
                raise S.Refused(tr('Choose the bank or exchange house.'), problems={'agent': tr('Invalid.')})
            prof = {'id': None, 'agent': ag, 'branch_ids': [int(x) for x in d.get('branch_ids') or [] if str(x).isdigit()]}
        if (prof.get('method') or 'wps') != 'wps':
            raise S.Refused(tr('This set-up pays by another method – make its file in Payroll run › Payments.'))
        br = list(prof.get('branch_ids') or [])
        if not br and prof.get('id'):   # the company default pays the branches no other active set-up pays
            others = {b for p in rows if p.get('active', True) and p['id'] != prof['id'] and (p.get('method') or 'wps') == (prof.get('method') or 'wps')
                      for b in p.get('branch_ids') or []}
            if others:
                br = [b for b in M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', flat=True) if b not in others]
        if not self.every_branch():
            br = [b for b in (br or self.c.branches) if b in set(self.c.branches)]
            if not br:
                raise S.Refused(tr('None of the set-up’s branches is yours.'), status=403)
        layout = B.AGENT[prof.get('agent') or 'cbuae']['layout']
        if layout != 'mudad_wages':
            try:
                from CountryPack.services import WPS_SIF_OFF, feature
                if not feature('wps_sif') and not all(B.branch_country(b) == 'AE' for b in br or [0]):
                    raise S.Refused(tr(WPS_SIF_OFF))
            except ImportError:
                pass
        res = B.make(run, prof, branch_ids=br, justifications=d.get('justifications') or {})
        res['layout_name'], res['agent_name'] = tr(res['layout_name']), tr(res['agent_name'])
        res['skipped'] = [dict(x, reason=tr(x.get('reason') or '')) for x in res.get('skipped') or []]
        res['run'] = {'id': run.pk, 'name': run.name, 'month': run.month, 'year': run.year, 'status': run.status}
        res['profile'] = prof.get('id')
        res['branch_ids'] = br
        return run, prof, res


def _public(res):
    x = {k: v for k, v in res.items() if k not in ('content', 'accounts', 'people')}
    x['lines'] = len(x.get('preview_lines') or [])
    x['preview_lines'] = (x.get('preview_lines') or [])[:400]
    return x


class BankFilePreviewView(_FileBase):
    def post(self, request):
        run, prof, res = self.made(request)
        return Response(_public(res))


class BankFileDownloadView(_FileBase):
    def post(self, request):
        run, prof, res = self.made(request)
        content = res['content'] if isinstance(res['content'], bytes) else res['content'].encode('utf-8')
        u = request.user
        B.log_file({'profile': prof.get('id'), 'agent': res['agent'], 'layout': res['layout'], 'run': run.pk, 'file_name': res['file_name'],
                    'employees': (res.get('summary') or {}).get('employees'), 'total': (res.get('summary') or {}).get('total'),
                    'accounts': res.get('accounts') or {}, 'by': getattr(u, 'username', ''), 'at': datetime.datetime.now().isoformat(timespec='seconds')})
        try:   # the run's history shows the file as the WPS screen does (WorkspaceWpsMadeView)
            s = res.get('summary') or {}
            info = {'file': res['layout'][:10], 'employees': int(s.get('employees') or 0), 'total': str(S.q2(s.get('total') or 0)),
                    'skipped': int(s.get('skipped') or 0), 'bank': res['agent_name'], 'file_name': res['file_name']}
            M('Chatter', 'RecordLog').objects.create(
                model='PayrollManagement.payrollrun', object_id=str(run.pk), record_label=(run.name or f'{run.month:02d}/{run.year}')[:255],
                action='updated', changes={'wps_file': {'label': 'WPS file', 'old': None,
                                                        'new': f"{res['layout_name']} – {info['employees']} – {info['total']}", 'info': info}},
                user=u, user_name=getattr(u, 'username', '')[:150], path=request.path[:255], method='POST')
        except Exception:
            pass
        x = _public(res)
        x['content_b64'] = base64.b64encode(content).decode('ascii')
        return Response(x)
