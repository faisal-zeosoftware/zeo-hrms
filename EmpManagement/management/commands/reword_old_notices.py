"""v1.73.21 (item 3) – the bell notices written before 1.73.19 / 1.73.21 told the approver "Your LeaveRequest … is waiting for your
Approval" (as if he had asked) and glued the type to the number ("Sick Leave(Document No: …"). New notices read
"A leave request Sick Leave of Bikash Thapa (Document No: DP2-LEA-0030) is waiting for your approval." This command rewrites the
stored old ones the same way – in every company's notice lists and in the bell inbox (top bar, phone). The employee's own notices
("… has been Approved") are not touched. Without --fix it only counts.

    python manage.py reword_old_notices            # count, every company
    python manage.py reword_old_notices --fix      # rewrite
    python manage.py reword_old_notices --schema ztdemo1725 --fix
"""
import re

from django.apps import apps
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

KIND = {'Leave': 'leave request', 'General': 'general request', 'Document': 'document request', 'Resignation': 'resignation request',
        'Asset': 'asset request', 'Loan': 'loan request', 'Airticket': 'air ticket request', 'AirTicket': 'air ticket request',
        'AdvanceSalary': 'salary advance request', 'LateinEarly': 'late in / early out request', 'Latein': 'late in / early out request'}
OLD = re.compile(r'^(?:Your|A|New) (Leave|General|Document|Resignation|Asset|Loan|Airticket|AirTicket|LateinEarly|Latein)Request\s+(.*?)\s*'
                 r'\(Document No:\s*([^)]+?)\s*\) is waiting for your Approval\.?\s*$', re.S)
OLD_ADV = re.compile(r'^Your AdvanceSalaryRequest (\S+) is waiting for your Approval\.\s*$')
MODELS = ['calendars.LvApprovalNotify', 'calendars.LateinEarlyRequestNotification', 'EmpManagement.RequestNotification',
          'EmpManagement.DocRequestNotification', 'EmpManagement.ResignationRequestNotification', 'OrganisationManager.AssetNotification',
          'PayrollManagement.LoanNotification', 'PayrollManagement.AdvanceSalaryNotification', 'PayrollManagement.AirticketNotification']


def employee_of(doc):
    """first and last name of the employee of the request with this document number (this company)"""
    try:
        from DashboardManagement.services import REQUESTS
    except Exception:   # noqa: BLE001
        return ''
    for _key, _label, rm, *_ in REQUESTS:
        try:
            R = apps.get_model(rm)
        except LookupError:
            continue
        if not any(f.name == 'document_number' for f in R._meta.fields):
            continue
        r = R.objects.filter(document_number=doc).select_related('employee').first() if any(f.name == 'employee' for f in R._meta.fields) else None
        if r is not None and r.employee_id:
            e = r.employee
            return ' '.join(x for x in [str(e.emp_first_name or ''), str(e.emp_last_name or '')] if x).strip()
    return ''


def reword(msg, names):
    """the new text of an old approver notice, or None"""
    m = OLD.match(msg or '')
    if m:
        kind, what, doc = KIND[m.group(1)], m.group(2).strip(), m.group(3).strip()
    else:
        m = OLD_ADV.match(msg or '')
        if not m:
            return None
        kind, what, doc = KIND['AdvanceSalary'], '', m.group(1).strip()
    if kind == 'resignation request' and what.lower() == 'resignation':
        what = ''
    if doc not in names:
        names[doc] = employee_of(doc)
    who = names[doc]
    return ('An ' if kind[:1] in 'aeiou' else 'A ') + kind + (f" {what}" if what else '') + (f" of {who}" if who else '') + f" (Document No: {doc}) is waiting for your approval."


class Command(BaseCommand):
    help = 'Rewrite the old "Your …Request … is waiting for your Approval" bell notices to the approver wording (v1.73.21).'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true', help='rewrite (else only count)')
        parser.add_argument('--schema', help='one company only')

    def handle(self, *args, **o):
        T = get_tenant_model()
        schemas = [o['schema']] if o.get('schema') else list(T.objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        total = 0
        for sch in schemas:
            n = 0
            names = {}
            with schema_context(sch):
                for lab in MODELS:
                    try:
                        M = apps.get_model(lab)
                    except LookupError:
                        continue
                    try:
                        rows = list(M.objects.filter(message__icontains='is waiting for your Approval').values_list('pk', 'message'))
                    except Exception:   # noqa: BLE001 – a company without that table
                        continue
                    for pk, msg in rows:
                        new = reword(msg, names)
                        if new and new != msg:
                            n += 1
                            if o['fix']:
                                M.objects.filter(pk=pk).update(message=new)
                # the bell inbox (public schema, one row per user and company)
                try:
                    Inbox = apps.get_model('UserManagement.UserNotificationInbox')
                    with schema_context('public'):
                        rows = list(Inbox.objects.filter(schema_name=sch, message__icontains='is waiting for your Approval').values_list('pk', 'message'))
                    for pk, msg in rows:
                        new = reword(msg, names)   # names looked up in the company (above)
                        if new and new != msg:
                            n += 1
                            if o['fix']:
                                with schema_context('public'):
                                    Inbox.objects.filter(pk=pk).update(message=new)
                except LookupError:
                    pass
            if n:
                self.stdout.write(f'{sch}: {n} notice(s) {"rewritten" if o["fix"] else "to rewrite"}')
            total += n
        self.stdout.write(f'{total} old notice(s) {"rewritten" if o["fix"] else "found"}' + ('' if o['fix'] or not total else ' – run with --fix'))
