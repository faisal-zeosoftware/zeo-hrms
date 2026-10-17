"""v1.14.0 – Country packs API (base /country-pack/api/, company by ?schema= like every other call).

GET  current/          any logged-in user (ESS too): the resolved pack
GET  packs/            company admin: the packs and the company country
GET  settings/         company admin: pack_override, label_overrides, feature_overrides (+ the pack defaults for the screen)
PATCH settings/        company admin; unknown keys → 400 naming the key
POST apply-defaults/   company admin: loads the pack's defaults (idempotent), returns what was added
"""
from django.db import connection
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .models import CountryPackSettings
from .packs import FEATURE_KEYS, FEATURE_TITLES, LABEL_KEYS, LABEL_TITLES, PACKS
from Translations.i18n import tr  # v1.15.0 language packs: messages in the user's language

ADMIN_ONLY = 'Only the company administrator can see or change the country settings.'
MAX_LABEL = 60


def _admin(request):
    try:
        from AccessControl.access import ctx
        return bool(ctx(request).admin)
    except Exception:
        return bool(getattr(request.user, 'is_superuser', False))


def _tenant(request):
    s = getattr(connection, 'schema_name', 'public')
    return None if s == 'public' else s


class _Admin(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if _tenant(request) is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': tr('Choose a company first (?schema=).')})
        if not _admin(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(ADMIN_ONLY)


class CurrentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(S.public_pack(S.current_pack(_tenant(request) or 'public')))


class PacksView(_Admin):
    def get(self, request):
        p = S.current_pack()
        return Response({'packs': S.packs_list(), 'pack': p['pack'], 'auto_pack': p['_auto_pack'],
                         'country': {'code': p['country_code'], 'name': p['country_name']}})


def settings_payload(row=None):
    row = row or CountryPackSettings.get()
    p = S.current_pack()
    return {
        'pack_override': row.pack_override or None,
        'label_overrides': {k: v for k, v in (row.label_overrides or {}).items() if k in LABEL_KEYS},
        'feature_overrides': {k: v for k, v in (row.feature_overrides or {}).items() if k in FEATURE_KEYS},
        'updated_at': row.updated_at,
        # for the Country settings screen
        'pack': p['pack'], 'auto_pack': p['_auto_pack'], 'country': {'code': p['country_code'], 'name': p['country_name']},
        'defaults': p['_defaults'], 'resolved': S.public_pack(p),
        'label_titles': [{'key': k, 'title': LABEL_TITLES[k]} for k in LABEL_KEYS],
        'feature_titles': [{'key': k, 'title': FEATURE_TITLES[k][0], 'help': FEATURE_TITLES[k][1]} for k in FEATURE_KEYS],
        'packs': S.packs_list(),
    }


class SettingsView(_Admin):
    def get(self, request):
        return Response(settings_payload())

    def patch(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        allowed = {'pack_override', 'label_overrides', 'feature_overrides'}
        for k in data:
            if k not in allowed:
                return Response({'detail': tr('Unknown setting "{0}". Use pack_override, label_overrides or feature_overrides.', k), 'field': k},
                                status=status.HTTP_400_BAD_REQUEST)
        row = CountryPackSettings.get()
        if 'pack_override' in data:
            v = data.get('pack_override')
            v = (str(v).strip().upper() if v not in (None, '') else '')
            if v and v not in PACKS:
                return Response({'detail': tr('Unknown pack "{0}". Use one of {1} or leave it empty for the company country.', v, ", ".join(PACKS)),
                                 'field': 'pack_override'}, status=status.HTTP_400_BAD_REQUEST)
            row.pack_override = v
        # validate first, compare with the pack defaults after the (possibly new) pack is known
        lo = fo = None
        if 'label_overrides' in data:
            lo = data.get('label_overrides') or {}
            if not isinstance(lo, dict):
                return Response({'detail': tr('label_overrides must be an object {key: caption}.'), 'field': 'label_overrides'}, status=400)
            for k, v in lo.items():
                if k not in LABEL_KEYS:
                    return Response({'detail': tr('Unknown label key "{0}".', k), 'field': f'label_overrides.{k}'}, status=400)
                if v is not None and not isinstance(v, str):
                    return Response({'detail': tr('The caption for "{0}" must be text.', k), 'field': f'label_overrides.{k}'}, status=400)
                if v and len(v.strip()) > MAX_LABEL:
                    return Response({'detail': tr('The caption for "{0}" is too long – use up to {1} characters.', k, MAX_LABEL), 'field': f'label_overrides.{k}'},
                                    status=400)
        if 'feature_overrides' in data:
            fo = data.get('feature_overrides') or {}
            if not isinstance(fo, dict):
                return Response({'detail': tr('feature_overrides must be an object {key: true/false}.'), 'field': 'feature_overrides'}, status=400)
            for k, v in fo.items():
                if k not in FEATURE_KEYS:
                    return Response({'detail': tr('Unknown feature key "{0}".', k), 'field': f'feature_overrides.{k}'}, status=400)
                if v is not None and not isinstance(v, bool):
                    return Response({'detail': tr('The feature "{0}" must be true, false or null (pack default).', k), 'field': f'feature_overrides.{k}'},
                                    status=400)
        row.updated_by_id = request.user.pk
        row.save()
        S.clear_cache()
        defaults = S.current_pack()['_defaults']
        if lo is not None:   # keep only real changes: an empty caption or the pack's own caption = back to the default
            row.label_overrides = {k: v.strip() for k, v in lo.items() if v and v.strip() and v.strip() != defaults['labels'].get(k)}
        if fo is not None:
            row.feature_overrides = {k: v for k, v in fo.items() if v is not None and v != defaults['features'].get(k)}
        if lo is not None or fo is not None:
            row.save()
            S.clear_cache()
        _note(request, row, data)
        return Response(settings_payload(row))


def _note(request, row, data):
    """History note on the settings row (Chatter), when the app is there."""
    try:
        from Chatter.models import Message
        parts = []
        if 'pack_override' in data:
            parts.append(f'pack: {row.pack_override or "from the company country"}')
        if 'label_overrides' in data:
            parts.append(f'{len(row.label_overrides or {})} changed caption(s)')
        if 'feature_overrides' in data:
            parts.append(', '.join(f'{k} {"on" if v else "off"}' for k, v in (row.feature_overrides or {}).items()) or 'features: pack defaults')
        from Chatter.tracking import model_key
        Message.objects.create(model=model_key(type(row)), object_id=str(row.pk), author=request.user,
                               body='Country settings changed – ' + '; '.join(parts))
    except Exception:
        pass


class ApplyDefaultsView(_Admin):
    def post(self, request):
        # v1.26.0: the pack picked on the screen is saved first, so "Apply country defaults" uses it (before, the
        # saved pack was used and the user had to press Save first).
        data = request.data if isinstance(request.data, dict) else {}
        if 'pack_override' in data:
            v = data.get('pack_override')
            v = (str(v).strip().upper() if v not in (None, '') else '')
            if v and v not in PACKS:
                return Response({'detail': tr('Unknown pack "{0}". Use one of {1} or leave it empty for the company country.', v, ", ".join(PACKS)),
                                 'field': 'pack_override'}, status=status.HTTP_400_BAD_REQUEST)
            row = CountryPackSettings.get()
            if (row.pack_override or '') != v:
                row.pack_override = v
                row.updated_by_id = request.user.pk
                row.save()
                S.clear_cache()
                _note(request, row, {'pack_override': v})
        res = S.apply_defaults(request.user)
        res['settings'] = settings_payload()
        return Response(res)
