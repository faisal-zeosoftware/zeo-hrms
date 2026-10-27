"""
v1.16.0 – saved list views API (used by the shared list tools, shared-ui/z-list.directive.ts).

GET    /views/api/views/?screen=<key>        views of a screen the user may use (own + shared), the user's default
POST   /views/api/views/                     save the current list set-up as a named view
PATCH  /views/api/views/<id>/                rename / update the set-up / share or unshare
DELETE /views/api/views/<id>/                delete
POST   /views/api/views/default/             {screen, view_id|null} – the view applied when the screen opens

Personal views belong to their owner. Shared views are visible to everyone in the company; only company admins
and users holding "Can share saved list views with everyone" (SavedViews.share_savedview) may share a view, and
they may also change or delete shared views of other users.
"""
import json

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import ctx, request_schema
from Translations.i18n import tr

from .models import SavedView, SavedViewDefault

VIEW_KINDS = ('list', 'kanban', 'calendar', 'graph', 'pivot')
SIZES = (50, 100, 500, 1000)
MAX_BYTES = 64 * 1024


def can_share(request):
    c = ctx(request)
    return bool(c.admin or 'share_savedview' in c.codes)


def _user_name(u):
    full = ' '.join(x for x in [getattr(u, 'first_name', ''), getattr(u, 'last_name', '')] if x).strip()
    return full or getattr(u, 'username', '') or ''


def my_groups(request):
    """v1.46.0: the role (group) ids of the user in this company – views shared with a team."""
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        utp = UserTenantPermissions.objects.filter(profile=request.user).first()
        return list(utp.groups.values_list('id', flat=True)) if utp else []
    except Exception:
        return []


def _visible(request, qs=None):
    from django.db.models import Q
    qs = SavedView.objects.all() if qs is None else qs
    cond = Q(owner_id=request.user.id) | Q(shared=True)
    for g in my_groups(request):   # v1.46.0 shared with my team
        cond |= Q(share_groups__contains=[g])
    return qs.filter(cond)


def _groups_ok(request, ids):
    """Share with a team: admins / sharers with any role, other users with their own roles."""
    ids = [int(i) for i in (ids or []) if str(i).isdigit()]
    if can_share(request):
        return ids, None
    mine = set(my_groups(request))
    bad = [i for i in ids if i not in mine]
    return ids, (tr('You can share a view only with the teams (roles) you belong to.') if bad else None)


def _can_edit(request, v):
    return v.owner_id == request.user.id or (v.shared and can_share(request))


def _out(request, v, default_id=None):
    return {
        'id': v.id, 'screen': v.screen, 'name': v.name, 'shared': v.shared, 'mine': v.owner_id == request.user.id,
        'share_groups': v.share_groups or [],
        'owner': v.owner_name, 'data': v.data or {}, 'can_edit': _can_edit(request, v),
        'is_default': default_id == v.id, 'updated_at': v.updated_at.isoformat() if v.updated_at else None,
    }


def clean_data(data):
    """Check the saved set-up; returns (data, error)."""
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, tr('The view settings must be an object (search, filters, group by, sort, columns, rows per page, view).')
    try:
        size = len(json.dumps(data))
    except (TypeError, ValueError):
        return None, tr('The view settings could not be read.')
    if size > MAX_BYTES:
        return None, tr('This view is too large to save. Remove some filters and try again.')
    if 'search' in data and not isinstance(data['search'], str):
        return None, tr('Search text must be text.')
    for k in ('chips', 'groupBy'):
        if k in data and not isinstance(data[k], list):
            return None, tr('{0} must be a list.', 'Filters' if k == 'chips' else 'Group by')
    if len(data.get('groupBy') or []) > 3:
        return None, tr('Group by takes at most three levels.')
    if 'sort' in data and data['sort'] is not None:
        s = data['sort']
        if not isinstance(s, dict) or not s.get('col') or s.get('dir') not in (1, -1):
            return None, tr('Sort must name a column and a direction (1 = ascending, -1 = descending).')
    if 'columns' in data and data['columns'] is not None:
        c = data['columns']
        if not isinstance(c, dict):
            return None, tr('Columns must be an object with order, hidden and widths.')
        for k in ('order', 'hide'):
            if k in c and not isinstance(c[k], list):
                return None, tr('Columns {0} must be a list of column names.', k)
        if 'widths' in c:
            w = c['widths']
            if not isinstance(w, dict) or any(not isinstance(x, (int, float)) or x < 20 or x > 2000 for x in w.values()):
                return None, tr('Column widths must be numbers between 20 and 2000 pixels.')
    if 'size' in data and data['size'] is not None and data['size'] not in SIZES:
        return None, tr('Rows per page must be one of 50, 100, 500 or 1000.')
    if 'view' in data and data['view'] is not None and data['view'] not in VIEW_KINDS:
        return None, tr('View must be one of: list, kanban, calendar, graph, pivot.')
    return data, None


def _clean_name(raw):
    name = ' '.join(str(raw or '').split())
    if not name:
        return None, tr('Give the view a name.')
    if len(name) > 80:
        return None, tr('The name can be at most 80 characters.')
    return name, None


def _need_schema(request):
    if not request_schema(request):
        return Response({'detail': tr('Pick a company first.')}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _clash(request, screen, name, exclude=None):
    qs = SavedView.objects.filter(screen=screen, owner_id=request.user.id, name__iexact=name)
    if exclude:
        qs = qs.exclude(pk=exclude)
    return qs.exists()


class SavedViewListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bad = _need_schema(request)
        if bad:
            return bad
        screen = (request.query_params.get('screen') or '').strip()[:255]
        qs = _visible(request)
        default_id = None
        if screen:
            qs = qs.filter(screen=screen)
            d = SavedViewDefault.objects.filter(user_id=request.user.id, screen=screen).first()
            if d:
                if qs.filter(pk=d.view_id).exists():
                    default_id = d.view_id
                else:   # the view was deleted or unshared
                    d.delete()
        else:
            qs = qs.filter(owner_id=request.user.id)
        rows = sorted(qs, key=lambda v: (not v.owner_id == request.user.id, v.name.lower(), v.id))
        return Response({'screen': screen, 'views': [_out(request, v, default_id) for v in rows],
                         'default_id': default_id, 'can_share': can_share(request)})

    def post(self, request):
        bad = _need_schema(request)
        if bad:
            return bad
        d = request.data if isinstance(request.data, dict) else {}
        screen = str(d.get('screen') or '').strip()
        if not screen:
            return Response({'screen': [tr('Screen is required.')], 'detail': tr('Screen is required.')}, status=400)
        if len(screen) > 255:
            return Response({'screen': [tr('Screen can be at most 255 characters.')], 'detail': tr('Screen can be at most 255 characters.')}, status=400)
        name, err = _clean_name(d.get('name'))
        if err:
            return Response({'name': [err], 'detail': err}, status=400)
        data, err = clean_data(d.get('data'))
        if err:
            return Response({'data': [err], 'detail': err}, status=400)
        shared = str(d.get('shared')).lower() in ('true', '1')
        if shared and not can_share(request):
            return Response({'detail': tr('Only company admins and users with the right to share views can share a view with everyone. Save it as a personal view.')}, status=403)
        if _clash(request, screen, name):
            msg = tr('You already have a view named "{0}" on this screen. Pick another name or update that view.', name)
            return Response({'name': [msg], 'detail': msg}, status=400)
        groups, gerr = _groups_ok(request, d.get('share_groups'))
        if gerr:
            return Response({'detail': gerr}, status=403)
        with transaction.atomic():
            v = SavedView.objects.create(screen=screen, name=name, owner_id=request.user.id, owner_name=_user_name(request.user),
                                         shared=shared, data=data, share_groups=groups)
            default_id = None
            if str(d.get('default')).lower() in ('true', '1'):
                SavedViewDefault.objects.update_or_create(user_id=request.user.id, screen=screen, defaults={'view_id': v.id})
                default_id = v.id
        return Response(_out(request, v, default_id), status=201)


class SavedViewDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        return _visible(request).filter(pk=pk).first()

    def get(self, request, pk):
        bad = _need_schema(request)
        if bad:
            return bad
        v = self._get(request, pk)
        if not v:
            return Response({'detail': tr('This view does not exist or is not shared with you.')}, status=404)
        d = SavedViewDefault.objects.filter(user_id=request.user.id, screen=v.screen).first()
        return Response(_out(request, v, d.view_id if d else None))

    def patch(self, request, pk):
        bad = _need_schema(request)
        if bad:
            return bad
        v = self._get(request, pk)
        if not v:
            return Response({'detail': tr('This view does not exist or is not shared with you.')}, status=404)
        if not _can_edit(request, v):
            return Response({'detail': tr('This view was shared by {0}. Save your changes as a new view instead.', v.owner_name or tr('another user'))}, status=403)
        d = request.data if isinstance(request.data, dict) else {}
        if 'name' in d:
            name, err = _clean_name(d.get('name'))
            if err:
                return Response({'name': [err], 'detail': err}, status=400)
            if v.owner_id == request.user.id and _clash(request, v.screen, name, exclude=v.pk):
                msg = tr('You already have a view named "{0}" on this screen. Pick another name or update that view.', name)
                return Response({'name': [msg], 'detail': msg}, status=400)
            v.name = name
        if 'data' in d:
            data, err = clean_data(d.get('data'))
            if err:
                return Response({'data': [err], 'detail': err}, status=400)
            v.data = data
        if 'shared' in d:
            shared = str(d.get('shared')).lower() in ('true', '1')
            if shared != v.shared and not can_share(request):
                return Response({'detail': tr('Only company admins and users with the right to share views can share a view with everyone. Save it as a personal view.')}, status=403)
            v.shared = shared
        if 'share_groups' in d:   # v1.46.0
            groups, gerr = _groups_ok(request, d.get('share_groups'))
            if gerr:
                return Response({'detail': gerr}, status=403)
            v.share_groups = groups
        with transaction.atomic():
            v.save()
            if not v.shared and not v.share_groups:   # unshared: other users can no longer use it as their default
                SavedViewDefault.objects.filter(view_id=v.id).exclude(user_id=v.owner_id).delete()
        dd = SavedViewDefault.objects.filter(user_id=request.user.id, screen=v.screen).first()
        return Response(_out(request, v, dd.view_id if dd else None))

    put = patch

    def delete(self, request, pk):
        bad = _need_schema(request)
        if bad:
            return bad
        v = self._get(request, pk)
        if not v:
            return Response({'detail': tr('This view does not exist or is not shared with you.')}, status=404)
        if not _can_edit(request, v):
            return Response({'detail': tr('Only the person who saved this view or a company admin can delete it.')}, status=403)
        with transaction.atomic():
            SavedViewDefault.objects.filter(view_id=v.id).delete()
            v.delete()
        return Response(status=204)


class SavedViewDefaultView(APIView):
    """Make a view my default on its screen (view_id null: no default)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        bad = _need_schema(request)
        if bad:
            return bad
        d = request.data if isinstance(request.data, dict) else {}
        screen = str(d.get('screen') or '').strip()[:255]
        vid = d.get('view_id')
        if not screen:
            return Response({'screen': [tr('Screen is required.')], 'detail': tr('Screen is required.')}, status=400)
        if vid in (None, '', 0, '0'):
            SavedViewDefault.objects.filter(user_id=request.user.id, screen=screen).delete()
            return Response({'screen': screen, 'default_id': None})
        try:
            vid = int(vid)
        except (TypeError, ValueError):
            return Response({'view_id': [tr('Pick a saved view.')], 'detail': tr('Pick a saved view.')}, status=400)
        v = _visible(request).filter(pk=vid).first()
        if not v:
            return Response({'detail': tr('This view does not exist or is not shared with you.')}, status=404)
        if v.screen != screen:
            return Response({'view_id': [tr('This view belongs to another screen.')], 'detail': tr('This view belongs to another screen.')}, status=400)
        SavedViewDefault.objects.update_or_create(user_id=request.user.id, screen=screen, defaults={'view_id': v.id})
        return Response({'screen': screen, 'default_id': v.id})



class SavedViewGroupsView(APIView):
    """v1.46.0 GET views/groups/ – the teams (roles) a view can be shared with."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bad = _need_schema(request)
        if bad:
            return bad
        from django.contrib.auth.models import Group
        mine = set(my_groups(request))
        from ZeoApps import groupscope   # v1.62.0 isolation: the groups of this company (all groups of the server were offered)
        qs = Group.objects.filter(pk__in=groupscope.visible_ids()) if can_share(request) else Group.objects.filter(pk__in=mine)
        return Response([{'id': g.id, 'name': groupscope.label(g), 'mine': g.id in mine} for g in qs.order_by('name')])
