"""JSON rows of Projects records (shared by the staff API and the customer portal)."""
from decimal import Decimal

from . import services as S
from .models import OPEN_STAGES


def d(v):
    return v.isoformat() if v else None


def num(v):
    return None if v is None else float(v)


def bname(b):
    """v1.22.0: branch name for the Branch column / filter of the lists."""
    return b.branch_name if b is not None else ''


def emp_ref(e):
    return {'id': e.pk, 'name': S.emp_name(e), 'code': e.emp_code} if e else None


def customer_row(c, counts=False):
    r = {'id': c.pk, 'code': c.code, 'name': c.name, 'trn': c.trn, 'email': c.email, 'phone': c.phone, 'website': c.website,
         'address': c.address, 'city': c.city, 'country': c.country, 'email_domains': c.email_domains,
         'account_manager': emp_ref(c.account_manager), 'branch': c.branch_id, 'branch_name': bname(c.branch), 'default_sla': c.default_sla_id,
         'notes': c.notes, 'is_active': c.is_active, 'created_at': d(c.created_at),
         'partner': c.partner_id, 'partner_number': c.partner.number if c.partner_id else ''}   # v1.42.0 business partner
    if counts:
        r['open_tickets'] = c.tickets.filter(stage__in=OPEN_STAGES).count()
        r['contracts_active'] = c.contracts.filter(status__in=('active', 'expiring')).count()
        r['contacts'] = c.contacts.filter(is_active=True).count()
    return r


def contact_row(c):
    return {'id': c.pk, 'customer': c.customer_id, 'customer_name': c.customer.name, 'name': c.name, 'email': c.email,
            'phone': c.phone, 'designation': c.designation, 'is_primary': c.is_primary, 'is_active': c.is_active,
            'portal': bool(c.portal_user_id), 'portal_username': c.portal_user.username if c.portal_user_id else '',
            'portal_active': bool(c.portal_user_id and c.portal_user.is_active), 'portal_role': c.portal_role,
            'portal_can_approve': c.portal_can_approve}


def ticket_row(t, staff=True):
    st = S.sla_state(t)
    r = {'id': t.pk, 'number': t.number, 'subject': t.subject, 'stage': t.stage, 'stage_label': t.get_stage_display(),
         'priority': t.priority, 'priority_label': t.get_priority_display(), 'channel': t.channel,
         'customer': t.customer_id, 'customer_name': t.customer.name if t.customer_id else 'Internal',
         'branch_name': bname(t.customer.branch) if t.customer_id and t.customer.branch_id else
                        (bname(t.requester_employee.emp_branch_id) if t.requester_employee_id and t.requester_employee.emp_branch_id_id else ''),
         'contact': t.contact_id, 'contact_name': t.contact.name if t.contact_id else (t.requester_name or
                                                                                      (S.emp_name(t.requester_employee) if t.requester_employee_id else '')),
         'product': t.product_id, 'product_name': t.product.name if t.product_id else '',
         'category': t.category_id, 'category_name': t.category.name if t.category_id else '',
         'contract': t.contract_id, 'contract_number': t.contract.number if t.contract_id else '',
         'billable_status': t.billable_status, 'billable_label': t.get_billable_status_display(),
         'created_at': d(t.created_at), 'updated_at': d(t.updated_at), 'resolved_at': d(t.resolved_at), 'closed_at': d(t.closed_at),
         'response_due': d(t.response_due), 'resolution_due': d(t.resolution_due), 'first_response_at': d(t.first_response_at),
         'sla_state': st['state'], 'sla_minutes_left': st['minutes_left'], 'is_open': t.stage in OPEN_STAGES,
         'satisfaction': t.satisfaction}
    if staff:
        r.update({'team': t.team_id, 'team_name': t.team.name if t.team_id else '', 'assigned_to': emp_ref(t.assigned_to),
                  'response_breached': t.response_breached, 'resolution_breached': t.resolution_breached,
                  'escalation_level': t.escalation_level, 'covered': t.covered, 'coverage_note': t.coverage_note,
                  'requester_employee': emp_ref(t.requester_employee), 'reopen_count': t.reopen_count,
                  'project_id': t.project_id, 'task_id': t.task_id,
                  'approval_status': t.approval_status, 'approval_status_label': t.get_approval_status_display(),
                  'approval_reason': t.approval_reason, 'approval_reason_label': t.get_approval_reason_display() if t.approval_reason else '',
                  'channel_label': t.get_channel_display()})
    return r


def ticket_detail(t, staff=True, show_internal=True):
    r = ticket_row(t, staff)
    r['description'] = t.description
    r['resolution_note'] = t.resolution_note
    r['satisfaction_note'] = t.satisfaction_note
    r['sla_policy'] = t.sla_policy.name if t.sla_policy_id else ''
    r['response_target'] = S.sla.human(t.response_minutes)
    r['resolution_target'] = S.sla.human(t.resolution_minutes)
    r['sla_business_hours'] = t.sla_business_hours
    if staff:
        lim = S.hours_limit(t)
        r.update({'approval_requested_hours': num(t.approval_requested_hours), 'approved_extra_hours': num(t.approved_extra_hours),
                  'approval_note': t.approval_note, 'approval_by': S.user_name(t.approval_by) if t.approval_by_id else '',
                  'approval_at': d(t.approval_at), 'hours_logged': num(S.logged_hours(t)), 'hours_limit': num(lim) if lim is not None else None,
                  'whatsapp_phone': (t.contact.phone if t.contact_id and t.contact.phone else (t.customer.phone if t.customer_id else '')) or ''})
    msgs = t.messages.all().prefetch_related('files')
    files = t.files.all()
    events = t.events.all()
    if not show_internal:
        msgs = msgs.filter(is_internal=False)
        files = files.filter(is_internal=False)
        events = events.filter(public=True)
    r['messages'] = [{'id': m.id, 'body': m.body, 'internal': m.is_internal, 'via': m.via, 'author': m.author_name,
                      'from_customer': bool(m.author_contact_id) or (m.via == 'email' and not m.author_user_id),
                      'at': d(m.created_at), 'files': [file_row(f) for f in m.files.all()]} for m in msgs]
    r['files'] = [file_row(f) for f in files]
    r['events'] = [{'kind': e.kind, 'text': e.text, 'by': e.user_name, 'at': d(e.at)} for e in events[:200]]
    if staff:
        r['contact_email'] = t.contact.email if t.contact_id else t.requester_email
        r['worklogs'] = [worklog_row(w) for w in t.worklogs.select_related('employee')]
        r['hours'] = num(sum((w.hours for w in t.worklogs.exclude(status='rejected')), Decimal(0)))
        r['dev_requests'] = [dev_row(x) for x in t.dev_requests.all()]
        if t.contract_id:
            r['contract_usage'] = jsonable(S.contract_usage(t.contract))
    return r


def file_row(f):
    return {'id': f.id, 'name': f.name, 'size': f.size, 'internal': f.is_internal, 'at': d(f.uploaded_at)}


def worklog_row(w):
    return {'id': w.id, 'ticket': w.ticket_id, 'ticket_number': w.ticket.number, 'ticket_subject': w.ticket.subject,
            'customer_name': w.ticket.customer.name if w.ticket.customer_id else 'Internal',
            'branch_name': bname(w.ticket.customer.branch) if w.ticket.customer_id and w.ticket.customer.branch_id else
                           (bname(w.employee.emp_branch_id) if w.employee_id and w.employee.emp_branch_id_id else ''),
            'employee': emp_ref(w.employee), 'date': d(w.date), 'hours': num(w.hours),
            'description': w.description, 'onsite': w.onsite, 'billable': w.billable, 'counts_against_amc': w.counts_against_amc,
            'travel_cost': num(w.travel_cost), 'other_charges': num(w.other_charges), 'status': w.status,
            'cost_amount': num(w.cost_amount), 'rejection_reason': w.rejection_reason}


def contract_row(c, usage=False):
    r = {'id': c.pk, 'number': c.number, 'title': c.title, 'customer': c.customer_id, 'customer_name': c.customer.name,
         'branch_name': bname(c.customer.branch) if c.customer.branch_id else '',
         'contract_type': c.contract_type, 'contract_type_label': c.get_contract_type_display(),
         'products': [{'id': p.id, 'name': p.name} for p in c.products.all()],
         'start_date': d(c.start_date), 'end_date': d(c.end_date), 'days_left': (c.end_date - S.local_today()).days,
         'value': num(c.value), 'currency': c.currency, 'billing_frequency': c.billing_frequency,
         'billing_frequency_label': c.get_billing_frequency_display(), 'unlimited_hours': c.unlimited_hours,
         'included_hours': num(c.included_hours), 'onsite_hours': num(c.onsite_hours), 'hourly_rate_outside': num(c.hourly_rate_outside),
         'sla_policy': c.sla_policy_id, 'sla_policy_name': c.sla_policy.name if c.sla_policy_id else '',
         'support_timing': c.support_timing, 'exclusions': c.exclusions, 'account_manager': emp_ref(c.account_manager),
         'status': c.status, 'status_label': c.get_status_display(), 'version': c.version, 'previous': c.previous_id,
         'previous_number': c.previous.number if c.previous_id else '',
         'renewal_status': c.renewal_status, 'renewal_status_label': c.get_renewal_status_display(),
         'renewal_owner': emp_ref(c.renewal_owner), 'renewal_quote_ref': c.renewal_quote_ref, 'renewal_value': num(c.renewal_value),
         'renewal_note': c.renewal_note, 'lost_reason': c.lost_reason, 'notes': c.notes}
    if usage:
        r['usage'] = jsonable(S.contract_usage(c))
    return r


def billing_row(b):
    return {'id': b.id, 'contract': b.contract_id, 'contract_number': b.contract.number, 'customer_name': b.contract.customer.name,
            'branch_name': bname(b.contract.customer.branch) if b.contract.customer.branch_id else '',
            'due_date': d(b.due_date), 'period_label': b.period_label, 'amount': num(b.amount), 'currency': b.contract.currency,
            'status': b.status, 'status_label': b.get_status_display(), 'invoice_no': b.invoice_no, 'invoice_date': d(b.invoice_date),
            'paid_date': d(b.paid_date), 'note': b.note}


def dev_row(x):
    return {'id': x.id, 'number': x.number, 'title': x.title, 'status': x.status, 'status_label': x.get_status_display(),
            'ticket': x.ticket_id, 'ticket_number': x.ticket.number if x.ticket_id else '',
            'customer': x.customer_id, 'customer_name': x.customer.name if x.customer_id else '',
            'branch_name': bname(x.customer.branch) if x.customer_id and x.customer.branch_id else '',
            'issue': x.issue, 'proposed_solution': x.proposed_solution, 'estimated_hours': num(x.estimated_hours),
            'estimated_cost': num(x.estimated_cost), 'quoted_amount': num(x.quoted_amount), 'chargeable': x.chargeable,
            'needs_customer_approval': x.needs_customer_approval,
            'requested_by': S.user_name(x.requested_by), 'requested_at': d(x.requested_at),
            'tech_by': S.user_name(x.tech_by), 'tech_at': d(x.tech_at), 'tech_note': x.tech_note,
            'commercial_by': S.user_name(x.commercial_by), 'commercial_at': d(x.commercial_at), 'commercial_note': x.commercial_note,
            'customer_decision_by': x.customer_decision_by, 'customer_decision_at': d(x.customer_decision_at), 'customer_note': x.customer_note,
            'project_id': x.project_id, 'task_id': x.task_id, 'assigned_to': emp_ref(x.assigned_to),
            'deployed_at': d(x.deployed_at), 'confirmed_at': d(x.confirmed_at), 'rejected_reason': x.rejected_reason}


def milestone_row(m):
    return {'id': m.id, 'project_id': m.project_id, 'sequence': m.sequence, 'name': m.name, 'description': m.description,
            'planned_date': d(m.planned_date), 'depends_on': m.depends_on_id, 'amount': num(m.amount), 'status': m.status,
            'status_label': m.get_status_display(), 'completed_at': d(m.completed_at), 'accepted_by': m.accepted_by,
            'accepted_at': d(m.accepted_at), 'acceptance_note': m.acceptance_note, 'invoice_status': m.invoice_status,
            'invoice_status_label': m.get_invoice_status_display(), 'invoice_no': m.invoice_no, 'invoice_date': d(m.invoice_date),
            'paid_date': d(m.paid_date), 'overdue': bool(m.planned_date and m.planned_date < S.local_today() and m.status != 'accepted')}


def cr_row(c):
    return {'id': c.id, 'number': c.number, 'project_id': c.project_id, 'title': c.title, 'description': c.description,
            'extra_hours': num(c.extra_hours), 'extra_amount': num(c.extra_amount), 'extra_days': c.extra_days,
            'status': c.status, 'status_label': c.get_status_display(), 'requested_by': S.user_name(c.requested_by),
            'requested_at': d(c.requested_at), 'decided_by': S.user_name(c.decided_by), 'decided_at': d(c.decided_at),
            'decision_note': c.decision_note, 'customer_decision_by': c.customer_decision_by,
            'customer_decision_at': d(c.customer_decision_at)}


def risk_row(r):
    return {'id': r.id, 'project_id': r.project_id, 'kind': r.kind, 'title': r.title, 'description': r.description,
            'impact': r.impact, 'probability': r.probability, 'owner': emp_ref(r.owner), 'due_date': d(r.due_date),
            'status': r.status, 'mitigation': r.mitigation}


def expense_row(x):
    return {'id': x.id, 'project_id': x.project_id, 'date': d(x.date), 'category': x.category,
            'category_label': x.get_category_display(), 'description': x.description, 'vendor': x.vendor, 'amount': num(x.amount)}


def jsonable(v):
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items() if not str(k).startswith('_')}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v
