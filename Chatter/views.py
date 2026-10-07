"""
Record panel API (all under /chatter/api/, company chosen with ?schema= like every other API).

A record is addressed by the screen's list API (`endpoint`, e.g. /organisation/api/Department/) and
its id. The endpoint is resolved to its view, so the model and the access rules are exactly the
ones of that screen: whoever can open the record can see and add notes, activities and files.

GET    record/?endpoint=&id=            everything for one record (history, notes, files, activities, fields)
POST   notes/                           {endpoint, id, body, attachment_ids}
DELETE notes/<pk>/                      own note (admins: any)
POST   attachments/                     multipart: endpoint, id, file [, field]
GET    attachments/<pk>/download/
DELETE attachments/<pk>/
POST   activities/                      {endpoint, id, activity_type, summary, note, due_date, assigned_to, page}
PATCH  activities/<pk>/                 {state: done|cancelled|open, feedback, due_date, summary, note, assigned_to}
GET    todo/?scope=mine|created|all     the user's activities (To-do)
GET    users/                           users of the company (to assign activities)
GET/POST/PUT/DELETE fields/             form designer: extra fields of a screen (?screen=<endpoint>)
GET    fields/screens/                  screens that can get extra fields
PUT    values/                          {endpoint, id, values: {name: value}}
GET    values/?endpoint=&ids=1,2        extra field values for list columns and export
GET/PUT layout/dashboard/?name=main     dashboard designer (per user)
GET/PUT layout/list/?key=               column chooser and last view (per user)
GET    orgchart/                        employees with their manager, by the user's rights
"""
import datetime
import mimetypes
import re

from django.db import connection
from django.db.models import Q
from django.http import FileResponse, Http404
from django.urls import resolve, Resolver404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Activity, Attachment, DashboardLayout, FieldDef, FieldValue, ListLayout, Message, RecordLog, FIELD_TYPES
from .tracking import model_key

MAX_FILE = 25 * 1024 * 1024
TYPE_NAMES = dict(FIELD_TYPES)


# ---------------------------------------------------------------- record resolution & access
class Rec:
    def __init__(self, endpoint, view, model, obj_id, obj):
        self.endpoint, self.view, self.model, self.id, self.obj = endpoint, view, model, str(obj_id), obj
        self.key = model_key(model)


def _clean_endpoint(endpoint):
    path = '/' + str(endpoint or '').split('?')[0].strip('/') + '/'
    path = re.sub(r'/\d+/$', '/', path)   # a detail URL was given
    return path


def _view_of(request, endpoint):
    path = _clean_endpoint(endpoint)
    try:
        match = resolve(path)
    except Resolver404:
        return path, None
    cls = getattr(match.func, 'cls', None) or getattr(match.func, 'view_class', None)
    if cls is None:
        return path, None
    view = cls()
    view.request = request
    view.format_kwarg = None
    view.args, view.kwargs = (), {}
    view.action = 'retrieve'
    return path, view


def model_of(request, endpoint):
    from AccessControl.access import view_model
    path, view = _view_of(request, endpoint)
    if view is None:
        return path, None, None
    model = view_model(view)
    if model is None:
        try:
            model = view.get_serializer_class().Meta.model
        except Exception:
            model = None
    return path, view, model


def visible(request, view, model, ids):
    """The ids (as strings) of `ids` the user may open on this screen."""
    from AccessControl.access import scope_queryset
    ids = [i for i in ids if str(i).strip()]
    if not ids:
        return set()
    try:
        qs = model._default_manager.filter(pk__in=ids)
    except (ValueError, TypeError):
        return set()
    try:
        qs = scope_queryset(request, view, qs)
    except Exception:
        return set()
    return {str(pk) for pk in qs.values_list('pk', flat=True)}


def get_record(request, endpoint, obj_id):
    path, view, model = model_of(request, endpoint)
    if model is None or obj_id in (None, ''):
        return None, Response({'detail': 'This screen has no record panel.'}, status=status.HTTP_400_BAD_REQUEST)
    if str(obj_id) not in visible(request, view, model, [obj_id]):
        return None, Response({'detail': 'Record not found or you do not have access to it.'}, status=status.HTTP_404_NOT_FOUND)
    obj = model._default_manager.filter(pk=obj_id).first()
    return Rec(path, view, model, obj_id, obj), None


def is_admin(request):
    from AccessControl.access import ctx
    try:
        return ctx(request).admin
    except Exception:
        return bool(getattr(request.user, 'is_superuser', False))


def has_code(request, *codes):
    from AccessControl.access import ctx
    try:
        c = ctx(request)
        return c.admin or any(x in c.codes for x in codes)
    except Exception:
        return False


def _uname(u):
    if u is None:
        return ''
    try:
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(users=u).only('emp_first_name', 'emp_last_name').first()
        if e:
            return ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x).strip() or u.username
    except Exception:
        pass
    return (u.get_full_name() if hasattr(u, 'get_full_name') else '') or getattr(u, 'username', '') or getattr(u, 'email', '')


_name_cache = {}


def uname(u):
    if u is None:
        return ''
    key = (connection.schema_name, u.pk)
    if key not in _name_cache:
        _name_cache[key] = _uname(u)
        if len(_name_cache) > 2000:
            _name_cache.clear()
    return _name_cache[key]


# ---------------------------------------------------------------- serialisers (plain dicts)
def att_json(a, request=None):
    return {'id': a.id, 'name': a.name, 'size': a.size, 'mime': a.mime, 'field': a.field,
            'uploaded_by': uname(a.uploaded_by), 'uploaded_by_id': a.uploaded_by_id, 'at': a.uploaded_at,
            'url': f'/chatter/api/attachments/{a.id}/download/'}


def msg_json(m):
    return {'id': m.id, 'body': m.body, 'author': uname(m.author), 'author_id': m.author_id, 'at': m.created_at,
            'attachments': [att_json(a) for a in m.attachments.all()]}


def act_json(a, today=None):
    today = today or timezone.localdate()
    due = a.due_date
    when = 'overdue' if a.state == 'open' and due < today else 'today' if a.state == 'open' and due == today else 'planned' if a.state == 'open' else a.state
    return {'id': a.id, 'model': a.model, 'object_id': a.object_id, 'record_label': a.record_label, 'screen': a.screen,
            'page': a.page, 'activity_type': a.activity_type, 'type_label': dict(Activity.TYPES).get(a.activity_type, a.activity_type),
            'summary': a.summary, 'note': a.note, 'due_date': a.due_date, 'when': when,
            'days': (due - today).days, 'assigned_to': a.assigned_to_id, 'assigned_to_name': uname(a.assigned_to),
            'created_by': uname(a.created_by), 'created_by_id': a.created_by_id, 'created_at': a.created_at,
            'state': a.state, 'done_at': a.done_at, 'done_by': uname(a.done_by), 'feedback': a.feedback}


def log_json(l):
    return {'id': l.id, 'action': l.action, 'changes': l.changes, 'user': l.user_name or uname(l.user), 'at': l.at,
            'ip': l.ip, 'path': l.path, 'method': l.method}


def def_json(d):
    return {'id': d.id, 'screen': d.screen, 'model': d.model, 'name': d.name, 'label': d.label, 'field_type': d.field_type,
            'type_label': TYPE_NAMES.get(d.field_type, d.field_type), 'options': d.options or [], 'required': d.required,
            'section': d.section, 'order': d.order, 'help_text': d.help_text, 'default': d.default,
            'show_in_list': d.show_in_list, 'active': d.active}


# ---------------------------------------------------------------- record panel
class RecordView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rec, err = get_record(request, request.query_params.get('endpoint'), request.query_params.get('id'))
        if err:
            return err
        q = dict(model=rec.key, object_id=rec.id)
        defs = list(FieldDef.objects.filter(model=rec.key, active=True))
        vals = {v.field_id: v.value for v in FieldValue.objects.filter(field__in=defs, object_id=rec.id)}
        acts = Activity.objects.filter(**q).exclude(state='cancelled').select_related('assigned_to', 'created_by', 'done_by')
        return Response({
            'model': rec.key, 'id': rec.id, 'label': str(rec.obj)[:255] if rec.obj is not None else '',
            'history': [log_json(l) for l in RecordLog.objects.filter(**q).select_related('user')[:300]],
            'notes': [msg_json(m) for m in Message.objects.filter(**q).select_related('author').prefetch_related('attachments')[:300]],
            'attachments': [att_json(a) for a in Attachment.objects.filter(**q).select_related('uploaded_by')],
            'activities': [act_json(a) for a in acts],
            'fields': [def_json(d) for d in defs],
            'values': {d.name: vals.get(d.id) for d in defs},
            'admin': is_admin(request),
            'me': request.user.id,
        })


class NotesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        body = str(request.data.get('body') or '').strip()
        ids = [i for i in (request.data.get('attachment_ids') or []) if str(i).isdigit()]
        if not body and not ids:
            return Response({'detail': 'Write a note or attach a file.'}, status=status.HTTP_400_BAD_REQUEST)
        m = Message.objects.create(model=rec.key, object_id=rec.id, body=body[:20000], author=request.user)
        if ids:
            m.attachments.set(Attachment.objects.filter(id__in=ids, model=rec.key, object_id=rec.id))
        return Response(msg_json(m), status=status.HTTP_201_CREATED)


class NoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        m = Message.objects.filter(pk=pk).first()
        if m is None:
            raise Http404
        if m.author_id != request.user.id and not is_admin(request):
            return Response({'detail': 'You can only delete your own notes.'}, status=status.HTTP_403_FORBIDDEN)
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AttachmentsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        files = request.FILES.getlist('file')
        if not files:
            return Response({'detail': 'Choose a file.'}, status=status.HTTP_400_BAD_REQUEST)
        out = []
        for f in files:
            if f.size > MAX_FILE:
                return Response({'detail': f'“{f.name}” is larger than 25 MB.'}, status=status.HTTP_400_BAD_REQUEST)
            a = Attachment.objects.create(model=rec.key, object_id=rec.id, file=f, name=f.name[:255], size=f.size,
                                          mime=(f.content_type or mimetypes.guess_type(f.name)[0] or '')[:120],
                                          field=str(request.data.get('field') or '')[:100], uploaded_by=request.user)
            out.append(att_json(a))
        return Response(out, status=status.HTTP_201_CREATED)


def _att_access(request, a):
    from django.apps import apps
    # the user must be able to open the record the file belongs to
    try:
        model = apps.get_model(a.model)
    except Exception:
        return False
    if is_admin(request):
        return True
    from AccessControl.access import scope_queryset

    class _V:  # scope rules only need the model
        zeo_scope = True
    try:
        return scope_queryset(request, _V(), model._default_manager.filter(pk=a.object_id)).exists()
    except Exception:
        return False


class AttachmentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        a = Attachment.objects.filter(pk=pk).first()
        if a is None or not _att_access(request, a):
            raise Http404
        resp = FileResponse(a.file.open('rb'), as_attachment=request.query_params.get('inline') != '1', filename=a.name)
        return resp

    def delete(self, request, pk):
        a = Attachment.objects.filter(pk=pk).first()
        if a is None or not _att_access(request, a):
            raise Http404
        if a.uploaded_by_id != request.user.id and not is_admin(request):
            return Response({'detail': 'You can only delete files you attached.'}, status=status.HTTP_403_FORBIDDEN)
        a.file.delete(save=False)
        a.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _company_users(request):
    from UserManagement.models import CustomUser
    schema = connection.schema_name
    # members of the company, plus the user asking (a global admin may not be a member)
    return CustomUser.objects.filter(Q(tenants__schema_name=schema) | Q(pk=request.user.pk), is_active=True).distinct()


def _parse_date(v):
    if isinstance(v, datetime.date):
        return v
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(str(v)[:10], fmt).date()
        except (TypeError, ValueError):
            continue
    return None


class ActivitiesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        d = request.data
        summary = str(d.get('summary') or '').strip()
        due = _parse_date(d.get('due_date')) or timezone.localdate()
        atype = d.get('activity_type') or 'todo'
        if atype not in dict(Activity.TYPES):
            return Response({'detail': 'Unknown activity type.'}, status=status.HTTP_400_BAD_REQUEST)
        who = d.get('assigned_to') or request.user.id
        user = _company_users(request).filter(pk=who).first()
        if user is None:
            return Response({'detail': 'Choose a user of this company.'}, status=status.HTTP_400_BAD_REQUEST)
        a = Activity.objects.create(model=rec.key, object_id=rec.id, record_label=str(rec.obj)[:255] if rec.obj else '',
                                    screen=rec.endpoint, page=str(d.get('page') or '')[:255], activity_type=atype,
                                    summary=summary or dict(Activity.TYPES)[atype], note=str(d.get('note') or '')[:5000],
                                    due_date=due, assigned_to=user, created_by=request.user)
        return Response(act_json(a), status=status.HTTP_201_CREATED)


class ActivityDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        a = Activity.objects.filter(pk=pk).first()
        if a is None:
            raise Http404
        if request.user.id not in (a.assigned_to_id, a.created_by_id) and not is_admin(request):
            return Response({'detail': 'Only the assignee or the person who planned it can change this activity.'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        if 'state' in d:
            st = d.get('state')
            if st not in dict(Activity.STATES):
                return Response({'detail': 'Unknown state.'}, status=status.HTTP_400_BAD_REQUEST)
            a.state = st
            a.done_at = timezone.now() if st == 'done' else None
            a.done_by = request.user if st == 'done' else None
        if 'feedback' in d:
            a.feedback = str(d.get('feedback') or '')[:5000]
        if 'summary' in d and str(d.get('summary')).strip():
            a.summary = str(d['summary']).strip()[:255]
        if 'note' in d:
            a.note = str(d.get('note') or '')[:5000]
        if 'due_date' in d:
            due = _parse_date(d.get('due_date'))
            if due:
                a.due_date = due
        if 'activity_type' in d and d['activity_type'] in dict(Activity.TYPES):
            a.activity_type = d['activity_type']
        if 'assigned_to' in d:
            u = _company_users(request).filter(pk=d.get('assigned_to')).first()
            if u is None:
                return Response({'detail': 'Choose a user of this company.'}, status=status.HTTP_400_BAD_REQUEST)
            a.assigned_to = u
        a.save()
        if a.state == 'done':  # done activities also show in the record's notes, like Odoo
            txt = f'Activity done: {a.get_activity_type_display()} – {a.summary}' + (f'\n{a.feedback}' if a.feedback else '')
            Message.objects.create(model=a.model, object_id=a.object_id, body=txt, author=request.user)
        return Response(act_json(a))


class TodoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get('scope', 'mine')
        qs = Activity.objects.select_related('assigned_to', 'created_by', 'done_by')
        if scope == 'created':
            qs = qs.filter(created_by=request.user)
        elif scope == 'all' and is_admin(request):
            pass
        else:
            qs = qs.filter(assigned_to=request.user)
        state = request.query_params.get('state', 'open')
        if state != 'any':
            qs = qs.filter(state=state)
        today = timezone.localdate()
        rows = [act_json(a, today) for a in qs.order_by('due_date', 'id')[:1000]]
        mine = Activity.objects.filter(assigned_to=request.user, state='open')
        return Response({'results': rows, 'counts': {
            'overdue': mine.filter(due_date__lt=today).count(), 'today': mine.filter(due_date=today).count(),
            'planned': mine.filter(due_date__gt=today).count()}})


class UsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{'id': u.id, 'name': uname(u), 'username': u.username} for u in _company_users(request).order_by('username')])


# ---------------------------------------------------------------- form designer (extra fields)
SLUG = re.compile(r'[^a-z0-9_]+')


def _slug(label):
    s = SLUG.sub('_', str(label).strip().lower()).strip('_')
    return (s or 'field')[:90]


def _check_def(data):
    t = data.get('field_type') or 'text'
    if t not in TYPE_NAMES:
        return 'Choose a field type.'
    if not str(data.get('label') or '').strip():
        return 'Give the field a name.'
    if t in ('dropdown', 'multiselect', 'radio'):
        opts = [str(o).strip() for o in (data.get('options') or []) if str(o).strip()]
        if not opts:
            return 'Add at least one option.'
        if len({o.lower() for o in opts}) != len(opts):
            return 'Options must be different from each other.'
    return None


class FieldScreensView(APIView):
    """Screens that can get extra fields: every list API the app loads that creates records."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        counts = {}
        for d in FieldDef.objects.filter(active=True):
            counts[d.screen] = counts.get(d.screen, 0) + 1
        return Response({'counts': counts})


class FieldsView(APIView):
    permission_classes = [IsAuthenticated]

    def _can_design(self, request):
        return has_code(request, 'add_fielddef', 'change_fielddef', 'view_emp_customfield', 'add_emp_customfield')

    def get(self, request):
        screen = request.query_params.get('screen')
        path, view, model = model_of(request, screen) if screen else (None, None, None)
        qs = FieldDef.objects.all()
        if screen:
            if model is None:
                return Response({'detail': 'This screen cannot get extra fields.'}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(model=model_key(model))
        if request.query_params.get('active') == '1':
            qs = qs.filter(active=True)
        return Response({'model': model_key(model) if model else None, 'screen': path,
                         'can_design': self._can_design(request), 'types': [{'value': k, 'label': v} for k, v in FIELD_TYPES],
                         'fields': [def_json(d) for d in qs]})

    def post(self, request):
        if not self._can_design(request):
            return Response({'detail': 'You do not have permission to design forms.'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        path, view, model = model_of(request, d.get('screen'))
        if model is None:
            return Response({'detail': 'This screen cannot get extra fields.'}, status=status.HTTP_400_BAD_REQUEST)
        msg = _check_def(d)
        if msg:
            return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
        key = model_key(model)
        name = _slug(d.get('name') or d.get('label'))
        if FieldDef.objects.filter(model=key, name=name).exists() or FieldDef.objects.filter(model=key, label__iexact=str(d['label']).strip()).exists():
            return Response({'detail': f'A field called “{d["label"]}” already exists on this screen.'}, status=status.HTTP_400_BAD_REQUEST)
        last = FieldDef.objects.filter(model=key).order_by('-order').first()
        f = FieldDef.objects.create(
            screen=path, model=key, name=name, label=str(d['label']).strip()[:150], field_type=d.get('field_type') or 'text',
            options=[str(o).strip() for o in (d.get('options') or []) if str(o).strip()], required=bool(d.get('required')),
            section=str(d.get('section') or '').strip()[:100], order=int(d.get('order') or ((last.order + 10) if last else 10)),
            help_text=str(d.get('help_text') or '')[:255], default=str(d.get('default') or '')[:255],
            show_in_list=bool(d.get('show_in_list', True)), created_by=request.user)
        return Response(def_json(f), status=status.HTTP_201_CREATED)

    def put(self, request):
        """Update one field ({id, ...}) or re-order many ({order: [id, id, ...]})."""
        if not self._can_design(request):
            return Response({'detail': 'You do not have permission to design forms.'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        if isinstance(d.get('order'), list):
            for i, pk in enumerate(d['order']):
                FieldDef.objects.filter(pk=pk).update(order=(i + 1) * 10)
            return Response({'ok': True})
        f = FieldDef.objects.filter(pk=d.get('id')).first()
        if f is None:
            raise Http404
        merged = {**def_json(f), **{k: v for k, v in d.items() if k != 'id'}}
        msg = _check_def(merged)
        if msg:
            return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
        if FieldDef.objects.filter(model=f.model, label__iexact=str(merged['label']).strip()).exclude(pk=f.pk).exists():
            return Response({'detail': f'A field called “{merged["label"]}” already exists on this screen.'}, status=status.HTTP_400_BAD_REQUEST)
        f.label = str(merged['label']).strip()[:150]
        f.field_type = merged['field_type']
        f.options = [str(o).strip() for o in (merged.get('options') or []) if str(o).strip()]
        f.required = bool(merged.get('required'))
        f.section = str(merged.get('section') or '').strip()[:100]
        f.order = int(merged.get('order') or f.order)
        f.help_text = str(merged.get('help_text') or '')[:255]
        f.default = str(merged.get('default') or '')[:255]
        f.show_in_list = bool(merged.get('show_in_list'))
        f.active = bool(merged.get('active', True))
        f.save()
        return Response(def_json(f))

    def delete(self, request):
        if not self._can_design(request):
            return Response({'detail': 'You do not have permission to design forms.'}, status=status.HTTP_403_FORBIDDEN)
        f = FieldDef.objects.filter(pk=request.query_params.get('id')).first()
        if f is None:
            raise Http404
        f.delete()  # values go with it
        return Response(status=status.HTTP_204_NO_CONTENT)


EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE = re.compile(r'^[+0-9 ()\-]{6,20}$')
URL = re.compile(r'^(https?://)?[^\s/$.?#].[^\s]*$', re.I)
COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


def clean_value(f, v):
    """Check and normalise a value for a designer field. Returns (value, error)."""
    empty = v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)
    if empty:
        return (None, f'{f.label} is required.') if f.required else (None, None)
    t = f.field_type
    try:
        if t in ('text', 'textarea'):
            return str(v)[:10000], None
        if t == 'integer':
            return int(str(v).replace(',', '').strip()), None
        if t in ('decimal', 'currency', 'percent'):
            n = float(str(v).replace(',', '').replace('%', '').strip())
            if t == 'percent' and not 0 <= n <= 100:
                return None, f'{f.label}: enter a percentage between 0 and 100.'
            return round(n, 4), None
        if t == 'rating':
            n = int(v)
            return (n, None) if 1 <= n <= 5 else (None, f'{f.label}: choose 1 to 5 stars.')
        if t == 'date':
            d = _parse_date(v)
            return (d.isoformat(), None) if d else (None, f'{f.label}: enter a date.')
        if t == 'datetime':
            s = str(v).strip().replace(' ', 'T')
            datetime.datetime.fromisoformat(s[:19])
            return s[:16], None
        if t == 'time':
            datetime.datetime.strptime(str(v).strip()[:5], '%H:%M')
            return str(v).strip()[:5], None
        if t == 'checkbox':
            return (str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'on')) if not isinstance(v, bool) else v, None
        if t in ('dropdown', 'radio'):
            opts = {o.lower(): o for o in f.options or []}
            o = opts.get(str(v).strip().lower())
            return (o, None) if o else (None, f'{f.label}: choose one of {", ".join(f.options)}.')
        if t == 'multiselect':
            items = v if isinstance(v, list) else [x for x in re.split(r'[;,]', str(v))]
            opts = {o.lower(): o for o in f.options or []}
            out = []
            for x in items:
                o = opts.get(str(x).strip().lower())
                if not o:
                    return None, f'{f.label}: “{x}” is not one of the options.'
                out.append(o)
            return out, None
        if t == 'email':
            s = str(v).strip()
            return (s, None) if EMAIL.match(s) else (None, f'{f.label}: enter a valid e-mail address.')
        if t == 'phone':
            s = str(v).strip()
            return (s, None) if PHONE.match(s) else (None, f'{f.label}: enter a valid phone number.')
        if t == 'url':
            s = str(v).strip()
            return (s, None) if URL.match(s) else (None, f'{f.label}: enter a valid web link.')
        if t == 'color':
            s = str(v).strip()
            return (s, None) if COLOR.match(s) else (None, f'{f.label}: choose a colour.')
        if t == 'employee':
            from EmpManagement.models import emp_master
            e = emp_master.objects.filter(Q(pk=v) if str(v).isdigit() else Q(emp_code__iexact=str(v).strip())).first()
            return ({'id': e.id, 'code': e.emp_code, 'name': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)}, None) if e else (None, f'{f.label}: employee not found.')
        if t == 'file':
            if isinstance(v, dict) and v.get('id'):
                a = Attachment.objects.filter(pk=v['id']).first()
                return ({'id': a.id, 'name': a.name}, None) if a else (None, f'{f.label}: the file was not found.')
            return None, f'{f.label}: upload a file.'
    except (TypeError, ValueError):
        return None, f'{f.label}: “{v}” is not a valid {TYPE_NAMES.get(t, t).lower()}.'
    return v, None


def save_values(request, model_label, obj_id, values, partial=True):
    """Validate and store designer values for a record. Returns a list of error messages."""
    defs = {d.name: d for d in FieldDef.objects.filter(model=model_label, active=True)}
    by_label = {d.label.lower(): d for d in defs.values()}
    errors, clean = [], {}
    for k, v in (values or {}).items():
        f = defs.get(k) or by_label.get(str(k).lower())
        if f is None:
            continue
        val, err = clean_value(f, v)
        if err:
            errors.append(err)
        else:
            clean[f] = val
    if not partial:
        for f in defs.values():
            if f.required and f not in clean and not FieldValue.objects.filter(field=f, object_id=str(obj_id)).exclude(value=None).exists():
                errors.append(f'{f.label} is required.')
    if errors:
        return errors
    for f, val in clean.items():
        old = FieldValue.objects.filter(field=f, object_id=str(obj_id)).first()
        old_val = old.value if old else None
        if old_val == val:
            continue
        FieldValue.objects.update_or_create(field=f, object_id=str(obj_id), defaults={'value': val, 'updated_by': request.user})
        _log_value(request, model_label, obj_id, f, old_val, val)
    return []


def _show(v):
    if isinstance(v, dict):
        return v.get('name') or v.get('code') or str(v)
    if isinstance(v, list):
        return ', '.join(map(str, v))
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    return v


def _log_value(request, model_label, obj_id, f, old, new):
    from .tracking import current_request
    req = current_request()
    meta = getattr(req, 'META', {}) if req is not None else {}
    RecordLog.objects.create(model=model_label, object_id=str(obj_id), action='updated',
                             changes={f'x_{f.name}': {'label': f.label, 'old': _show(old), 'new': _show(new)}},
                             user=request.user, user_name=uname(request.user)[:150],
                             ip=(meta.get('HTTP_X_FORWARDED_FOR') or meta.get('REMOTE_ADDR') or '').split(',')[0][:64],
                             path=(getattr(req, 'path', '') or '')[:255], method=getattr(req, 'method', '') or '')


class ValuesView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        errors = save_values(request, rec.key, rec.id, request.data.get('values') or {}, partial=not request.data.get('full'))
        if errors:
            return Response({'detail': ' '.join(errors), 'errors': errors}, status=status.HTTP_400_BAD_REQUEST)
        defs = FieldDef.objects.filter(model=rec.key, active=True)
        vals = {v.field.name: v.value for v in FieldValue.objects.filter(field__in=defs, object_id=rec.id).select_related('field')}
        return Response({'values': vals})

    def get(self, request):
        path, view, model = model_of(request, request.query_params.get('endpoint'))
        if model is None:
            return Response({'fields': [], 'values': {}})
        key = model_key(model)
        defs = list(FieldDef.objects.filter(model=key, active=True))
        ecf = []
        if model._meta.model_name == 'emp_master':   # employee custom fields of the employee form designer
            try:
                from EmpManagement.models import Emp_CustomField
                ecf = list(Emp_CustomField.objects.values_list('emp_custom_field', flat=True))
            except Exception:
                ecf = []
        if not defs and not ecf:
            return Response({'fields': [], 'values': {}})
        ids = [i for i in str(request.query_params.get('ids') or '').split(',') if i.strip()][:5000]
        ok = visible(request, view, model, ids)
        out = {}
        for v in FieldValue.objects.filter(field__in=defs, object_id__in=list(ok)).select_related('field'):
            out.setdefault(v.object_id, {})[v.field.name] = _show(v.value)
        fields = [def_json(d) for d in defs]
        if ecf:
            from EmpManagement.models import Emp_CustomFieldValue
            for v in Emp_CustomFieldValue.objects.filter(emp_master_id__in=list(ok)).values('emp_master_id', 'emp_custom_field', 'field_value'):
                out.setdefault(str(v['emp_master_id']), {})['ecf:' + v['emp_custom_field']] = v['field_value']
            fields += [{'name': 'ecf:' + n, 'label': n, 'field_type': 'text', 'show_in_list': True, 'active': True} for n in ecf]
        return Response({'fields': fields, 'values': out})


# ---------------------------------------------------------------- per-user layouts
class DashboardLayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        l = DashboardLayout.objects.filter(user=request.user, dashboard=request.query_params.get('name', 'main')).first()
        return Response({'hidden': l.hidden if l else [], 'order': l.order if l else []})

    def put(self, request):
        name = request.query_params.get('name', 'main')
        hidden = [str(x)[:60] for x in (request.data.get('hidden') or [])][:200]
        order = [str(x)[:60] for x in (request.data.get('order') or [])][:200]
        DashboardLayout.objects.update_or_create(user=request.user, dashboard=name[:40], defaults={'hidden': hidden, 'order': order})
        return Response({'hidden': hidden, 'order': order})


class ListLayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        l = ListLayout.objects.filter(user=request.user, key=request.query_params.get('key', '')[:255]).first()
        return Response(l.data if l else {})

    def put(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ListLayout.objects.update_or_create(user=request.user, key=request.query_params.get('key', '')[:255], defaults={'data': data})
        return Response(data)


# ---------------------------------------------------------------- organisation chart
class OrgChartView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from EmpManagement.models import emp_master
        from AccessControl.access import scope_queryset

        class _V:
            zeo_scope = True
            self_service_actions = ('list',)   # everyone may see the company's structure (names and roles only)
        qs = emp_master.objects.filter(is_active=True) if hasattr(emp_master, 'is_active') else emp_master.objects.all()
        qs = scope_queryset(request, _V(), qs).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_reporting_manager')
        emps = list(qs)
        by_user = {}
        for e in emp_master.objects.exclude(users=None).only('id', 'users'):
            by_user[e.users_id] = e.id
        out = []
        for e in emps:
            mgr = by_user.get(e.emp_reporting_manager_id) if getattr(e, 'emp_reporting_manager_id', None) else None
            photo = None
            try:
                photo = e.emp_profile_pic.url if e.emp_profile_pic else None
            except Exception:
                photo = None
            out.append({'id': e.id, 'code': e.emp_code, 'name': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x),
                        'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '',
                        'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                        'branch': str(e.emp_branch_id) if e.emp_branch_id_id else '',
                        'email': getattr(e, 'emp_personal_email', '') or '', 'photo': photo,
                        'manager': mgr if mgr != e.id else None})
        return Response(out)
