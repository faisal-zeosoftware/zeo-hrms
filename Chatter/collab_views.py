"""v1.45.0 – collaboration API (all under /chatter/api/, ?schema= like the rest).

POST   followers/                {endpoint, id, action: follow|unfollow|add|remove, user_ids, emails, follower_id}
GET    inbox/?kind=&app=&unread=1&done=0   messages, @mentions, e-mail replies, my activities and my approvals in one list
POST   inbox/                    {ids: [..] | 'all', action: read|unread|done|undone}
GET    stars/                    {starred, recent}
POST   stars/                    {endpoint, id, page, label} – star / unstar
GET    tags/                     colour tags with their use count;  POST {name, color};  PATCH tags/<pk>/ {name, color};  DELETE tags/<pk>/
POST   record-tags/              {endpoint, id, add: [names or ids], remove: [names or ids]}
GET    tag-values/?endpoint=&ids=1,2,3   {id: [{id, name, color}]} – list columns, filters and group-by
POST   mass/                     {endpoint, ids, action: tag_add|tag_remove|message|activity|follow, …} – selected rows of a list
GET/PUT mail-settings/           the reply mailbox (admins);  POST mail-settings/check/ – read the mailbox now
"""
import datetime

import datetime as _dt
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import collab as C
from . import social as SO
from .models import Activity, Follower, InboxItem, MailSettings, Message, Tag, TagLink
from .views import _company_users, _parse_date, act_json, get_record, is_admin, model_of, uname, visible


def bad(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': msg}, status=code)


class FollowersView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        d = request.data
        a = d.get('action') or 'follow'
        label = str(rec.obj)[:255] if rec.obj is not None else ''
        page = str(d.get('page') or '')[:255]
        if a == 'follow':
            C.follow(rec.key, rec.id, user=request.user, reason='self', label=label, page=page, force=True)
        elif a == 'unfollow':
            C.unfollow(rec.key, rec.id, request.user)
        elif a == 'add':
            users = _company_users(request).filter(pk__in=[i for i in d.get('user_ids') or [] if str(i).isdigit()])
            for u in users:
                C.follow(rec.key, rec.id, user=u, reason='manual', label=label, page=page, force=True)
            emails = d.get('emails') or []
            if isinstance(emails, str):
                emails = [x for x in emails.replace(';', ',').split(',')]
            for e in emails:
                e = e.strip()
                if e and '@' not in e:
                    return bad(f'"{e}" is not an e-mail address.')
                if e:
                    C.follow(rec.key, rec.id, email_addr=e, name=e, reason='manual', label=label, page=page, force=True)
        elif a == 'remove':
            f = Follower.objects.filter(pk=d.get('follower_id'), model=rec.key, object_id=rec.id).first()
            if f is None:
                return bad('Follower not found.', 404)
            if f.user_id:
                f.muted = True
                f.save(update_fields=['muted'])
            else:
                f.delete()
        else:
            return bad('Unknown action.')
        return Response({'followers': C.followers_json(rec.key, rec.id), 'following': C.is_following(rec.key, rec.id, request.user)})


# ------------------------------------------------------------------ inbox (Odoo Discuss)
def _item(i):
    return {'key': f'i{i.id}', 'id': i.id, 'source': 'inbox', 'kind': i.kind, 'kind_label': i.get_kind_display(), 'app': SO.app_of(i.model),
            'title': i.title, 'body': i.body, 'from': i.from_name, 'record': i.record_label, 'page': i.page, 'model': i.model,
            'object_id': i.object_id, 'at': i.created_at, 'unread': not i.is_read, 'done': i.done}


def _approvals(user):
    try:
        from ZeoFlow.models import ApprovalStep
    except Exception:
        return []
    out = []
    qs = ApprovalStep.objects.filter(Q(approver=user) | Q(delegate=user), status='pending', request__status='pending').select_related('request')[:200]
    for s in qs:
        r = s.request
        out.append({'key': f'a{s.id}', 'id': s.id, 'source': 'approval', 'kind': 'approval', 'kind_label': 'Approval', 'app': SO.app_of(r.model),
                    'title': f'Approve {r.record_label}', 'body': (r.note or '')[:500] + (f' – {r.amount:,.2f}' if r.amount else ''),
                    'from': uname(r.submitted_by), 'record': r.record_label, 'page': r.record_link, 'model': r.model, 'object_id': r.object_id,
                    'at': s.activated_at or r.submitted_at, 'unread': True, 'done': False})
    return out


def _activities(user):
    out = []
    today = timezone.localdate()
    for a in Activity.objects.filter(assigned_to=user, state='open').select_related('created_by')[:300]:
        j = act_json(a, today)
        out.append({'key': f'c{a.id}', 'id': a.id, 'source': 'activity', 'kind': 'activity', 'kind_label': j['type_label'], 'app': SO.app_of(a.model),
                    'title': a.summary, 'body': a.note, 'from': j['created_by'], 'record': a.record_label, 'page': a.page, 'model': a.model,
                    'object_id': a.object_id, 'at': datetime.datetime.combine(a.due_date, datetime.time(8, 0), tzinfo=timezone.get_current_timezone()),
                    'due': a.due_date, 'when': j['when'], 'unread': j['when'] in ('overdue', 'today'), 'done': False})
    return out


class InboxView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params
        u = request.user
        try:
            C.send_reminders(u)   # v1.46.0 reminders due today appear at once (the hourly job does it for everyone)
        except Exception:
            pass
        items = InboxItem.objects.filter(user=u)
        if q.get('done') != '1':
            items = items.filter(done=False)
        rows = [_item(i) for i in items[:500]] + _activities(u) + _approvals(u)
        counts = {'all': len(rows), 'unread': sum(1 for r in rows if r['unread'])}
        for r in rows:
            counts[r['kind']] = counts.get(r['kind'], 0) + 1
        apps = sorted({r['app'] for r in rows})
        if q.get('kind'):
            ks = q['kind'].split(',')
            rows = [r for r in rows if r['kind'] in ks]
        if q.get('app'):
            rows = [r for r in rows if r['app'] == q['app']]
        if q.get('unread') == '1':
            rows = [r for r in rows if r['unread']]
        rows.sort(key=lambda r: r['at'] or timezone.now(), reverse=True)
        return Response({'rows': rows, 'counts': counts, 'apps': apps,
                         'unread_messages': InboxItem.objects.filter(user=u, is_read=False, done=False).count()})

    def post(self, request):
        ids = request.data.get('ids')
        a = request.data.get('action') or 'read'
        qs = InboxItem.objects.filter(user=request.user)
        if ids != 'all':
            qs = qs.filter(pk__in=[i for i in (ids or []) if str(i).isdigit()])
        vals = {'read': {'is_read': True}, 'unread': {'is_read': False}, 'done': {'done': True, 'is_read': True}, 'undone': {'done': False}}.get(a)
        if vals is None:
            return bad('Unknown action.')
        n = qs.update(**vals)
        return Response({'updated': n, 'unread_messages': InboxItem.objects.filter(user=request.user, is_read=False, done=False).count()})


# ------------------------------------------------------------------ starred and recent
class StarsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(SO.lists(request.user))

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        label = str(request.data.get('label') or (str(rec.obj) if rec.obj is not None else ''))[:255]
        on = SO.toggle_star(request.user, rec, label, str(request.data.get('page') or '')[:255])
        return Response({'starred': on, **SO.lists(request.user)})


# ------------------------------------------------------------------ tags
class TagsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([SO.tag_json(t, t.n) for t in Tag.objects.annotate(n=Count('links'))])

    def post(self, request):
        name = ' '.join(str(request.data.get('name') or '').split())
        if not name:
            return bad('Name the tag.')
        if Tag.objects.filter(name__iexact=name).exists():
            return bad('This tag exists already.')
        return Response(SO.tag_json(SO.get_or_make(name, request.user, request.data.get('color'))), status=status.HTTP_201_CREATED)


class TagView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        t = Tag.objects.filter(pk=pk).first()
        if t is None:
            return bad('Tag not found.', 404)
        if not (is_admin(request) or t.created_by_id == request.user.id):
            return bad('Only the person who made the tag or an administrator changes it.', 403)
        if 'name' in request.data:
            name = ' '.join(str(request.data['name'] or '').split())[:60]
            if not name or Tag.objects.filter(name__iexact=name).exclude(pk=pk).exists():
                return bad('Use another name.')
            t.name = name
        if request.data.get('color') in dict(Tag._meta.get_field('color').choices):
            t.color = request.data['color']
        t.save()
        return Response(SO.tag_json(t))

    def delete(self, request, pk):
        if not is_admin(request):
            return bad('Only an administrator deletes a tag (it comes off every record).', 403)
        Tag.objects.filter(pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecordTagsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rec, err = get_record(request, request.data.get('endpoint'), request.data.get('id'))
        if err:
            return err
        SO.add_tags(rec.key, rec.id, request.data.get('add') or [], request.user)
        SO.remove_tags(rec.key, rec.id, request.data.get('remove') or [])
        return Response({'tags': SO.tags_of(rec.key, [rec.id]).get(rec.id, [])})


class TagValuesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        path, view, model = model_of(request, request.query_params.get('endpoint'))
        if model is None:
            return Response({})
        ids = [i for i in str(request.query_params.get('ids') or '').split(',') if i.strip()][:1000]
        ok = visible(request, view, model, ids)
        from .tracking import model_key
        return Response(SO.tags_of(model_key(model), [i for i in ids if i in ok]))


# ------------------------------------------------------------------ mass actions on selected rows
class MassView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = request.data
        path, view, model = model_of(request, d.get('endpoint'))
        if model is None:
            return bad('This screen has no records.')
        ids = [str(i) for i in (d.get('ids') or []) if str(i).strip()][:500]
        if not ids:
            return bad('Select rows first.')
        ok = visible(request, view, model, ids)
        a = d.get('action')
        done, skipped = [], [i for i in ids if i not in ok]
        page_tpl = str(d.get('page') or '')
        for i in [x for x in ids if x in ok]:
            rec, err = get_record(request, d.get('endpoint'), i)
            if err:
                skipped.append(i)
                continue
            label = str(rec.obj)[:255] if rec.obj is not None else ''
            page = page_tpl.replace('{id}', i)[:255]
            if a == 'tag_add':
                SO.add_tags(rec.key, rec.id, d.get('tags') or [], request.user)
            elif a == 'tag_remove':
                SO.remove_tags(rec.key, rec.id, d.get('tags') or [])
            elif a == 'follow':
                C.follow(rec.key, rec.id, user=request.user, reason='self', label=label, page=page, force=True)
            elif a == 'message':
                body = str(d.get('body') or '').strip()
                if not body:
                    return bad('Write the message.')
                C.auto_followers(rec, label, page)
                m = Message.objects.create(model=rec.key, object_id=rec.id, body=body[:20000], author=request.user, kind='message',
                                           subject=str(d.get('subject') or '')[:200], mentions=C.mention_ids(body), record_label=label, page=page)
                C.deliver(m, request.user)
            elif a == 'activity':
                u = _company_users(request).filter(pk=d.get('assigned_to')).first()
                due = _parse_date(d.get('due_date'))
                if u is None or due is None or not str(d.get('summary') or '').strip():
                    return bad('Choose the person, the due date and the summary.')
                Activity.objects.create(model=rec.key, object_id=rec.id, record_label=label, screen=path, page=page,
                                        activity_type=d.get('activity_type') if d.get('activity_type') in dict(Activity.TYPES) else 'todo',
                                        summary=str(d['summary'])[:255], note=str(d.get('note') or ''), due_date=due, assigned_to=u, created_by=request.user)
            else:
                return bad('Unknown action.')
            done.append(i)
        return Response({'done': len(done), 'skipped': len(skipped), 'ids': done})


# ------------------------------------------------------------------ reply mailbox
def _ms_json(ms):
    return {'enabled': ms.enabled, 'reply_to': ms.reply_to, 'imap_host': ms.imap_host, 'imap_port': ms.imap_port, 'imap_ssl': ms.imap_ssl,
            'imap_user': ms.imap_user, 'password_set': bool(ms.imap_password), 'folder': ms.folder, 'last_check': ms.last_check,
            'last_error': ms.last_error, 'replies_received': ms.replies_received}


class MailSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        return Response(_ms_json(MailSettings.objects.first() or MailSettings.objects.create()))

    def put(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        ms = MailSettings.objects.first() or MailSettings.objects.create()
        d = request.data
        for f in ('enabled', 'imap_ssl'):
            if f in d:
                setattr(ms, f, bool(d[f]))
        for f in ('reply_to', 'imap_host', 'imap_user', 'folder'):
            if f in d:
                setattr(ms, f, str(d[f] or '').strip())
        if str(d.get('imap_port') or '').isdigit():
            ms.imap_port = int(d['imap_port'])
        if d.get('imap_password'):
            C.put_password(ms, str(d['imap_password']))
        if ms.enabled and not (ms.reply_to and '@' in ms.reply_to and ms.imap_host and ms.imap_user and ms.imap_password):
            return bad('Fill the reply address, the IMAP server, the user and the password.')
        ms.save()
        return Response(_ms_json(ms))


class MailCheckView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        n = C.check_mailbox()
        ms = MailSettings.objects.first()
        return Response({'received': n, **(_ms_json(ms) if ms else {})})


# ------------------------------------------------------------------ v1.46.0 company-wide audit log
class AuditView(APIView):
    """GET audit/?q=&model=&user=&action=&from=&to=&offset=&limit=&export=csv – every change of every record (admins and
    holders of Chatter.view_audit_log)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import RecordLog
        from .views import has_code
        if not has_code(request, 'view_audit_log'):
            return bad('The audit log is for administrators.', 403)
        p = request.query_params
        qs = RecordLog.objects.all()
        if p.get('model'):
            qs = qs.filter(model=p['model'])
        if p.get('action') in ('created', 'updated', 'deleted'):
            qs = qs.filter(action=p['action'])
        if p.get('user'):
            qs = qs.filter(Q(user_name__icontains=p['user']) | Q(user__username__icontains=p['user']))
        if p.get('q'):
            qs = qs.filter(Q(record_label__icontains=p['q']) | Q(object_id=p['q']) | Q(changes__icontains=p['q']))
        d1, d2 = _parse_date(p.get('from')), _parse_date(p.get('to'))
        if d1:
            qs = qs.filter(at__date__gte=d1)
        if d2:
            qs = qs.filter(at__date__lte=d2)
        total = qs.count()
        off = int(p.get('offset') or 0) if str(p.get('offset') or '0').isdigit() else 0
        lim = min(int(p.get('limit') or 200) if str(p.get('limit') or '').isdigit() else 200, 5000)
        rows = [_audit_row(l) for l in qs.select_related('user')[off:off + lim]]
        if p.get('export') == 'csv':
            import csv
            from io import StringIO
            from django.http import HttpResponse
            buf = StringIO()
            w = csv.writer(buf)
            w.writerow(['When', 'Who', 'Action', 'Screen / record type', 'Record', 'Changes', 'IP'])
            for r in rows:
                w.writerow([r['at'], r['user'], r['action'], r['model_label'], r['record'], '; '.join(f"{c['label']}: {c['old']} -> {c['new']}" for c in r['changes']), r['ip']])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv')
            resp['Content-Disposition'] = 'attachment; filename="audit-log.csv"'
            return resp
        models = [{'key': k, 'label': _model_label(k)} for k in RecordLog.objects.values_list('model', flat=True).distinct().order_by('model')[:400]]
        return Response({'total': total, 'rows': rows, 'models': models})


_LABELS = {}


def _model_label(key):
    if key not in _LABELS:
        from django.apps import apps
        try:
            m = apps.get_model(key)
            _LABELS[key] = f'{SO.app_of(key)} · {str(m._meta.verbose_name).capitalize()}'
        except Exception:
            _LABELS[key] = key
    return _LABELS[key]


def _audit_row(l):
    ch = []
    for k, v in (l.changes or {}).items():
        if isinstance(v, dict):
            ch.append({'field': k, 'label': v.get('label', k), 'old': v.get('old'), 'new': v.get('new')})
    return {'id': l.id, 'at': l.at, 'user': l.user_name or uname(l.user), 'action': l.action, 'model': l.model, 'model_label': _model_label(l.model),
            'object_id': l.object_id, 'record': l.record_label, 'changes': ch[:60], 'ip': l.ip, 'path': l.path}


# ------------------------------------------------------------------ v1.46.0 print any record
SECRET = ('password', 'secret', 'token', 'otp', 'hash', 'api_key', 'pin')


def _val(obj, f):
    from django.db import models as dm
    try:
        if f.choices:
            return str(getattr(obj, f'get_{f.name}_display')())
        v = getattr(obj, f.name)
    except Exception:
        return ''
    if v is None or v == '':
        return ''
    if isinstance(f, dm.BooleanField):
        return 'Yes' if v else 'No'
    if isinstance(f, (dm.FileField,)):
        return v.name.split('/')[-1] if v else ''
    if isinstance(v, (list, dict)):
        return '' if not v else (', '.join(str(x.get('name') or x.get('label') or x) if isinstance(x, dict) else str(x) for x in v) if isinstance(v, list) else str(v))[:500]
    if isinstance(v, datetime.datetime):
        return timezone.localtime(v).strftime('%d/%m/%Y %H:%M') if timezone.is_aware(v) else v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, datetime.date):
        return v.strftime('%d/%m/%Y')
    return str(v)[:500]


class PrintView(APIView):
    """GET print/?endpoint=&id= – one standard PDF of any record: its fields, extra fields, notes and change history."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from io import BytesIO
        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape as esc
        from .models import FieldDef, FieldValue, RecordLog
        rec, err = get_record(request, request.query_params.get('endpoint'), request.query_params.get('id'))
        if err:
            return err
        obj = rec.obj
        st = getSampleStyleSheet()
        small = st['BodyText'].clone('small', fontSize=8.5, leading=11)
        h = st['Heading2'].clone('h', textColor=colors.HexColor('#1B1640'), spaceBefore=8, spaceAfter=4)
        company = ''
        try:
            from UserManagement.models import company as Co
            c = Co.objects.filter(schema_name=connection.schema_name).first()
            company = getattr(c, 'name', '') or getattr(c, 'company_name', '') or connection.schema_name
        except Exception:
            company = connection.schema_name
        label = str(obj)[:200] if obj is not None else rec.id
        what = str(rec.model._meta.verbose_name).capitalize()
        story = [Paragraph(esc(company), st['Normal']), Paragraph(esc(f'{what}: {label}'), st['Title']),
                 Paragraph(esc(f'{SO.app_of(rec.key)} · printed {timezone.localtime().strftime("%d/%m/%Y %H:%M")} by {uname(request.user)}'), small), Spacer(1, 4 * mm)]
        rows = []
        for f in rec.model._meta.concrete_fields:
            if f.primary_key or any(s in f.name.lower() for s in SECRET) or f.name in ('search_keys', 'used_in'):
                continue
            v = _val(obj, f)
            if v:
                rows.append([Paragraph(esc(str(f.verbose_name).capitalize()), small), Paragraph(esc(v), small)])
        for d in FieldDef.objects.filter(model=rec.key, active=True):
            fv = FieldValue.objects.filter(field=d, object_id=rec.id).first()
            if fv and fv.value not in (None, '', []):
                rows.append([Paragraph(esc(d.label), small), Paragraph(esc(str(fv.value)[:500]), small)])
        if rows:
            t = Table(rows, colWidths=[55 * mm, 120 * mm])
            t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#DCDFEA')), ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F4F5FA')),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            story += [Paragraph('Details', h), t]
        notes = Message.objects.filter(model=rec.key, object_id=rec.id).select_related('author').order_by('created_at')[:100]
        if notes:
            story.append(Paragraph('Notes and messages', h))
            for m in notes:
                kind = {'message': 'message', 'email_in': 'e-mail reply'}.get(m.kind, 'note')
                story.append(Paragraph(esc(f'{timezone.localtime(m.created_at).strftime("%d/%m/%Y %H:%M")} · {uname(m.author) or m.from_name} · {kind}' + (' · reply' if m.parent_id else '')), small))
                story.append(Paragraph(esc(C.plain(m.body)).replace('\n', '<br/>'), st['BodyText']))
        logs = RecordLog.objects.filter(model=rec.key, object_id=rec.id).order_by('-at')[:60]
        if logs:
            story.append(Paragraph('Change history', h))
            hr = [['When', 'Who', 'What']]
            for l in logs:
                ch = '; '.join(f"{v.get('label', k)}: {v.get('old') if v.get('old') not in (None, '') else '–'} → {v.get('new') if v.get('new') not in (None, '') else '–'}"
                               for k, v in (l.changes or {}).items() if isinstance(v, dict))
                hr.append([Paragraph(esc(timezone.localtime(l.at).strftime('%d/%m/%Y %H:%M')), small), Paragraph(esc(l.user_name or uname(l.user)), small),
                           Paragraph(esc(l.get_action_display() + (': ' + ch[:600] if ch and l.action == 'updated' else '')), small)])
            t = Table(hr, colWidths=[30 * mm, 35 * mm, 110 * mm], repeatRows=1)
            t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#DCDFEA')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#5B4FE0')),
                                   ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            story.append(t)
        buf = BytesIO()
        SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                          title=f'{what} {label}').build(story)
        resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in f'{what}-{label}')[:80]
        resp['Content-Disposition'] = f'inline; filename="{safe}.pdf"'
        return resp
