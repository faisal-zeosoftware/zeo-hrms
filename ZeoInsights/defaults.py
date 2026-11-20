"""v1.73.6 – the dashboards every company gets: Company overview and one per active app (HR, Payroll, CRM, Property, Maintenance,
Asset management, HSE, Projects & support).

They are made once per company (DashboardDefault remembers it), shared with everybody, owned by the first administrator who opens
Reports › Dashboards (or by the user given to `manage.py zeoinsights_defaults`). A dashboard the company deleted is not made again.
Cards whose report is not available (app not active, report not installed) are left out; a dashboard with fewer than 3 cards is
not made. Each card still runs as the person looking at it – rights and branches apply.
"""
from django.db import transaction

from DashboardManagement.reports import REPORTS


def N(r, t, c, v='count', p='report', icon=''):
    return dict(report=r, view='number', title=t, color=c, value=v, period=p, icon=icon, size='s')


def G(view, r, g, t, v='count', p='report', size='w', c=''):
    return dict(report=r, view=view, group=g, title=t, value=v, period=p, size=size, color=c)


def T(r, t, size='f', p='report'):
    return dict(report=r, view='table', title=t, size=size, limit=8, period=p)


# key, app (None = always), name, theme, cards
DEFAULTS = [
    ('company', None, 'Company overview', 'aurora', [
        N('employees', 'Employees', 'violet', icon='groups'),
        N('payroll-register', 'Net payroll – last month', 'teal', 'sum:net', 'last_month'),
        N('crm-opportunity', 'Sales pipeline (AED)', 'blue', 'sum:value', 'this_year'),
        N('prop-lease', 'Annual rent under lease (AED)', 'amber', 'sum:annual_rent'),
        G('donut', 'employees', 'department', 'Employees by department'),
        G('column', 'payroll-register', 'period', 'Net payroll by month (AED)', 'sum:net', 'last_12'),
        G('area', 'prop-lease', 'start', 'New lease rent by start month (AED)', 'sum:annual_rent', 'last_12', c='amber'),
        G('progress', 'crm-lead', 'source', 'Leads by source'),
        G('donut', 'prop-unit', 'status', 'Units by status'),
        G('bar', 'prop-unit', 'building', 'Unit rent by building (AED)', 'sum:rent', c='violet'),
        T('crm-followups', 'Follow-ups by agent')]),
    ('hr', 'hrms', 'HR overview', 'aurora', [
        N('employees', 'Employees', 'violet', icon='groups'),
        N('leave', 'Leave requests this year', 'rose', p='this_year', icon='event_busy'),
        N('documents', 'Employee documents', 'blue', icon='description'),
        N('gratuity', 'Gratuity accrued (AED)', 'amber', 'sum:amount', icon='savings'),
        G('donut', 'employees', 'nationality', 'Employees by nationality'),
        G('progress', 'employees', 'department', 'Employees by department'),
        G('column', 'leave', 'type', 'Leave days approved by type', 'sum:approved', 'this_year'),
        G('donut', 'documents', 'type', 'Documents by type'),
        T('documents', 'Documents and expiry')]),
    ('payroll', 'payroll', 'Payroll overview', 'emerald', [
        N('payroll-register', 'Net payroll – last month', 'teal', 'sum:net', 'last_month'),
        N('payroll-register', 'Gross payroll – last month', 'blue', 'sum:gross', 'last_month'),
        N('payroll-register', 'Deductions – last month', 'rose', 'sum:deductions', 'last_month', icon='remove_circle'),
        N('gratuity', 'Gratuity liability (AED)', 'amber', 'sum:amount', icon='savings'),
        G('column', 'payroll-register', 'period', 'Net payroll by month (AED)', 'sum:net', 'last_12'),
        G('donut', 'payroll-register', 'department', 'Net payroll by department – last month', 'sum:net', 'last_month'),
        G('bar', 'payroll-register', 'branch', 'Net payroll by branch – last month (AED)', 'sum:net', 'last_month', c='teal'),
        G('progress', 'payroll-register', 'category', 'Employees paid by category – last month', 'count', 'last_month'),
        T('payroll-department', 'Payroll by department', p='last_12')]),
    ('crm', 'crm', 'CRM overview', 'ocean', [
        N('crm-lead', 'Leads', 'violet', icon='person_search'),
        N('crm-opportunity', 'Pipeline value (AED)', 'blue', 'sum:value', 'this_year'),
        N('crm-quotation', 'Quotations (AED)', 'teal', 'sum:total', icon='request_quote'),
        N('crm-opportunity', 'Open opportunities', 'amber', icon='handshake'),
        G('donut', 'crm-lead', 'rating', 'Leads by rating'),
        G('column', 'crm-opportunity', 'stage', 'Pipeline value by stage (AED)', 'sum:value'),
        G('progress', 'crm-lead', 'source', 'Leads by source'),
        G('bar', 'crm-opportunity', 'owner', 'Pipeline by salesperson (AED)', 'sum:value', c='blue'),
        T('crm-followups', 'Follow-ups by agent')]),
    ('property', 'property', 'Property overview', 'sunset', [
        N('prop-unit', 'Units', 'violet', icon='apartment'),
        N('prop-lease', 'Active leases', 'teal', icon='key'),
        N('prop-lease', 'Annual rent under lease (AED)', 'amber', 'sum:annual_rent'),
        N('prop-unit', 'Asking rent – all units (AED)', 'blue', 'sum:rent', icon='sell'),
        G('donut', 'prop-unit', 'status', 'Units by status'),
        G('bar', 'prop-unit', 'building', 'Unit rent by building (AED)', 'sum:rent', c='violet'),
        G('area', 'prop-lease', 'start', 'New lease rent by start month (AED)', 'sum:annual_rent', 'last_12', c='amber'),
        G('progress', 'prop-unit', 'unit_type', 'Units by type'),
        T('prop-lease', 'Leases')]),
    ('maintenance', 'maintenance', 'Maintenance overview', 'ocean', [
        N('mnt-work-order', 'Work orders', 'violet', icon='build'),
        N('mnt-asset', 'Maintained assets', 'teal', icon='precision_manufacturing'),
        N('mnt-plan', 'Preventive plans', 'blue', icon='event_repeat'),
        N('mnt-work-order', 'Work orders this month', 'amber', p='this_month', icon='construction'),
        G('donut', 'mnt-work-order', 'priority', 'Work orders by priority'),
        G('progress', 'mnt-work-order', 'category', 'Work orders by category'),
        G('column', 'mnt-work-order', 'status', 'Work orders by status'),
        G('donut', 'mnt-asset', 'category', 'Assets by category'),
        T('mnt-plan', 'Preventive plans')]),
    ('assets', 'assets', 'Asset management overview', 'emerald', [
        N('asset-register', 'Assets', 'violet', icon='inventory_2'),
        N('asset-register', 'Purchase cost (AED)', 'blue', 'sum:cost', icon='shopping_cart'),
        N('asset-register', 'Net book value (AED)', 'teal', 'sum:book_value', icon='account_balance'),
        N('ast-ticket', 'Helpdesk tickets', 'rose', icon='support_agent'),
        G('donut', 'asset-register', 'type', 'Assets by type'),
        G('bar', 'asset-register', 'location', 'Book value by location (AED)', 'sum:book_value', c='teal'),
        G('progress', 'asset-register', 'condition', 'Assets by condition'),
        G('column', 'asset-register', 'status', 'Assets by status'),
        T('asset-register', 'Asset register')]),
    ('hse', 'hse', 'HSE overview', 'sunset', [
        N('hse-incidents', 'Incidents', 'rose', icon='health_and_safety'),
        N('hse-incidents', 'Incidents this month', 'amber', p='this_month', icon='warning'),
        N('hse-r-kpis', 'HSE KPIs tracked', 'teal', icon='monitoring'),
        N('hse-r-competency-types', 'Competency types', 'blue', icon='school'),
        G('donut', 'hse-incidents', 'category', 'Incidents by category'),
        G('progress', 'hse-incidents', 'site', 'Incidents by site'),
        G('column', 'hse-incidents', 'status', 'Incidents by status'),
        G('donut', 'hse-incidents', 'treatment', 'Incidents by treatment'),
        T('hse-incidents', 'Incident register')]),
    ('projects', 'projects', 'Projects & support overview', 'midnight', [
        N('pj-open-tickets', 'Open tickets', 'violet', 'sum:open', icon='support_agent'),
        N('pj-open-tickets', 'SLA breached', 'rose', 'sum:breached', icon='timer_off'),
        N('pj-received-resolved', 'Tickets received (12 months)', 'blue', 'sum:received', 'last_12'),
        N('pj-open-tickets', 'High priority open', 'teal', 'sum:high', icon='priority_high'),
        T('pj-open-tickets', 'Open tickets – summary', 'w'),
        T('pj-response-times', 'Response and resolution times', 'w')]),
]
APP_OF_KEY = {k: app for k, app, *_ in DEFAULTS}


def _active():
    try:
        from ZeoApps import services as S
        return set(S.active_keys())
    except Exception:   # noqa: BLE001
        return {'hrms', 'payroll'}


def plan():
    """[(key, name, theme, cards)] the company should have – only reports that exist, only active apps."""
    act = _active()
    out = []
    for key, app, name, theme, cards in DEFAULTS:
        if app and app not in act:
            continue
        keep = []
        for c in cards:
            if REPORTS.get(c['report']) is None:
                continue
            try:
                from ZeoApps.reports import app_of
                a = app_of(c['report'])
            except Exception:   # noqa: BLE001
                a = None
            if a and a not in act:
                continue
            keep.append(c)
        if len(keep) >= 3:
            out.append((key, name, theme, keep))
    return out


def ensure(owner):
    """Make the default dashboards this company does not have yet (and never had). Returns the names made."""
    from .models import CustomDashboard, DashboardDefault
    from .views import clean_card
    done = set(DashboardDefault.objects.values_list('key', flat=True))
    made = []
    for key, name, theme, cards in plan():
        old = [d for d in CustomDashboard.objects.filter(name=name).order_by('id') if not (d.options or {}).get('default')]
        if key not in done and old:
            # v1.73.19 (item 9): the dashboards 1.73.5 made had no record – 1.73.6 made them all a second time ("HR overview" twice); the
            # existing one is taken as the default instead
            DashboardDefault.objects.create(key=key, dashboard_id=old[0].pk)
            CustomDashboard.objects.filter(pk=old[0].pk).update(options={**(old[0].options or {}), 'default': key})
            done.add(key)
            continue
        if key in done and old:
            # the second copy made by 1.73.6: the old untouched copy (made before it, never edited after) is removed
            cur = CustomDashboard.objects.filter(pk=DashboardDefault.objects.filter(key=key).values('dashboard_id')[:1]).first()
            for d in old:
                if cur is not None and d.pk != cur.pk and d.created_at < cur.created_at and d.updated_at and \
                        (d.updated_at - d.created_at).total_seconds() < 5:
                    d.delete()
        if key in done:
            continue
        with transaction.atomic():
            db = CustomDashboard.objects.create(name=name, owner=owner, shared='all', groups=[],
                                                cards=[clean_card(dict(c)) for c in cards][:40],
                                                options={'theme': theme, 'filter': {'preset': '', 'from': '', 'to': ''}, 'default': key})
            DashboardDefault.objects.create(key=key, dashboard_id=db.pk)
        made.append(name)
    return made


def for_app(app):
    """The default dashboard of an app (crm, property, …, 'hrms' → HR overview), if the company still has it."""
    from .models import CustomDashboard, DashboardDefault
    key = {'hrms': 'hr', 'zeo-hr': 'hr'}.get(app, app)
    d = DashboardDefault.objects.filter(key=key).first()
    return CustomDashboard.objects.filter(pk=d.dashboard_id).first() if d and d.dashboard_id else None
