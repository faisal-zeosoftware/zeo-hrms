"""
v1.12.0 – Shift planner: shift rules, roster (draft → submitted → approved → published), employee availability,
open shifts, swap / change / cancellation requests and the notices sent about them.

The existing shift tables live in `calendars` (Shift, ShiftPattern, EmployeeShiftSchedule, ShiftOverride) and have no
committed migrations, so everything here is a side table keyed by plain integer ids (shift_id, employee_id,
branch_id ...). Attendance, leave and payroll read the result through ShiftPlanner.resolver (see INTEGRATION.md).
"""
from decimal import Decimal

from django.db import models

ZERO = Decimal('0')


class ShiftRule(models.Model):
    """Rules of one calendars.Shift (one row per shift)."""
    shift_id = models.IntegerField(unique=True)
    code = models.CharField(max_length=20, blank=True, default='', help_text='short code used on the roster and in uploads, e.g. GEN, N1')
    colour = models.CharField(max_length=9, blank=True, default='#5b4ff5')
    active = models.BooleanField(default=True)
    grace_in_minutes = models.PositiveIntegerField(default=0, help_text='late check-in allowed without being late')
    grace_out_minutes = models.PositiveIntegerField(default=0, help_text='early check-out allowed without being early')
    min_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='hours needed for a full day')
    max_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='hours counted at most in one day')
    half_day_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='hours needed for a half day')
    night_shift = models.BooleanField(default=False, help_text='night / cross-midnight shift (set from the times unless changed by hand)')
    night_auto = models.BooleanField(default=True, help_text='work out night_shift from the start and end times')
    ot_after_minutes = models.PositiveIntegerField(default=0, help_text='overtime starts this many minutes after the shift end')
    shift_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, help_text='paid per day worked on this shift')
    night_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, help_text='paid per night worked on this shift')
    branch_ids = models.JSONField(default=list, blank=True, help_text='branches that may use the shift; empty = all branches')
    notes = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['shift_id']

    def __str__(self):
        return f'Rules of shift {self.shift_id}'


class RosterPeriod(models.Model):
    STATUS = [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('published', 'Published'), ('rejected', 'Sent back')]
    name = models.CharField(max_length=120)
    branch_id = models.IntegerField(db_index=True)
    department_id = models.IntegerField(null=True, blank=True)
    date_from = models.DateField()
    date_to = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default='draft')
    approver_id = models.IntegerField(null=True, blank=True, help_text='user who approves this roster (optional)')
    default_shift_id = models.IntegerField(null=True, blank=True, help_text='shift for working days of employees without a shift schedule')
    min_rest_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('8'))
    max_week_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('48'))
    notes = models.TextField(blank=True, default='')
    decision_note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    submitted_by_id = models.IntegerField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by_id = models.IntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    published_by_id = models.IntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_from', '-id']
        permissions = [('approve_rosterperiod', 'Can approve shift rosters'), ('publish_rosterperiod', 'Can publish shift rosters')]

    def __str__(self):
        return self.name


class RosterEntry(models.Model):
    SOURCES = [('generated', 'Generated'), ('manual', 'Edited'), ('upload', 'Uploaded'), ('copy', 'Copied'), ('swap', 'Swap'),
               ('change', 'Shift change'), ('cancel', 'Cancellation'), ('open_shift', 'Open shift')]
    period = models.ForeignKey(RosterPeriod, on_delete=models.CASCADE, related_name='entries')
    employee_id = models.IntegerField(db_index=True)
    date = models.DateField(db_index=True)
    shift_id = models.IntegerField(null=True, blank=True)
    off = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True, default='')
    source = models.CharField(max_length=12, choices=SOURCES, default='manual')
    changed_after_publish = models.BooleanField(default=False)
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'employee_id']
        unique_together = ('period', 'employee_id', 'date')


class Availability(models.Model):
    KINDS = [('available', 'Available'), ('unavailable', 'Unavailable'), ('preferred', 'Preferred')]
    employee_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=12, choices=KINDS, default='unavailable')
    weekday = models.IntegerField(null=True, blank=True, help_text='0 = Monday … 6 = Sunday (repeats every week)')
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    shift_id = models.IntegerField(null=True, blank=True, help_text='preferred / available for this shift only')
    note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', 'weekday', 'date_from']


class OpenShift(models.Model):
    STATUS = [('open', 'Open'), ('filled', 'Filled'), ('cancelled', 'Cancelled')]
    date = models.DateField()
    shift_id = models.IntegerField()
    branch_id = models.IntegerField(db_index=True)
    department_id = models.IntegerField(null=True, blank=True)
    slots = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='open')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']


class OpenShiftClaim(models.Model):
    STATUS = [('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')]
    open_shift = models.ForeignKey(OpenShift, on_delete=models.CASCADE, related_name='claims')
    employee_id = models.IntegerField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    note = models.CharField(max_length=255, blank=True, default='')
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ShiftRequest(models.Model):
    KINDS = [('swap', 'Shift swap'), ('change', 'Shift change'), ('cancel', 'Shift cancellation')]
    STATUS = [('peer', 'Waiting for colleague'), ('pending', 'Waiting for approval'), ('approved', 'Approved'),
              ('rejected', 'Rejected'), ('declined', 'Declined by colleague'), ('withdrawn', 'Withdrawn')]
    kind = models.CharField(max_length=8, choices=KINDS)
    employee_id = models.IntegerField(db_index=True)
    date = models.DateField()
    from_shift_id = models.IntegerField(null=True, blank=True, help_text='shift on the day when the request was made (empty = day off)')
    to_shift_id = models.IntegerField(null=True, blank=True, help_text='shift change: the wanted shift')
    swap_employee_id = models.IntegerField(null=True, blank=True)
    swap_date = models.DateField(null=True, blank=True, help_text="swap: the colleague's day (default the same day)")
    swap_shift_id = models.IntegerField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    peer_decided_at = models.DateTimeField(null=True, blank=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ShiftNotice(models.Model):
    """Every shift notice sent (in-app inbox + e-mail), so the employee can read them in My schedule."""
    user_id = models.IntegerField(null=True, blank=True, db_index=True)
    employee_id = models.IntegerField(null=True, blank=True, db_index=True)
    kind = models.CharField(max_length=20)
    title = models.CharField(max_length=120)
    message = models.TextField()
    ref = models.CharField(max_length=60, blank=True, default='', help_text='e.g. reminder:2026-10-09 – stops sending the same notice twice')
    emailed = models.BooleanField(default=False)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
