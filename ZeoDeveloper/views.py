"""ZEO Developer API – /developer/api/…  (every call carries ?schema=<company>)

Everyone in the company   GET status/ · GET link-options/?target=&q= · GET qr/?text=&kind= · GET print/<layout>/<record>/
                          POST scripts/<id>/run/ {record} (button scripts – on a record the user may see)
Company administrators    GET/PUT settings/ (Developer mode, limits, allowed sites)
 (Developer mode on)      scripts/ (CRUD) · scripts/<id>/versions/ · compare/?a=&b= · restore/ {version} · run/ {dry_run, record}
                          POST test/ {code, module, record} · POST check/ {code} · GET help/ · GET models/?app= · GET fields/?key=
                          runs/ · actions/ (approve / reject) · rate-tables/ · reports/ · prints/ · scheduled/ (+ run/)
                          POST formula/preview/ {module, fields} · templates/ (+ install/) · POST export/ · POST import/preview/ · POST import/
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import json
import logging

from django.db import connection
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ZeoFlow.views import Member
from . import services as S
from .models import (DeveloperSettings, Script, ScriptVersion, ScriptRun, SafeAction, RateTable, ModuleReport, PrintLayout, ScheduledCalc,
                     InstalledPackage, VERSION)

log = logging.getLogger(__name__)


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def bad(msg, code=400, field=None):
    d = {'detail': msg}
    if field:
        d['field'] = field
    return Response(d, status=code)


class Base(APIView):
    zeo_access = False   # rights are checked here
    zeo_scope = False
    permission_classes = [Member]
    admin_only = True
    needs_mode = True

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        self.c = S.access_of(request.user)

    def guard(self, request):
        if self.admin_only and not self.c.admin:
            return bad(_tr('Only a company administrator may use ZEO Developer.'), 403)
        if self.needs_mode and not DeveloperSettings.get().developer_mode:
            return bad(_tr('Developer mode is off in this company (Settings › Developer mode).'), 409)
        return None

    def dispatch(self, request, *a, **k):
        return super().dispatch(request, *a, **k)

    def handle_exception(self, exc):
        from .packages import PackageError
        from .actions import ActionRefused
        if isinstance(exc, S.DevError):
            return bad(exc.message, exc.status, exc.field)
        if isinstance(exc, (PackageError, ActionRefused)):
            return bad(exc.message, exc.status)
        return super().handle_exception(exc)


def guarded(fn):
    def w(self, request, *a, **k):
        g = self.guard(request)
        if g is not None:
            return g
        return fn(self, request, *a, **k)
    w.__name__ = fn.__name__
    return w


# ------------------------------------------------------------------ status and settings
class StatusView(Base):
    admin_only = False
    needs_mode = False

    def get(self, request):
        st = DeveloperSettings.get()
        return Response({'developer_mode': st.developer_mode, 'is_admin': bool(self.c.admin), 'can_use': bool(st.developer_mode and self.c.admin),
                         'version': VERSION, 'api_version': 'v1'})


class SettingsView(Base):
    needs_mode = False

    @guarded
    def get(self, request):
        return Response(S.settings_json(DeveloperSettings.get()))

    @guarded
    def put(self, request):
        return Response(S.settings_json(S.save_settings(request.data or {}, request.user)))

    post = put


# ------------------------------------------------------------------ scripts
def script_json(s, full=False):
    out = {'id': s.pk, 'key': s.key, 'name': s.name, 'description': s.description, 'module_key': s.module_key, 'model_key': s.model_key,
           'trigger': s.trigger, 'trigger_label': dict(Script._meta.get_field('trigger').choices).get(s.trigger, s.trigger), 'move_key': s.move_key,
           'button_label': s.button_label, 'day_of_month': s.day_of_month, 'hour': s.hour, 'active': s.active, 'stopped': s.stopped,
           'failures': s.failures, 'version': s.version, 'api_version': s.api_version, 'last_run_at': s.last_run_at, 'updated_at': s.updated_at}
    if full:
        out['code'] = s.code
    return out


def _script(pk):
    s = Script.objects.filter(pk=pk).first()
    if s is None:
        raise S.DevError(_tr('Script not found.'), 404)
    return s


class ScriptsView(Base):
    @guarded
    def get(self, request):
        qs = Script.objects.all()
        m = request.query_params.get('module')
        if m:
            qs = qs.filter(module_key=m)
        return Response({'scripts': [script_json(s) for s in qs]})

    @guarded
    def post(self, request):
        s = S.save_script(request.data or {}, request.user)
        return Response(script_json(s, True), status=201)


class ScriptView(Base):
    @guarded
    def get(self, request, pk):
        return Response(script_json(_script(pk), True))

    @guarded
    def put(self, request, pk):
        return Response(script_json(S.save_script(request.data or {}, request.user, _script(pk)), True))

    patch = put

    @guarded
    def delete(self, request, pk):
        s = _script(pk)
        if s.active:
            return bad(_tr('Switch the script off before deleting it.'), 409)
        s.delete()
        return Response(status=204)


class VersionsView(Base):
    @guarded
    def get(self, request, pk):
        s = _script(pk)
        return Response({'versions': [{'version': v.version, 'note': v.note, 'saved_by': v.saved_by_name, 'saved_at': v.saved_at, 'lines': len(v.code.splitlines()),
                                       'current': v.version == s.version} for v in s.versions.all()]})


class CompareView(Base):
    @guarded
    def get(self, request, pk):
        q = request.query_params
        if not q.get('a'):
            return bad(_tr('Choose the versions to compare.'))
        return Response(S.compare(_script(pk), q.get('a'), q.get('b') or 'current'))


class RestoreView(Base):
    @guarded
    def post(self, request, pk):
        v = (request.data or {}).get('version')
        if not v:
            return bad(_tr('Choose the version to restore.'), field='version')
        return Response(script_json(S.restore(_script(pk), v, request.user), True))


def _record(module_key, rec, request):
    """A custom module record the user may see (by id or number)."""
    from ZeoFlow import custom as C
    from ZeoFlow.models import CustomModule
    m = CustomModule.objects.filter(key=module_key).first()
    if m is None:
        raise S.DevError(_tr('There is no custom module "{0}".', module_key), 404)
    qs = C.scope(m, request)
    s = str(rec)
    r = qs.filter(pk=int(s)).first() if s.isdigit() else qs.filter(number=s).first()
    if r is None:
        raise S.DevError(_tr('Record not found (or you may not see it).'), 404)
    return r


class RunView(Base):
    """POST {record, dry_run} – a button on a record (anyone who sees the record), or an administrator's run by hand."""
    admin_only = False

    def post(self, request, pk):
        s = _script(pk)
        d = request.data or {}
        dry = str(d.get('dry_run', '')).lower() in ('1', 'true', 'yes')
        st = DeveloperSettings.get()
        if not st.developer_mode:
            return bad(_tr('Developer mode is off in this company (Settings › Developer mode).'), 409)
        record = None
        if d.get('record'):
            if not s.module_key:
                return bad(_tr('This script does not belong to a custom module.'))
            record = _record(s.module_key, d['record'], request)
        if s.trigger == 'button':
            if record is None:
                return bad(_tr('Open the record and press the button there.'))
            if not (self.c.admin or s.active):
                return bad(_tr('The script is switched off.'), 409)
        elif not self.c.admin:
            return bad(_tr('Only a company administrator may run this script.'), 403)
        if dry and not self.c.admin:
            return bad(_tr('Only a company administrator may test scripts.'), 403)
        run = S.run_code(s.code, request.user, script=s if not dry else None, trigger='button' if s.trigger == 'button' and record else 'manual',
                         record=record, trigger_info={'event': 'button' if record else 'manual'}, dry_run=dry)
        if dry:
            run.script_name = s.name
        out = S.run_json(run)
        if record is not None:
            record.refresh_from_db()
            from ZeoFlow import custom as C
            out['record'] = C.record_json(record, request)
        return Response(out, status=200 if run.status == 'ok' else 422)


class TestView(Base):
    """POST {code, module, record} – "Test on sample data": runs the text in a transaction that is rolled back."""

    @guarded
    def post(self, request):
        d = request.data or {}
        code = str(d.get('code') or '')
        if not code.strip():
            return bad(_tr('Write the script first.'), field='code')
        record = _record(d['module'], d['record'], request) if d.get('module') and d.get('record') else None
        app_record = None
        if d.get('model_key') and d.get('record_id'):
            from .appdata import known, scoped, registry
            m = known(d['model_key'])
            from .api import RunContext
            obj = scoped(m, RunContext(request.user, self.c, DeveloperSettings.get())).filter(pk=int(d['record_id'])).first() if m is not None else None
            if obj is None:
                return bad(_tr('Record not found (or you may not see it).'), 404)
            app_record = (registry().model_key(m), obj)
        run = S.run_code(code, request.user, trigger='test', record=record, dry_run=True, app_record=app_record,
                         trigger_info={'event': d.get('event') or 'test'}, record_ref=f'{d.get("module")} {d.get("record")}' if record else '')
        return Response(S.run_json(run))


class CheckView(Base):
    @guarded
    def post(self, request):
        from .sandbox import check, ScriptError
        try:
            check(str((request.data or {}).get('code') or ''))
        except ScriptError as e:
            return Response({'ok': False, 'error': e.message, 'line': e.line})
        return Response({'ok': True})


class HelpView(Base):
    @guarded
    def get(self, request):
        from .api import HELP
        from .formulas import FUNCTIONS
        return Response({'api_version': 'v1', 'groups': [{'group': g['group'], 'items': [{'call': c, 'what': w, 'example': e} for c, w, e in g['items']]} for g in HELP],
                         'formula_functions': [{'name': k, 'what': v} for k, v in FUNCTIONS.items()]})


class ModelsView(Base):
    @guarded
    def get(self, request):
        from .appdata import registry, is_protected, is_money, routes
        app = request.query_params.get('app') or None
        r = routes()
        out = []
        for d in registry().models_of(app):
            m = registry().get_model(d['key'])
            out.append({'key': d['key'], 'label': d['label'], 'app': d['module'], 'readable': True, 'writable': not is_protected(m) and bool(r.get(d['key'])),
                        'approval': is_money(m)})
        return Response({'apps': registry().modules(), 'models': out})


class FieldsView(Base):
    @guarded
    def get(self, request):
        from .appdata import known, registry, visible_fields
        m = known(request.query_params.get('key'))
        if m is None:
            return bad(_tr('Unknown record type.'), 404)
        vis = {f.name for f in visible_fields(m)}
        return Response({'fields': [d for d in registry().fields_of(registry().model_key(m), with_related=False) if d['key'] in vis]})


# ------------------------------------------------------------------ run log, actions
class RunsView(Base):
    @guarded
    def get(self, request):
        q = request.query_params
        qs = ScriptRun.objects.all()
        if q.get('script'):
            qs = qs.filter(script_id=q['script'])
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        return Response({'runs': [S.run_json(r, False) for r in qs[:300]], 'this_month': S.runs_this_month(),
                         'limit': DeveloperSettings.get().monthly_run_limit})


class RunDetailView(Base):
    @guarded
    def get(self, request, pk):
        r = ScriptRun.objects.filter(pk=pk).first()
        if r is None:
            return bad(_tr('Run not found.'), 404)
        return Response(S.run_json(r))


def action_json(a):
    return {'id': a.pk, 'action': a.action, 'label': a.label, 'params': a.params, 'needs_approval': a.needs_approval, 'status': a.status,
            'status_label': a.get_status_display(), 'result': a.result, 'error': a.error, 'script': a.script.name if a.script_id else '',
            'requested_by': a.requested_by_name, 'requested_at': a.requested_at, 'decided_by': a.decided_by_name, 'decided_at': a.decided_at, 'note': a.note}


class ActionsView(Base):
    @guarded
    def get(self, request):
        qs = SafeAction.objects.select_related('script')
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return Response({'actions': [action_json(a) for a in qs[:300]], 'pending': SafeAction.objects.filter(status='pending').count()})


class ActionDecideView(Base):
    @guarded
    def post(self, request, pk, decision):
        from . import actions as A
        a = SafeAction.objects.filter(pk=pk).first()
        if a is None:
            return bad(_tr('Action not found.'), 404)
        note = str((request.data or {}).get('note') or '')
        a = A.approve(a, request.user, note) if decision == 'approve' else A.reject(a, request.user, note)
        return Response(action_json(a))


# ------------------------------------------------------------------ rate tables
def table_json(t):
    return {'id': t.pk, 'key': t.key, 'name': t.name, 'columns': t.columns, 'rows': t.rows, 'updated_at': t.updated_at}


class TablesView(Base):
    @guarded
    def get(self, request):
        return Response({'tables': [table_json(t) for t in RateTable.objects.all()]})

    @guarded
    def post(self, request):
        from .packages import save_table
        d = request.data or {}
        if RateTable.objects.filter(key=d.get('key')).exists():
            return bad(_tr('Another rate table has the key "{0}".', d.get('key')), field='key')
        return Response(table_json(save_table(d, request.user)), status=201)


class TableView(Base):
    def _t(self, pk):
        t = RateTable.objects.filter(pk=pk).first()
        if t is None:
            raise S.DevError(_tr('Rate table not found.'), 404)
        return t

    @guarded
    def get(self, request, pk):
        return Response(table_json(self._t(pk)))

    @guarded
    def put(self, request, pk):
        from .packages import save_table
        return Response(table_json(save_table(request.data or {}, request.user, self._t(pk))))

    @guarded
    def delete(self, request, pk):
        t = self._t(pk)
        from ZeoFlow.models import CustomModule
        used = [m.plural for m in CustomModule.objects.all() if t.key in json.dumps(m.fields)]
        if used:
            return bad(_tr('The rate table is used by: {0}.', ', '.join(used)), 409)
        t.delete()
        return Response(status=204)


# ------------------------------------------------------------------ reports, prints, scheduled calculations
def _module(key):
    from ZeoFlow.models import CustomModule
    m = CustomModule.objects.filter(key=key).first()
    if m is None:
        raise S.DevError(_tr('There is no custom module "{0}".', key), 400, 'module_key')
    return m


def report_json(r):
    return {'id': r.pk, 'key': f'dev-{r.pk}', 'module_key': r.module_key, 'name': r.name, 'description': r.description, 'section': r.section, 'kind': r.kind,
            'columns': r.columns, 'group_by': r.group_by, 'date_field': r.date_field, 'states': r.states, 'who': r.who, 'active': r.active}


class ReportsView(Base):
    @guarded
    def get(self, request):
        return Response({'reports': [report_json(r) for r in ModuleReport.objects.all()]})

    @guarded
    def post(self, request):
        from .reports import check_report
        d = request.data or {}
        m = _module(d.get('module_key'))
        try:
            data = check_report(d, m)
        except ValueError as e:
            return bad(str(e))
        return Response(report_json(ModuleReport.objects.create(module_key=m.key, **data)), status=201)


class ReportView(Base):
    @guarded
    def put(self, request, pk):
        from .reports import check_report
        r = ModuleReport.objects.filter(pk=pk).first()
        if r is None:
            return bad(_tr('Report not found.'), 404)
        d = request.data or {}
        try:
            data = check_report(d, _module(r.module_key))
        except ValueError as e:
            return bad(str(e))
        for k, v in data.items():
            setattr(r, k, v)
        if 'active' in d:
            r.active = str(d['active']).lower() in ('1', 'true', 'yes')
        r.save()
        return Response(report_json(r))

    @guarded
    def delete(self, request, pk):
        ModuleReport.objects.filter(pk=pk).delete()
        return Response(status=204)


def print_json(p):
    return {'id': p.pk, 'module_key': p.module_key, 'name': p.name, 'html': p.html, 'paper': p.paper, 'active': p.active}


class PrintsView(Base):
    @guarded
    def get(self, request):
        return Response({'prints': [print_json(p) for p in PrintLayout.objects.all()]})

    @guarded
    def post(self, request):
        from .reports import check_html
        d = request.data or {}
        m = _module(d.get('module_key'))
        if not str(d.get('name') or '').strip():
            return bad(_tr('Give the layout a name.'), field='name')
        try:
            check_html(d.get('html') or '')
        except ValueError as e:
            return bad(str(e), field='html')
        p = PrintLayout.objects.create(module_key=m.key, name=str(d['name'])[:120], html=d.get('html') or '', paper=str(d.get('paper') or 'A4')[:10])
        return Response(print_json(p), status=201)


class PrintView(Base):
    @guarded
    def put(self, request, pk):
        from .reports import check_html
        p = PrintLayout.objects.filter(pk=pk).first()
        if p is None:
            return bad(_tr('Print layout not found.'), 404)
        d = request.data or {}
        try:
            check_html(d.get('html', p.html) or '')
        except ValueError as e:
            return bad(str(e), field='html')
        p.name, p.html, p.paper = str(d.get('name') or p.name)[:120], d.get('html', p.html) or '', str(d.get('paper') or p.paper)[:10]
        if 'active' in d:
            p.active = str(d['active']).lower() in ('1', 'true', 'yes')
        p.save()
        return Response(print_json(p))

    @guarded
    def delete(self, request, pk):
        PrintLayout.objects.filter(pk=pk).delete()
        return Response(status=204)


class PrintRecordView(Base):
    """GET print/<layout>/<record>/ – the printed document of a record (HTML; the browser prints / saves it as PDF)."""
    admin_only = False

    @guarded
    def get(self, request, pk, rec):
        from .reports import render
        p = PrintLayout.objects.filter(pk=pk, active=True).first()
        if p is None:
            return bad(_tr('Print layout not found.'), 404)
        r = _record(p.module_key, rec, request)
        body = render(p, r)
        page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{r.number}</title><style>@page{{size:{p.paper}}}body{{font-family:Arial,sans-serif;font-size:12px}}'
                f'table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px}}</style></head><body>{body}</body></html>')
        resp = HttpResponse(page, content_type='text/html; charset=utf-8')
        resp['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return resp


def calc_json(c):
    return {'id': c.pk, 'module_key': c.module_key, 'name': c.name, 'field_key': c.field_key, 'formula': c.formula, 'frequency': c.frequency,
            'states': c.states, 'active': c.active, 'last_period': c.last_period, 'last_result': c.last_result}


class CalcsView(Base):
    @guarded
    def get(self, request):
        return Response({'calculations': [calc_json(c) for c in ScheduledCalc.objects.all()]})

    @guarded
    def post(self, request):
        from .schedule import check_calc
        d = request.data or {}
        m = _module(d.get('module_key'))
        try:
            data = check_calc(d, m)
        except ValueError as e:
            return bad(str(e), field='formula')
        return Response(calc_json(ScheduledCalc.objects.create(module_key=m.key, **data)), status=201)


class CalcView(Base):
    def _c(self, pk):
        c = ScheduledCalc.objects.filter(pk=pk).first()
        if c is None:
            raise S.DevError(_tr('Scheduled calculation not found.'), 404)
        return c

    @guarded
    def put(self, request, pk):
        from .schedule import check_calc
        c = self._c(pk)
        d = request.data or {}
        try:
            data = check_calc({**calc_json(c), **d}, _module(c.module_key))
        except ValueError as e:
            return bad(str(e), field='formula')
        for k, v in data.items():
            setattr(c, k, v)
        if 'active' in d:
            c.active = str(d['active']).lower() in ('1', 'true', 'yes')
        c.save()
        return Response(calc_json(c))

    @guarded
    def delete(self, request, pk):
        self._c(pk).delete()
        return Response(status=204)


class CalcRunView(Base):
    @guarded
    def post(self, request, pk):
        from .schedule import run_calc
        c = CalcView()._c(pk)
        dry = str((request.data or {}).get('dry_run', 'true')).lower() in ('1', 'true', 'yes')
        res = run_calc(c, request.user, dry_run=dry)
        return Response({**res, 'dry_run': dry})


# ------------------------------------------------------------------ formulas shown on sample records before saving (D1)
class FormulaPreviewView(Base):
    @guarded
    def post(self, request):
        """{module (key, optional), fields: [the designer's fields], samples: [data …] (optional)} → per sample record the calculated values."""
        from ZeoFlow import custom as C
        from ZeoFlow.models import CustomModule
        from .fields import compute, FieldError
        d = request.data or {}
        try:
            fields = C.clean_fields(d.get('fields'))
        except C.CustomError as e:
            return bad(str(e), field='fields')

        class M:
            pass
        mm = M()
        mm.fields = fields
        samples = []
        cur = CustomModule.objects.filter(key=d.get('module') or '').first()
        if cur is not None:
            samples = [(r.number, r.state, dict(r.data)) for r in cur.records.order_by('-id')[:3]]
        for i, s in enumerate(d.get('samples') or [], 1):
            if isinstance(s, dict):
                samples.append((f'Sample {i}', '', dict(s)))
        if not samples:
            samples = [('Sample', '', {f['key']: _sample_value(f) for f in fields})]
        out = []
        comp = [f['key'] for f in fields if f['type'] in ('formula', 'lookup', 'qr', 'barcode')]
        for number, state, data in samples[:5]:
            try:
                res = compute(mm, dict(data), number, state)
                out.append({'record': number, 'values': {k: res.get(k) for k in comp}, 'input': {k: v for k, v in data.items() if k not in comp}})
            except FieldError as e:
                out.append({'record': number, 'error': str(e), 'field': e.field, 'input': data})
        return Response({'samples': out, 'computed': comp})


def _sample_value(f):
    import datetime
    t = f['type']
    if t in ('number', 'money'):
        return '10'
    if t == 'date':
        return _zt_today().replace(day=1).isoformat()
    if t == 'yesno':
        return True
    if t == 'choice':
        return (f.get('options') or '').split('|')[0].strip()
    if t == 'table':
        return [{c['key']: ('5' if c['type'] in ('number', 'money') else 'x') for c in f.get('columns') or []}] * 2
    return 'sample'


# ------------------------------------------------------------------ links, QR
class LinkOptionsView(Base):
    admin_only = False

    @guarded
    def get(self, request):
        from .fields import link_options
        target = str(request.query_params.get('target') or '')
        if target.startswith('model:'):   # any app's record: only rows the user may see
            from .appdata import known, scoped
            from .api import RunContext
            m = known(target[6:])
            if m is None:
                return bad(_tr('Unknown record type.'), 404)
            q = str(request.query_params.get('q') or '').lower()
            ctx = RunContext(request.user, self.c, DeveloperSettings.get())
            out = []
            for o in scoped(m, ctx).order_by('-pk')[:2000]:
                lbl = str(o)[:120]
                if q and q not in lbl.lower():
                    continue
                out.append({'id': o.pk, 'label': lbl})
                if len(out) >= 50:
                    break
            return Response({'options': out})
        return Response({'options': link_options(target, request.query_params.get('q') or '')})


class QrView(Base):
    admin_only = False
    needs_mode = False

    def get(self, request):
        from .reports import qr_svg
        kind = 'barcode' if request.query_params.get('kind') == 'barcode' else 'qr'
        svg = qr_svg(request.query_params.get('text') or '', kind)
        resp = HttpResponse(svg, content_type='image/svg+xml')
        resp['Cache-Control'] = 'private, max-age=3600'
        return resp


# ------------------------------------------------------------------ demo apps, export / import
class TemplatesView(Base):
    @guarded
    def get(self, request):
        from .packages import templates
        installed = list(InstalledPackage.objects.values('name', 'source', 'summary', 'installed_at')[:50])
        return Response({'templates': templates(), 'installed': installed})


class TemplateInstallView(Base):
    @guarded
    def post(self, request, key):
        from .packages import install_template
        return Response({'installed': install_template(key, request.user)})


class ExportView(Base):
    @guarded
    def post(self, request):
        from .packages import export
        mods = (request.data or {}).get('modules') or []
        pkg = export(mods if isinstance(mods, list) else [mods])
        if (request.data or {}).get('download'):
            resp = HttpResponse(json.dumps(pkg, ensure_ascii=False, indent=1), content_type='application/json')
            resp['Content-Disposition'] = f'attachment; filename="zeo-books-package-{"-".join(m["key"] for m in pkg["modules"])[:60]}.json"'
            return resp
        return Response(pkg)


def _pkg(request):
    d = request.data or {}
    f = request.FILES.get('file') if hasattr(request, 'FILES') else None
    if f is not None:
        if f.size > 5_000_000:
            raise S.DevError(_tr('The package is larger than 5 MB.'))
        return f.read().decode('utf-8', errors='replace')
    return d.get('package')


class ImportPreviewView(Base):
    @guarded
    def post(self, request):
        from .packages import preview
        return Response({'items': preview(_pkg(request))})


class ImportView(Base):
    @guarded
    def post(self, request):
        from .packages import apply
        return Response({'installed': apply(_pkg(request), request.user)})
