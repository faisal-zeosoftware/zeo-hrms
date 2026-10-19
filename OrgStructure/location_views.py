"""v1.30.0 – Location level API (mounted under /org-structure/api/): control settings, user location access, location
numbering, the record's location and the switcher payload. Rights as in views.py (module docstring)."""
from datetime import timedelta

from django.apps import apps
from django.utils import timezone
from django.db.models import Count, Q
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr
from . import location as L, services as S
from .models import BranchProfile, CompanyProfile, Location, LocationNumbering, OrgSettings, RecordLocation, UserLocationAccess
from .views import Base, deny, _ints, _schema

M = apps.get_model


def _users_of_company(request):
    User = M('UserManagement', 'CustomUser')
    from django.db import connection
    sch = _schema(request) or connection.schema_name
    return User.objects.filter(tenants__schema_name=sch).distinct().order_by('username')


# ------------------------------------------------------------------ settings
class LocationControlView(Base, APIView):
    def get(self, request):
        s = OrgSettings.get()
        chosen = [k for k in (s.location_models or []) if isinstance(k, str)]
        cands = L.candidate_models()
        for c in cands:
            c['on'] = (c['key'] in chosen) if chosen else c['default']
        return Response({
            'location_control': s.location_control, 'location_required': s.location_required,
            'location_numbering': s.location_numbering, 'location_restrict': s.location_restrict,
            'use_locations': s.use_locations, 'label': s.label('locations'),
            'models': cands, 'chosen': chosen,
            'stats': {'locations': Location.objects.filter(active=True).count(),
                      'without_branch': Location.objects.filter(active=True, branch_id__isnull=True).count(),
                      'users_restricted': UserLocationAccess.objects.exclude(location_ids=[]).count(),
                      'series': LocationNumbering.objects.count(), 'tagged': RecordLocation.objects.count()},
            'can_change': S.is_hr_settings(request),
        })

    def put(self, request):
        if not S.is_hr_settings(request):
            return deny(tr('Only HR administrators can change the organisation settings.'))
        s = OrgSettings.get()
        d = request.data if isinstance(request.data, dict) else {}
        for k in ('location_control', 'location_required', 'location_numbering', 'location_restrict'):
            if k in d:
                setattr(s, k, str(d[k]).lower() in ('true', '1', 'yes', 'on'))
        if s.location_control:
            s.use_locations = True
            if Location.objects.filter(active=True, branch_id__isnull=True).exists():
                return deny(tr('Every active location needs a branch before location-level control can be switched on.'), 400)
        if 'models' in d:
            known = {c['key'] for c in L.candidate_models()}
            s.location_models = [k for k in (d.get('models') or []) if isinstance(k, str) and k in known]
        s.updated_by_id = request.user.pk
        s.save()
        L.invalidate()
        return self.get(request)

    post = put
    patch = put


# ------------------------------------------------------------------ user access
class LocationAccessView(Base, APIView):
    def _can(self, request):
        return S.has(request, 'change_userbranchaccess', 'add_userbranchaccess', 'change_orgsettings')

    def get(self, request):
        if not S.has(request, 'view_userbranchaccess', 'change_userbranchaccess', 'change_orgsettings'):
            return deny(tr('You may not see the users\' location access.'))
        rows = {r.user_id: r.location_ids for r in UserLocationAccess.objects.all()}
        names = dict(Location.objects.values_list('id', 'name'))
        Emp = M('EmpManagement', 'emp_master')
        emp_of = {}
        for e in Emp.objects.filter(users__isnull=False).values('id', 'emp_code', 'emp_first_name', 'users'):
            emp_of[e['users']] = e
        from .models import EmployeeOrg
        own = dict(EmployeeOrg.objects.filter(location_id__isnull=False).values_list('employee_id', 'location_id'))
        out = []
        for u in _users_of_company(request):
            ids = [int(i) for i in rows.get(u.pk, []) if str(i).isdigit()]
            e = emp_of.get(u.pk)
            out.append({'user_id': u.pk, 'username': u.username, 'email': u.email, 'is_superuser': u.is_superuser,
                        'employee': f"{e['emp_code']} – {e['emp_first_name']}" if e else '',
                        'own_location_id': own.get(e['id']) if e else None,
                        'own_location': names.get(own.get(e['id'])) if e else '',
                        'location_ids': ids, 'locations': ', '.join(names.get(i, f'#{i}') for i in ids)})
        return Response({'users': out, 'can_change': self._can(request),
                         'locations': [{'id': l.id, 'name': l.name, 'code': l.code, 'branch_id': l.branch_id}
                                       for l in Location.objects.filter(active=True).order_by('branch_id', 'code')]})

    def put(self, request):
        if not self._can(request):
            return deny(tr('You may not change the users\' location access.'))
        d = request.data if isinstance(request.data, dict) else {}
        uid = d.get('user_id')
        if not str(uid).isdigit():
            return deny(tr('Choose a user.'), 400)
        ids = _ints(d.get('location_ids'))
        known = set(Location.objects.values_list('id', flat=True))
        bad = [i for i in ids if i not in known]
        if bad:
            return deny(tr('Location {0} does not exist.', bad[0]), 400)
        allowed = L.allowed_locations(request)
        if allowed is not None and any(i not in allowed for i in ids):
            return deny(tr('You can only give access to your own locations.'))
        UserLocationAccess.objects.update_or_create(user_id=int(uid), defaults={'location_ids': ids, 'updated_by_id': request.user.pk})
        return Response({'ok': True, 'user_id': int(uid), 'location_ids': ids})


# ------------------------------------------------------------------ numbering
class LocationNumberingSerializer(serializers.ModelSerializer):
    location_name = serializers.SerializerMethodField()
    type_label = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()

    class Meta:
        model = LocationNumbering
        fields = '__all__'
        read_only_fields = ['created_at', 'created_by_id', 'current_number']

    def get_location_name(self, o):
        return o.location.name if o.location_id else ''

    def get_type_label(self, o):
        return dict(M('OrganisationManager', 'DocumentNumbering').DOCUMENT_TYPES).get(o.type, o.type)

    def get_preview(self, o):
        base = f'{o.prefix or ""}{o.suffix or ""}'
        space = max(o.total_length - len(base) - 1, 1)
        return f'{base}-{str(o.current_number + 1).zfill(space)}'

    def validate_type(self, v):
        if v not in dict(M('OrganisationManager', 'DocumentNumbering').DOCUMENT_TYPES):
            raise serializers.ValidationError(tr('Unknown document type.'))
        return v

    def validate(self, d):
        prefix = d.get('prefix', getattr(self.instance, 'prefix', '')) or ''
        suffix = d.get('suffix', getattr(self.instance, 'suffix', '')) or ''
        total = d.get('total_length', getattr(self.instance, 'total_length', 10)) or 0
        if total < len(prefix) + len(suffix) + 2:
            raise serializers.ValidationError({'total_length': tr('Total length is too short for the given prefix and suffix.')})
        loc = d.get('location', getattr(self.instance, 'location', None))
        typ = d.get('type', getattr(self.instance, 'type', None))
        qs = LocationNumbering.objects.filter(location=loc, type=typ)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({'type': tr('A numbering already exists for this location and type.')})
        # the same prefix on two locations (or a branch) gives the same numbers
        clash = LocationNumbering.objects.filter(type=typ, prefix__iexact=prefix, suffix__iexact=suffix).exclude(location=loc)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        DN = M('OrganisationManager', 'DocumentNumbering')
        if clash.exists() or DN.objects.filter(type=typ, prefix__iexact=prefix, suffix__iexact=suffix or '').exists():
            raise serializers.ValidationError({'prefix': tr('Prefix {0} is already used for this document type – choose a different prefix per location.', prefix)})
        return d


class LocationNumberingViewSet(Base, viewsets.ModelViewSet):
    queryset = LocationNumbering.objects.select_related('location')
    serializer_class = LocationNumberingSerializer

    def _can(self):
        return S.has(self.request, 'change_documentnumbering', 'add_documentnumbering', 'change_orgsettings')

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params
        if q.get('location'):
            qs = qs.filter(location_id=q['location'])
        if q.get('type'):
            qs = qs.filter(type=q['type'])
        allowed = L.allowed_locations(self.request)
        if allowed is not None:
            qs = qs.filter(location_id__in=allowed)
        return qs

    def create(self, request, *a, **kw):
        if not self._can():
            return deny(tr('You may not change document numbering.'))
        return super().create(request, *a, **kw)

    def perform_create(self, serializer):
        serializer.save(created_by_id=self.request.user.pk)

    def update(self, request, *a, **kw):
        if not self._can():
            return deny(tr('You may not change document numbering.'))
        return super().update(request, *a, **kw)

    def destroy(self, request, *a, **kw):
        if not self._can():
            return deny(tr('You may not change document numbering.'))
        return super().destroy(request, *a, **kw)

    def list(self, request, *a, **kw):
        res = super().list(request, *a, **kw)
        DN = M('OrganisationManager', 'DocumentNumbering')
        return Response({'rows': res.data, 'types': [{'value': k, 'label': v} for k, v in DN.DOCUMENT_TYPES],
                         'locations': [{'id': l.id, 'name': l.name, 'code': l.code, 'branch_id': l.branch_id}
                                       for l in Location.objects.filter(active=True).order_by('branch_id', 'code')],
                         'can_change': self._can(), 'numbering_on': L.flag('numbering')})


# ------------------------------------------------------------------ a record's location
class RecordLocationView(Base, APIView):
    def _model(self, request):
        key = str(request.query_params.get('model') or (request.data or {}).get('model') or '')
        if '.' not in key:
            return None
        try:
            return apps.get_model(*key.split('.', 1))
        except LookupError:
            return None

    def get(self, request):
        model = self._model(request)
        rid = request.query_params.get('id')
        if model is None or not str(rid).isdigit():
            return Response({'enabled': L.enabled(), 'applies': False})
        applies = L.enabled() and L.has_location(model) and L.model_key(model) not in L.ALWAYS
        t = L.get_tag(model, int(rid)) if applies else None
        allowed = L.allowed_locations(request)
        qs = Location.objects.filter(active=True)
        if allowed is not None:
            qs = qs.filter(id__in=allowed)
        can = S.has(request, f'change_{model._meta.model_name}', 'change_orgsettings')
        return Response({'enabled': L.enabled(), 'applies': applies, 'label': L.settings_row().label('locations') if L.settings_row() else 'Location',
                         'location_id': t.location_id if t else None, 'location': t.location.name if t else '',
                         'can_change': bool(can), 'options': [{'id': l.id, 'name': l.name, 'branch_id': l.branch_id} for l in qs.order_by('branch_id', 'code')]})

    def put(self, request):
        model = self._model(request)
        d = request.data if isinstance(request.data, dict) else {}
        rid, loc = d.get('id'), d.get('location')
        if model is None or not str(rid).isdigit():
            return deny(tr('Record not found.'), 404)
        if not S.has(request, f'change_{model._meta.model_name}', 'change_orgsettings'):
            return deny(tr('You may not change this record.'))
        if not L.enabled() or not L.has_location(model):
            return deny(tr('Location-level control is off for this record type.'), 400)
        if loc in (None, ''):
            RecordLocation.objects.filter(model=L.model_key(model), record_id=int(rid)).delete()
            return Response({'ok': True, 'location_id': None})
        if not str(loc).isdigit() or not Location.objects.filter(pk=int(loc)).exists():
            return deny(tr('Choose a location from the list.'), 400)
        allowed = L.allowed_locations(request)
        if allowed is not None and int(loc) not in allowed:
            return deny(tr('You can only work with records of your own locations.'))
        L.tag(model, type('o', (), {'pk': int(rid)})(), int(loc), request.user.pk)
        return Response({'ok': True, 'location_id': int(loc), 'location': Location.objects.get(pk=int(loc)).name})


class MyLocationsView(Base, APIView):
    """The switcher: flags, the user's locations by branch, the working location."""
    def get(self, request):
        return Response(L.payload(request))


class LocationReportView(Base, APIView):
    """Records per location and record type (for the control page and reports)."""
    def get(self, request):
        if not L.enabled():
            return Response({'rows': []})
        names = dict(Location.objects.values_list('id', 'name'))
        labels = {c['key']: c['label'] for c in L.candidate_models()}
        rows = RecordLocation.objects.values('location_id', 'model').annotate(n=Count('id')).order_by('location_id', 'model')
        allowed = L.effective_locations(request)
        out = [{'location_id': r['location_id'], 'location': names.get(r['location_id'], ''), 'model': r['model'],
                'label': labels.get(r['model'], r['model']), 'count': r['n']} for r in rows
               if allowed is None or r['location_id'] in allowed]
        return Response({'rows': out})


# ================================================================== v1.31.0 company and branch profiles
def _pack_regs():
    try:
        from CountryPack import services as cp
        from CountryPack.packs import registrations_for
        p = cp.current_pack()
        return p['pack'], p.get('name', ''), registrations_for(p['pack'])
    except Exception:
        from CountryPack.packs import REG_NEUTRAL
        return 'GEN', '', dict(REG_NEUTRAL)


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfile
        fields = '__all__'
        read_only_fields = ['updated_at', 'updated_by_id']

    def validate_fiscal_year_start_month(self, v):
        if v in (None, '') or 1 <= int(v) <= 12:
            return v or None
        raise serializers.ValidationError(tr('Month 1–12.'))

    def validate_bank_accounts(self, v):
        if v in (None, ''):
            return []
        if isinstance(v, str):
            import json
            try:
                v = json.loads(v)
            except Exception:
                raise serializers.ValidationError(tr('Bank accounts must be a list.'))
        if not isinstance(v, list):
            raise serializers.ValidationError(tr('Bank accounts must be a list.'))
        return [{k: str(x.get(k, '') or '') for k in ('bank', 'account_name', 'account_no', 'iban', 'swift', 'currency', 'branch')} | {'wps': bool(x.get('wps'))}
                for x in v if isinstance(x, dict)]

    def validate_extra_regs(self, v):
        if v in (None, ''):
            return {}
        if isinstance(v, str):
            import json
            try:
                v = json.loads(v)
            except Exception:
                raise serializers.ValidationError(tr('Registrations must be a list of values.'))
        return {str(k)[:40]: str(x or '')[:80] for k, x in (v or {}).items()} if isinstance(v, dict) else {}


class CompanyProfileView(Base, APIView):
    """Settings → Company → Legal & registration. GET everybody of the company (screens print letters with it); PUT company admin /
    change_company / change_orgsettings. Multipart accepted (signature, stamp)."""
    def _can(self, request):
        return S.has(request, 'change_company', 'change_orgsettings')

    def _payload(self, request):
        row = CompanyProfile.get()
        pack, pack_name, regs = _pack_regs()
        d = CompanyProfileSerializer(row, context={'request': request}).data
        d.update({'pack': pack, 'pack_name': pack_name, 'labels': regs, 'can_change': self._can(request),
                  'company_types': CompanyProfile.COMPANY_TYPES, 'jurisdictions': [j for j in CompanyProfile.JURISDICTIONS if j[0]],
                  'expiring': [k for k in ('licence_expiry', 'establishment_expiry') if getattr(row, k) and (getattr(row, k) - timezone.now().date()).days <= 60]})
        return d

    def get(self, request):
        return Response(self._payload(request))

    def put(self, request):
        if not self._can(request):
            return deny(tr('Only company administrators can change the company details.'))
        row = CompanyProfile.get()
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        for k in ('signature', 'stamp'):   # keep the file unless a new one or an explicit clear ('') is sent
            if k in data and data.get(k) in (None, '', 'null'):
                data.pop(k, None)
                if str(request.data.get(k + '_clear', '')).lower() in ('1', 'true'):
                    setattr(row, k, None)
        ser = CompanyProfileSerializer(row, data=data, partial=True, context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save(updated_by_id=request.user.pk)
        return Response(self._payload(request))

    post = put
    patch = put


class BranchProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BranchProfile
        fields = '__all__'
        read_only_fields = ['updated_at', 'updated_by_id', 'branch_id']

    def validate_bank_accounts(self, v):
        return CompanyProfileSerializer.validate_bank_accounts(self, v)

    def validate_extra_regs(self, v):
        return CompanyProfileSerializer.validate_extra_regs(self, v)


class BranchProfileView(Base, APIView):
    """GET / PUT api/branch-profile/<branch_id>/ – the optional details of a branch."""
    def _can(self, request, branch_id):
        if not S.has(request, 'change_brnch_mstr', 'change_orgsettings'):
            return False
        allowed = S.user_branches(request)
        return allowed is None or int(branch_id) in allowed

    def _payload(self, request, branch_id):
        row = BranchProfile.objects.filter(branch_id=branch_id).first() or BranchProfile(branch_id=branch_id)
        pack, pack_name, regs = _pack_regs()
        d = BranchProfileSerializer(row).data
        d.update({'pack': pack, 'labels': regs, 'branch_types': BranchProfile.BRANCH_TYPES, 'can_change': self._can(request, branch_id),
                  'exists': bool(row.pk)})
        return d

    def get(self, request, branch_id):
        if not M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).exists():
            return deny(tr('Branch {0} does not exist.', branch_id), 404)
        return Response(self._payload(request, branch_id))

    def put(self, request, branch_id):
        if not M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).exists():
            return deny(tr('Branch {0} does not exist.', branch_id), 404)
        if not self._can(request, branch_id):
            return deny(tr('You may not change this branch.'))
        row = BranchProfile.objects.filter(branch_id=branch_id).first() or BranchProfile(branch_id=branch_id)
        ser = BranchProfileSerializer(row, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = ser.save(updated_by_id=request.user.pk, branch_id=int(branch_id))
        if obj.is_default:
            BranchProfile.objects.filter(is_default=True).exclude(pk=obj.pk).update(is_default=False)
        return Response(self._payload(request, branch_id))

    post = put
    patch = put


class ExpiryView(Base, APIView):
    """Company / branch / location registrations that expire within ?days= (default 90) – for the expiring-documents page."""
    def get(self, request):
        days = int(request.query_params.get('days') or 90)
        today = timezone.now().date()
        limit = today + timedelta(days=days)
        out = []
        pack, _, regs = _pack_regs()
        cp = CompanyProfile.get()
        for f, lab in (('licence_expiry', regs.get('licence_no')), ('establishment_expiry', regs.get('establishment_no'))):
            d = getattr(cp, f)
            if d and d <= limit:
                out.append({'kind': 'Company', 'name': lab, 'number': getattr(cp, f.replace('_expiry', '_no')), 'expires': d, 'days': (d - today).days})
        names = dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('id', 'branch_name'))
        for b in BranchProfile.objects.all():
            for f, lab in (('licence_expiry', regs.get('licence_no')), ('establishment_expiry', regs.get('establishment_no'))):
                d = getattr(b, f)
                if d and d <= limit:
                    out.append({'kind': 'Branch', 'name': f'{names.get(b.branch_id, b.branch_id)} – {lab}', 'number': getattr(b, f.replace('_expiry', '_no')), 'expires': d, 'days': (d - today).days})
        for l in Location.objects.filter(active=True, permit_expiry__isnull=False, permit_expiry__lte=limit):
            out.append({'kind': 'Location', 'name': f'{l.name} – permit', 'number': l.permit_no, 'expires': l.permit_expiry, 'days': (l.permit_expiry - today).days})
        out.sort(key=lambda r: r['expires'])
        return Response({'rows': out, 'days': days})
