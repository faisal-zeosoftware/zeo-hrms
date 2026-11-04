"""v1.62.0 people – Company policy and Announcements (admin) as registers.

GET  api/company-policies/                   every policy: title, applies to, acknowledged x of y (%), version, file
POST api/company-policies/                   new policy (OrganisationManager CompanyPolicy API: file, audience)
POST api/company-policies/<id>/remind|delete/
GET  api/announcements/                      every announcement: title, short text, audience in words, published / expires (dd/mm/yyyy),
                                             pinned, read by, comments, status (Scheduled / Live / Expired)
POST api/announcements/                      new announcement (OrganisationManager announcement API – e-mails as before)
POST api/announcements/<id>/delete|pin|unpin/
The acknowledgement counts and reminders are those of Policy acknowledgements (OrgStructure).
"""
from django.apps import apps
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

M = apps.get_model


def _can(request, *codes):
    from Chatter.views import has_code
    return has_code(request, *codes)


def _delegate(viewset_cls, request, data, action='create', pk=None):
    v = viewset_cls()
    v.request, v.args, v.kwargs, v.format_kwarg, v.action = request, (), ({'pk': pk} if pk else {}), None, action
    v.check_permissions(request)
    return v


def _audience(obj, rels):
    parts = []
    for rel, label in rels:
        try:
            names = [str(x) for x in getattr(obj, rel).all()[:4]]
        except Exception:
            names = []
        if names:
            parts.append(', '.join(names))
    return '; '.join(parts) or tr('Everyone')


class PoliciesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from OrgStructure.views import PolicyStatusView
        v = PolicyStatusView()
        v.request, v.args, v.kwargs, v.format_kwarg = request, (), {}, None
        r = v.get(request)
        if r.status_code != 200:
            return r
        P = M('OrganisationManager', 'CompanyPolicy')
        pol = {p.pk: p for p in P.objects.prefetch_related('branch', 'specific_users').select_related('department', 'designation', 'category')}
        out = []
        for x in r.data:
            p = pol.get(x['id'])
            aud = [str(b) for b in p.branch.all()] if p else []
            for f in ('department', 'designation', 'category'):
                if p and getattr(p, f + '_id', None):
                    aud.append(str(getattr(p, f)))
            out.append({**x, 'audience': ', '.join(aud) or tr('Everyone'),
                        'ack_label': (tr('{0} of {1} ({2} %)', x['acknowledged'], x['applies_to'], round(x['percent'])) if x['requires_ack'] else tr('Not needed')),
                        'ack_level': 'ok' if x['percent'] >= 100 else ('warn' if x['percent'] >= 50 else 'bad') if x['requires_ack'] else 'none',
                        'updated': (p.updated_at.date() if p and p.updated_at else None), 'file': p.policy_file.url if p and p.policy_file else None})
        return Response(out)

    def post(self, request):
        from OrganisationManager.views import CompanyPolicyViewSet
        v = _delegate(CompanyPolicyViewSet, request, request.data)
        ser = v.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v.perform_create(ser)
        return Response({'id': ser.instance.pk, 'detail': tr('Policy "{0}" published – employees confirm it under My policies.', ser.instance.title)}, status=201)


class PolicyActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        if action == 'remind':
            from OrgStructure.views import PolicyRemindView
            v = PolicyRemindView()
            v.request, v.args, v.kwargs, v.format_kwarg = request, (), {}, None
            r = v.post(request, pk)
            if r.status_code == 200:
                return Response({'detail': tr('Reminder sent to {0} employees.', r.data.get('notified', 0))})
            return r
        if action == 'delete':
            if not _can(request, 'delete_companypolicy'):
                return Response({'detail': tr('You may not delete policies.')}, status=403)
            M('OrganisationManager', 'CompanyPolicy').objects.filter(pk=pk).delete()
            return Response({'detail': tr('Policy deleted.')})
        return Response({'detail': tr('Unknown action.')}, status=400)


AUD = [('branches', 'Branches'), ('department', 'Departments'), ('designation', 'Designations'), ('category', 'Categories'),
       ('specific_employees', 'Employees')]


class AnnouncementsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, 'view_announcement', 'add_announcement', 'change_announcement'):
            return Response({'detail': tr('You may not see the announcements list.')}, status=403)
        A = M('OrganisationManager', 'Announcement')
        V = M('OrganisationManager', 'AnnouncementView')
        C = M('OrganisationManager', 'AnnouncementComment')
        from django.db.models import Count
        views = dict(V.objects.values('announcement_id').annotate(n=Count('id')).values_list('announcement_id', 'n'))
        comments = dict(C.objects.values('announcement_id').annotate(n=Count('id')).values_list('announcement_id', 'n'))
        now = timezone.now()
        out = []
        for a in A.objects.prefetch_related('branches', 'department', 'designation', 'category', 'specific_employees').order_by('-is_sticky', '-created_at')[:500]:
            pub = a.schedule_at or a.created_at
            st = 'expired' if a.expires_at and a.expires_at < now else ('scheduled' if a.schedule_at and a.schedule_at > now else 'live')
            text = ' '.join((a.message or '').split())
            out.append({'id': a.pk, 'title': a.title, 'summary': text[:140] + ('…' if len(text) > 140 else ''), 'message': a.message,
                        'audience': _audience(a, AUD), 'published': timezone.localtime(pub).date() if pub else None,
                        'expires': timezone.localtime(a.expires_at).date() if a.expires_at else None, 'pinned': bool(a.is_sticky),
                        'pinned_label': tr('Pinned') if a.is_sticky else '', 'email': bool(a.send_email),
                        'read_by': views.get(a.pk, 0), 'comments': comments.get(a.pk, 0) if a.allow_comments else None,
                        'status': st, 'status_label': {'expired': tr('Expired'), 'scheduled': tr('Scheduled'), 'live': tr('Live')}[st]})
        return Response(out)

    def post(self, request):
        from OrganisationManager.views import AnnouncementViewSet
        v = _delegate(AnnouncementViewSet, request, request.data)
        ser = v.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v.perform_create(ser)
        return Response({'id': ser.instance.pk, 'detail': tr('Announcement published.')}, status=201)


class AnnouncementActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        A = M('OrganisationManager', 'Announcement')
        a = A.objects.filter(pk=pk).first()
        if a is None:
            return Response({'detail': tr('Announcement not found.')}, status=404)
        if action == 'delete':
            if not _can(request, 'delete_announcement'):
                return Response({'detail': tr('You may not delete announcements.')}, status=403)
            a.delete()
            return Response({'detail': tr('Announcement deleted.')})
        if action in ('pin', 'unpin'):
            if not _can(request, 'change_announcement'):
                return Response({'detail': tr('You may not change announcements.')}, status=403)
            A.objects.filter(pk=pk).update(is_sticky=(action == 'pin'))
            return Response({'detail': tr('Pinned to the top of the wall.') if action == 'pin' else tr('No longer pinned.')})
        return Response({'detail': tr('Unknown action.')}, status=400)


class OrgOverviewView(APIView):
    """v1.62.0 people – Organisation overview: every branch with its departments, head count (active employees), managers of the
    department (most common reporting manager) and the designations, plus totals. Branch rights apply."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from collections import Counter, defaultdict
        from AccessControl.access import allowed_branches
        from .names import user_names
        E = M('EmpManagement', 'emp_master')
        B = M('OrganisationManager', 'brnch_mstr')
        br = allowed_branches(request)
        emps = E.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_desgntn_id')
        branches = B.objects.all().order_by('branch_name')
        if br is not None:
            emps = emps.filter(emp_branch_id__in=br)
            branches = branches.filter(pk__in=br)
        by = defaultdict(lambda: defaultdict(list))
        for e in emps:
            by[e.emp_branch_id_id][e.emp_dept_id_id].append(e)
        mgr_ids = {e.emp_reporting_manager_id for e in emps if e.emp_reporting_manager_id}
        names = user_names(mgr_ids, with_code=False)
        out = []
        for b in branches:
            depts = []
            for did, lst in sorted(by.get(b.pk, {}).items(), key=lambda kv: -len(kv[1])):
                d = lst[0].emp_dept_id
                mgr = Counter(e.emp_reporting_manager_id for e in lst if e.emp_reporting_manager_id).most_common(1)
                desig = Counter(str(e.emp_desgntn_id) for e in lst if e.emp_desgntn_id_id).most_common(3)
                depts.append({'id': did, 'name': str(d) if d else tr('No department'), 'headcount': len(lst),
                              'manager': names.get(mgr[0][0], '') if mgr else '',
                              'designations': ', '.join(f'{n} ({c})' for n, c in desig)})
            out.append({'id': b.pk, 'name': b.branch_name, 'code': getattr(b, 'branch_code', '') or '', 'headcount': sum(x['headcount'] for x in depts),
                        'departments': depts})
        return Response({'branches': out, 'total': sum(b['headcount'] for b in out), 'departments': len({d['id'] for b in out for d in b['departments']})})
