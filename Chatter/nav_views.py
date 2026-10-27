"""v1.47.0 – global search, quick create, company wall, My team hub with 1:1 notes, user flags and "What's new".

GET  search/?q=                    employees and records of every active app the user may open
GET  quick/                        what the user may create from the '+' button
POST todo/  (views.TodoView)       a to-do for me (or a colleague) without a record
GET  wall/                         birthdays, anniversaries, new joiners, announcements – with reactions and comments
POST wall/react/  {item, emoji}    toggle a reaction
POST wall/comment/ {item, body}    a wish or a comment;  DELETE wall/comment/<pk>/
GET  my-team/?indirect=1           the manager's hub
GET  one-on-ones/?employee= | ?mine=1    POST one-on-ones/    PATCH / DELETE one-on-ones/<pk>/
GET  flags/   POST flags/ {key, value}
GET  whats-new/   POST whats-new/ {seen: version}
"""
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import nav as N
from .models import OneOnOne, WallComment, WallReaction
from .views import _parse_date, is_admin


def bad(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': msg}, status=code)


class Base(APIView):
    permission_classes = [IsAuthenticated]


class SearchView(Base):
    def get(self, request):
        return Response(N.search(request, request.query_params.get('q', '')))


class QuickView(Base):
    def get(self, request):
        return Response(N.quick_creates(request))


# ------------------------------------------------------------------ wall
class WallView(Base):
    def get(self, request):
        return Response(N.wall(request))


class WallReactView(Base):
    def post(self, request):
        item, emoji = str(request.data.get('item') or ''), str(request.data.get('emoji') or '')
        if emoji not in N.WALL_EMOJIS:
            return bad('Choose one of the offered reactions.')
        if not N.item_ok(request, item):
            return bad('This item is not on your wall.', status.HTTP_404_NOT_FOUND)
        r = WallReaction.objects.filter(item=item, user=request.user, emoji=emoji).first()
        if r:
            r.delete()
            on = False
        else:
            WallReaction.objects.create(item=item, user=request.user, emoji=emoji)
            on = True
            N.notify_celebrant(item, request.user, emoji, emoji=True)
        return Response({'item': item, 'emoji': emoji, 'on': on, 'items': N.social([{'item': item}], request.user)})


class WallCommentView(Base):
    def post(self, request):
        item, body = str(request.data.get('item') or ''), str(request.data.get('body') or '').strip()
        if not body:
            return bad('Write a message.')
        if not N.item_ok(request, item):
            return bad('This item is not on your wall.', status.HTTP_404_NOT_FOUND)
        if not N.comments_allowed(item):
            return bad('Comments are switched off for this announcement.')
        c = WallComment.objects.create(item=item, user=request.user, body=body[:2000])
        N.notify_celebrant(item, request.user, body)
        return Response(N.comment_json(c, request.user), status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        c = WallComment.objects.filter(pk=pk).first()
        if c is None:
            return bad('Comment not found.', status.HTTP_404_NOT_FOUND)
        if c.user_id != request.user.id and not is_admin(request):
            return bad('Only the author can delete this comment.', status.HTTP_403_FORBIDDEN)
        c.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ my team and 1:1s
class MyTeamView(Base):
    def get(self, request):
        return Response(N.team_hub(request, request.query_params.get('indirect') in ('1', 'true')))


def _oo_in(o, d, request):
    if 'date' in d:
        dt = _parse_date(d.get('date'))
        if not dt:
            return 'Choose the date of the meeting.'
        o.date = dt
    for f in ('agenda', 'notes', 'private_note'):
        if f in d:
            setattr(o, f, str(d.get(f) or '')[:20000])
    if 'shared' in d:
        o.shared = d.get('shared') in (True, 'true', '1', 1)
    if 'actions' in d:
        acts = d.get('actions') or []
        if not isinstance(acts, list):
            return 'Action items must be a list.'
        o.actions = [{'text': str(a.get('text') or '')[:300], 'done': bool(a.get('done'))} for a in acts
                     if isinstance(a, dict) and str(a.get('text') or '').strip()][:50]
    return None


class OneOnOnesView(Base):
    def get(self, request):
        from EmpManagement.models import emp_master
        if request.query_params.get('mine') in ('1', 'true'):
            me = N.my_emp(request.user)
            if me is None:
                return Response([])
            return Response([N.oo_json(o, False) for o in OneOnOne.objects.filter(employee=me, shared=True).select_related('employee', 'manager')])
        e = emp_master.objects.filter(pk=request.query_params.get('employee') or 0).first()
        if e is None:
            return bad('Choose the team member.')
        if not N.is_manager_of(request, e):
            return bad('Only the manager of this employee (or HR) sees the 1:1 notes.', status.HTTP_403_FORBIDDEN)
        return Response([N.oo_json(o) for o in OneOnOne.objects.filter(employee=e).select_related('employee', 'manager')])

    def post(self, request):
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(pk=request.data.get('employee') or 0).first()
        if e is None:
            return bad('Choose the team member.')
        if not N.is_manager_of(request, e):
            return bad('You can plan 1:1s only with the people who report to you.', status.HTTP_403_FORBIDDEN)
        o = OneOnOne(employee=e, manager=request.user, created_by=request.user, date=timezone.localdate())
        err = _oo_in(o, request.data, request)
        if err:
            return bad(err)
        o.save()
        if o.shared and e.users_id and e.users_id != request.user.id:
            try:
                from . import collab as C
                C._bell(e.users, f'1:1 with {N.uname(request.user)} on {o.date:%d %b %Y}', (o.agenda or '')[:200],
                        '/main-sidebar/my-one-on-ones', 'Chatter.oneonone', o.id)
            except Exception:
                pass
        return Response(N.oo_json(o), status=status.HTTP_201_CREATED)


class OneOnOneView(Base):
    def _get(self, request, pk):
        o = OneOnOne.objects.filter(pk=pk).select_related('employee', 'manager').first()
        if o is None or not N.is_manager_of(request, o.employee):
            return None
        return o

    def patch(self, request, pk):
        o = self._get(request, pk)
        if o is None:
            me = N.my_emp(request.user)   # the employee ticks the action items of a shared 1:1
            o = OneOnOne.objects.filter(pk=pk, employee=me, shared=True).first() if me else None
            if o is None:
                return bad('1:1 not found.', status.HTTP_404_NOT_FOUND)
            if set(request.data.keys()) - {'actions'}:
                return bad('You can only tick the action items of this 1:1.', status.HTTP_403_FORBIDDEN)
            old = [a.get('text') for a in (o.actions or [])]
            new = request.data.get('actions') or []
            if [str(a.get('text') or '') for a in new if isinstance(a, dict)] != old:
                return bad('You can only tick the action items of this 1:1.', status.HTTP_403_FORBIDDEN)
            err = _oo_in(o, {'actions': new}, request)
            o.save()
            return Response(N.oo_json(o, False))
        err = _oo_in(o, request.data, request)
        if err:
            return bad(err)
        o.save()
        return Response(N.oo_json(o))

    def delete(self, request, pk):
        o = self._get(request, pk)
        if o is None:
            return bad('1:1 not found.', status.HTTP_404_NOT_FOUND)
        o.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ flags and what's new
class FlagsView(Base):
    def get(self, request):
        return Response(N.flags(request.user))

    def post(self, request):
        f = N.set_flag(request.user, request.data.get('key'), request.data.get('value'))
        if f is None:
            return bad('Give the key.')
        return Response({f.key: f.value})


def _version():
    try:
        return (Path(settings.BASE_DIR) / 'VERSION').read_text().strip()
    except Exception:
        return N.RELEASES[0]['version']


class WhatsNewView(Base):
    def get(self, request):
        seen = (N.flags(request.user).get('whatsnew') or {}).get('seen', '')
        rel = [{'version': r['version'], 'date': r['date'], 'title': r['title'],
                'items': [{'icon': i, 'title': t, 'text': x} for i, t, x in r['items']]} for r in N.RELEASES]
        return Response({'current': _version(), 'seen': seen, 'releases': rel})

    def post(self, request):
        v = str(request.data.get('seen') or _version())[:20]
        N.set_flag(request.user, 'whatsnew', {'seen': v, 'at': timezone.now().isoformat()})
        return Response({'seen': v})
