"""v1.67.6 (setup) item 8 – the industry type is locked once an app works with it.

Before: the industry of an app (Settings › Apps, the app's own "Industry pack" with "make it the app's industry", activating
again with another industry) and the company master's industry type could be changed at any time, also after the app had
records made under the industry's setup (pack categories, permit types, pipelines, checklists …).

Now: when an app is active with an industry and has transactions, its industry cannot be changed (409, with the reason);
adding another pack's content without making it the app's industry stays possible. The company master's industry type cannot
be changed while an active app that follows it (industry taken from the company master) has transactions.
"""
from django.apps import apps as djapps

# app → the transaction models whose rows mean "the app is in use"
TRANSACTIONS = {
    'hrms': ['calendars.employee_leave_request', 'EmpManagement.GeneralRequest', 'calendars.Attendance'],
    'payroll': ['PayrollManagement.PayrollRun', 'PayrollManagement.Payslip'],
    'projects': ['ZeoProjects.Ticket', 'ZeoProjects.AMCContract', 'ProjectManagement.Project'],
    'crm': ['ZeoCRM.Lead', 'ZeoCRM.Opportunity', 'ZeoCRM.Quotation', 'ZeoCRM.SalesOrder'],
    'property': ['ZeoProperty.Lease', 'ZeoProperty.Bill', 'ZeoProperty.Receipt', 'ZeoProperty.Sale'],
    'maintenance': ['ZeoMaintenance.WorkOrder'],
    'assets': ['ZeoAssets.WorkOrder', 'ZeoAssets.Ticket', 'ZeoAssets.Audit'],
    'people': ['ZeoPeople.SignRequest', 'ZeoPeople.BenefitClaim', 'ZeoPeople.Survey'],
    'hse': ['ZeoHSE.Incident', 'ZeoHSE.Permit', 'ZeoHSE.Inspection', 'ZeoHSE.Observation'],
}


def transactions(app):
    """(model label, count) of the first transaction model with rows, or None."""
    for lab in TRANSACTIONS.get(app, []):
        try:
            n = djapps.get_model(lab).objects.count()
        except (LookupError, Exception):   # noqa: BLE001
            continue
        if n:
            return lab, n
    return None


def _title(app):
    from .services import BY_KEY, tr_
    return tr_(BY_KEY[app][2]) if app in BY_KEY else app


def current_industry(app):
    from . import services as S
    try:
        if app in S.CORE:
            r = S.core_state(app)
            return (r.industry or '') if r is not None else ''
        if app == 'hse':
            from ZeoHSE.models import HSESettings
            return HSESettings.get().industry or ''
        return S.kit(app).settings().industry or ''
    except Exception:   # noqa: BLE001
        return ''


def refusal(app, new_industry):
    """The message when `app` may not change its industry to `new_industry`, else ''."""
    from .services import is_active, tr_
    if not new_industry or not is_active(app):
        return ''
    cur = current_industry(app)
    if not cur or cur == new_industry:
        return ''
    t = transactions(app)
    if not t:
        return ''
    return tr_('The industry of {0} cannot be changed: {0} already has records made with its current industry ({1} {2}). '
               'You can still add another industry pack without making it the industry of {0}.', _title(app), t[1], _model_name(t[0]))


def company_refusal(new_code):
    """The message when the company master's industry type may not change to `new_code`, else ''."""
    from . import services as S
    code, _ = S.company_industry()
    if (new_code or '') == (code or ''):
        return ''
    busy = []
    for app in S.KEYS:
        try:
            if not S.is_active(app):
                continue
            src = _source(app)
            if src != 'company':
                continue
            t = transactions(app)
            if t:
                busy.append(_title(app))
        except Exception:   # noqa: BLE001
            continue
    if not busy:
        return ''
    return S.tr_('The industry type cannot be changed: {0} work with it and already have records. '
                 'Change it only in a company whose apps have no transactions yet.', ', '.join(busy))


def _source(app):
    from . import services as S
    try:
        if app in S.CORE:
            r = S.core_state(app)
            return (r.industry_source or 'company') if r is not None else 'company'
        if app == 'hse':
            from ZeoHSE.models import HSESettings
            return HSESettings.get().industry_source or 'company'
        return S.kit(app).settings().industry_source or 'company'
    except Exception:   # noqa: BLE001
        return 'company'


def _model_name(label):
    try:
        m = djapps.get_model(label)
        return str(m._meta.verbose_name_plural)
    except LookupError:
        return label
