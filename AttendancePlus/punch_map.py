"""v1.69.0 – the punch map: the geofences of the company and the punches of a day on one map.

GET attendance-plus/api/punch-map/?date=YYYY-MM-DD&branch=1,2&employee=5&outside=1
  fences  – the active geofences (name, type, centre, radius, branches, how many employees are tied to it)
  punches – the punches of that day that carry a location: employee, time, in / out, source, inside or outside the geofence,
            the nearest geofence and the distance to its edge (metres)
  summary – punches with a location, inside, outside, without a location
HR (the attendance rights) sees their branches; anyone else gets 403.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import math
import re
from datetime import date

from django.apps import apps
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from .access import c, is_hr

M = apps.get_model


def metres(lat1, lng1, lat2, lng2):
    """great-circle distance (haversine), metres"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest(lat, lng, fences):
    """(fence, metres outside its edge – 0 or less = inside) of the fence whose edge is nearest"""
    best = None
    for f in fences:
        d = metres(lat, lng, f['lat'], f['lng']) - f['radius']
        if best is None or d < best[1]:
            best = (f, d)
    return best or (None, None)


class PunchMapView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        x = c(request)
        if not is_hr(request):
            return Response({'detail': tr('This page is for HR users only.')}, status=403)
        q = request.query_params
        try:
            day = date.fromisoformat(q.get('date')) if q.get('date') else _zt_today()
        except ValueError:
            return Response({'detail': tr('Give the date as YYYY-MM-DD.')}, status=400)
        allowed = None if (x.admin or x.branches is None) else set(x.branches)
        picked = {int(b) for b in re.findall(r'\d+', q.get('branch', '') or '')}
        branches = (picked & allowed if allowed is not None else picked) if picked else allowed
        if picked and allowed is not None and not branches:
            return Response({'detail': tr('This branch is not one of yours.')}, status=403)
        Emp = M('EmpManagement', 'emp_master')
        eq = Emp.objects.all()
        if branches is not None:
            eq = eq.filter(emp_branch_id__in=branches)
        if q.get('employee'):
            if not str(q.get('employee')).isdigit():
                return Response({'detail': tr('Unknown employee.')}, status=400)
            eq = eq.filter(pk=int(q['employee']))
        emps = {e['id']: e for e in eq.values('id', 'emp_code', 'emp_first_name', 'emp_last_name', 'emp_branch_id', 'emp_branch_id__branch_name')}
        G = M('OrganisationManager', 'BranchGeoFence')
        fences = []
        for g in G.objects.filter(is_active=True).prefetch_related('branch'):
            bids = [b.pk for b in g.branch.all()]
            if branches is not None and bids and not set(bids) & branches:
                continue
            fences.append({'id': g.pk, 'name': g.location_name, 'type': g.get_location_type_display() if g.location_type else '',
                           'lat': float(g.latitude), 'lng': float(g.longitude), 'radius': int(g.radius or 0),
                           'branches': [b.branch_name for b in g.branch.all()], '_bids': set(bids),
                           '_emps': set(g.employee.values_list('id', flat=True))})
        P = M('AttendancePlus', 'Punch')
        ps = P.objects.filter(work_date=day, is_void=False, employee_id__in=list(emps)).order_by('ts')
        out, no_loc = [], 0
        for p in ps:
            e = emps.get(p.employee_id)
            if p.lat is None or p.lng is None:
                no_loc += 1
                continue
            lat, lng = float(p.lat), float(p.lng)
            # the geofences that apply to this employee: tied to them, or (none tied) of their branch / of every branch
            mine = [g for g in fences if g['_emps'] and e['id'] in g['_emps']] or \
                [g for g in fences if not g['_emps'] and (not g['_bids'] or e['emp_branch_id'] in g['_bids'])]
            f, d = nearest(lat, lng, mine)
            inside = None if f is None else d <= 0
            if p.geofence_ok is not None and f is None:
                inside = bool(p.geofence_ok)
            out.append({'id': p.pk, 'employee_id': p.employee_id, 'employee': f"{e['emp_first_name'] or ''} {e['emp_last_name'] or ''}".strip(),
                        'code': e['emp_code'], 'branch': e['emp_branch_id__branch_name'] or '',
                        'time': timezone.localtime(p.ts).strftime('%H:%M'), 'kind': p.resolved_kind or p.kind, 'source': p.get_source_display(),
                        'lat': lat, 'lng': lng, 'location': p.location or '', 'inside': inside, 'recorded_ok': p.geofence_ok,
                        'fence': f['name'] if f else '', 'metres_outside': round(max(d, 0)) if f else None})
        if q.get('outside') in ('1', 'true'):
            out = [p for p in out if p['inside'] is False]
        summary = {'with_location': len(out) if q.get('outside') not in ('1', 'true') else None, 'inside': sum(1 for p in out if p['inside']),
                   'outside': sum(1 for p in out if p['inside'] is False), 'no_location': no_loc, 'fences': len(fences),
                   'employees': len({p['employee_id'] for p in out})}
        for g in fences:
            g['employees'] = len(g.pop('_emps'))
            g.pop('_bids')
        return Response({'date': day.isoformat(), 'fences': fences, 'punches': out, 'summary': summary})
