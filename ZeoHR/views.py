"""ZEO HR operations – the screens that are not plain registers: the document expiry hub, signing an asset handover,
the employee's exit interview and show-cause notices, and the printed documents (case letter, handover, exit summary)."""
import base64
import io
import logging

from django.core.files.base import ContentFile
from django.http import Http404, HttpResponse
from rest_framework.response import Response

from zeo.appkit.engine import AppError, In, d, emp_name, now, today, user_name
from zeo.appkit.views import Base, guard

from . import catalogue as CAT
from .app import PRO, VIEW, app, finish_handover, hr, hub, me, start_renewal
from .models import AssetHandover, DisciplinaryCase, ExitInterview

log = logging.getLogger(__name__)


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))[:60]


# ------------------------------------------------------------------ PDF helper
class Doc:
    """A plain A4 document: heading, key / value lines, paragraphs, tables, a signature image."""

    def __init__(self, title):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        try:
            from Translations.pdf import fonts, shape
            self.reg, self.bold = fonts()
            self.shape = shape
        except Exception:
            self.reg, self.bold, self.shape = 'Helvetica', 'Helvetica-Bold', (lambda t: t)
        self.mm, self.buf = mm, io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.w, self.h = A4
        self.x, self.y = 20 * mm, self.h - 20 * mm
        try:
            from ZeoApps.services import company
            co = company()
            name = getattr(co, 'name', '') or getattr(co, 'company_name', '') or ''
        except Exception:
            name = ''
        if name:
            self.c.setFont(self.bold, 10)
            self.c.setFillColorRGB(0.35, 0.33, 0.6)
            self.c.drawString(self.x, self.y, self.shape(name))
            self.c.setFillColorRGB(0, 0, 0)
            self.y -= 9 * mm
        self.c.setFont(self.bold, 15)
        self.c.drawString(self.x, self.y, self.shape(title))
        self.y -= 10 * mm

    def need(self, h):
        if self.y - h < 20 * self.mm:
            self.c.showPage()
            self.y = self.h - 20 * self.mm

    def kv(self, rows):
        for k, v in rows:
            if v in (None, ''):
                continue
            self.need(7 * self.mm)
            self.c.setFont(self.bold, 10)
            self.c.drawString(self.x, self.y, self.shape(str(k)))
            self.c.setFont(self.reg, 10)
            self.c.drawString(self.x + 45 * self.mm, self.y, self.shape(str(v))[:95])
            self.y -= 6 * self.mm
        self.y -= 3 * self.mm

    def head(self, text):
        self.need(12 * self.mm)
        self.c.setFont(self.bold, 11.5)
        self.c.drawString(self.x, self.y, self.shape(text))
        self.y -= 7 * self.mm

    def para(self, text, size=10):
        width = self.w - 40 * self.mm
        for block in str(text or '').split('\n'):
            line = ''
            for wd in block.split(' ') + ['\n']:
                if wd == '\n' or self.c.stringWidth(line + ' ' + wd, self.reg, size) > width:
                    self.need(6 * self.mm)
                    self.c.setFont(self.reg, size)
                    self.c.drawString(self.x, self.y, self.shape(line.strip()))
                    self.y -= 5.2 * self.mm
                    line = wd if wd != '\n' else ''
                else:
                    line += ' ' + wd
        self.y -= 2 * self.mm

    def table(self, cols, rows, widths):
        mm = self.mm
        self.need(10 * mm)
        xx = self.x
        self.c.setFont(self.bold, 9.5)
        for cname, wdt in zip(cols, widths):
            self.c.drawString(xx, self.y, self.shape(cname))
            xx += wdt * mm
        self.y -= 2 * mm
        self.c.line(self.x, self.y, self.w - 20 * mm, self.y)
        self.y -= 5 * mm
        self.c.setFont(self.reg, 9.5)
        for r in rows:
            self.need(7 * mm)
            xx = self.x
            for v, wdt in zip(r, widths):
                s = self.shape(str(v or ''))
                while s and self.c.stringWidth(s, self.reg, 9.5) > (wdt - 2) * mm:
                    s = s[:-1]
                self.c.drawString(xx, self.y, s)
                xx += wdt * mm
            self.y -= 6 * mm
        self.y -= 3 * mm

    def signature(self, sig, label):
        mm = self.mm
        self.need(40 * mm)
        self.c.setFont(self.bold, 10)
        self.c.drawString(self.x, self.y, self.shape(label))
        self.y -= 4 * mm
        if sig and sig.startswith('data:image'):
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(io.BytesIO(base64.b64decode(sig.split(',', 1)[1])))
                self.c.drawImage(img, self.x, self.y - 26 * mm, width=65 * mm, height=26 * mm, preserveAspectRatio=True, mask='auto')
                self.y -= 30 * mm
                return
            except Exception:
                log.exception('signature image')
        if sig:
            self.c.setFont('Helvetica-Oblique', 22)
            self.c.drawString(self.x, self.y - 10 * mm, (sig[6:] if sig.startswith('typed:') else sig)[:40])
            self.y -= 18 * mm
        else:
            self.y -= 14 * mm
            self.c.line(self.x, self.y, self.x + 70 * mm, self.y)
            self.y -= 6 * mm

    def bytes(self):
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def case_pdf(o):
    letter = 'Decision' if o.decision else 'Show-cause notice' if o.show_cause_text else 'Case summary'
    p = Doc(f'{letter} – {o.number}')
    p.kv([('Employee', f'{emp_name(o.employee)} ({o.employee.emp_code})'), ('Date', d(o.decision_date or o.show_cause_sent or today())),
          ('Subject', o.title), ('Category', o.get_category_display()), ('Incident date', d(o.incident_date))])
    if o.decision:
        p.head('Decision')
        extra = f" – {o.fine_days:g} days' wage" if o.decision == 'fine' and o.fine_days else f' – {o.suspension_days} days' if o.decision == 'suspension' else ''
        p.para(f'{o.get_decision_display()}{extra}, effective {d(o.effective_date or o.decision_date)}.')
        if o.decision_notes:
            p.para(o.decision_notes)
        p.para('You may appeal against this decision in writing to Human Resources.')
        if o.appeal_outcome:
            p.head('Appeal')
            p.para(f'{o.get_appeal_outcome_display()}. {o.appeal_notes}')
    elif o.show_cause_text:
        p.para(o.show_cause_text)
    else:
        p.para(o.description)
    p.signature('', 'Human Resources')
    p.signature('', 'Received by the employee (name, signature, date)')
    return p.bytes()


def handover_pdf(o):
    p = Doc(f'Asset {"handover" if o.kind == "issue" else "return"} – {o.number}')
    p.kv([('Employee', f'{emp_name(o.employee)} ({o.employee.emp_code})'), ('Date', d(o.date)),
          ('Department', str(o.employee.emp_dept_id) if o.employee.emp_dept_id_id else ''), ('Status', o.get_status_display())])
    p.table(['Asset', 'Serial / tag', 'Condition', 'Accessories', 'Note'],
            [[i.get('asset'), i.get('serial'), (i.get('condition') or '').title(), i.get('accessories'), i.get('note')] for i in (o.items or [])],
            [55, 32, 22, 36, 25])
    if o.damage_charge:
        p.kv([('Charge for damage / loss', f'{o.damage_charge:,.2f}')])
    p.head('Terms')
    p.para(o.terms)
    p.signature(o.signature, f'Employee: {o.signed_by_name or emp_name(o.employee)}' + (f' – signed {d(o.signed_at)}' if o.signed_at else ''))
    if o.witnessed_by_id:
        p.kv([('Signed in front of', user_name(o.witnessed_by))])
    if o.audit:
        p.head('Audit trail')
        for a in o.audit[-10:]:
            p.para(f'{str(a.get("at", ""))[:19].replace("T", " ")}  {a.get("what", "")}  {a.get("by", "")}  {a.get("ip", "")}', 8.5)
    return p.bytes()


def exit_pdf(o):
    p = Doc(f'Exit interview – {o.number}')
    p.kv([('Employee', f'{emp_name(o.employee)} ({o.employee.emp_code})'), ('Last working day', d(o.last_working_date)),
          ('Main reason', o.get_reason_display() if o.reason else ''), ('Would rehire', o.get_rehire_display() if o.rehire else ''),
          ('Would recommend (0–10)', o.recommend if o.recommend is not None else ''), ('Answered', d(o.answered_at))])
    for i, q in enumerate(o.questions or []):
        a = (o.answers or [])[i] if i < len(o.answers or []) else ''
        p.head(f'{i + 1}. {q.get("text", "")}')
        p.para('Yes' if a is True else 'No' if a is False else str(a if a not in (None, '') else '–'))
    return p.bytes()


class PdfView(Base):
    @guard
    def get(self, request, kind, pk):
        r = self.app.regs.get(kind)
        if r is None or not r.pdf:
            raise Http404
        o = r.scope(request).filter(pk=pk).first()
        if o is None:
            raise Http404
        if kind == 'handover' and o.signed_document and request.query_params.get('signed') != '0':
            data = o.signed_document.open('rb').read()
        else:
            data = None
            try:   # v1.62.0 print – the company's default design of the warning letter / asset handover form
                from PrintTemplates import hooks as _ph
                data = {'case': _ph.warning_pdf, 'handover': _ph.handover_pdf}.get(kind, lambda x: None)(o)
            except ImportError:
                pass
            data = data or {'case': case_pdf, 'handover': handover_pdf, 'exit_interview': exit_pdf}[kind](o)
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{o.number}.pdf"'
        return resp


# ------------------------------------------------------------------ document expiry hub
class HubView(Base):
    @guard
    def get(self, request):
        if not (hr(request) or app.any(request, *PRO) or app.any(request, *VIEW)):
            raise AppError('Only HR and the PRO see the document expiry hub.', status=403)
        days = In(request).i('days') or 90
        return Response(hub(request, min(max(days, 7), 365)))

    @guard
    def post(self, request):
        """{rows: [{employee_id, type, number, expiry, source, _id}]} – start the renewals of the selected documents."""
        if not app.any(request, *PRO):
            raise AppError('Only HR and the PRO start renewals.', status=403)
        from AccessControl.access import scope_queryset
        from EmpManagement.models import emp_master

        class _V:
            zeo_scope = True
        allowed = set(scope_queryset(request, _V(), emp_master.objects.all()).values_list('id', flat=True))
        made = []
        from UserManagement.models import CustomUser
        assign = CustomUser.objects.filter(pk=request.data.get('assigned_to')).first() if request.data.get('assigned_to') else None
        for x in request.data.get('rows') or []:
            if x.get('employee_id') not in allowed or not x.get('type'):
                continue
            exp = x.get('expiry')
            from django.utils.dateparse import parse_date
            row = {'type': str(x['type'])[:60], 'number': str(x.get('number') or ''), 'expiry': parse_date(str(exp)) if exp else None,
                   'source': x.get('source') or '', '_id': x.get('_id')}
            r, created = start_renewal(x['employee_id'], row, request.user, assign)
            made.append({'id': r.pk, 'number': r.number, 'created': created})
        return Response({'renewals': made, 'created': sum(1 for m in made if m['created'])})


# ------------------------------------------------------------------ asset handover signing
def ho_payload(o, request):
    lines = '\n'.join(f'• {i.get("asset", "")}' + (f' ({i.get("serial")})' if i.get('serial') else '') + f' – {(i.get("condition") or "").title()}'
                      + (f', {i.get("accessories")}' if i.get('accessories') else '') for i in (o.items or []))
    return {'id': o.pk, 'number': o.number, 'title': f'Asset {"handover" if o.kind == "issue" else "return"} {o.number}', 'kind': o.get_kind_display(),
            'status': o.status, 'status_label': o.get_status_display(), 'text': f'{lines}\n\n{o.terms}', 'message': o.notes,
            'signer': emp_name(o.employee), 'has_document': False, 'otp_needed': False, 'expires_at': '', 'signed_at': d(o.signed_at),
            'signed_file': bool(o.signed_document), 'audit': o.audit or [], 'witness': hr(request) and not (me(request) and me(request).pk == o.employee_id)}


def _ho(request, pk):
    o = AssetHandover.objects.filter(pk=pk).select_related('employee').first()
    e = me(request)
    if o is None or not (hr(request) or app.any(request, *VIEW) or (e and o.employee_id == e.pk)):
        raise Http404
    return o


class HandoverSignView(Base):
    """GET the handover to sign; POST {signature} (the employee, or HR with the employee in front of them); POST {decline}."""
    @guard
    def get(self, request, pk):
        return Response(ho_payload(_ho(request, pk), request))

    @guard
    def post(self, request, pk):
        o = _ho(request, pk)
        e = me(request)
        mine = bool(e and o.employee_id == e.pk)
        if not mine and not hr(request):
            raise AppError('Only the employee (or HR with the employee present) signs.', status=403)
        f = In(request)
        if o.status != 'sent':
            raise AppError('This handover is not waiting for a signature.')
        if f.s('decline'):
            o.status = 'draft'
            o.audit = (o.audit or []) + [{'at': now().isoformat(), 'what': 'declined', 'by': user_name(request.user), 'note': f.s('decline')[:200]}]
            o.save()
            app.event('handover', o.pk, f'Declined by the employee – {f.s("decline")[:200]}', request.user)
            app.close_todos('handover', o.pk)
            if o.created_by_id:
                app.todo(o.created_by, f'Handover {o.number} declined', 'handover', o.pk, o.number, app.page + f'r/handover/{o.pk}', f.s('decline'))
            return Response(ho_payload(o, request))
        sig = f.s('signature')
        if not sig:
            raise AppError('Draw or type the signature.', 'signature')
        o.signature = sig[:200000]
        o.status, o.signed_at, o.signed_ip = 'signed', now(), _ip(request)
        o.signed_by_name = emp_name(o.employee)
        if not mine:
            o.witnessed_by = request.user
        o.audit = (o.audit or []) + [{'at': now().isoformat(), 'what': 'signed' + ('' if mine else ' in front of HR'), 'by': user_name(request.user), 'ip': o.signed_ip}]
        o.save()
        try:
            o.signed_document.save(f'{o.number}-signed.pdf', ContentFile(handover_pdf(o)), save=True)
        except Exception:
            log.exception('handover pdf')
        finish_handover(o)
        app.event('handover', o.pk, 'Signed' + ('' if mine else f' in front of {user_name(request.user)}'), request.user)
        app.close_todos('handover', o.pk)
        return Response(ho_payload(o, request))


class HandoverDocumentView(Base):
    @guard
    def get(self, request, pk):
        o = _ho(request, pk)
        data = o.signed_document.open('rb').read() if o.signed_document else handover_pdf(o)
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{o.number}-signed.pdf"'
        return resp


# ------------------------------------------------------------------ exit interview (the employee answers)
class ExitTakeView(Base):
    @guard
    def get(self, request, pk):
        o = ExitInterview.objects.filter(pk=pk).first()
        e = me(request)
        if o is None or not ((e and o.employee_id == e.pk) or hr(request)):
            raise Http404
        return Response({'id': o.pk, 'number': o.number, 'employee': emp_name(o.employee), 'status': o.status, 'status_label': o.get_status_display(),
                         'last_working_date': o.last_working_date, 'questions': o.questions or [], 'answers': o.answers or [],
                         'can_answer': bool(e and o.employee_id == e.pk and o.status in ('planned', 'sent'))})

    @guard
    def post(self, request, pk):
        o = ExitInterview.objects.filter(pk=pk).first()
        e = me(request)
        if o is None or not (e and o.employee_id == e.pk):
            raise Http404
        if o.status not in ('planned', 'sent'):
            raise AppError('This exit interview is already answered.')
        ans = request.data.get('answers') or []
        qs = o.questions or []
        if not isinstance(ans, list) or len(ans) != len(qs):
            raise AppError('Answer the questions.')
        for i, q in enumerate(qs):
            if q.get('required') and ans[i] in (None, '', []):
                raise AppError(f'Question {i + 1} needs an answer.', f'q{i}')
            if q.get('type') in ('rating',) and ans[i] not in (None, '') and not (1 <= int(ans[i]) <= 5):
                raise AppError(f'Question {i + 1}: 1 to 5.', f'q{i}')
            if q.get('type') == 'nps' and ans[i] not in (None, '') and not (0 <= int(ans[i]) <= 10):
                raise AppError(f'Question {i + 1}: 0 to 10.', f'q{i}')
        o.answers = ans
        for i, q in enumerate(qs):
            if q.get('type') == 'choice' and 'reason' in q.get('text', '').lower() and not o.reason:
                o.reason = CAT.REASON_OF.get(ans[i], 'other' if ans[i] else '')
            if q.get('type') == 'nps' and ans[i] not in (None, ''):
                o.recommend = int(ans[i])
            if q.get('type') == 'yesno' and 'work for us again' in q.get('text', '').lower() and ans[i] is not None and not o.rehire:
                o.rehire = 'yes' if ans[i] else 'no'
        o.status, o.answered_at = 'answered', now()
        o.save()
        app.event('exit_interview', o.pk, 'Answered by the employee', request.user)
        app.close_todos('exit_interview', o.pk)
        if o.interviewer_id:
            app.todo(o.interviewer, f'Exit interview answered: {emp_name(o.employee)}', 'exit_interview', o.pk, o.number, app.page + f'r/exit_interview/{o.pk}')
        return Response({'ok': True, 'status': o.status})


# ------------------------------------------------------------------ show-cause notices and decisions (the employee)
class MyCasesView(Base):
    """The employee's cases once a notice has been sent: the notice, the hearing, the decision – and the written reply."""
    needs_activation = False

    def _mine(self, request):
        e = me(request)
        if e is None:
            return DisciplinaryCase.objects.none()
        return DisciplinaryCase.objects.filter(employee=e).exclude(status__in=('reported', 'investigation', 'withdrawn')).exclude(show_cause_sent__isnull=True, decision='')

    @guard
    def get(self, request):
        return Response([{'id': c.pk, 'number': c.number, 'title': c.title, 'status': c.status, 'status_label': c.get_status_display(),
                          'show_cause': c.show_cause_text, 'sent': c.show_cause_sent, 'response_due': c.response_due, 'response': c.employee_response,
                          'can_reply': c.status == 'show_cause' and not c.response_received, 'hearing_at': c.hearing_at, 'hearing_place': c.hearing_place,
                          'decision': c.get_decision_display() if c.decision else '', 'decision_date': c.decision_date, 'decision_notes': c.decision_notes,
                          'appeal_outcome': c.get_appeal_outcome_display() if c.appeal_outcome else '', 'can_appeal': c.status == 'decided' and not c.appeal_text}
                         for c in self._mine(request)])

    @guard
    def post(self, request):
        c = self._mine(request).filter(pk=request.data.get('id')).first()
        if c is None:
            raise Http404
        f = In(request)
        if f.s('reply'):
            if c.status != 'show_cause' or c.response_received:
                raise AppError('This notice is not waiting for your reply.')
            c.employee_response, c.response_received = f.s('reply')[:10000], today()
            c.save(update_fields=['employee_response', 'response_received'])
            app.event('case', c.pk, 'Reply received from the employee', request.user)
            app.close_todos('case', c.pk)
            for u in ([c.investigator] if c.investigator_id else []) + app.users_with_role('hro_hr')[:3]:
                app.todo(u, f'Show-cause reply received: {c.number}', 'case', c.pk, c.number, app.page + f'r/case/{c.pk}')
        elif f.s('appeal'):
            if c.status != 'decided' or c.appeal_text:
                raise AppError('This decision cannot be appealed here.')
            c.appeal_text, c.appeal_date, c.status = f.s('appeal')[:10000], today(), 'appeal'
            c.save(update_fields=['appeal_text', 'appeal_date', 'status'])
            app.event('case', c.pk, 'Appeal received from the employee', request.user)
            for u in app.users_with_role('hro_manager')[:3]:
                app.todo(u, f'Appeal on {c.number}', 'case', c.pk, c.number, app.page + f'r/case/{c.pk}')
        else:
            raise AppError('Write your reply.')
        return self.get(request)


class ServicesView(Base):
    """The PRO services of the company country (for the New form)."""
    needs_activation = False

    @guard
    def get(self, request):
        c = CAT.company_country()
        return Response({'country': c, 'services': CAT.services(c)})


class MyTicketsView(Base):
    """v1.51.0 Self service → HR help: GET my tickets and the categories; POST create / reply / rate / reopen."""
    needs_activation = False

    @guard
    def get(self, request):
        from .helpdesk import categories, my_json
        from .models import HRTicket
        e = me(request)
        rows = [my_json(o) for o in HRTicket.objects.filter(employee=e).select_related('category', 'assigned_to')[:100]] if e else []
        return Response({'tickets': rows, 'categories': [{'id': c.pk, 'name': c.name, 'description': c.description, 'redirect': c.redirect,
                                                          'response_hours': c.response_hours, 'resolve_hours': c.resolve_hours} for c in categories()]})

    @guard
    def post(self, request):
        from .helpdesk import employee_post, my_json
        e = me(request)
        if e is None:
            raise AppError('Only an employee asks HR here.')
        o = employee_post(e, request.data, request.user)
        return Response(my_json(o), status=201)


# ====================================================================== v1.58.0 assignments and matrix report
class AssignmentsReportView(Base):
    """GET – full-time equivalents by department, assignments ending in the next ?days (30), and the matrix: every employee with
    assignments, the share of the main job and of each assignment. HR team and viewers; their branches only."""
    @guard
    def get(self, request):
        if not (hr(request) or app.any(request, *VIEW)):
            raise AppError('Only HR sees the assignments report.', status=403)
        from datetime import timedelta
        from decimal import Decimal

        from django.db.models import Q

        from zeo.appkit.engine import branch_ids

        from . import matrix as MX
        from Translations.i18n import tr as _tr

        from .models import Assignment, MatrixLink
        b = branch_ids(request)
        days = min(max(In(request).i('days') or 30, 1), 365)
        t = today()
        live = Assignment.objects.filter(status__in=MX.LIVE).select_related('employee', 'employee__emp_dept_id', 'employee__emp_branch_id', 'department',
                                                                           'host_branch', 'project', 'manager', 'designation')
        if b is not None:
            live = live.filter(Q(host_branch_id__in=b) | Q(employee__emp_branch_id__in=b))
        rows = {}
        for a in live:
            e = a.employee
            r = rows.setdefault(e.pk, {'employee_id': e.pk, 'code': e.emp_code, 'name': emp_name(e), 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                                       'branch': str(e.emp_branch_id) if e.emp_branch_id_id else '', 'assignments': [], 'main_share': '100'})
            where = a.project.title if a.project_id else (a.department.dept_name if a.department_id else (a.host_branch.branch_name if a.host_branch_id else ''))
            r['assignments'].append({'id': a.pk, 'number': a.number, 'kind': _tr(a.get_kind_display()), 'where': where, 'percent': MX.pct(a.percent),
                                     'manager': user_name(a.manager) if a.manager_id else '', 'start': a.start_date, 'end': a.end_date, 'status': a.status,
                                     'status_label': a.get_status_display()})
        for eid, r in rows.items():
            r['main_share'] = MX.pct(MX.main_share(eid, t))
        ending = [{'id': a.pk, 'number': a.number, 'employee': emp_name(a.employee), 'kind': _tr(a.get_kind_display()), 'end': a.end_date,
                   'days': (a.end_date - t).days} for a in live.filter(end_date__isnull=False, end_date__gte=t, end_date__lte=t + timedelta(days=days)).order_by('end_date')]
        fte = MX.fte_by_department(t, b)
        links = MatrixLink.objects.select_related('employee', 'functional_manager')
        if b is not None:
            links = links.filter(employee__emp_branch_id__in=b)
        return Response({'date': t, 'days': days, 'fte': fte,
                         'ending': ending, 'matrix': sorted(rows.values(), key=lambda r: r['name'].lower()),
                         'functional': [{'employee': emp_name(m.employee), 'employee_id': m.employee_id, 'manager': user_name(m.functional_manager),
                                         'area': m.area, 'weight': MX.pct(m.review_weight)} for m in links],
                         'totals': {'assignments': live.filter(status='active').count(), 'planned': live.filter(status='planned').count(),
                                    'ending': len(ending), 'functional_managers': links.count()}})
