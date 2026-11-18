"""v1.73.15 – demo data API (Create company › "Do you want demo data?", Choose a company › Demo data).

GET    /users/api/demo-data/            the choices (levels, industries, countries, minutes of each choice), the demo companies, the job
POST   /users/api/demo-data/            {"level": "simple"|"heavy", "scope": "all"|"one", "industry": "CON", "country": "AE"} → 202 {job}
POST   /users/api/demo-data/refresh/    {"level": optional} – the demo companies again with fresh dates → 202 {job}
DELETE /users/api/demo-data/            delete every demo company (only companies marked as demo)
GET    /users/api/demo-data/job/[<id>/] progress of the (last) job

Who may: the users who may create companies (system administrators and company administrators).
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import demo_jobs as J
from .demo_data import COUNTRIES, INDUSTRIES, LEVELS, PLAN, estimate


def may_make(request):
    """System administrators and company administrators (the users who create companies); never a self-service user."""
    user = request.user
    if not getattr(user, 'is_authenticated', False) or getattr(user, 'is_ess', False):
        return False
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    try:
        from AccessControl.access import ctx
        return bool(ctx(request).admin)
    except Exception:   # noqa: BLE001
        return False


def choices():
    levels = [{'key': k, 'title': tr(v['title']), 'text': tr(v['text']), 'months': v['months'],
               'minutes': {'all': estimate(k, 'all'), 'one': estimate(k, 'one')}} for k, v in LEVELS.items()]
    return {
        'levels': levels,
        'scopes': [{'key': 'all', 'title': tr('All industries'), 'text': tr('10 demo companies in the 7 countries')},
                   {'key': 'one', 'title': tr('One industry'), 'text': tr('One demo company of the industry you choose')}],
        'industries': [{'key': k, 'title': tr(v['title']), 'apps': v['apps']} for k, v in INDUSTRIES.items()],
        'countries': [{'key': k, 'title': tr(v['country'])} for k, v in COUNTRIES.items()],
        'plan': [{'key': p[0], 'name': p[3], 'country': COUNTRIES[p[1]]['country'], 'industry': INDUSTRIES[p[2]]['title']} for p in PLAN],
    }


def start_from(data, user):
    """Start the job from a form / API body. → (job, None) or (None, error)."""
    level = str(data.get('level') or data.get('demo') or data.get('demo_level') or '').strip().lower()
    if level in ('', 'none', 'no', 'false', '0'):
        return None, None
    if level not in LEVELS:
        return None, tr('Choose Simple demo or Heavy demo.')
    scope = str(data.get('scope') or data.get('demo_scope') or 'all').strip().lower()
    industry = str(data.get('industry') or data.get('demo_industry') or '').strip().upper()
    # demo_country on Create company (its "country" field is the company's own country); "country" (a code) on this API
    raw_c = str(data.get('country') or '').strip()
    if not data.get('demo_country') and len(raw_c) == 2 and raw_c.isalpha() and raw_c.upper() not in COUNTRIES:
        return None, tr('Choose the country of the demo data.')   # v1.73.18 (merge test): an unknown country code made an AE company
    country = str(data.get('demo_country') or (data.get('country') if str(data.get('country') or '').upper() in COUNTRIES else '') or 'AE').strip().upper()
    if scope == 'one' and industry not in INDUSTRIES:
        return None, tr('Choose the industry of the demo data.')
    if scope == 'one' and country not in COUNTRIES:
        return None, tr('Choose the country of the demo data.')
    if J.running():
        return J.running()['job'], None
    return J.start(level, user, scope='one' if scope == 'one' else 'all', industry=industry, country=country), None


class _Base(APIView):
    permission_classes = [IsAuthenticated]

    def refuse(self, request):
        if not may_make(request):
            return Response({'detail': tr('Only administrators can make or remove demo data.')}, status=403)
        return None


class DemoView(_Base):
    def get(self, request):
        r = self.refuse(request)
        if r:
            return r
        return Response(dict(choices(), companies=J.demo_companies(), job=J.status()))

    def post(self, request):
        r = self.refuse(request)
        if r:
            return r
        job, err = start_from(request.data, request.user)
        if err:
            return Response({'detail': err}, status=400)
        if not job:
            return Response({'detail': tr('Choose Simple demo or Heavy demo.')}, status=400)
        return Response({'job': job, 'status': J.status(job)}, status=202)

    def delete(self, request):
        r = self.refuse(request)
        if r:
            return r
        try:
            gone = J.remove(request.user)
        except ValueError as e:
            return Response({'detail': tr(str(e))}, status=409)
        return Response({'removed': gone})


class DemoRefreshView(_Base):
    def post(self, request):
        r = self.refuse(request)
        if r:
            return r
        try:
            job = J.refresh(request.user, (request.data or {}).get('level'))
        except ValueError as e:
            return Response({'detail': tr(str(e))}, status=400)
        return Response({'job': job, 'status': J.status(job)}, status=202)


class DemoJobView(_Base):
    def get(self, request, job=None):
        st = J.status(job)
        if not st:
            return Response({'detail': tr('No demo data job.')}, status=404)
        return Response(st)
