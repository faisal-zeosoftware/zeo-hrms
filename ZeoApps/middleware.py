"""v1.22.0 – a switched-off HRMS app answers 409 on its API (as Projects, CRM, Property, Maintenance and HSE do).

The menu already hides HRMS when it is not active (Settings › Apps); this closes the API too. The notification counters the
app shell asks for on every screen answer an empty list, so the bell keeps working in a company that does not use HRMS.
Settings, users, branches, Ask ZEO, the report centre (which hides the HRMS reports itself) and the other apps stay open.
"""
import json
import re

from django.db import connection
from django.http import HttpResponse

PAYROLL_PREFIXES = ('/payroll/', '/payroll-plus/', '/accounting/')   # v1.23.0 Payroll is its own app
HRMS_PREFIXES = ('/employee/', '/calendars/', '/payroll/', '/project/', '/performance/', '/recruitment/', '/learning/',
                 '/leave-policy/', '/hr-actions/', '/expense/', '/project-control/', '/org-structure/', '/shift-planner/',
                 '/attendance-plus/', '/asset-plus/', '/employee-profile/', '/self-service/', '/payroll-plus/', '/accounting/', '/iclock/')
NOTIFY = re.compile(r'notif|notify', re.I)
# v1.67.3 (apps) the company core stays open without HR: the employee directory (list, pick, one employee, new / change – not the
# HR pages hanging under an employee), the logins of employees (Settings › Users and Give access) and the company e-mail account
CORE_OPEN = re.compile(r'^/employee/api/(?:emplist|emp-by-user|email-config|ess-users|update-ess-user)(?:/|$)'
                       r'|^/employee/api/Employee/(?:\d+/)?$')
# v1.67.3 (apps) paths an app shares with HR: open when any of the apps is active (Projects tasks, timesheets and costing live in
# the project module; the HR asset register under Asset Management)
SHARED = (('/project/', ('hrms', 'projects')), ('/project-control/', ('hrms', 'projects')), ('/asset-plus/', ('hrms', 'assets')))
# v1.67.3 (apps) the phone app's HR lists (my requests, approvals): empty without HR, sending answers 409
MOBILE_HR = ('/mobile-co/api/requests/', '/mobile-co/api/approvals/')


def _json(data, status):
    return HttpResponse(json.dumps(data), status=status, content_type='application/json')


class HRMSActiveMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(MOBILE_HR) and getattr(connection, 'schema_name', 'public') != 'public' and request.method != 'OPTIONS':
            try:
                from .services import is_active
                off = not is_active('hrms')
            except Exception:
                off = False
            if off:
                if request.method == 'GET':
                    return _json([], 200)
                return _json({'detail': 'HR is not active in this company. A company administrator activates it in Settings › Apps.',
                              'activated': False}, 409)
            return self.get_response(request)
        if path.startswith(HRMS_PREFIXES) and CORE_OPEN.match(path):   # v1.67.3 (apps) the company core
            return self.get_response(request)
        if path.startswith(HRMS_PREFIXES) and getattr(connection, 'schema_name', 'public') != 'public' and request.method != 'OPTIONS':
            app = 'payroll' if path.startswith(PAYROLL_PREFIXES) else 'hrms'
            shared = next((a for p, a in SHARED if path.startswith(p)), None)
            try:
                from .services import is_active
                off = (not any(is_active(a) for a in shared)) if shared else not is_active(app)
            except Exception:
                off = False
            if off:
                if request.method == 'GET' and NOTIFY.search(path):
                    return _json([], 200)
                if app == 'payroll':
                    return _json({'detail': 'Payroll is not active in this company. A company administrator activates it in Settings › Apps.',
                                  'activated': False}, 409)
                return _json({'detail': 'HR is not active in this company. A company administrator activates it in Settings › Apps.',
                              'activated': False}, 409)
        return self.get_response(request)
