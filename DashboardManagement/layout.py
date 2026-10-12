"""
v1.16.0 – dashboard layout per user and per dashboard, kept on the server.

GET  /dashboard/api/layout/<name>/   → {order, hidden, sizes, period, cards}
PUT  /dashboard/api/layout/<name>/   body: the same (validated)
    order   card keys in the order the user put them
    hidden  card keys the user hid
    sizes   {card key: 's' | 'w' | 'f'}  (small / wide / full width)
    period  {preset, from, to}           the dashboard period
    cards   {card key: {preset, from, to}} a card's own period (overrides the dashboard period)
Stored in Chatter.ListLayout under the key "dashboard:<name>" (no new table); hidden / order are mirrored to
Chatter.DashboardLayout for the older designer.

v1.17.0 – company default layout per dashboard (cards, order, sizes, periods) that users start from:
GET  → the user's own layout (source "user"), else the company layout (source "company"), else the built-in one (source "none");
       `company` = a company layout exists, `can_default` = the user may save one (company admin).
PUT  ?company=1   (company admin) save the body as the company layout     – Chatter.ListLayout key "dashboard-company:<name>"
DELETE            the user's own layout → back to the company layout (returned)
DELETE ?company=1 (company admin) remove the company layout
"""
import re

from django.apps import apps
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .period import PRESETS

NAME = re.compile(r'^[a-z0-9_-]{1,30}$')
KEY = re.compile(r'^[A-Za-z0-9_.:-]{1,60}$')


def _period(v):
    if not isinstance(v, dict):
        return None
    p = v.get('preset')
    if p == 'dashboard':   # a card that follows the dashboard period although it has a default of its own
        return {'preset': 'dashboard'}
    if p in PRESETS:
        return {'preset': p}
    if p == 'custom':
        a, b = parse_date(str(v.get('from') or '')), parse_date(str(v.get('to') or ''))
        if a and b:
            return {'preset': 'custom', 'from': min(a, b).isoformat(), 'to': max(a, b).isoformat()}
    return None


def clean(data):
    data = data if isinstance(data, dict) else {}
    keys = lambda v: [str(x) for x in (v or []) if isinstance(x, (str, int)) and KEY.match(str(x))][:200]   # noqa: E731
    sizes = {str(k): v for k, v in (data.get('sizes') or {}).items() if KEY.match(str(k)) and v in ('s', 'w', 'f')} if isinstance(data.get('sizes'), dict) else {}
    cards = {}
    if isinstance(data.get('cards'), dict):
        for k, v in list(data['cards'].items())[:200]:
            p = _period(v)
            if KEY.match(str(k)) and p:
                cards[str(k)] = p
    return {'order': keys(data.get('order')), 'hidden': keys(data.get('hidden')), 'sizes': sizes,
            'period': _period(data.get('period')), 'cards': cards, 'v': 2}


def _admin(request):
    from AccessControl.access import ctx
    return bool(ctx(request).admin)


class LayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def _models(self):
        return apps.get_model('Chatter', 'ListLayout'), apps.get_model('Chatter', 'DashboardLayout')

    def _company(self, name):
        LL, _ = self._models()
        row = LL.objects.filter(key=f'dashboard-company:{name}').order_by('-updated_at').first()
        return clean(row.data) if row and isinstance(row.data, dict) else None

    def _meta(self, request, name, data, source):
        comp = self._company(name)
        return dict(data, source=source, company=comp is not None, can_default=_admin(request))

    def get(self, request, name):
        if not NAME.match(name):
            return Response({'detail': 'Unknown dashboard.'}, status=404)
        LL, DL = self._models()
        row = LL.objects.filter(user=request.user, key=f'dashboard:{name}').first()
        if row and isinstance(row.data, dict) and row.data.get('v') == 2:
            return Response(self._meta(request, name, clean(row.data), 'user'))
        comp = self._company(name)   # v1.17.0: the company layout users start from
        if comp is not None:
            return Response(self._meta(request, name, comp, 'company'))
        old = DL.objects.filter(user=request.user, dashboard=name).first()   # the older designer: hidden / order only
        return Response(self._meta(request, name, clean({'hidden': old.hidden if old else [], 'order': old.order if old else []}), 'none') | {'saved': False})

    def put(self, request, name):
        if not NAME.match(name):
            return Response({'detail': 'Unknown dashboard.'}, status=404)
        LL, DL = self._models()
        data = clean(request.data)
        if request.query_params.get('company') == '1':   # v1.17.0
            if not _admin(request):
                return Response({'detail': 'Only a company admin can save the company layout.'}, status=403)
            LL.objects.filter(key=f'dashboard-company:{name}').exclude(user=request.user).delete()
            LL.objects.update_or_create(user=request.user, key=f'dashboard-company:{name}', defaults={'data': data})
            return Response(self._meta(request, name, data, 'company'))
        LL.objects.update_or_create(user=request.user, key=f'dashboard:{name}', defaults={'data': data})
        DL.objects.update_or_create(user=request.user, dashboard=name[:40], defaults={'hidden': data['hidden'], 'order': data['order']})
        return Response(self._meta(request, name, data, 'user'))

    def delete(self, request, name):
        """Reset: the user's layout → the company layout (or the built-in default); ?company=1 removes the company layout."""
        if not NAME.match(name):
            return Response({'detail': 'Unknown dashboard.'}, status=404)
        LL, DL = self._models()
        if request.query_params.get('company') == '1':
            if not _admin(request):
                return Response({'detail': 'Only a company admin can remove the company layout.'}, status=403)
            LL.objects.filter(key=f'dashboard-company:{name}').delete()
            return Response(self._meta(request, name, clean({}), 'none'))
        LL.objects.filter(user=request.user, key=f'dashboard:{name}').delete()
        DL.objects.filter(user=request.user, dashboard=name).delete()
        comp = self._company(name)
        return Response(self._meta(request, name, comp if comp is not None else clean({}), 'company' if comp is not None else 'none'))
