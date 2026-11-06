"""v1.67.3 (apps) – the companies a scheduled job leaves out.

`off_schemas()` of ZeoCloud.lifecycle (v1.67.1: 'public' and every company that is not active) plus the companies where the
app the job belongs to is not active – a company without HR gets no HR daily job (leave accrual and reset, document expiry,
attendance, probation, shift reminders, policy reminders, complaint escalation, certificates …), one without Payroll no payroll
job. Used by the HR / Payroll tasks.py files as `_zeo_off()`.
"""
import logging

log = logging.getLogger(__name__)


def off_for(*apps):
    from django_tenants.utils import get_tenant_model, schema_context
    from ZeoCloud.lifecycle import off_schemas
    off = list(off_schemas())
    for s in get_tenant_model().objects.exclude(schema_name__in=off).values_list('schema_name', flat=True):
        try:
            with schema_context(s):
                from .services import is_active
                if not any(is_active(a) for a in apps):
                    off.append(s)
        except Exception:   # a schema that cannot be read is left to the job (as before)
            log.exception('app state of %s', s)
    return off


def hr_off():
    return off_for('hrms')


def payroll_off():
    return off_for('payroll')


def hr_assets_off():
    """The HR asset register reminders run where HR or Asset Management is active (one shared register)."""
    return off_for('hrms', 'assets')
