from rest_framework import serializers

from . import registry as R
from .models import ApprovalFlow, ApprovalRequest, ApprovalStep, FlowTemplate, ScheduledAction, Workflow, WorkflowRun


def _uname(u):
    if u is None:
        return ''
    return getattr(u, 'username', '') or str(u)


class WorkflowSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()
    model_label = serializers.SerializerMethodField()
    module_label = serializers.SerializerMethodField()
    trigger_label = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    problems = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = '__all__'
        read_only_fields = ('version', 'published_at', 'last_run_at', 'runs_count', 'errors_count', 'history', 'created_by', 'created_at', 'updated_at')

    def get_owner_name(self, o):
        return _uname(o.owner)

    def get_model_label(self, o):
        m = R.get_model(o.model) if o.model else None
        return R._nice(m._meta.verbose_name) if m else o.model

    def get_module_label(self, o):
        return R.MODULE_LABELS.get(o.module, o.module)

    def get_trigger_label(self, o):
        return dict(Workflow._meta.get_field('trigger').choices).get(o.trigger, o.trigger)

    def get_summary(self, o):
        acts = [dict((k, v) for k, v, _ in R.ACTIONS).get(a.get('type'), a.get('type')) for a in (o.actions or [])]
        cfg = o.trigger_config or {}
        trig = self.get_trigger_label(o)
        if o.trigger == 'field_changed' and cfg.get('field'):
            trig = f"When {cfg['field']} changes" + (f" to {cfg['to']}" if cfg.get('to') else '')
        if o.trigger == 'date' and cfg.get('date_field'):
            trig = f"{cfg.get('offset_days') or 0} days {cfg.get('when') or 'on'} {cfg['date_field']}" if cfg.get('when') != 'on' else f"On {cfg['date_field']}"
        if o.trigger == 'schedule':
            trig = f"{(cfg.get('every') or 'daily').capitalize()} at {cfg.get('time') or '08:00'}"
        return {'trigger': trig, 'actions': acts, 'conditions': len((o.conditions or {}).get('items') or [])}

    def get_problems(self, o):
        return self.check(o)

    @staticmethod
    def check(o):
        errs = []
        if not o.name:
            errs.append('Give the rule a name.')
        if o.trigger != 'schedule' and R.get_model(o.model) is None:
            errs.append('Choose the record type.')
        if o.trigger == 'field_changed' and not (o.trigger_config or {}).get('field'):
            errs.append('Choose the field to watch.')
        if o.trigger == 'date' and not (o.trigger_config or {}).get('date_field'):
            errs.append('Choose the date field.')
        if not o.actions:
            errs.append('Add at least one action.')
        for i, a in enumerate(o.actions or [], start=1):
            t = a.get('type')
            if t == 'send_email' and not (a.get('to')) :
                errs.append(f'Action {i} (e-mail): choose who receives it.')
            if t == 'send_email' and not (a.get('template') or a.get('subject')):
                errs.append(f'Action {i} (e-mail): a subject or a template.')
            if t == 'notify' and not a.get('to'):
                errs.append(f'Action {i} (notification): choose who receives it.')
            if t == 'update_fields' and not a.get('fields'):
                errs.append(f'Action {i} (update fields): choose the fields.')
            if t == 'create_record' and R.get_model(a.get('model') or '') is None:
                errs.append(f'Action {i} (create record): choose the record type.')
            if t == 'submit_approval' and not ApprovalFlow.objects.filter(pk=a.get('flow') or 0, active=True).exists():
                errs.append(f'Action {i} (approval): choose an active approval flow.')
            if t == 'wait' and not (a.get('days') or a.get('hours') or a.get('minutes') or a.get('until_field')):
                errs.append(f'Action {i} (wait): give a duration or a date field.')
        return errs


class RunSerializer(serializers.ModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    initiated_by_name = serializers.SerializerMethodField()
    duration_ms = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowRun
        fields = '__all__'

    def get_initiated_by_name(self, o):
        return _uname(o.initiated_by)

    def get_duration_ms(self, o):
        return int((o.finished_at - o.started_at).total_seconds() * 1000) if o.finished_at and o.started_at else None

    def get_link(self, o):
        return R.record_link(o.model, o.object_id) if o.model and o.object_id else ''


class ScheduledSerializer(serializers.ModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True, default='')
    link = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledAction
        fields = '__all__'

    def get_link(self, o):
        return R.record_link(o.model, o.object_id) if o.model and o.object_id else ''


class FlowTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlowTemplate
        fields = '__all__'


class ApprovalFlowSerializer(serializers.ModelSerializer):
    model_label = serializers.SerializerMethodField()
    pending = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalFlow
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'updated_at')

    def get_model_label(self, o):
        m = R.get_model(o.model) if o.model else None
        return R._nice(m._meta.verbose_name) if m else o.model

    def get_pending(self, o):
        return o.requests.filter(status='pending').count()


class StepSerializer(serializers.ModelSerializer):
    approver_name = serializers.SerializerMethodField()
    delegate_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    escalated_to_name = serializers.SerializerMethodField()
    can_act = serializers.SerializerMethodField()
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalStep
        fields = '__all__'

    def get_approver_name(self, o):
        return _uname(o.approver)

    def get_delegate_name(self, o):
        return _uname(o.delegate)

    def get_decided_by_name(self, o):
        return _uname(o.decided_by)

    def get_escalated_to_name(self, o):
        return _uname(o.escalated_to)

    def get_can_act(self, o):
        from .approvals import can_act
        return o.status == 'pending' and can_act(o, self.context.get('user'))

    def get_overdue(self, o):
        from django.utils import timezone
        return bool(o.status == 'pending' and o.due_at and o.due_at < timezone.now())


class ApprovalRequestSerializer(serializers.ModelSerializer):
    flow_name = serializers.CharField(source='flow.name', read_only=True)
    submitted_by_name = serializers.SerializerMethodField()
    steps = StepSerializer(many=True, read_only=True)
    model_label = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRequest
        fields = '__all__'

    def get_submitted_by_name(self, o):
        return _uname(o.submitted_by)

    def get_model_label(self, o):
        m = R.get_model(o.model) if o.model else None
        return R._nice(m._meta.verbose_name) if m else o.model

    def get_status_label(self, o):
        return dict(ApprovalRequest._meta.get_field('status').choices).get(o.status, o.status)
