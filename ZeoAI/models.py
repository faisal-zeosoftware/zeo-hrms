"""ZEO AI assistants (v1.37.0) – payroll auditor, attendance & shift, recruitment, performance & development, workforce analytics.

Everything runs on ZEO's own rules and statistics with a written explanation; the external model of Settings → AI settings is used
only for wording (summaries, job descriptions, interview questions) when the company switched it on – never to decide.
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'


class AIAssistantSettings(models.Model):
    # payroll auditor
    gross_change_pct = models.DecimalField('flag a gross change above (%)', max_digits=5, decimal_places=1, default=15)
    gross_critical_pct = models.DecimalField('critical above (%)', max_digits=5, decimal_places=1, default=40)
    net_change_pct = models.DecimalField('flag a net change above (%) when the gross did not move', max_digits=5, decimal_places=1, default=10)
    totals_change_pct = models.DecimalField('flag a payroll total change above (%)', max_digits=5, decimal_places=1, default=10)
    require_audit = models.BooleanField('a run must be audited before approval', default=True)
    block_on_critical = models.BooleanField('approval is refused while critical findings are open', default=True)
    # attendance
    late_repeat = models.PositiveSmallIntegerField('repeated late arrivals – flag from (times in 30 days)', default=3)
    missed_repeat = models.PositiveSmallIntegerField('repeated missed punches – flag from (times in 30 days)', default=2)
    min_staff_pct = models.PositiveSmallIntegerField('coverage gap when rostered staff falls below (% of the usual)', default=80)
    ot_cost_hours_per_month = models.PositiveSmallIntegerField('standard hours per month for the overtime rate', default=240)
    # workforce
    forecast_months = models.PositiveSmallIntegerField('forecast horizon (months)', default=6)
    turnover_alert_pct = models.DecimalField('turnover alert above (% in 12 months)', max_digits=5, decimal_places=1, default=20)
    absence_alert_pct = models.DecimalField('absenteeism alert above (% of working days)', max_digits=5, decimal_places=1, default=5)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


SEVERITY = (('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical'))
F_STATUS = (('open', 'Open'), ('acknowledged', 'Acknowledged'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed (not an issue)'))


class AuditRun(models.Model):
    payroll_run_id = models.IntegerField(db_index=True)
    previous_run_id = models.IntegerField(null=True, blank=True)
    run_label = models.CharField(max_length=120, blank=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    payslips = models.PositiveIntegerField(default=0)
    totals = models.JSONField(default=dict, blank=True)     # gross/net/deductions now and previous
    counts = models.JSONField(default=dict, blank=True)     # by severity / rule
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ['-at']


class Finding(models.Model):
    audit = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name='findings')
    rule = models.CharField(max_length=30, db_index=True)
    severity = models.CharField(max_length=10, choices=SEVERITY, default='warning')
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payslip_id = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    explanation = models.TextField()
    previous = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    current = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    change_pct = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=F_STATUS, default='open')
    note = models.CharField(max_length=300, blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    key = models.CharField(max_length=120, blank=True, db_index=True)   # rule:employee – carries acknowledgements to the next audit of the same run

    class Meta:
        ordering = ['-severity', 'rule', 'id']


class AILog(models.Model):
    """What the assistants did (audits, roster suggestions, CV reads, forecasts) – for the manager's trust and for tuning."""
    kind = models.CharField(max_length=20, db_index=True)
    ref = models.CharField(max_length=120, blank=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    summary = models.CharField(max_length=300, blank=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-at']
