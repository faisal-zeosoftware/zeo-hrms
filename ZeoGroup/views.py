"""v1.27.0 – group API (/group/api/...). Public schema; the group key is the account (acc<id>) or "server"."""
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr
from . import operations as O, roles as R, services as S
from .scope import GroupError, as_json, group_of, groups_for, acting_user


def _lang(request):
    return request.headers.get('X-Language') or ''


class _G(APIView):
    permission_classes = [IsAuthenticated]
    need = None

    def group(self, request, key, need=None):
        return group_of(request.user, key, need or self.need)

    def handle_exception(self, exc):
        if isinstance(exc, GroupError):
            return Response({'detail': exc.message}, status=exc.status)
        if isinstance(exc, ValueError):
            return Response({'detail': str(exc)}, status=400)
        return super().handle_exception(exc)


class GroupsView(_G):
    """The groups of the signed-in person (company list tile)."""
    def get(self, request):
        return Response({'groups': [as_json(g) for g in groups_for(request.user)]})


class DashboardView(_G):
    need = 'dashboard'

    def get(self, request, key):
        g = self.group(request, key)
        if g['role'] in ('owner', 'admin'):
            R.sync(g)
        fresh = request.query_params.get('fresh') in ('1', 'true')
        return Response(dict(S.dashboard(g, acting_user(g, request.user), fresh), group=as_json(g)))


class ReportsView(_G):
    need = 'reports'

    def get(self, request, key, rkey=None):
        g = self.group(request, key)
        if not rkey:
            return Response({'reports': [dict(r, title=tr(r['title'])) for r in S.report_list()]})
        params = {k: request.query_params.get(k) for k in ('from', 'to', 'as_of') if request.query_params.get(k)}
        data = S.run_report(g, acting_user(g, request.user), rkey, params, _lang(request))
        for c in data['columns']:
            if c['key'] == '_company':
                c['label'] = tr('Company')
        if request.query_params.get('download') == 'xlsx':
            resp = HttpResponse(S.report_xlsx(data), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="group_{rkey}.xlsx"'
            return resp
        return Response(data)


class InboxView(_G):
    need = 'inbox'

    def get(self, request, key):
        return Response(S.inbox(self.group(request, key), request.user))


class FeedView(_G):
    need = 'inbox'

    def get(self, request, key):
        return Response(S.feed(self.group(request, key), request.user))

    def post(self, request, key):
        n = S.feed_read(self.group(request, key), request.user, request.data.get('ids'))
        return Response({'read': n})


class CopyView(_G):
    need = 'copy'

    def get(self, request, key):
        from .models import SettingsCopy
        g = self.group(request, key)
        names = {c.schema_name: c.name for c in g['companies']}
        hist = [{'id': h.id, 'source': names.get(h.source_schema, h.source_schema), 'targets': [names.get(t, t) for t in h.targets],
                 'kinds': h.kinds, 'overwrite': h.overwrite, 'result': h.result, 'by': getattr(h.by, 'email', ''), 'at': h.at.isoformat()}
                for h in SettingsCopy.objects.filter(source_schema__in=list(names)).select_related('by')[:30]]
        return Response({'kinds': [{'key': k, 'label': tr(l)} for k, l in O.KINDS], 'history': hist})

    def post(self, request, key):
        g = self.group(request, key)
        d = request.data
        res = O.copy_settings(g, request.user, d.get('source'), d.get('targets') or [], d.get('kinds') or [],
                              str(d.get('overwrite')).lower() in ('1', 'true', 'yes'))
        return Response(res)


class TransferView(_G):
    need = 'transfer'

    def get(self, request, key, what=''):
        g = self.group(request, key)
        q = request.query_params
        if what == 'employees':
            from django.apps import apps
            from django.db.models import Q
            from django_tenants.utils import schema_context
            from .scope import company_in
            comp = company_in(g, q.get('schema') or '')
            text = (q.get('q') or '').strip()
            with schema_context(comp.schema_name):
                qs = apps.get_model('EmpManagement', 'emp_master').objects.filter(is_active=True)
                if text:
                    qs = qs.filter(Q(emp_code__icontains=text) | Q(emp_first_name__icontains=text) | Q(emp_last_name__icontains=text))
                rows = [{'id': e.id, 'emp_code': e.emp_code, 'name': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)}
                        for e in qs.order_by('emp_code')[:30]]
            return Response({'employees': rows})
        if what == 'form':
            return Response(O.transfer_form(g, q.get('source'), int(q.get('employee') or 0), q.get('target')))
        from .models import CompanyTransfer
        names = [c.schema_name for c in g['companies']]
        rows = CompanyTransfer.objects.filter(source_schema__in=names).select_related('by')[:50]
        return Response({'history': [{'id': r.id, 'name': r.name, 'emp_code': r.emp_code, 'target_code': r.target_code,
                                      'source': r.source_company, 'target': r.target_company, 'effective_date': r.effective_date.isoformat(),
                                      'keep_service_date': r.keep_service_date, 'state': r.state, 'error': r.error, 'result': r.result,
                                      'by': getattr(r.by, 'email', ''), 'at': r.at.isoformat()} for r in rows]})

    def post(self, request, key, what=''):
        g = self.group(request, key)
        d = request.data
        try:
            rec = O.transfer(g, request.user, d.get('source'), int(d.get('employee') or 0), d.get('target'), d)
        except Exception as e:
            if isinstance(e, (GroupError, ValueError)):
                raise
            raise ValueError(tr('The transfer could not be made: {0}', str(e)[:300]))
        return Response({'id': rec.id, 'state': rec.state, 'result': rec.result,
                         'detail': tr('{0} now works for {1} as {2}. The record in {3} is closed.', rec.name, rec.target_company, rec.target_code, rec.source_company)})


class RolesView(_G):
    need = 'roles'

    def get(self, request, key):
        g = self.group(request, key)
        return Response({'roles': R.role_rows(g), 'members': [{'id': u.id, 'email': u.email or u.username} for u in R.members(g)[:500]]})

    def post(self, request, key, pk=None):
        g = self.group(request, key)
        r = R.set_role(g, request.user, int(request.data.get('user_id') or 0), request.data.get('role'))
        return Response({'id': r.id, 'roles': R.role_rows(g)})

    def delete(self, request, key, pk=None):
        g = self.group(request, key)
        R.remove_role(g, int(pk or 0))
        return Response({'roles': R.role_rows(g)})


class ConsolidationView(_G):
    """v1.53.0 – GET ?from=YYYY-MM&to=YYYY-MM&currency=XXX: the payroll cost of every company in one currency."""
    need = 'dashboard'

    def get(self, request, key):
        from datetime import date
        from django.utils import timezone
        from . import consolidation as C
        g = self.group(request, key)
        today = timezone.localdate()
        last = C.parse_month(request.query_params.get('to'), today.replace(day=1))
        first = C.parse_month(request.query_params.get('from'), date(last.year - (1 if last.month < 12 else 0), last.month % 12 + 1, 1))
        if first > last:
            return Response({'detail': tr('The start month is after the end month.')}, status=400)
        cur = (request.query_params.get('currency') or '').upper()[:3] or None
        data = C.consolidate(g, first, last, cur)
        data['reporting_currency'] = C.reporting_currency(key)
        data['can_edit'] = 'copy' in RIGHTS_OF(g)
        data['currencies'] = C.CURRENCIES
        return Response(data)


def RIGHTS_OF(g):
    from .scope import RIGHTS
    return RIGHTS.get(g['role'], set())


class RatesView(_G):
    """v1.53.0 – the group's reporting currency and exchange rates. GET (dashboard right) · POST / DELETE (copy right)."""
    need = 'dashboard'

    def get(self, request, key):
        from . import consolidation as C
        g = self.group(request, key)
        return Response({'currency': C.reporting_currency(key), 'rates': C.rate_rows(key), 'currencies': C.CURRENCIES,
                         'pegs': {k: float(v) for k, v in C.USD_PEGS.items()}, 'can_edit': 'copy' in RIGHTS_OF(g),
                         'company_currencies': [{'company': c.name, 'currency': C.company_currency(c)} for c in g['companies']]})

    def post(self, request, key):
        from decimal import Decimal, InvalidOperation
        from . import consolidation as C
        from .models import ExchangeRate, GroupCurrency
        g = self.group(request, key, 'copy')
        d = request.data
        if d.get('currency') and not d.get('rate'):
            cur = str(d['currency']).upper()[:3]
            if len(cur) != 3 or not cur.isalpha():
                return Response({'detail': tr('Give a 3-letter currency code.')}, status=400)
            GroupCurrency.objects.update_or_create(key=key, defaults={'currency': cur})
            return self.get(request, key)
        cur, to = str(d.get('from_currency') or '').upper()[:3], str(d.get('to_currency') or C.reporting_currency(key)).upper()[:3]
        m = C.parse_month(d.get('month'))
        try:
            rate = Decimal(str(d.get('rate')))
        except (InvalidOperation, TypeError):
            rate = None
        if len(cur) != 3 or len(to) != 3 or cur == to or m is None or rate is None or rate <= 0:
            return Response({'detail': tr('Give the currency, the month (YYYY-MM) and a rate above 0.')}, status=400)
        ExchangeRate.objects.update_or_create(key=key, currency=cur, to_currency=to, month=m,
                                              defaults={'rate': rate, 'note': str(d.get('note') or '')[:120], 'by': request.user})
        return self.get(request, key)

    def delete(self, request, key):
        from .models import ExchangeRate
        self.group(request, key, 'copy')
        ExchangeRate.objects.filter(key=key, pk=request.query_params.get('id')).delete()
        return self.get(request, key)
