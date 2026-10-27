"""
/flow/api/…  (every call carries ?schema=<company>)

GET   meta/                         modules, record types, operators, action types, users, roles, templates, approval flows
GET   meta/fields/?model=app.model  the fields of a record type (type, choices, links, one level of linked fields)
GET   dashboard/                    counts: rules by state, runs today / failed / waiting, pending approvals, scheduled
CRUD  workflows/                    rules; POST workflows/<id>/activate|deactivate|archive|duplicate|test|run
GET   runs/?workflow=&status=&model=&from=&to=&q=   execution log; POST runs/<id>/retry
GET   scheduled/?status=            scheduled actions; POST scheduled/<id>/cancel
CRUD  templates/                    e-mail / in-app templates; POST templates/<id>/preview {model, id}
CRUD  approval-flows/               approval chains
GET   approvals/?box=mine|all|submitted&status=   approval requests; POST approvals/submit {flow, model, id, note}
POST  approvals/<id>/steps/<step>/decide {decision, comment} · delegate {user, comment}; POST approvals/<id>/cancel · resubmit
GET   notifications/?unread=1       my automation notifications; POST notifications/read {ids|all}
GET   audit/                        configuration changes
"""
import datetime
import logging

from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import ctx as _ctx
from . import approvals as AP
from . import engine as E
from . import registry as R
from .models import (ApprovalFlow, ApprovalRequest, ApprovalStep, FlowAudit, FlowNotification, FlowTemplate, ScheduledAction, Workflow,
                     WorkflowRun)
from .serializers import (ApprovalFlowSerializer, ApprovalRequestSerializer, FlowTemplateSerializer, RunSerializer, ScheduledSerializer,
                          WorkflowSerializer)

log = logging.getLogger(__name__)


def bad(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': msg}, status=code)


class Member(BasePermission):
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = connection.schema_name if connection.schema_name != 'public' else request.GET.get('schema')
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return bool(u.is_superuser or u.tenants.filter(schema_name=sch).exists())


def is_admin(request):
    c = _ctx(request)
    return bool(c and c.admin)


def can_configure(request, code='change_workflow'):
    c = _ctx(request)
    return bool(c and (c.admin or code in c.codes))


def can_view(request, code='view_workflow'):
    c = _ctx(request)
    return bool(c and (c.admin or code in c.codes or code.replace('view_', 'change_') in c.codes))


def audit(request, kind, obj_id, act, detail=None):
    try:
        FlowAudit.objects.create(kind=kind, object_id=str(obj_id), action=act, user=request.user, detail=detail or {})
    except Exception:
        pass


class Base(viewsets.ModelViewSet):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]
    view_code = 'view_workflow'
    change_code = 'change_workflow'
    audit_kind = 'workflow'

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            if not can_view(request, self.view_code):
                self.permission_denied(request, message='You do not have the right to see the automation settings.')
        elif not can_configure(request, self.change_code):
            self.permission_denied(request, message='Only an administrator (or a user with the automation right) can change this.')

    def perform_create(self, serializer):
        obj = serializer.save(**({'created_by': self.request.user} if hasattr(serializer.Meta.model, 'created_by') else {}))
        audit(self.request, self.audit_kind, obj.pk, 'created', {'name': getattr(obj, 'name', '')})
        E.invalidate()

    def perform_update(self, serializer):
        obj = serializer.save()
        audit(self.request, self.audit_kind, obj.pk, 'changed', {'name': getattr(obj, 'name', '')})
        E.invalidate()

    def perform_destroy(self, instance):
        audit(self.request, self.audit_kind, instance.pk, 'deleted', {'name': getattr(instance, 'name', '')})
        super().perform_destroy(instance)
        E.invalidate()


# ------------------------------------------------------------------------------------------------ meta
class MetaView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        from django.contrib.auth.models import Group
        from UserManagement.models import CustomUser
        users = [{'id': u.pk, 'label': u.username, 'email': u.email or ''}
                 for u in CustomUser.objects.filter(tenants__schema_name=connection.schema_name, is_active=True).order_by('username').distinct()[:500]]
        from ZeoApps import groupscope   # v1.62.0 isolation: the groups of this company only (every company's were listed)
        roles = sorted(({'id': g.pk, 'label': groupscope.label(g)} for g in Group.objects.filter(pk__in=groupscope.visible_ids())), key=lambda r: r['label'].lower())
        return Response({
            'modules': R.modules(), 'models': R.models_of(), 'operators': [{'key': k, 'label': v} for k, v in R.OPERATORS],
            'operators_by_type': R.TYPES_FOR_OPERATOR,
            'actions': [{'key': k, 'label': l, 'hint': h} for k, l, h in R.ACTIONS], 'triggers': [{'key': k, 'label': v} for k, v in __import__('ZeoFlow.models', fromlist=['TRIGGERS']).TRIGGERS],
            'users': users, 'roles': roles,
            'templates': list(FlowTemplate.objects.filter(active=True).values('id', 'name', 'kind', 'model')),
            'webhooks': _webhooks(),   # v1.52.0 action "Call a webhook"
            'approval_flows': list(ApprovalFlow.objects.filter(active=True).values('id', 'name', 'model')),
            'recipient_types': [{'key': 'owner', 'label': 'Record owner / employee'}, {'key': 'manager', 'label': "Employee's reporting manager"},
                                {'key': 'employee', 'label': 'The employee of the record'}, {'key': 'triggering_user', 'label': 'The user who made the change'},
                                {'key': 'user', 'label': 'A user'}, {'key': 'role', 'label': 'A role (group)'}, {'key': 'field', 'label': 'A user field of the record'},
                                {'key': 'email', 'label': 'An e-mail address'}],
            'variables': ['{{ record.<field> }}', '{{ record.<link>.<field> }}', '{{ old.<field> }}', '{{ user.name }}', '{{ user.email }}',
                          '{{ company.name }}', '{{ today }}', '{{ now }}', '{{ link }}'],
            'can_configure': can_configure(request), 'is_admin': is_admin(request),
        })


class FieldsView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        key = request.GET.get('model', '')
        if R.get_model(key) is None:
            return bad('Unknown record type.')
        return Response({'model': key, 'fields': R.fields_of(key, with_related=request.GET.get('related', '1') != '0')})


class DashboardView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        if not can_view(request):
            return bad('No right to see the automation dashboard.', status.HTTP_403_FORBIDDEN)
        today = E.local_today()
        by_state = {r['state']: r['n'] for r in Workflow.objects.values('state').annotate(n=Count('id'))}
        runs = WorkflowRun.objects
        week = timezone.now() - datetime.timedelta(days=7)
        return Response({
            'workflows': {'total': sum(by_state.values()), **{k: by_state.get(k, 0) for k, _ in Workflow._meta.get_field('state').choices}},
            'runs': {'today': runs.filter(day=today).count(), 'failed_7d': runs.filter(started_at__gte=week, status='failed').count(),
                     'ok_7d': runs.filter(started_at__gte=week, status='ok').count(), 'waiting': runs.filter(status='waiting').count()},
            'approvals': {'pending': ApprovalRequest.objects.filter(status='pending').count(),
                          'mine': ApprovalStep.objects.filter(status='pending').filter(Q(approver=request.user) | Q(delegate=request.user) | Q(escalated_to=request.user)).count(),
                          'overdue': ApprovalStep.objects.filter(status='pending', due_at__lt=timezone.now()).count()},
            'scheduled': {'pending': ScheduledAction.objects.filter(status='pending').count(),
                          'overdue': ScheduledAction.objects.filter(status='pending', due_at__lt=timezone.now()).count()},
            'recent_runs': RunSerializer(runs.select_related('workflow')[:10], many=True).data,
            'top_errors': list(runs.filter(started_at__gte=week, status='failed').values('workflow__name').annotate(n=Count('id')).order_by('-n')[:5]),
        })


# ------------------------------------------------------------------------------------------------ workflows
class WorkflowViewSet(Base):
    queryset = Workflow.objects.all()
    serializer_class = WorkflowSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related('owner', 'created_by')
        st = self.request.GET.get('state')
        if st:
            qs = qs.filter(state__in=st.split(','))
        if self.request.GET.get('module'):
            qs = qs.filter(module=self.request.GET['module'])
        if self.request.GET.get('model'):
            qs = qs.filter(model=self.request.GET['model'])
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(model__icontains=q))
        return qs

    def _set_state(self, request, pk, state):
        wf = self.get_object()
        if state == 'active':
            errs = WorkflowSerializer.check(wf)
            if errs:
                return Response({'detail': 'The rule cannot be activated: ' + ' '.join(errs), 'errors': errs}, status=400)
            wf.state = 'active'
            if not wf.published_at:
                wf.published_at = timezone.now()
            else:
                wf.version += 1
                wf.published_at = timezone.now()
            wf.history = (wf.history or [])[-19:] + [{'version': wf.version, 'at': timezone.now().isoformat(), 'by': request.user.username,
                                                        'snapshot': {'trigger': wf.trigger, 'trigger_config': wf.trigger_config, 'conditions': wf.conditions, 'actions': wf.actions, 'delay_minutes': wf.delay_minutes}}]
        else:
            wf.state = state
        wf.save()
        E.invalidate()
        audit(request, 'workflow', wf.pk, state, {'name': wf.name})
        return Response(WorkflowSerializer(wf).data)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        return self._set_state(request, pk, 'active')

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        return self._set_state(request, pk, 'inactive')

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        return self._set_state(request, pk, 'archived')

    @action(detail=False, methods=['post'])
    def load_samples(self, request):
        """Five working example rules as drafts (welcome, probation review, designation change, visa expiry, leave request)."""
        from .samples import load
        made = load(request.user)
        E.invalidate()
        return Response({'created': made})

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        wf = self.get_object()
        wf.pk = None
        wf.name = f'{wf.name} (copy)'[:120]
        wf.state, wf.version, wf.published_at, wf.last_run_at, wf.runs_count, wf.errors_count, wf.history = 'draft', 1, None, None, 0, 0, []
        wf.created_by = request.user
        wf.save()
        audit(request, 'workflow', wf.pk, 'duplicated', {'name': wf.name})
        return Response(WorkflowSerializer(wf).data, status=201)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        wf = self.get_object()
        v = int(request.data.get('version') or 0)
        snap = next((h for h in (wf.history or []) if h.get('version') == v), None)
        if not snap:
            return bad('Version not found.')
        for k, val in snap['snapshot'].items():
            setattr(wf, k, val)
        wf.state = 'draft'
        wf.save()
        E.invalidate()
        audit(request, 'workflow', wf.pk, 'rolled back', {'to_version': v})
        return Response(WorkflowSerializer(wf).data)

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """Dry run on one record: which conditions hold, what each action WOULD do (nothing is changed or sent)."""
        wf = self.get_object()
        model = R.get_model(wf.model)
        obj = model._default_manager.filter(pk=request.data.get('id')).first() if model and request.data.get('id') else None
        if wf.model and obj is None:
            return bad('Give the id of a record of this type to test with.')
        ok = E.eval_group(wf.conditions or {}, obj, None)
        ctx = E.context_for(obj, None, request.user)
        lines = []

        def walk(acts, path=''):
            for i, a in enumerate(acts or []):
                t = a.get('type')
                d = {'index': f'{path}{i + 1}', 'type': t, 'label': a.get('label') or t}
                if t == 'send_email':
                    tpl = E._template(a.get('template'))
                    users, emails = E.resolve_recipients(a.get('to'), ctx)
                    d['detail'] = f"to {', '.join(emails) or '(nobody)'} · subject: {E.render(a.get('subject') or (tpl.subject if tpl else ''), ctx)}"
                    d['body'] = E.render(a.get('body') or (tpl.body if tpl else ''), ctx)[:1500]
                elif t == 'notify':
                    users, _ = E.resolve_recipients(a.get('to'), ctx)
                    d['detail'] = f"to {', '.join(u.username for u in users) or '(nobody)'} · {E.render(a.get('title') or a.get('message') or '', ctx)}"
                elif t == 'create_task':
                    users, _ = E.resolve_recipients(a.get('assign') or [{'type': 'owner'}], ctx)
                    d['detail'] = f"'{E.render(a.get('summary') or 'Follow up', ctx)}' for {', '.join(u.username for u in users) or '(nobody)'}"
                elif t == 'update_fields':
                    d['detail'] = ', '.join(f'{k} = {E.render(str(v), ctx)}' for k, v in (a.get('fields') or {}).items())
                elif t == 'create_record':
                    d['detail'] = f"{a.get('model')}: " + ', '.join(f'{k} = {E.render(str(v), ctx)}' for k, v in (a.get('fields') or {}).items())
                elif t == 'branch':
                    b = E.eval_group(a.get('conditions') or {}, obj, None)
                    d['detail'] = 'then' if b else 'else'
                    lines.append(d)
                    walk(a.get('then') if b else a.get('else'), d['index'] + ('a.' if b else 'b.'))
                    continue
                elif t == 'wait':
                    d['detail'] = f"{a.get('days') or 0} d {a.get('hours') or 0} h" + (f" / until {a.get('until_field')}" if a.get('until_field') else '')
                elif t == 'submit_approval':
                    f = ApprovalFlow.objects.filter(pk=a.get('flow')).first()
                    d['detail'] = f.name if f else '(flow not found)'
                else:
                    d['detail'] = ''
                lines.append(d)
        walk(wf.actions)
        return Response({'record': ctx['record_label'], 'conditions_met': ok, 'would_run': ok and not E._limits_ok(wf, obj), 'limit': E._limits_ok(wf, obj) if obj else '',
                         'actions': lines, 'variables': {'link': ctx['link'], 'today': str(ctx['today'])}})

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        """Manual trigger: run the rule now for a record (any trigger type – the conditions are checked)."""
        wf = self.get_object()
        if wf.state != 'active' and not request.data.get('force'):
            return bad('Activate the rule first (or send force: true to run a draft once).')
        model = R.get_model(wf.model)
        obj = model._default_manager.filter(pk=request.data.get('id')).first() if model and request.data.get('id') else None
        if wf.model and obj is None:
            return bad('Record not found.')
        run = E.start_run(wf, obj, 'manual', None, request.user, 'user', force=bool(request.data.get('ignore_conditions')))
        if run is None:
            return bad('The rule did not run (loop protection).')
        return Response(RunSerializer(run).data)


class RunViewSet(viewsets.ReadOnlyModelViewSet):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]
    queryset = WorkflowRun.objects.select_related('workflow', 'initiated_by')
    serializer_class = RunSerializer

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        if not can_view(request):
            self.permission_denied(request, message='No right to see the execution log.')

    def get_queryset(self):
        qs = super().get_queryset()
        g = self.request.GET
        if g.get('workflow'):
            qs = qs.filter(workflow_id=g['workflow'])
        if g.get('status'):
            qs = qs.filter(status__in=g['status'].split(','))
        if g.get('model'):
            qs = qs.filter(model=g['model'])
        if g.get('from'):
            qs = qs.filter(day__gte=g['from'])
        if g.get('to'):
            qs = qs.filter(day__lte=g['to'])
        if g.get('q'):
            qs = qs.filter(Q(record_label__icontains=g['q']) | Q(error__icontains=g['q']) | Q(workflow__name__icontains=g['q']))
        return qs[:int(g.get('limit') or 300)]

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        if not can_configure(request):
            return bad('Only an administrator can retry a run.', 403)
        run = WorkflowRun.objects.filter(pk=pk).select_related('workflow').first()
        if run is None:
            return bad('Run not found.', 404)
        if run.status not in ('failed', 'cancelled'):
            return bad('Only a failed run can be retried.')
        run = E.retry(run, request.user)
        return Response(RunSerializer(run).data)


class ScheduledViewSet(viewsets.ReadOnlyModelViewSet):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]
    queryset = ScheduledAction.objects.select_related('workflow', 'run')
    serializer_class = ScheduledSerializer

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        if not can_view(request):
            self.permission_denied(request, message='No right to see the scheduled actions.')

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.GET.get('status')
        if st == 'overdue':
            qs = qs.filter(status='pending', due_at__lt=timezone.now())
        elif st:
            qs = qs.filter(status__in=st.split(','))
        return qs[:500]

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        if not can_configure(request):
            return bad('Only an administrator can cancel a scheduled action.', 403)
        sa = ScheduledAction.objects.filter(pk=pk).first()
        if sa is None or sa.status != 'pending':
            return bad('Only a pending action can be cancelled.')
        sa.status, sa.done_at, sa.note = 'cancelled', timezone.now(), f'cancelled by {request.user.username}'
        sa.save()
        if sa.run_id:
            WorkflowRun.objects.filter(pk=sa.run_id, status='waiting').update(status='cancelled', finished_at=timezone.now(), error='scheduled action cancelled')
        return Response(ScheduledSerializer(sa).data)

    @action(detail=False, methods=['post'])
    def run_due(self, request):
        """Run the scheduler now (what Celery beat does every 15 minutes)."""
        if not can_configure(request):
            return bad('Only an administrator can run the scheduler.', 403)
        return Response(E.run_due())


class TemplateViewSet(Base):
    queryset = FlowTemplate.objects.all()
    serializer_class = FlowTemplateSerializer
    audit_kind = 'template'

    @action(detail=True, methods=['post'])
    def preview(self, request, pk=None):
        t = self.get_object()
        model = R.get_model(request.data.get('model') or t.model)
        obj = model._default_manager.filter(pk=request.data.get('id')).first() if model and request.data.get('id') else (model._default_manager.first() if model else None)
        ctx = E.context_for(obj, None, request.user)
        return Response({'record': ctx['record_label'], 'subject': E.render(t.subject, ctx), 'body': E.render(t.body, ctx)})


# ------------------------------------------------------------------------------------------------ approvals
class ApprovalFlowViewSet(Base):
    queryset = ApprovalFlow.objects.all()
    serializer_class = ApprovalFlowSerializer
    view_code = 'view_approvalflow'
    change_code = 'change_approvalflow'
    audit_kind = 'approval_flow'


class ApprovalViewSet(viewsets.ReadOnlyModelViewSet):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]
    queryset = ApprovalRequest.objects.select_related('flow', 'submitted_by').prefetch_related('steps', 'steps__approver', 'steps__delegate', 'steps__decided_by', 'steps__escalated_to')
    serializer_class = ApprovalRequestSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        u = self.request.user
        box = self.request.GET.get('box') or 'mine'
        if box == 'mine':
            qs = qs.filter(steps__status='pending').filter(Q(steps__approver=u) | Q(steps__delegate=u) | Q(steps__escalated_to=u)).distinct()
        elif box == 'submitted':
            qs = qs.filter(submitted_by=u)
        elif box == 'history':
            qs = qs.filter(Q(steps__approver=u) | Q(steps__delegate=u) | Q(submitted_by=u)).distinct()
        elif box == 'all':
            if not can_view(self.request, 'view_approvalflow'):
                qs = qs.filter(Q(steps__approver=u) | Q(steps__delegate=u) | Q(submitted_by=u)).distinct()
        st = self.request.GET.get('status')
        if st:
            qs = qs.filter(status__in=st.split(','))
        if self.request.GET.get('model'):
            qs = qs.filter(model=self.request.GET['model'])
        return qs[:500]

    def get_serializer_context(self):
        c = super().get_serializer_context()
        c['user'] = self.request.user
        return c

    @action(detail=False, methods=['post'])
    def submit(self, request):
        model = R.get_model(request.data.get('model') or '')
        obj = model._default_manager.filter(pk=request.data.get('id')).first() if model else None
        if obj is None:
            return bad('Record not found.')
        try:
            req = AP.submit(request.data.get('flow'), obj, user=request.user, note=request.data.get('note') or '')
        except AP.Refused as e:
            return bad(str(e))
        return Response(ApprovalRequestSerializer(req, context={'user': request.user}).data, status=201)

    @action(detail=True, methods=['post'], url_path=r'steps/(?P<step>\d+)/decide')
    def decide(self, request, pk=None, step=None):
        s = ApprovalStep.objects.filter(pk=step, request_id=pk).select_related('request', 'request__flow').first()
        if s is None:
            return bad('Step not found.', 404)
        d = request.data.get('decision')
        if d not in ('approved', 'rejected', 'changes'):
            return bad('decision must be approved, rejected or changes.')
        try:
            req = AP.decide(s, request.user, d, request.data.get('comment') or '', request.FILES.get('attachment'))
        except AP.Refused as e:
            return bad(str(e), 403)
        return Response(ApprovalRequestSerializer(ApprovalRequest.objects.get(pk=req.pk), context={'user': request.user}).data)

    @action(detail=True, methods=['post'], url_path=r'steps/(?P<step>\d+)/delegate')
    def delegate(self, request, pk=None, step=None):
        from UserManagement.models import CustomUser
        s = ApprovalStep.objects.filter(pk=step, request_id=pk).select_related('request').first()
        if s is None:
            return bad('Step not found.', 404)
        to = CustomUser.objects.filter(pk=request.data.get('user')).first()
        try:
            AP.delegate(s, request.user, to, request.data.get('comment') or '')
        except AP.Refused as e:
            return bad(str(e), 403)
        return Response(ApprovalRequestSerializer(ApprovalRequest.objects.get(pk=pk), context={'user': request.user}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        req = self.get_queryset().model.objects.filter(pk=pk).first()
        if req is None:
            return bad('Request not found.', 404)
        try:
            AP.cancel(req, request.user, request.data.get('note') or '')
        except AP.Refused as e:
            return bad(str(e), 403)
        return Response(ApprovalRequestSerializer(req, context={'user': request.user}).data)

    @action(detail=True, methods=['post'])
    def resubmit(self, request, pk=None):
        req = ApprovalRequest.objects.filter(pk=pk).first()
        if req is None:
            return bad('Request not found.', 404)
        try:
            new = AP.resubmit(req, request.user, request.data.get('note') or '')
        except AP.Refused as e:
            return bad(str(e))
        return Response(ApprovalRequestSerializer(new, context={'user': request.user}).data, status=201)

    @action(detail=False, methods=['get'])
    def record(self, request):
        """The approval requests of one record (for the record page)."""
        qs = ApprovalRequest.objects.filter(model=request.GET.get('model', ''), object_id=str(request.GET.get('id', '')))
        return Response(ApprovalRequestSerializer(qs[:20], many=True, context={'user': request.user}).data)


class RecordFlowView(APIView):
    """v1.38.0: the workflow strip of a record page – its approval requests, the approval flows it can be sent to,
    the automation rules that watch this record type and the runs that touched this record. GET ?model=app.model&id=pk"""
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        model = request.GET.get('model') or ''
        pk = str(request.GET.get('id') or '')
        if not model:
            return Response({'model': '', 'approvals': [], 'flows': [], 'rules': [], 'runs': []})
        ctx = {'user': request.user}
        approvals = ApprovalRequestSerializer(ApprovalRequest.objects.filter(model__iexact=model, object_id=pk).order_by('-pk')[:10], many=True, context=ctx).data if pk else []
        open_ = [a for a in approvals if a.get('status') in ('pending', 'changes')]
        flows = list(ApprovalFlow.objects.filter(model__iexact=model, active=True).order_by('name').values('id', 'name', 'description')) if not open_ else []
        rules = list(Workflow.objects.filter(model__iexact=model, state='active').order_by('name').values('id', 'name', 'trigger'))
        runs = []
        if pk and can_view(request):
            runs = [{'id': r.pk, 'workflow': r.workflow.name, 'status': r.status, 'started_at': r.started_at, 'error': r.error[:200], 'trigger': r.trigger}
                    for r in WorkflowRun.objects.filter(model__iexact=model, object_id=pk).select_related('workflow').order_by('-pk')[:10]]
        return Response({'model': model, 'approvals': approvals, 'flows': flows, 'rules': rules, 'runs': runs,
                         'can_manage': can_view(request, 'view_approvalflow') or can_view(request)})


class NotificationView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        qs = FlowNotification.objects.filter(recipient=request.user)
        if request.GET.get('unread') == '1':
            qs = qs.filter(is_read=False)
        rows = [{'id': n.pk, 'title': n.title, 'message': n.message, 'link': n.link, 'is_read': n.is_read, 'created_at': n.created_at,
                 'model': n.model, 'object_id': n.object_id} for n in qs[:100]]
        return Response({'rows': rows, 'unread': FlowNotification.objects.filter(recipient=request.user, is_read=False).count()})

    def post(self, request):
        ids = request.data.get('ids')
        qs = FlowNotification.objects.filter(recipient=request.user, is_read=False)
        if ids and ids != 'all':
            qs = qs.filter(pk__in=[int(x) for x in ids if str(x).isdigit()])
        n = qs.update(is_read=True)
        return Response({'read': n})


class AuditView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [Member]

    def get(self, request):
        if not can_view(request):
            return bad('No right.', 403)
        rows = [{'id': a.pk, 'kind': a.kind, 'object_id': a.object_id, 'action': a.action, 'user': getattr(a.user, 'username', ''), 'at': a.at, 'detail': a.detail}
                for a in FlowAudit.objects.select_related('user')[:300]]
        return Response(rows)



def _webhooks():
    """v1.52.0 the company's webhooks (Settings → API keys and webhooks) for the action "Call a webhook"."""
    try:
        from ZeoAdmin.models import Webhook
        return list(Webhook.objects.filter(active=True).values('id', 'name', 'url'))
    except Exception:
        return []
