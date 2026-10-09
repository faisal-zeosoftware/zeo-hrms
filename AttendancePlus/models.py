"""AttendancePlus (v1.12.0) – attendance rules, punches from every method, devices / kiosks,
daily attendance results and attendance corrections.

All tables are new; existing tables (calendars.Attendance, EmployeeOvertime, AttendanceCalendar,
EmployeeMachineMapping, emp_master ...) are referenced by integer ids so the existing apps keep
their (uncommitted) migrations untouched.
"""
import secrets
from datetime import time as dt_time

from django.db import models

METHODS = (
    ('web', 'Web'), ('mobile', 'Mobile app'), ('biometric', 'Biometric / face'), ('kiosk', 'Kiosk'),
    ('device', 'Attendance device'), ('qr', 'QR / barcode'), ('import', 'Bulk import'),
    ('correction', 'Attendance correction'), ('manual', 'Manual entry by HR'),
)
METHOD_KEYS = [m[0] for m in METHODS]
PUNCH_KINDS = (
    ('in', 'Clock in'), ('out', 'Clock out'), ('break_out', 'Break start'), ('break_in', 'Break end'),
    ('lunch_out', 'Lunch start'), ('lunch_in', 'Lunch end'), ('auto', 'Automatic (in or out)'),
)
DAY_STATUS = (
    ('present', 'Present'), ('absent', 'Absent'), ('half_day', 'Half day'), ('leave', 'Leave'),
    ('holiday', 'Holiday'), ('weekly_off', 'Weekly off'), ('missing_punch', 'Missing punch'),
    ('not_marked', 'Not marked yet'),
)
SCOPES = (('company', 'Whole company'), ('branch', 'Branch'), ('department', 'Department'),
          ('category', 'Category'), ('employee', 'Employee'))


def new_token():
    return secrets.token_urlsafe(32)


class AttendanceRule(models.Model):
    """One set of attendance rules. The most specific active rule wins
    (employee > category > department > branch > company); priority breaks ties (higher first)."""
    name = models.CharField(max_length=120)
    scope = models.CharField(max_length=12, choices=SCOPES, default='company')
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    department_id = models.IntegerField(null=True, blank=True)
    category_id = models.IntegerField(null=True, blank=True)
    employee_id = models.IntegerField(null=True, blank=True)
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # --- shift fallback (when no shift is planned for the day)
    default_start = models.TimeField(null=True, blank=True)
    default_end = models.TimeField(null=True, blank=True)
    # --- late / early
    grace_in_minutes = models.PositiveIntegerField(default=0)
    late_from = models.CharField(max_length=12, default='shift_start',
                                 choices=(('shift_start', 'Shift start'), ('grace_end', 'End of grace period')))
    late_tolerance_minutes = models.PositiveIntegerField(default=0)   # 0 = off; later than this = late_beyond_action
    late_beyond_action = models.CharField(max_length=10, default='half_day',
                                          choices=(('none', 'Only flag'), ('half_day', 'Half day'), ('absent', 'Absent')))
    late_policy_id = models.IntegerField(null=True, blank=True)       # calendars.LateComingPolicy
    grace_out_minutes = models.PositiveIntegerField(default=0)
    early_tolerance_minutes = models.PositiveIntegerField(default=0)  # 0 = off
    early_beyond_action = models.CharField(max_length=10, default='half_day',
                                           choices=(('none', 'Only flag'), ('half_day', 'Half day'), ('absent', 'Absent')))
    early_policy_id = models.IntegerField(null=True, blank=True)      # calendars.EarlyExitPolicy
    # --- hours
    min_hours_full_day = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # 0 = off
    min_hours_half_day = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    max_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)          # 0 = off
    max_hours_action = models.CharField(max_length=6, default='cap', choices=(('cap', 'Cap the hours'), ('flag', 'Only flag')))
    # --- overtime
    ot_enabled = models.BooleanField(default=True)
    ot_basis = models.CharField(max_length=10, default='shift', choices=(('shift', 'After shift hours'), ('daily', 'After daily threshold')))
    ot_after_minutes = models.PositiveIntegerField(default=0)        # minimum extra minutes before OT starts
    ot_daily_threshold_hours = models.DecimalField(max_digits=5, decimal_places=2, default=8)
    ot_weekly_threshold_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)   # 0 = off
    ot_monthly_threshold_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)  # 0 = off
    ot_min_minutes = models.PositiveIntegerField(default=0)
    ot_max_hours_per_day = models.DecimalField(max_digits=5, decimal_places=2, default=0)       # 0 = no cap
    ot_needs_approval = models.BooleanField(default=True)
    ot_rate_normal = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    ot_rate_weekend = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    ot_rate_holiday = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    ot_rate_night = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    # --- night / cross midnight
    night_start = models.TimeField(default=dt_time(22, 0))
    night_end = models.TimeField(default=dt_time(6, 0))
    night_min_minutes = models.PositiveIntegerField(default=120)
    cross_midnight = models.CharField(max_length=12, default='shift_start',
                                      choices=(('shift_start', 'Punch belongs to the day the shift starts'), ('calendar', 'Punch belongs to its calendar day')))
    day_change_hour = models.PositiveIntegerField(default=4)   # without a shift: punches before this hour belong to the previous day if it is open
    # --- breaks
    break_minutes = models.PositiveIntegerField(default=0)     # used when the shift has no break
    break_paid = models.BooleanField(default=False)
    auto_deduct_break = models.BooleanField(default=True)
    auto_deduct_after_hours = models.DecimalField(max_digits=4, decimal_places=2, default=5)
    # --- rounding
    rounding_minutes = models.PositiveIntegerField(default=0)
    rounding_mode = models.CharField(max_length=10, default='none',
                                     choices=(('none', 'No rounding'), ('nearest', 'Nearest'), ('strict', 'In up, out down'), ('lenient', 'In down, out up')))
    # --- absence
    mark_absent_if_no_punch = models.BooleanField(default=True)
    exempt_manual_source = models.BooleanField(default=False)  # employees whose attendance source is "manual" are present without punches
    exempt_category_ids = models.JSONField(default=list, blank=True)
    exempt_employee_ids = models.JSONField(default=list, blank=True)
    missing_punch_after_hours = models.DecimalField(max_digits=4, decimal_places=1, default=4)
    # --- methods & checks
    allowed_methods = models.JSONField(default=list, blank=True)   # [] = all
    require_gps = models.BooleanField(default=False)
    require_geofence = models.BooleanField(default=False)
    require_selfie = models.BooleanField(default=False)
    require_ip = models.BooleanField(default=False)
    min_minutes_between_punches = models.PositiveIntegerField(default=1)
    # --- corrections
    correction_window_days = models.PositiveIntegerField(default=30)
    correction_max_per_month = models.PositiveIntegerField(default=5)   # 0 = no limit
    correction_needs_hr = models.BooleanField(default=True)
    half_day_unpaid_fraction = models.DecimalField(max_digits=3, decimal_places=2, default=0.5)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'id']

    def __str__(self):
        return self.name


class IPRestriction(models.Model):
    """Allowed networks for web / mobile punches (CIDR list). Branch empty = every branch."""
    name = models.CharField(max_length=120)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    cidrs = models.TextField(help_text='One network per line, e.g. 10.0.0.0/24 or 94.200.1.5')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Device(models.Model):
    """Attendance device (ZKTeco ADMS push or any device that posts JSON with its API key)."""
    TYPES = (('zkteco', 'ZKTeco (ADMS push)'), ('other', 'Other (JSON push API)'))
    name = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=64, unique=True)
    device_type = models.CharField(max_length=10, choices=TYPES, default='zkteco')
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    api_key = models.CharField(max_length=64, default=new_token, unique=True)
    use_status_keys = models.BooleanField(default=False)  # trust the in / out / break key pressed on the device
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_ip = models.CharField(max_length=64, blank=True)
    last_stamp = models.CharField(max_length=32, blank=True)  # ADMS ATTLOGStamp
    punches_received = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name} ({self.serial_number})'


class Kiosk(models.Model):
    """A shared tablet / PC at a site where employees punch with their QR / badge, code + PIN or face."""
    name = models.CharField(max_length=120)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    token = models.CharField(max_length=64, default=new_token, unique=True)
    is_active = models.BooleanField(default=True)
    allow_qr = models.BooleanField(default=True)
    allow_pin = models.BooleanField(default=True)
    allow_face = models.BooleanField(default=False)
    show_site_qr = models.BooleanField(default=False)
    site_qr_seconds = models.PositiveIntegerField(default=30)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class EmployeeKey(models.Model):
    """Per-employee kiosk PIN (hashed) and QR version (raise it to cancel printed badges)."""
    employee_id = models.IntegerField(unique=True)
    pin_hash = models.CharField(max_length=128, blank=True)
    qr_version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class Punch(models.Model):
    employee_id = models.IntegerField(db_index=True)
    ts = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=10, choices=PUNCH_KINDS, default='auto')
    resolved_kind = models.CharField(max_length=10, blank=True)  # in / out / break_out ... after pairing
    source = models.CharField(max_length=12, choices=METHODS, default='web')
    work_date = models.DateField(db_index=True, null=True, blank=True)
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL)
    kiosk = models.ForeignKey(Kiosk, null=True, blank=True, on_delete=models.SET_NULL)
    device_ref = models.CharField(max_length=64, blank=True)    # app device id / serial
    ip = models.CharField(max_length=64, blank=True)
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    geofence_ok = models.BooleanField(null=True)
    photo = models.ImageField(upload_to='attendance_plus/punch/', null=True, blank=True)
    verified_by = models.CharField(max_length=12, blank=True)   # face / qr / pin / login / device
    is_void = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=255, blank=True)
    correction = models.ForeignKey('CorrectionRequest', null=True, blank=True, on_delete=models.SET_NULL, related_name='punches')
    note = models.CharField(max_length=255, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ts', 'id']
        constraints = [models.UniqueConstraint(fields=['employee_id', 'ts'], name='attplus_punch_once')]


class AttendanceDay(models.Model):
    """Daily attendance result for one employee (computed from punches + shift + rules)."""
    employee_id = models.IntegerField(db_index=True)
    date = models.DateField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    shift_id = models.IntegerField(null=True, blank=True)
    shift_name = models.CharField(max_length=80, blank=True)
    shift_start = models.DateTimeField(null=True, blank=True)
    shift_end = models.DateTimeField(null=True, blank=True)
    rule_id = models.IntegerField(null=True, blank=True)
    first_in = models.DateTimeField(null=True, blank=True)
    last_out = models.DateTimeField(null=True, blank=True)
    worked_minutes = models.IntegerField(default=0)
    break_minutes = models.IntegerField(default=0)
    late_minutes = models.IntegerField(default=0)
    early_minutes = models.IntegerField(default=0)
    ot_minutes = models.IntegerField(default=0)
    period_ot_minutes = models.IntegerField(default=0)   # weekly / monthly threshold OT placed on this day
    ot_type = models.CharField(max_length=10, blank=True)  # NORMAL / WEEKEND / HOLIDAY
    ot_rate = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    night_minutes = models.IntegerField(default=0)
    status = models.CharField(max_length=14, choices=DAY_STATUS, default='not_marked')
    is_late = models.BooleanField(default=False)
    is_early = models.BooleanField(default=False)
    is_night_shift = models.BooleanField(default=False)
    missing_punch = models.BooleanField(default=False)
    over_max_hours = models.BooleanField(default=False)
    penalty_waived = models.BooleanField(default=False)
    punch_count = models.IntegerField(default=0)
    sources = models.JSONField(default=list, blank=True)
    flags = models.JSONField(default=dict, blank=True)
    notified_missing_at = models.DateTimeField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', 'employee_id']
        constraints = [models.UniqueConstraint(fields=['employee_id', 'date'], name='attplus_day_once')]


class CorrectionRequest(models.Model):
    KINDS = (('missing_punch', 'Missing punch'), ('wrong_time', 'Wrong time'), ('forgot', 'Forgot to punch'),
             ('on_duty', 'On duty / outside work'), ('wfh', 'Work from home'))
    STATES = (('manager', 'Waiting for manager'), ('hr', 'Waiting for HR'), ('approved', 'Approved'),
              ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    date = models.DateField()
    kind = models.CharField(max_length=14, choices=KINDS, default='missing_punch')
    proposed_in = models.DateTimeField(null=True, blank=True)
    proposed_out = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    attachment = models.FileField(upload_to='attendance_plus/corrections/', null=True, blank=True)
    waive_penalty = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATES, default='manager', db_index=True)
    manager_user_id = models.IntegerField(null=True, blank=True)
    manager_at = models.DateTimeField(null=True, blank=True)
    manager_note = models.TextField(blank=True)
    hr_user_id = models.IntegerField(null=True, blank=True)
    hr_at = models.DateTimeField(null=True, blank=True)
    hr_note = models.TextField(blank=True)
    original = models.JSONField(default=dict, blank=True)   # punches / day before the change
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
