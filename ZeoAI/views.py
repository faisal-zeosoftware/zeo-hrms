"""v1.37.0 – AI assistants API, mounted at /zeo-ai/.

GET/PUT api/settings/                                   thresholds (company admin / HR manager)
GET  api/payroll/runs/                                   payroll runs with their audit state
POST api/payroll/runs/<id>/audit/                        run (or re-run) the audit → the audit with its findings
GET  api/payroll/runs/<id>/audit/                        last audit of the run
POST api/payroll/findings/<id>/                          {status: acknowledged|resolved|dismissed|open, note}
GET  api/payroll/runs/<id>/audit.xlsx                    findings as Excel
GET  api/attendance/patterns/?branch=&department=&days=  repeated lates / missed punches / anomalies
GET  api/attendance/coverage/?branch=&department=&days=  staffing gaps and overtime forecast
GET  api/attendance/replacements/?employee=&date=&shift= who can cover
POST api/attendance/roster/                              {branch, department, date_from, date_to, shift} → recommended roster (not saved)
GET  api/recruitment/metrics/                            pipeline and time-to-hire
GET  api/recruitment/candidates/<id>/?job=               CV extraction, summary, match against a job
GET  api/recruitment/jobs/<id>/description/              generated JD      GET .../questions/?candidate=   interview questions
GET  api/recruitment/jobs/<id>/slots/                    free interview slots
GET  api/recruitment/applications/<id>/draft/?kind=      candidate e-mail draft
GET  api/performance/employees/<id>/summary|gaps|courses|objectives|plan/
GET  api/performance/template/?designation=&department=
GET  api/performance/training/?department=&branch=       completion & overdue needs
GET  api/performance/certificates/?employee=&within=
GET  api/workforce/headcount|budget|hotspots|recruitment|compare/
POST api/workforce/ask/                                  {question}
GET  api/log/
"""
from datetime import date, datetime

from django.apps import apps
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import ctx, run_in_branches
from Translations.i18n import tr

from . import attendance as AT
from . import payroll_audit as PA
from . import performance as PF
from . import recruitment as RC
from . import workforce as WF
from .models import AIAssistantSettings, AILog, AuditRun, Finding

M = apps.get_model
PAYROLL = {'view_payrollrun', 'change_payrollrun', 'view_payslip', 'add_payrollrun'}
ATTEND = {'view_attendance', 'change_attendance', 'view_rosterperiod', 'change_rosterperiod', 'add_rosterperiod', 'view_attendanceday'}
RECRUIT = {'view_candidate', 'add_candidate', 'change_application', 'view_application', 'view_jobopening', 'change_jobopening'}
PERF = {'view_goalsheet', 'change_goalsheet', 'view_appraisalcycle', 'view_course', 'view_certificate', 'change_certificate'}
WORK = {'view_emp_master', 'view_payrollrun', 'view_dashboard', 'view_report'}


class Base(APIView):
    permission_classes = [IsAuthenticated]
    zeo_access = False
    zeo_scope = False
    need = set()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.c = ctx(request)
        if self.need and not self.can(self.need) and not self.is_manager():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(tr('You do not have the rights for this assistant.'))

    def can(self, codes):
        return self.c.admin or bool(set(codes) & self.c.codes)

    def is_manager(self):
        """Managers (someone reports to them) may use the attendance, performance and workforce assistants for their own people."""
        E = M('EmpManagement', 'emp_master')
        return E.objects.filter(emp_reporting_manager=self.request.user, is_active=True).exists()

    def log(self, kind, ref='', summary='', data=None):
        AILog.objects.create(kind=kind, ref=str(ref)[:120], by=self.request.user, summary=summary[:300], data=data or {})

    def i(self, name, default=None):
        v = self.request.query_params.get(name)
        try:
            return int(v) if v not in (None, '') else default
        except ValueError:
            return default

    def d(self, name, default=None):
        v = self.request.query_params.get(name) or (self.request.data.get(name) if hasattr(self.request, 'data') and isinstance(self.request.data, dict) else None)
        try:
            return date.fromisoformat(v) if v else default
        except ValueError:
            return default


def _settings_row(s):
    return {f.name: (float(v) if hasattr(v, 'as_tuple') else v) for f in s._meta.fields if f.name not in ('id', 'updated_at') for v in [getattr(s, f.name)]}


class SettingsView(Base):
    def get(self, request):
        return Response(_settings_row(AIAssistantSettings.get()))

    def put(self, request):
        if not (self.c.admin or self.can({'change_payrollsettings', 'change_companysettings', 'change_company'})):
            return Response({'detail': tr('Only the company administrator can change these settings.')}, status=403)
        s = AIAssistantSettings.get()
        for f in s._meta.fields:
            if f.name in request.data and f.name not in ('id', 'updated_at'):
                setattr(s, f.name, request.data[f.name])
        s.full_clean()
        s.save()
        return Response(_settings_row(s))


# ---------------------------------------------------------------- payroll auditor
def _audit_json(a, with_findings=True):
    out = {'id': a.id, 'payroll_run_id': a.payroll_run_id, 'previous_run_id': a.previous_run_id, 'run_label': a.run_label, 'by': getattr(a.by, 'username', ''), 'at': a.at,
           'payslips': a.payslips, 'totals': a.totals, 'counts': a.counts, 'summary': a.summary}
    if with_findings:
        rank = {'critical': 0, 'warning': 1, 'info': 2}
        out['findings'] = [_finding_json(f) for f in sorted(a.findings.select_related('employee'), key=lambda f: (rank.get(f.severity, 3), f.status != 'open', f.rule, f.id))]
    return out


def _finding_json(f):
    e = f.employee
    return {'id': f.id, 'rule': f.rule, 'severity': f.severity, 'employee_id': f.employee_id, 'employee': PA.emp_name(e) if e else '', 'emp_code': getattr(e, 'emp_code', '') if e else '',
            'payslip_id': f.payslip_id, 'title': f.title, 'explanation': f.explanation, 'previous': f.previous, 'current': f.current, 'change_pct': f.change_pct, 'data': f.data,
            'status': f.status, 'note': f.note, 'decided_by': getattr(f.decided_by, 'username', ''), 'decided_at': f.decided_at}


class PayrollRunsView(Base):
    need = PAYROLL

    def get(self, request):
        PR = M('PayrollManagement', 'PayrollRun')
        runs = [r for r in PR.objects.order_by('-year', '-month', '-id')[:36] if run_in_branches(self.c.branches, r)]
        audits = {}
        for a in AuditRun.objects.filter(payroll_run_id__in=[r.id for r in runs]).order_by('at'):   # the newest audit of each run wins
            audits[a.payroll_run_id] = a
        out = []
        for r in runs:
            a = audits.get(r.id)
            n = r.payslips.count()
            out.append({'id': r.id, 'name': r.name, 'month': r.month, 'year': r.year, 'status': r.status, 'payslips': n, 'payment_date': r.payment_date,
                        'audit': {'id': a.id, 'at': a.at, 'counts': a.counts, 'open_critical': a.findings.filter(severity='critical', status='open').count(),
                                  'open': a.findings.filter(status='open').count()} if a else None, 'gate': PA.gate(r)[0]})
        return Response({'runs': out, 'settings': _settings_row(AIAssistantSettings.get())})


class PayrollAuditView(Base):
    need = PAYROLL

    def _run(self, run_id):
        PR = M('PayrollManagement', 'PayrollRun')
        run = PR.objects.filter(pk=run_id).first()
        if run is None or not run_in_branches(self.c.branches, run):
            return None
        return run

    def get(self, request, run_id):
        run = self._run(run_id)
        if run is None:
            return Response({'detail': tr('Not found.')}, status=404)
        a = AuditRun.objects.filter(payroll_run_id=run.pk).first()
        if a is None:
            return Response({'audit': None, 'gate': PA.gate(run)})
        return Response({'audit': _audit_json(a), 'gate': PA.gate(run)})

    def post(self, request, run_id):
        run = self._run(run_id)
        if run is None:
            return Response({'detail': tr('Not found.')}, status=404)
        a = PA.audit(run, request.user)
        self.log('payroll_audit', run.pk, a.summary, {'counts': a.counts})
        return Response({'audit': _audit_json(a), 'gate': PA.gate(run)})


class FindingView(Base):
    need = PAYROLL

    def post(self, request, pk):
        f = Finding.objects.filter(pk=pk).select_related('audit').first()
        if f is None:
            return Response({'detail': tr('Not found.')}, status=404)
        st = request.data.get('status')
        if st not in ('open', 'acknowledged', 'resolved', 'dismissed'):
            return Response({'detail': tr('Unknown status.')}, status=400)
        f.status, f.note = st, (request.data.get('note') or '')[:300]
        f.decided_by, f.decided_at = (request.user, datetime.now()) if st != 'open' else (None, None)
        f.save()
        a = f.audit
        a.counts['open'] = a.findings.filter(status='open').count()
        a.counts['open_critical'] = a.findings.filter(status='open', severity='critical').count()
        a.save(update_fields=['counts'])
        return Response(_finding_json(f))


class AuditXlsxView(Base):
    need = PAYROLL

    def get(self, request, run_id):
        from django.http import HttpResponse
        from openpyxl import Workbook
        a = AuditRun.objects.filter(payroll_run_id=run_id).first()
        if a is None:
            return Response({'detail': tr('Not found.')}, status=404)
        wb = Workbook()
        ws = wb.active
        ws.title = 'Findings'
        ws.append(['Severity', 'Rule', 'Employee', 'Code', 'Title', 'Explanation', 'Previous', 'Current', 'Change %', 'Status', 'Note', 'Decided by'])
        for f in a.findings.select_related('employee', 'decided_by'):
            ws.append([f.severity, f.rule, PA.emp_name(f.employee) if f.employee else '', getattr(f.employee, 'emp_code', '') if f.employee else '', f.title, f.explanation,
                       float(f.previous) if f.previous is not None else None, float(f.current) if f.current is not None else None, float(f.change_pct) if f.change_pct is not None else None,
                       f.status, f.note, getattr(f.decided_by, 'username', '')])
        ws2 = wb.create_sheet('Summary')
        ws2.append(['Run', a.run_label]); ws2.append(['Audited at', a.at.strftime('%Y-%m-%d %H:%M')]); ws2.append(['Payslips', a.payslips]); ws2.append(['Summary', a.summary])
        for k, v in (a.totals.get('now') or {}).items():
            ws2.append([f'Now {k}', v])
        for k, v in (a.totals.get('previous') or {}).items():
            ws2.append([f'Previous {k}', v])
        resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="payroll-audit-{a.run_label or run_id}.xlsx"'.replace(' ', '_')
        wb.save(resp)
        return resp


# ---------------------------------------------------------------- attendance & shifts
class PatternsView(Base):
    need = ATTEND

    def get(self, request):
        return Response(AT.patterns(self.i('branch'), self.i('department'), self.i('days', 30)))


class CoverageView(Base):
    need = ATTEND

    def get(self, request):
        return Response(AT.coverage(self.i('branch'), self.i('department'), self.i('days', 14)))


class ReplacementsView(Base):
    need = ATTEND

    def get(self, request):
        emp, day = self.i('employee'), self.d('date', date.today())
        if not emp:
            return Response({'detail': tr('employee is required.')}, status=400)
        return Response(AT.replacements(emp, day, self.i('shift'), self.i('limit', 8)))


class RosterView(Base):
    need = ATTEND

    def post(self, request):
        d = request.data
        try:
            df, dt = date.fromisoformat(d.get('date_from')), date.fromisoformat(d.get('date_to'))
        except (TypeError, ValueError):
            return Response({'detail': tr('date_from and date_to are required.')}, status=400)
        if (dt - df).days > 42 or dt < df:
            return Response({'detail': tr('Up to six weeks at a time.')}, status=400)
        out = AT.roster_recommendation(d.get('branch') or None, d.get('department') or None, df, dt, d.get('shift') or None)
        self.log('roster', f'{df}..{dt}', f'Roster recommended for {df} – {dt}', {'branch': d.get('branch'), 'department': d.get('department')})
        return Response(out)


# ---------------------------------------------------------------- recruitment
class RecruitMetricsView(Base):
    need = RECRUIT

    def get(self, request):
        return Response(RC.metrics(self.i('days', 180)))


class CandidateView(Base):
    need = RECRUIT

    def get(self, request, pk):
        Cand, Job = M('RecruitmentManagement', 'Candidate'), M('RecruitmentManagement', 'JobOpening')
        c = Cand.objects.filter(pk=pk).first()
        if c is None:
            return Response({'detail': tr('Not found.')}, status=404)
        cv = RC.extract(RC.cv_text(c))
        out = {'candidate': {'id': c.id, 'name': f'{c.first_name} {c.last_name}'.strip(), 'email': c.email, 'has_cv': bool(c.cv)}, 'extracted': cv, 'summary': RC.summary(c, cv), 'matches': []}
        jobs = Job.objects.filter(pk=self.i('job')) if self.i('job') else Job.objects.filter(status__in=('open', 'published')).order_by('-created_at')[:8]
        for j in jobs:
            m = RC.match(c, j, cv)
            out['matches'].append({'job_id': j.id, 'job': j.title, 'score': m['score'], 'reasons': m['reasons'], 'missing': m['missing']})
        out['matches'].sort(key=lambda r: -r['score'])
        out['note'] = tr('Scores and summaries help the recruiter to screen; the hiring decision stays with people.')
        self.log('cv_read', c.id, f'CV read for {out["candidate"]["name"]}', {'skills': cv.get('skills', [])[:10]})
        return Response(out)


class JobAIView(Base):
    need = RECRUIT

    def get(self, request, pk, what):
        Job = M('RecruitmentManagement', 'JobOpening')
        j = Job.objects.filter(pk=pk).select_related('department', 'branch').first()
        if j is None:
            return Response({'detail': tr('Not found.')}, status=404)
        if what == 'description':
            return Response({'job': j.title, 'description': RC.job_description(j), 'requirements': RC.job_requirements(j)})
        if what == 'questions':
            cand = M('RecruitmentManagement', 'Candidate').objects.filter(pk=self.i('candidate')).first() if self.i('candidate') else None
            return Response({'job': j.title, 'candidate': f'{cand.first_name} {cand.last_name}'.strip() if cand else '', 'questions': RC.interview_questions(j, cand)})
        if what == 'slots':
            return Response({'job': j.title, 'slots': RC.slots(j, self.i('days', 7))})
        if what == 'shortlist':
            App = M('RecruitmentManagement', 'Application')
            rows = []
            for a in App.objects.filter(job=j).exclude(stage__in=('rejected', 'withdrawn')).select_related('candidate')[:60]:
                m = RC.match(a.candidate, j)
                rows.append({'application_id': a.id, 'candidate_id': a.candidate_id, 'candidate': f'{a.candidate.first_name} {a.candidate.last_name}'.strip(), 'stage': a.stage, 'score': m['score'],
                             'missing': m['missing'][:4], 'experience_years': m['cv'].get('experience_years'), 'skills': m['cv'].get('skills', [])[:8]})
            rows.sort(key=lambda r: -r['score'])
            return Response({'job': j.title, 'rows': rows, 'note': tr('Ranking is a screening aid – read the CVs before deciding.')})
        return Response({'detail': tr('Unknown request.')}, status=400)


class DraftView(Base):
    need = RECRUIT

    def get(self, request, pk):
        App = M('RecruitmentManagement', 'Application')
        a = App.objects.filter(pk=pk).select_related('candidate', 'job').first()
        if a is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(RC.drafts(a, request.query_params.get('kind', 'invite')))


# ---------------------------------------------------------------- performance & development
class EmployeeAIView(Base):
    need = PERF

    def _ok(self, emp_id):
        if self.c.admin or self.can(PERF):
            return True
        E = M('EmpManagement', 'emp_master')
        return E.objects.filter(pk=emp_id, emp_reporting_manager=self.request.user).exists()

    def get(self, request, pk, what):
        if not self._ok(pk):
            return Response({'detail': tr('Only the manager or HR can open this.')}, status=403)
        E = M('EmpManagement', 'emp_master')
        if not E.objects.filter(pk=pk).exists():
            return Response({'detail': tr('Not found.')}, status=404)
        fn = {'summary': lambda: PF.review_summary(pk), 'gaps': lambda: PF.skill_gaps(pk), 'courses': lambda: PF.recommend_courses(pk),
              'objectives': lambda: PF.objectives(pk), 'plan': lambda: PF.development_plan(pk), 'certificates': lambda: PF.certificates(employee_id=pk, within=self.i('within', 90))}.get(what)
        if fn is None:
            return Response({'detail': tr('Unknown request.')}, status=400)
        return Response(fn())


class TemplateView(Base):
    need = PERF

    def get(self, request):
        return Response(PF.appraisal_template(self.i('designation'), self.i('department')))


class TrainingView(Base):
    need = PERF

    def get(self, request):
        return Response(PF.training_status(self.i('department'), self.i('branch'), self.i('days', 90)))


class CertificatesView(Base):
    need = PERF

    def get(self, request):
        return Response(PF.certificates(self.i('employee'), self.i('within', 90)))


class PlanSaveView(Base):
    """Save the drafted development plan into People (when the People app is active)."""
    need = PERF

    def post(self, request, pk):
        try:
            DP = M('ZeoPeople', 'DevelopmentPlan')
        except LookupError:
            return Response({'detail': tr('The People app is not installed.')}, status=400)
        d = PF.development_plan(pk)
        steps = request.data.get('steps') or d['steps']
        from ZeoPeople.app import app as people
        E = M('EmpManagement', 'emp_master').objects.get(pk=pk)
        row = DP.objects.create(number=people.next_number('development_plan', 'PDP'), employee=E, branch=E.emp_branch_id, title=(request.data.get('title') or d['title'])[:150],
                                steps=steps, status='draft', start=date.today(), notes=d['summary'][:1000])
        self.log('dev_plan', row.id, f'Development plan drafted for {d["employee"]["name"]}')
        return Response({'id': row.id, 'number': row.number, 'title': row.title})


# ---------------------------------------------------------------- workforce
class WorkforceView(Base):
    need = WORK

    def get(self, request, what):
        g = lambda k, d=0.0: float(request.query_params.get(k) or d)
        if what == 'headcount':
            return Response(WF.headcount(self.i('branch'), self.i('department'), self.i('months')))
        if what == 'budget':
            return Response(WF.salary_budget(self.i('branch'), self.i('department'), self.i('months'), g('growth'), g('increment'), request.query_params.get('increment_month') or None))
        if what == 'hotspots':
            return Response(WF.hotspots(self.i('months', 12)))
        if what == 'recruitment':
            return Response(WF.recruitment_needs(g('growth'), self.i('months')))
        if what == 'compare':
            return Response(WF.compare(request.query_params.get('by', 'branch'), request.user))
        return Response({'detail': tr('Unknown request.')}, status=400)


class AskView(Base):
    need = WORK

    def post(self, request):
        q = (request.data.get('question') or '').strip()
        if not q:
            return Response({'detail': tr('Ask a question.')}, status=400)
        got = WF.answer(q, request.user)
        if got is None:
            return Response({'answer': tr('I can forecast headcount and salary budgets, show turnover and absence hot-spots, estimate hiring needs and compare costs by branch, department or company. '
                                          'Try: *Salary budget for the next 6 months with 5% growth* or *Which departments have high turnover?*'), 'kind': ''})
        md, data, kind = got
        self.log('ask', '', q[:120], {'kind': kind})
        return Response({'answer': md, 'data': data, 'kind': kind})


class LogView(Base):
    def get(self, request):
        rows = AILog.objects.select_related('by')[:100]
        return Response([{'id': r.id, 'kind': r.kind, 'ref': r.ref, 'by': getattr(r.by, 'username', ''), 'at': r.at, 'summary': r.summary} for r in rows])


class LookupsView(Base):
    """Pickers of the AI assistants page: employees (HR: all, a manager: their reports), branches, departments, open jobs, shifts."""
    def get(self, request):
        E = M('EmpManagement', 'emp_master')
        q = E.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_desgntn_id')
        if not (self.c.admin or self.can(PERF | PAYROLL | ATTEND | WORK)):
            q = q.filter(emp_reporting_manager=request.user)
        if self.c.branches is not None:
            q = q.filter(emp_branch_id__in=self.c.branches)
        emps = [{'id': e.id, 'code': e.emp_code, 'name': f'{e.emp_first_name} {e.emp_last_name or ""}'.strip(), 'department': getattr(e.emp_dept_id, 'dept_name', '') if e.emp_dept_id_id else '',
                 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') if e.emp_desgntn_id_id else '', 'branch_id': e.emp_branch_id_id, 'department_id': e.emp_dept_id_id} for e in q.order_by('emp_first_name')[:500]]
        B = M('OrganisationManager', 'brnch_mstr')
        bq = B.objects.filter(br_is_active=True) if self.c.branches is None else B.objects.filter(pk__in=self.c.branches)
        branches = [{'id': b.id, 'name': b.branch_name} for b in bq.order_by('branch_name')]
        depts = [{'id': d.id, 'name': d.dept_name} for d in M('OrganisationManager', 'dept_master').objects.order_by('dept_name')]
        desg = [{'id': d.id, 'name': d.desgntn_job_title} for d in M('OrganisationManager', 'desgntn_master').objects.order_by('desgntn_job_title')]
        jobs = [{'id': j.id, 'title': f'{j.job_code} – {j.title}', 'status': j.status} for j in M('RecruitmentManagement', 'JobOpening').objects.exclude(status__in=('closed', 'cancelled')).order_by('-created_at')[:50]]
        shifts = [{'id': s.id, 'name': s.name} for s in M('calendars', 'Shift').objects.all()[:50]]
        return Response({'employees': emps, 'branches': branches, 'departments': depts, 'designations': desg, 'jobs': jobs, 'shifts': shifts,
                         'rights': {'payroll': self.can(PAYROLL), 'attendance': self.can(ATTEND) or self.is_manager(), 'recruitment': self.can(RECRUIT), 'performance': self.can(PERF) or self.is_manager(), 'workforce': self.can(WORK) or self.is_manager(), 'admin': self.c.admin}})
