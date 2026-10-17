"""Sample rules (created as drafts) so a company can start from working examples."""
from . import registry as R

SAMPLES = [
    {'name': 'Welcome a new employee', 'module': 'hrms', 'model': 'EmpManagement.emp_master', 'trigger': 'created',
     'description': 'When an employee is created: welcome e-mail to the employee, onboarding task for HR.',
     'actions': [
         {'type': 'send_email', 'to': [{'type': 'employee'}], 'subject': 'Welcome to {{ company.name }}, {{ record.emp_first_name }}!',
          'body': 'Dear {{ record.emp_first_name }},\n\nWelcome to {{ company.name }}. Your employee code is {{ record.emp_code }} and you join on {{ record.emp_joined_date }}.\n\nHR team'},
         {'type': 'create_task', 'assign': [{'type': 'role', 'value': 'HR Manager'}], 'summary': 'Onboarding: {{ record.emp_code }} {{ record.emp_first_name }}', 'due_in_days': 3,
          'note': 'Documents, bank details, visa, equipment.'}]},
    {'name': 'Probation review 30 days before confirmation', 'module': 'hrms', 'model': 'EmpManagement.emp_master', 'trigger': 'date',
     'trigger_config': {'date_field': 'emp_date_of_confirmation', 'when': 'before', 'offset_days': 30},
     'conditions': {'op': 'and', 'items': [{'field': 'is_active', 'operator': 'true'}]},
     'description': 'Reminds the reporting manager and HR a month before the confirmation date.',
     'actions': [
         {'type': 'notify', 'to': [{'type': 'manager'}, {'type': 'role', 'value': 'HR Manager'}], 'title': 'Probation review due',
          'message': '{{ record.emp_first_name }} {{ record.emp_last_name }} ({{ record.emp_code }}) is confirmed on {{ record.emp_date_of_confirmation }} – plan the review.'},
         {'type': 'create_task', 'assign': [{'type': 'manager'}], 'summary': 'Probation review: {{ record.emp_code }}', 'due_in_days': 7}]},
    {'name': 'Designation change – tell HR', 'module': 'hrms', 'model': 'EmpManagement.emp_master', 'trigger': 'field_changed',
     'trigger_config': {'field': 'emp_desgntn_id'}, 'description': 'When the designation of an employee changes, HR is notified and a contract task is created.',
     'actions': [
         {'type': 'notify', 'to': [{'type': 'role', 'value': 'HR Manager'}], 'title': 'Designation changed',
          'message': '{{ record.emp_first_name }} ({{ record.emp_code }}) is now {{ record.emp_desgntn_id }} (was {{ old.emp_desgntn_id }}).'},
         {'type': 'create_task', 'assign': [{'type': 'triggering_user'}], 'summary': 'Update the contract of {{ record.emp_code }}', 'due_in_days': 5}]},
    {'name': 'Visa expires in 60 days', 'module': 'hrms', 'model': 'EmployeeProfile.employeeidentity', 'trigger': 'date',
     'trigger_config': {'date_field': 'visa_expiry_date', 'when': 'before', 'offset_days': 60},
     'description': 'Two months before the residence visa expires: HR notified, task for the PRO, e-mail to the employee.',
     'actions': [
         {'type': 'notify', 'to': [{'type': 'role', 'value': 'HR Manager'}], 'title': 'Visa renewal due',
          'message': 'The visa of {{ record.employee }} expires on {{ record.visa_expiry_date }}.'},
         {'type': 'create_task', 'assign': [{'type': 'role', 'value': 'HR Manager'}], 'summary': 'Visa renewal: {{ record.employee }}', 'due_in_days': 14},
         {'type': 'send_email', 'to': [{'type': 'employee'}], 'subject': 'Your residence visa expires on {{ record.visa_expiry_date }}',
          'body': 'Dear {{ record.employee }},\n\nYour residence visa expires on {{ record.visa_expiry_date }}. HR will contact you for the renewal papers.\n\nHR team'}]},
    {'name': 'Leave request – notify the manager', 'module': 'hrms', 'model': 'calendars.employee_leave_request', 'trigger': 'created',
     'description': 'A new leave request notifies the reporting manager; after 2 days without decision HR is reminded.',
     'actions': [
         {'type': 'notify', 'to': [{'type': 'manager'}], 'title': 'Leave request to approve',
          'message': '{{ record.employee }} asks for {{ record.leave_type }} from {{ record.start_date }} to {{ record.end_date }} ({{ record.number_of_days }} days).'},
         {'type': 'wait', 'days': 2},
         {'type': 'branch', 'conditions': {'op': 'and', 'items': [{'field': 'status', 'operator': 'eq', 'value': 'pending'}]},
          'then': [{'type': 'notify', 'to': [{'type': 'role', 'value': 'HR Manager'}], 'title': 'Leave request still pending',
                    'message': 'The leave request {{ record.document_number }} of {{ record.employee }} has waited 2 days.'}], 'else': []}]},
]


def load(user=None):
    """Create the samples that fit this installation as drafts (skips the ones that exist by name)."""
    from .models import Workflow
    made = []
    for s in SAMPLES:
        if R.get_model(s['model']) is None or Workflow.objects.filter(name=s['name']).exists():
            continue
        w = Workflow.objects.create(name=s['name'], description=s.get('description', ''), module=s['module'], model=s['model'], trigger=s['trigger'],
                                    trigger_config=s.get('trigger_config', {}), conditions=s.get('conditions', {'op': 'and', 'items': []}),
                                    actions=s['actions'], state='draft', created_by=user)
        made.append(w.name)
    return made
