"""v1.53.0 – insights API (/insights/api/…): the report builder and the dashboard builder."""
import uuid

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import builder as B
from . import dashboards as D
from .models import CustomDashboard, CustomReport


def bad(msg, code=400):
    return Response({'detail': msg}, status=code)


class Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from AccessControl.access import ctx
        self.c = ctx(request)
        if not self.c.backoffice:
            self.permission_denied(request, message='Reports are for HR and other staff users.')


def _owner(u):
    # v1.62.0 reports: the person's name (employee record), not the login name
    if not u:
        return ''
    try:
        from DashboardManagement.report_present import _people
        name = _people(None).get(u.username)
        if name:
            return name
    except Exception:
        pass
    return (u.get_full_name() if hasattr(u, 'get_full_name') else '') or u.username


def _groups():
    try:
        from django.contrib.auth.models import Group
        from ZeoApps import groupscope   # v1.62.0 isolation: the groups of this company only
        qs = Group.objects.filter(pk__in=groupscope.visible_ids()) if groupscope.schema() else Group.objects.all()
        return sorted(({'id': g.id, 'name': groupscope.label(g)} for g in qs), key=lambda r: r['name'].lower())
    except Exception:
        return []


def _share(obj, d):
    if d.get('shared') in ('me', 'all', 'groups'):
        obj.shared = d['shared']
    if isinstance(d.get('groups'), list):
        obj.groups = [int(g) for g in d['groups'] if str(g).isdigit()]


# ------------------------------------------------------------------ report builder (62)
def report_json(cr, user=None):
    return {'id': cr.pk, 'key': f'c-{cr.pk}', 'name': cr.name, 'description': cr.description, 'section': cr.section, **B.as_dict(cr),
            'shared': cr.shared, 'groups': cr.groups, 'owner': _owner(cr.owner), 'owner_id': cr.owner_id, 'mine': user is not None and cr.owner_id == user.pk,
            'updated_at': cr.updated_at}


class MetaView(Base):
    def get(self, request):
        from DashboardManagement.reports import REPORTS, _allowed
        reports = [{'key': k, 'title': v[0], 'section': v[1], 'period': v[4]} for k, v in REPORTS.items() if _allowed(self.c, v[5])]
        try:   # v1.53.1 report names in the user's language
            from Translations.reports import localize_list
            reports = localize_list(reports, request)
        except Exception:
            pass
        reports += [{'key': r['key'], 'title': r['title'], 'section': r['section'], 'period': r['period'], 'custom': True} for r in B.listing(request, self.c)]
        return Response({'models': B.models_for(self.c), 'reports': sorted(reports, key=lambda r: (r['section'], r['title'])),
                         'groups': _groups(), 'ops': B.OPS, 'periods': [{'key': k, 'label': l} for k, l in D.PERIODS], 'admin': self.c.admin})


class FieldsView(Base):
    def get(self, request):
        key = request.query_params.get('model') or ''
        if B.model_allowed(self.c, key) is None:
            return bad('Choose a record type you may view.', 403)
        return Response(B.fields_for(key))


class ReportsView(Base):
    def get(self, request):
        rows = [report_json(cr, request.user) for cr in CustomReport.objects.select_related('owner') if B.visible(cr, request.user, self.c)]
        return Response(rows)

    def post(self, request):
        d = request.data
        cr = CustomReport.objects.filter(pk=d.get('id')).first() if d.get('id') else CustomReport(owner=request.user)
        if cr is None or (cr.pk and cr.owner_id != request.user.pk and not self.c.admin):
            return bad('Only the person who made the report (or an administrator) can change it.', 403)
        _, err = B.clean(d, self.c)
        if err:
            return bad(err)
        name = (d.get('name') or '').strip()[:120]
        if not name:
            return bad('Give the report a name.')
        cr.name, cr.description, cr.section = name, (d.get('description') or '')[:255], ((d.get('section') or 'My reports').strip() or 'My reports')[:60]
        cr.model = d['model']
        cr.columns = [{'field': x['field'], 'label': (x.get('label') or '')[:80]} for x in d['columns']]
        cr.filters = [{'field': f['field'], 'op': f['op'], 'value': f.get('value', ''), 'value2': f.get('value2', '')} for f in d.get('filters') or []]
        cr.group_by = d.get('group_by') or ''
        cr.aggregates = [{'fn': a['fn'], 'field': a.get('field') or ''} for a in d.get('aggregates') or []]
        cr.sort = d.get('sort') or ''
        try:
            cr.limit = max(int(d.get('limit') or 0), 0)
        except (TypeError, ValueError):
            cr.limit = 0
        cr.date_field = d.get('date_field') or ''
        _share(cr, d)
        cr.save()
        return Response(report_json(cr, request.user), status=201)


class ReportView(Base):
    def delete(self, request, pk):
        cr = CustomReport.objects.filter(pk=pk).first()
        if cr is None or (cr.owner_id != request.user.pk and not self.c.admin):
            return bad('Not found.', 404)
        cr.delete()
        return Response(status=204)


class PreviewView(Base):
    """POST a definition (not saved) → the first 50 rows, as the report centre would show them."""
    def post(self, request):
        d = request.data
        try:
            cols, rows, extra = B.run(d, request, limit=50 if not d.get('group_by') else None)
        except ValueError as e:
            return bad(str(e))
        return Response({'columns': [{'key': k, 'label': l, 'type': t} for k, l, t in cols], 'rows': rows[:50], 'count': len(rows)})


# ------------------------------------------------------------------ dashboard builder (61)
def _suite(cards):
    """v1.62.0 reports: 'hrms' when most cards are HRMS reports, else the app of the cards (assets, hse …)."""
    try:
        from ZeoApps.reports import app_of
    except Exception:
        return ''
    apps = []
    for x in cards:
        k = str(x.get('report') or '')
        if k.startswith('c-'):
            m = CustomReport.objects.filter(pk=k[2:]).values_list('model', flat=True).first() if k[2:].isdigit() else None
            apps.append('hrms' if (m or '').split('.')[0] in ('EmpManagement', 'calendars', 'PayrollManagement', 'OrganisationManager') else (m or '').split('.')[0].lower())
        else:
            apps.append(app_of(k) or 'hrms')
    if not apps:
        return ''
    top = max(set(apps), key=apps.count)
    return top


def dash_json(db, user, c=None, full=False):
    out = {'id': db.pk, 'name': db.name, 'description': db.description, 'shared': db.shared, 'groups': db.groups, 'owner': _owner(db.owner),
           'owner_id': db.owner_id, 'can_edit': db.owner_id == user.pk or bool(c and c.admin), 'cards_count': len(db.cards or []), 'updated_at': db.updated_at,
           'suite': _suite(db.cards or [])}   # v1.62.0 reports: the HRMS report centre opens an HR dashboard first
    if full:
        out['cards'] = db.cards or []
    return out


def clean_card(x):
    from DashboardManagement.reports import REPORTS
    if not isinstance(x, dict) or REPORTS.get(x.get('report') or '') is None:
        raise ValueError('Choose the report of the card.')
    view = x.get('view') if x.get('view') in D.VIEWS else 'number'
    return {'id': str(x.get('id') or uuid.uuid4().hex[:8])[:12], 'title': (x.get('title') or '')[:80], 'report': x['report'],
            'period': x.get('period') if x.get('period') in dict(D.PERIODS) else 'report', 'view': view,
            'value': x.get('value') if str(x.get('value') or '').startswith(('count', 'sum:')) else 'count', 'group': (x.get('group') or '')[:80],
            'size': x.get('size') if x.get('size') in D.SIZES else ('w' if view in ('bar', 'line', 'table') else 's'),
            'limit': min(max(int(x.get('limit') or 8), 1), 50), 'columns': [str(k)[:80] for k in (x.get('columns') or [])][:8]}


class DashboardsView(Base):
    def get(self, request):
        return Response([dash_json(db, request.user, self.c) for db in CustomDashboard.objects.select_related('owner') if B.visible(db, request.user, self.c)])

    def post(self, request):
        d = request.data
        db = CustomDashboard.objects.filter(pk=d.get('id')).first() if d.get('id') else CustomDashboard(owner=request.user)
        if db is None or (db.pk and db.owner_id != request.user.pk and not self.c.admin):
            return bad('Only the person who made the dashboard (or an administrator) can change it.', 403)
        name = (d.get('name') or db.name or '').strip()[:120]
        if not name:
            return bad('Give the dashboard a name.')
        db.name, db.description = name, str((d.get('description') if 'description' in d else db.description) or '')[:255]
        if isinstance(d.get('cards'), list):
            try:
                db.cards = [clean_card(x) for x in d['cards']][:40]
            except (ValueError, TypeError) as e:
                return bad(str(e))
        _share(db, d)
        db.save()
        return Response(dash_json(db, request.user, self.c, full=True), status=201)


class DashboardView(Base):
    def _get(self, request, pk):
        db = CustomDashboard.objects.select_related('owner').filter(pk=pk).first()
        if db is None or not B.visible(db, request.user, self.c):
            return None
        return db

    def get(self, request, pk):
        db = self._get(request, pk)
        if db is None:
            return bad('Not found.', 404)
        return Response(dash_json(db, request.user, self.c, full=True))

    def delete(self, request, pk):
        db = self._get(request, pk)
        if db is None or (db.owner_id != request.user.pk and not self.c.admin):
            return bad('Not found.', 404)
        db.delete()
        return Response(status=204)


class AddCardView(Base):
    """POST {card} – add a card (from a report: "Add to a dashboard")."""
    def post(self, request, pk):
        db = CustomDashboard.objects.filter(pk=pk).first()
        if db is None or (db.owner_id != request.user.pk and not self.c.admin):
            return bad('Choose one of your dashboards.', 404)
        try:
            card = clean_card(request.data.get('card') or request.data)
        except (ValueError, TypeError) as e:
            return bad(str(e))
        db.cards = (db.cards or []) + [card]
        db.save(update_fields=['cards', 'updated_at'])
        return Response(dash_json(db, request.user, self.c, full=True), status=201)


class CardView(Base):
    """GET the figures of one card, run as the person looking at the dashboard."""
    def get(self, request, pk, cid):
        db = CustomDashboard.objects.filter(pk=pk).first()
        if db is None or not B.visible(db, request.user, self.c):
            return bad('Not found.', 404)
        card = next((x for x in db.cards or [] if str(x.get('id')) == str(cid)), None)
        if card is None:
            return bad('Not found.', 404)
        return Response(D.card_data(request, card))


class CardPreviewView(Base):
    def post(self, request):
        try:
            card = clean_card(request.data)
        except (ValueError, TypeError) as e:
            return bad(str(e))
        return Response(D.card_data(request, card))
