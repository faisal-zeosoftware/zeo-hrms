"""Who may do what in Projects.

Rights are normal ZEO group permissions (v1.67.19: "add" creates, "change" edits – checked separately) (Settings → Permission groups). `zeoprojects_setup --roles` creates the
role groups of the specification with these rights:

  Support Manager      all tickets, assign / escalate / close, approve work logs, technical approval, SLA set-up
  Support Engineer     tickets of own teams and own tickets, work logs, raise development requests
  AMC / Account Manager customers, contacts, contracts, renewals
  Project Manager      projects (with ProjectManagement rights), milestones, change requests, risks
  Developer/Consultant assigned development tasks (ProjectManagement task rights) and own tickets
  Finance Team         billing of contracts and milestones, profitability, commercial approval
  Management           every dashboard and report (read only)

A company administrator can do everything. Customer portal users have no group rights at all; the portal API
checks their contact instead.
"""
from django.db import connection
from rest_framework.permissions import BasePermission

R = {
    'ticket_all': ('view_ticket', 'assign_ticket'),
    'ticket_manage': ('assign_ticket',),
    'ticket_work': ('work_ticket', 'change_ticket', 'assign_ticket'),
    'ticket_add': ('add_ticket', 'work_ticket', 'assign_ticket'),
    'ticket_delete': ('delete_ticket',),   # v1.73.0: Delete on the Tickets list (only tickets with no work on them yet)
    'worklog_approve': ('approve_worklog', 'assign_ticket'),
    'customer_view': ('view_customer', 'view_amccontract', 'view_ticket', 'work_ticket', 'assign_ticket', 'view_projectprofile'),
    'customer_edit': ('add_customer', 'change_customer'),
    # v1.67.19: "add" and "change" are separate rights – an add-only group creates customers / sites / equipment / contracts /
    # projects but does not change them (the *_edit keys stay for "may do either", e.g. which menu to show)
    'customer_add': ('add_customer',),
    'customer_change': ('change_customer',),
    'customer_delete': ('delete_customer',),   # v1.73.0: Delete on the Customers list (used customers are only made inactive)
    'contact_portal': ('change_contact',),
    'amc_view': ('view_amccontract',),
    'amc_edit': ('add_amccontract', 'change_amccontract'),
    'amc_add': ('add_amccontract',),
    'amc_change': ('change_amccontract',),
    'amc_delete': ('delete_amccontract',),   # v1.73.0: Delete on the AMC contracts list (only contracts not used yet)
    'amc_renew': ('renew_amccontract', 'change_amccontract'),
    'billing_view': ('view_amcbilling', 'view_projectfinance'),
    'billing_edit': ('change_amcbilling',),
    'dev_view': ('view_devrequest', 'approve_tech_devrequest', 'approve_commercial_devrequest', 'assign_ticket'),
    'dev_tech': ('approve_tech_devrequest',),
    'dev_commercial': ('approve_commercial_devrequest',),
    'project_view': ('view_project', 'view_projectprofile'),
    'project_edit': ('change_project', 'change_projectprofile'),
    'project_add': ('add_project',),
    'project_delete': ('delete_project',),   # v1.73.0: Delete on the Projects list (only projects not used yet)
    'project_all': ('change_all_projects',),   # v1.67.24 (pending 288): change every project, not only the managed ones
    'cr_approve': ('approve_changerequest',),
    'finance': ('view_projectfinance', 'view_amcbilling'),
    'dashboard': ('view_ticket', 'assign_ticket', 'view_amccontract', 'view_projectprofile', 'view_projectfinance'),
    'setup': ('change_projectssettings', 'change_slapolicy', 'change_supportteam'),
    # v1.67.17 (checklist 47): Service KPIs and service reports – the support manager and Management ("every dashboard and
    # report"); approve_changerequest is the right only the Management role holds besides administrators
    'kpi_view': ('assign_ticket', 'approve_changerequest'),
}


def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def ctx(request):
    try:
        return _ctx(request)
    except Exception:
        return None


def can(request, key):
    c = ctx(request)
    if c is None:
        return False
    return bool(c.admin or any(x in c.codes for x in R[key]))


def is_admin(request):
    c = ctx(request)
    return bool(c and c.admin)


def my_emp(request):
    c = ctx(request)
    return c.emp if c else None


def rights(request):
    """All right keys of the user (sent to the app to show / hide buttons)."""
    out = {k: can(request, k) for k in R}
    out['admin'] = is_admin(request)
    out['employee_id'] = getattr(my_emp(request), 'pk', None)
    return out


def schema_of(request):
    if connection.schema_name != 'public':
        return connection.schema_name
    return request.GET.get('schema')


class CompanyMember(BasePermission):
    """Logged in and member of the company in ?schema=. Customer portal users are kept out of the staff API."""
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = schema_of(request)
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        if not (u.is_superuser or u.tenants.filter(schema_name=sch).exists()):
            return False
        if portal_contact(u) is not None:
            self.message = 'Customer portal users can only use the portal.'
            return False
        return True


def portal_contact(user):
    """The customer contact behind a portal login (None for staff)."""
    from .models import Contact
    if not user or not user.is_authenticated:
        return None
    return Contact.objects.filter(portal_user=user, is_active=True, customer__is_active=True).select_related('customer').first()


class PortalMember(BasePermission):
    message = 'This page is for customer portal users.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = schema_of(request)
        if not sch or sch == 'public' or not u.tenants.filter(schema_name=sch).exists():
            self.message = 'You do not have access to this portal.'
            return False
        from .models import ProjectsSettings
        if not ProjectsSettings.get().portal_enabled:
            self.message = 'The customer portal is switched off.'
            return False
        c = portal_contact(u)
        if c is None:
            return False
        request.zp_contact = c
        return True


class Denied(Exception):
    def __init__(self, message='You do not have permission to do this.'):
        self.message = message


def need(request, key, message=None):
    if not can(request, key):
        raise Denied(message or 'You do not have the right to do this. Ask your administrator for the Projects rights.')


# ------------------------------------------------------------------ row visibility
def ticket_scope(request, qs=None):
    """Tickets the user may see: all (support manager / management) or own teams, assigned, requested, raised."""
    from django.db.models import Q
    from .models import Ticket
    qs = qs if qs is not None else Ticket.objects.all()
    qs = in_branches(qs, request)   # v1.22.0
    if can(request, 'ticket_all'):
        return qs
    e = my_emp(request)
    q = Q(created_by=request.user)
    if e is not None:
        q |= Q(assigned_to=e) | Q(requester_employee=e) | Q(team__members=e) | Q(team__lead=e)
    return qs.filter(pk__in=qs.filter(q).values('pk'))


def can_work_ticket(request, t):
    if can(request, 'ticket_manage'):
        return True
    e = my_emp(request)
    if e is None:
        return False
    if t.assigned_to_id == e.pk:
        return True
    if t.team_id and can(request, 'ticket_work') and (t.team.lead_id == e.pk or t.team.members.filter(pk=e.pk).exists()):
        return True
    return False


def visible_project_ids(request):
    """ZEO projects the user may see (same rules as ProjectControl)."""
    try:
        from ProjectControl.services import visible_projects
        return list(visible_projects(request).values_list('pk', flat=True))
    except Exception:
        from ProjectManagement.models import Project
        return list(Project.objects.values_list('pk', flat=True)) if is_admin(request) else []


def manages_project(request, project_id):
    """v1.67.24 (pending 288, decision 14 Nov): "Can change project" lets a user change the projects they manage; every project
    needs "Can change every project" (or a company administrator). Before, "change project" changed every project of the company."""
    if is_admin(request) or can(request, 'project_all'):
        return True
    e = my_emp(request)
    if e is None:
        return False
    from ProjectManagement.models import Project
    return Project.objects.filter(pk=project_id, managers=e).exists()


# ------------------------------------------------------------------ v1.22.0 branches (Settings › Branch permissions)
# how each record reaches a branch: the customer's branch, else the employee's (internal helpdesk tickets, work logs)
BRANCH_PATHS = {
    'Customer': ('branch',), 'Contact': ('customer__branch',), 'AMCContract': ('customer__branch',), 'AMCBilling': ('contract__customer__branch',),
    'Ticket': ('customer__branch', 'requester_employee__emp_branch_id'), 'WorkLog': ('ticket__customer__branch', 'employee__emp_branch_id'),
    'DevRequest': ('customer__branch', 'ticket__customer__branch'), 'ProjectProfile': ('customer__branch',),
    'emp_master': ('emp_branch_id',),
}


def branch_ids(request):
    from zeo.appkit.engine import branch_ids as b
    return b(request)


def in_branches(qs, request):
    """Rows of the user's branches; rows with no branch on any path are shared by every branch."""
    from django.db.models import Q
    b = branch_ids(request)
    paths = BRANCH_PATHS.get(qs.model.__name__)
    if not paths:
        return qs
    pick = getattr(request, '_zeo_dash_branch', None) if request is not None else None
    if pick:   # the Branch picked on the dashboard
        pq = Q()
        for p in paths:
            pq |= Q(**{p: pick})
        qs = qs.filter(pq)
    if b is None:
        return qs.distinct() if pick else qs
    anyq, none = Q(), Q()
    for p in paths:
        anyq |= Q(**{p + '__in': b})
        none &= Q(**{p + '__isnull': True})
    return qs.filter(anyq | none).distinct()


def branch_filter(qs, request):
    """?branch=<id> on a list (the Branch filter of the screen)."""
    v = request.query_params.get('branch') if hasattr(request, 'query_params') else None
    paths = BRANCH_PATHS.get(qs.model.__name__)
    if not v or not str(v).isdigit() or not paths:
        return qs
    from django.db.models import Q
    q = Q()
    for p in paths:
        q |= Q(**{p: int(v)})
    return qs.filter(q).distinct()
