"""ZEO Developer 1.74.0 – tables (all new; the custom modules themselves stay in ZeoFlow.CustomModule / CustomRecord)."""
from django.db import models

VERSION = '1.70.3'   # the ZEO version that brought ZEO Developer (built as 1.74.0 on 1.67.26)
API_VERSION = 'v1'

TRIGGERS = (
    ('manual', 'Run by hand (Developer › Scripts)'),
    ('create', 'When a record is created'),
    ('update', 'When a record is changed'),
    ('move', 'When a record moves to another state'),
    ('button', 'A button on the record'),
    ('daily', 'Every day'),
    ('monthly', 'Every month'),
    ('app_create', 'When a record of an app is created'),   # all apps: CRM leads, Projects tickets, HSE incidents …
    ('app_update', 'When a record of an app is changed'),
)


class DeveloperSettings(models.Model):
    """One row per company: Developer mode and the limits of scripts."""
    developer_mode = models.BooleanField(default=False)
    monthly_run_limit = models.PositiveIntegerField(default=5000, help_text='script runs a month (all scripts of the company)')
    max_failures = models.PositiveIntegerField(default=5, help_text='a script is switched off after this many failures in a row')
    time_limit_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=5, help_text='per run, at most 30')
    max_steps = models.PositiveIntegerField(default=2_000_000)
    max_items = models.PositiveIntegerField(default=200_000, help_text='largest list / text / table one script may make')
    max_records_read = models.PositiveIntegerField(default=5000, help_text='records a run may read')
    max_records_changed = models.PositiveIntegerField(default=500, help_text='records a run may create or change')
    allowed_sites = models.JSONField(default=list, blank=True, help_text='host names scripts may call with http.get / http.post')
    run_as_user_id = models.IntegerField(null=True, blank=True, help_text='scheduled scripts run with the rights of this administrator')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        obj = cls.objects.order_by('pk').first()
        return obj or cls.objects.create()


class RateTable(models.Model):
    """A table of rates a formula looks up: LOOKUP("fuel_norm", vehicle_type, "litres_100km")."""
    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    columns = models.JSONField(default=list, blank=True, help_text='["type", "litres_100km"] – the first column is the key')
    rows = models.JSONField(default=list, blank=True, help_text='[{"type": "Pick-up", "litres_100km": "12"}]')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class Script(models.Model):
    key = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default='')
    module_key = models.CharField(max_length=40, blank=True, default='', db_index=True, help_text='custom module the script belongs to (empty = company)')
    model_key = models.CharField(max_length=80, blank=True, default='', db_index=True, help_text='app_create / app_update: the record type, e.g. ZeoCRM.lead')
    trigger = models.CharField(max_length=12, choices=TRIGGERS, default='manual')
    move_key = models.CharField(max_length=40, blank=True, default='', help_text='trigger "move": only this move (empty = every move)')
    button_label = models.CharField(max_length=40, blank=True, default='')
    day_of_month = models.PositiveSmallIntegerField(default=1, help_text='monthly scripts: the day they run')
    hour = models.PositiveSmallIntegerField(default=2, help_text='daily / monthly scripts: the hour (company time) they run')
    code = models.TextField(blank=True, default='')
    api_version = models.CharField(max_length=4, default=API_VERSION)
    version = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=False, help_text='scripts start switched off – test, then switch on')
    stopped = models.BooleanField(default=False, help_text='switched off by ZEO after repeated failures')
    failures = models.PositiveIntegerField(default=0, help_text='failures in a row')
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_period = models.CharField(max_length=10, blank=True, default='', help_text='daily / monthly: the last day / month it ran')
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['module_key', 'name']

    def __str__(self):
        return self.name


class ScriptVersion(models.Model):
    script = models.ForeignKey(Script, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    code = models.TextField(blank=True, default='')
    note = models.CharField(max_length=200, blank=True, default='')
    saved_by_id = models.IntegerField(null=True, blank=True)
    saved_by_name = models.CharField(max_length=120, blank=True, default='')
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        unique_together = [('script', 'version')]


class ScriptRun(models.Model):
    """Every run (D8): who, trigger, how long, records read / changed, result, error."""
    script = models.ForeignKey(Script, null=True, blank=True, on_delete=models.SET_NULL, related_name='runs')
    script_name = models.CharField(max_length=120, blank=True, default='')
    trigger = models.CharField(max_length=12, default='manual')
    record_ref = models.CharField(max_length=60, blank=True, default='', help_text='module key / record number the run was for')
    user_id = models.IntegerField(null=True, blank=True)
    user_name = models.CharField(max_length=120, blank=True, default='')
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default='ok', db_index=True)   # ok / error / limit / refused / skipped
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    duration_ms = models.PositiveIntegerField(default=0)
    steps = models.PositiveIntegerField(default=0)
    records_read = models.PositiveIntegerField(default=0)
    records_changed = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default='')
    error_line = models.PositiveIntegerField(null=True, blank=True)
    output = models.TextField(blank=True, default='')
    changes = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-started_at', '-id']


class SafeAction(models.Model):
    """D2 – everything a script or a module does in the core apps goes through here (history), and the actions that touch money
    wait for an administrator's approval."""
    STATES = (('pending', 'Waiting for approval'), ('done', 'Done'), ('rejected', 'Rejected'), ('failed', 'Failed'), ('dry', 'Test only'))
    action = models.CharField(max_length=40, db_index=True)
    label = models.CharField(max_length=255, blank=True, default='')
    params = models.JSONField(default=dict, blank=True)
    needs_approval = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATES, default='done', db_index=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default='')
    script = models.ForeignKey(Script, null=True, blank=True, on_delete=models.SET_NULL, related_name='actions')
    run = models.ForeignKey(ScriptRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='actions')
    requested_by_id = models.IntegerField(null=True, blank=True)
    requested_by_name = models.CharField(max_length=120, blank=True, default='')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_by_name = models.CharField(max_length=120, blank=True, default='')
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['-requested_at', '-id']


class ModuleReport(models.Model):
    """A report of a custom module, listed in the report centre (key dev-<id>)."""
    KINDS = (('list', 'List of records'), ('summary', 'Totals by a field'))
    module_key = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default='')
    section = models.CharField(max_length=60, blank=True, default='Custom modules')
    kind = models.CharField(max_length=10, choices=KINDS, default='list')
    columns = models.JSONField(default=list, blank=True, help_text='field keys shown (list) / summed (summary)')
    group_by = models.JSONField(default=list, blank=True, help_text='summary: field keys (and "month:<date field>")')
    date_field = models.CharField(max_length=40, blank=True, default='', help_text='the period of the report filters this date field')
    states = models.JSONField(default=list, blank=True, help_text='only records in these states (empty = all)')
    who = models.CharField(max_length=10, default='hr', choices=(('admin', 'Administrators'), ('hr', 'HR and administrators'), ('all', 'Everyone who sees the records')))
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['module_key', 'name']


class PrintLayout(models.Model):
    """A printed document of a custom module: HTML with {{ field }} places (values are escaped – no template code runs)."""
    module_key = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    html = models.TextField(blank=True, default='')
    paper = models.CharField(max_length=10, default='A4')
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['module_key', 'name']


class ScheduledCalc(models.Model):
    """No-code scheduled calculation (D1): every day / month, set a field of the records of a module to a formula
    – e.g. monthly straight-line depreciation: book_value = MAX(cost - cost / life_months * MONTHS(purchase_date, TODAY()), 0)."""
    module_key = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    field_key = models.CharField(max_length=40)
    formula = models.TextField()
    frequency = models.CharField(max_length=10, default='monthly', choices=(('daily', 'Every day'), ('monthly', 'Every month')))
    states = models.JSONField(default=list, blank=True, help_text='only records in these states (empty = all)')
    active = models.BooleanField(default=True)
    last_period = models.CharField(max_length=10, blank=True, default='')
    last_result = models.CharField(max_length=300, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['module_key', 'name']


class InstalledPackage(models.Model):
    """History of packages / demo apps installed in the company (D7, D9)."""
    name = models.CharField(max_length=120)
    source = models.CharField(max_length=20, default='import')   # template / import
    summary = models.JSONField(default=dict, blank=True)
    installed_by_id = models.IntegerField(null=True, blank=True)
    installed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-installed_at']
