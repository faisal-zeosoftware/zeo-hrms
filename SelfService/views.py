"""v1.13.0 – Employee self service API (mounted at /self-service/).

Every view requires a logged-in user of the company (AccessControl.ZeoAccess). Rights:
  * employees: their own profile, change requests, letters, complaints, payslips (approved / paid), documents, announcements;
  * HR (change_profilechangerequest or change_emp_master): change-request queue and field policy of their branches;
  * HR letters (change_letterrequest / change_emp_master / change_documentrequest): letter queue, direct issue, templates;
  * grievance officers (handle_grievance) and assigned users: complaints of their branches – never the employee's own manager;
  * announcement read counts: view_announcement / change_announcement.
"""
import json
import mimetypes
import os

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import grievances as G
from . import letters as L
from . import profile as P
from . import services as S
from .models import (GRIEVANCE_CATEGORIES, LETTER_TYPES, ChangeRequestAttachment, Grievance, GrievanceMessage, GrievanceSetting,
                     LetterRequest, LetterTemplate, ProfileChangeRequest)


def deny(msg, code=403):
    return Response({'detail': msg}, status=code)


def bad(exc):
    if isinstance(exc, ValidationError):
        d = exc.message_dict if hasattr(exc, 'error_dict') else {'detail': exc.messages}
        return Response({k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in d.items()}, status=400)
    return Response({'detail': str(exc)}, status=400)


def _file(f, name=None, inline=False):
    if not f or not f.name:
        return deny('There is no file.', 404)
    try:
        fh = f.open('rb')
    except Exception:
        return deny('The file is missing on the server – ask HR to upload it again.', 404)
    name = name or os.path.basename(f.name)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    return FileResponse(fh, as_attachment=not inline, filename=name, content_type=ctype)


def _data(request):
    d = request.data
    if hasattr(d, 'getlist'):
        out = {k: d.get(k) for k in d.keys()}
    else:
        out = dict(d or {})
    if isinstance(out.get('data'), str):
        try:
            out['data'] = json.loads(out['data'])
        except ValueError:
            out['data'] = {}
    return out


def _files(request, key='files'):
    if hasattr(request, 'FILES'):
        return request.FILES.getlist(key) or ([request.FILES[key]] if key in request.FILES else [])
    return []


class Base(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def emp(self, request):
        return S.my_employee(request)


# ================================================================================================ meta / dashboard
class MetaView(Base):
    def get(self, request):
        return Response({
            'groups': [{'key': g['key'], 'label': g['label'], 'read_only': bool(g.get('read_only')), 'record_group': g['key'] in P.RECORD_GROUPS,
                        'fields': [{'key': k, 'label': l, 'type': t, **P.field_meta(g['key'], k)} for k, l, t in g['fields']]}
                       for g in P.groups_definition()],
            'letter_types': [{'value': k, 'label': v, 'needs': list(L.NEEDS.get(k, ()))} for k, v in LETTER_TYPES],
            'complaint_categories': [{'value': k, 'label': v} for k, v in GRIEVANCE_CATEGORIES],
            'placeholders': [{'key': k, 'label': v} for k, v in L.PLACEHOLDERS],
            'employee_profile_installed': P.ep_installed(),
            'rights': {'hr_changes': S.can(request, *S.CR_HR), 'settings': S.can(request, *S.SETTINGS_HR), 'hr_letters': S.can(request, *S.LETTER_HR),
                       'templates': S.can(request, *S.TEMPLATE_HR), 'grievances': S.can(request, *S.GRIEVANCE),
                       'announcements': S.can(request, *S.ANNOUNCE_HR)},
            'has_employee': self.emp(request) is not None,
        })


class OptionsView(Base):
    """Look-up lists for the profile forms: ?source=Core.Nationality etc."""

    def get(self, request):
        rows = P.options(request.GET.get('source') or '')
        if rows is None:
            return deny('Unknown list.', 404)
        return Response(rows)


class DashboardView(Base):
    def get(self, request):
        emp = self.emp(request)
        if emp is None:
            return Response({'detail': 'No employee record is linked to your login.', 'cards': {}})
        return Response(S.dashboard(request, emp))


# ================================================================================================ profile
class MyProfileView(Base):
    def get(self, request):
        emp = self.emp(request)
        if emp is None:
            return deny('No employee record is linked to your login – ask HR to link it.', 404)
        return Response(P.read_profile(emp, masked=True))


class EmployeeProfileHRView(Base):
    def get(self, request, emp_id):
        if not S.can(request, *S.CR_HR):
            return deny('Only HR can open another employee\'s self-service profile.')
        emp = S.employee(emp_id)
        if emp is None or not S.branch_ok(request, emp.emp_branch_id_id):
            return deny('Employee not found in your branches.', 404)
        return Response(P.read_profile(emp, masked=False))


class ProfileChangeView(Base):
    """POST {group, action: create|update|delete, record_id, data: {...}, reason, files[]}"""

    def post(self, request):
        emp = self.emp(request)
        if emp is None:
            return deny('No employee record is linked to your login.', 404)
        d = _data(request)
        rid = d.get('record_id')
        try:
            rid = int(rid) if rid not in (None, '', 'null') else None
        except (TypeError, ValueError):
            return Response({'record_id': 'Invalid record.'}, status=400)
        data = d.get('data') if isinstance(d.get('data'), dict) else {}
        for k, f in request.FILES.items():
            if k != 'files':
                data[k] = f
        try:
            res = S.submit_change(request, emp, d.get('group'), d.get('action') or 'update', rid, data, d.get('reason') or '', _files(request))
        except ValidationError as exc:
            return bad(exc)
        req = res['request']
        msg = []
        if res['applied']:
            msg.append('Saved.')
        if req:
            msg.append(f'Your request {req.number} was sent to HR for approval.')
        return Response({'applied': res['applied'], 'record_id': res.get('record_id'), 'request': S.change_json(req) if req else None,
                         'message': ' '.join(msg)}, status=201 if req else 200)


class MyChangeRequestsView(Base):
    def get(self, request):
        emp = self.emp(request)
        if emp is None:
            return Response([])
        return Response([S.change_json(r) for r in ProfileChangeRequest.objects.filter(employee_id=emp.pk)])


class ChangeRequestsView(Base):
    """HR queue."""

    def get(self, request):
        if not S.can(request, *S.CR_HR):
            return deny('Only HR can review profile change requests.')
        qs = ProfileChangeRequest.objects.filter(S.branch_q(request))
        st = request.GET.get('status')
        if st:
            qs = qs.filter(status=st)
        if request.GET.get('employee'):
            qs = qs.filter(employee_id=request.GET['employee'])
        return Response([S.change_json(r, hr=True) for r in qs[:500]])


def _cr_for(request, pk):
    r = ProfileChangeRequest.objects.filter(pk=pk).first()
    if r is None:
        return None, False
    emp = S.my_employee(request)
    own = emp is not None and r.employee_id == emp.pk
    hr = S.can(request, *S.CR_HR) and S.branch_ok(request, r.branch_id)
    if not own and not hr:
        return None, False
    return r, hr


class ChangeRequestView(Base):
    def get(self, request, pk):
        r, hr = _cr_for(request, pk)
        if r is None:
            return deny('Request not found.', 404)
        return Response(S.change_json(r, hr=hr))


class ChangeRequestActionView(Base):
    def post(self, request, pk, act):
        r, hr = _cr_for(request, pk)
        if r is None:
            return deny('Request not found.', 404)
        emp = S.my_employee(request)
        try:
            if act == 'withdraw':
                if emp is None or r.employee_id != emp.pk:
                    return deny('Only the employee who asked can withdraw the request.')
                if r.status != 'pending':
                    return Response({'status': f'This request is already {r.get_status_display().lower()}.'}, status=400)
                r.status, r.decided_at = 'withdrawn', timezone.now()
                r.save()
                return Response(S.change_json(r))
            if act in ('approve', 'reject'):
                if not hr:
                    return deny('Only HR of the employee\'s branch can approve or reject this request.')
                S.decide_change(request, r, act == 'approve', (request.data or {}).get('note') or (request.data or {}).get('decision_note') or '')
                return Response(S.change_json(r, hr=True))
        except ValidationError as exc:
            return bad(exc)
        return deny('Unknown action.', 404)


class ChangeRequestFileView(Base):
    def get(self, request, pk, aid):
        r, hr = _cr_for(request, pk)
        if r is None:
            return deny('Request not found.', 404)
        a = ChangeRequestAttachment.objects.filter(request=r, pk=aid).first()
        if a is None:
            return deny('File not found.', 404)
        return _file(a.file, a.name or None)


class PolicySettingsView(Base):
    def get(self, request):
        return Response(P.policies())

    def put(self, request):
        if not S.can(request, *S.SETTINGS_HR):
            return deny('Only HR can change the self-service field settings.')
        rows = request.data if isinstance(request.data, list) else (request.data or {}).get('rows') or []
        saved, errors = S.save_policies(rows, request.user)
        if errors:
            return Response({'saved': saved, 'errors': errors, 'detail': 'Some rows were not saved: ' + ' '.join(errors)}, status=400)
        return Response({'saved': saved, 'rows': P.policies()})


# ================================================================================================ letters
def letter_json(lr, hr=False):
    out = {'id': lr.id, 'number': lr.number, 'letter_type': lr.letter_type, 'letter_label': dict(LETTER_TYPES).get(lr.letter_type, lr.letter_type),
           'language': lr.language, 'addressee': lr.addressee, 'purpose': lr.purpose, 'bank_name': lr.bank_name, 'destination': lr.destination,
           'travel_from': lr.travel_from, 'travel_to': lr.travel_to, 'status': lr.status, 'status_label': lr.get_status_display(),
           'issued_directly': lr.issued_directly, 'reference_no': lr.reference_no, 'verification_code': lr.verification_code if lr.status == 'issued' else '',
           'has_pdf': bool(lr.pdf), 'decided_by': S.uname(lr.decided_by_id), 'decided_at': lr.decided_at, 'decision_note': lr.decision_note,
           'created_at': lr.created_at}
    if hr:
        out['employee_id'] = lr.employee_id
        out['employee'] = P.person(S.employee(lr.employee_id))
    return out


def _letter_from(data, emp, user, direct=False):
    t, lang = L.validate_request(data)
    return LetterRequest(number=S.next_number(LetterRequest, 'LTR'), employee_id=emp.pk, branch_id=emp.emp_branch_id_id, letter_type=t, language=lang,
                         addressee=(data.get('addressee') or '')[:300], purpose=(data.get('purpose') or '')[:500], bank_name=(data.get('bank_name') or '')[:150],
                         destination=(data.get('destination') or '')[:150], travel_from=parse_date(str(data.get('travel_from') or '')) if data.get('travel_from') else None,
                         travel_to=parse_date(str(data.get('travel_to') or '')) if data.get('travel_to') else None, issued_directly=direct, created_by_id=user.pk)


class MyLettersView(Base):
    def get(self, request):
        emp = self.emp(request)
        return Response([letter_json(x) for x in LetterRequest.objects.filter(employee_id=emp.pk)] if emp else [])

    def post(self, request):
        emp = self.emp(request)
        if emp is None:
            return deny('No employee record is linked to your login.', 404)
        try:
            lr = _letter_from(_data(request), emp, request.user)
        except ValidationError as exc:
            return bad(exc)
        lr.save()
        for u in S.users_with(emp.emp_branch_id_id, S.LETTER_HR, exclude=(request.user.pk,)):
            S.notify(user=u, title='HR letter requested', message=f'{P.person(emp)} asked for a {lr.get_letter_type_display().lower()} ({lr.number}).')
        return Response(letter_json(lr), status=201)


class LettersHRView(Base):
    def get(self, request):
        if not S.can(request, *S.LETTER_HR):
            return deny('Only HR can see letter requests.')
        qs = LetterRequest.objects.filter(S.branch_q(request))
        if request.GET.get('status'):
            qs = qs.filter(status=request.GET['status'])
        return Response([letter_json(x, hr=True) for x in qs[:500]])


class LetterIssueView(Base):
    """HR issues a letter directly: {employee_id, letter_type, language, addressee, purpose, ...}"""

    def post(self, request):
        if not S.can(request, *S.LETTER_HR):
            return deny('Only HR can issue letters.')
        d = _data(request)
        emp = S.employee(d.get('employee_id') or d.get('employee') or 0)
        if emp is None or not S.branch_ok(request, emp.emp_branch_id_id):
            return Response({'employee_id': 'Choose an employee of your branches.'}, status=400)
        lr = None
        try:
            lr = _letter_from(d, emp, request.user, direct=True)
            lr.save()
            L.issue(lr, request.user, d.get('note') or '')
        except ValidationError as exc:
            if lr is not None and lr.pk:
                lr.delete()
            return bad(exc)
        S.notify(employee=emp, title='HR letter ready', message=f'Your {lr.get_letter_type_display().lower()} ({lr.reference_no}) is ready in My letters.')
        return Response(letter_json(lr, hr=True), status=201)


def _letter_for(request, pk):
    lr = LetterRequest.objects.filter(pk=pk).first()
    if lr is None:
        return None, False
    emp = S.my_employee(request)
    own = emp is not None and lr.employee_id == emp.pk
    hr = S.can(request, *S.LETTER_HR) and S.branch_ok(request, lr.branch_id)
    return (lr, hr) if (own or hr) else (None, False)


class LetterActionView(Base):
    def post(self, request, pk, act):
        lr, hr = _letter_for(request, pk)
        if lr is None:
            return deny('Letter not found.', 404)
        emp = S.my_employee(request)
        note = (request.data or {}).get('note') or ''
        if act == 'withdraw':
            if emp is None or lr.employee_id != emp.pk:
                return deny('Only the employee who asked can withdraw the request.')
            if lr.status != 'pending':
                return Response({'status': f'This letter is already {lr.get_status_display().lower()}.'}, status=400)
            lr.status = 'withdrawn'
            lr.save()
            return Response(letter_json(lr))
        if act in ('approve', 'reject'):
            if not hr:
                return deny('Only HR of the employee\'s branch can approve or reject letters.')
            if emp is not None and lr.employee_id == emp.pk:
                return deny('You cannot approve your own letter request.')
            if lr.status != 'pending':
                return Response({'status': f'This letter is already {lr.get_status_display().lower()}.'}, status=400)
            if act == 'reject':
                if not note.strip():
                    return Response({'note': 'Give the reason for rejecting.'}, status=400)
                lr.status, lr.decided_by_id, lr.decided_at, lr.decision_note = 'rejected', request.user.pk, timezone.now(), note[:500]
                lr.save()
                S.notify(employee=S.employee(lr.employee_id), title='HR letter rejected', message=f'Your letter request {lr.number} was rejected: {note}')
                return Response(letter_json(lr, hr=True))
            try:
                L.issue(lr, request.user, note)
            except ValidationError as exc:
                return bad(exc)
            S.notify(employee=S.employee(lr.employee_id), title='HR letter ready', message=f'Your {lr.get_letter_type_display().lower()} ({lr.reference_no}) is ready in My letters.')
            return Response(letter_json(lr, hr=True))
        return deny('Unknown action.', 404)


class LetterPdfView(Base):
    def get(self, request, pk):
        lr, hr = _letter_for(request, pk)
        if lr is None:
            return deny('Letter not found.', 404)
        if lr.status != 'issued' or not lr.pdf:
            return deny('The letter is not ready yet.', 404)
        return _file(lr.pdf, f'{lr.reference_no}.pdf', inline=request.GET.get('inline') == '1')


class LetterVerifyView(Base):
    def get(self, request):
        code = (request.GET.get('code') or '').strip().upper()
        if len(code) < 6:
            return Response({'code': 'Enter the verification code printed at the bottom of the letter.'}, status=400)
        lr = LetterRequest.objects.filter(verification_code=code, status='issued').first()
        if lr is None:
            return Response({'valid': False, 'detail': 'No letter was issued with this code.'})
        emp = S.employee(lr.employee_id)
        return Response({'valid': True, 'reference_no': lr.reference_no, 'letter': lr.get_letter_type_display(), 'employee': P.person(emp),
                         'issued_at': lr.decided_at, 'addressee': lr.addressee})


class LetterTemplatesView(Base):
    def get(self, request):
        if not S.can(request, *S.TEMPLATE_HR, *S.LETTER_HR):
            return deny('Only HR can see letter templates.')
        L.ensure_templates()
        return Response([_tpl_json(t) for t in LetterTemplate.objects.all()])

    def post(self, request):
        if not S.can(request, *S.TEMPLATE_HR):
            return deny('Only HR can change letter templates.')
        t = LetterTemplate()
        return _save_tpl(request, t, created=True)


def _tpl_json(t):
    return {'id': t.id, 'letter_type': t.letter_type, 'letter_label': t.get_letter_type_display(), 'language': t.language, 'branch_id': t.branch_id,
            'title': t.title, 'body': t.body, 'footer': t.footer, 'signatory_name': t.signatory_name, 'signatory_title': t.signatory_title,
            'show_salary': t.show_salary, 'is_active': t.is_active, 'updated_at': t.updated_at}


def _save_tpl(request, t, created=False):
    d = _data(request)
    for k in ('letter_type', 'language', 'title', 'body', 'footer', 'signatory_name', 'signatory_title'):
        if k in d:
            setattr(t, k, d[k] or '')
    for k in ('show_salary', 'is_active'):
        if k in d:
            setattr(t, k, str(d[k]).lower() in ('1', 'true', 'yes', 'on'))
    if 'branch_id' in d:
        t.branch_id = int(d['branch_id']) if d['branch_id'] not in (None, '', 'null') else None
    err = {}
    if t.letter_type not in dict(LETTER_TYPES):
        err['letter_type'] = 'Choose the letter type.'
    if t.language not in ('en', 'ar'):
        err['language'] = 'Choose English or Arabic.'
    if not (t.title or '').strip():
        err['title'] = 'Enter the letter title.'
    if not (t.body or '').strip():
        err['body'] = 'Enter the letter text.'
    import re as _re
    unknown = sorted({m for m in _re.findall(r'\{(\w+)\}', (t.body or '') + (t.title or '') + (t.footer or '')) if m not in dict(L.PLACEHOLDERS)})
    if unknown:
        err['body'] = 'Unknown placeholders: ' + ', '.join('{' + u + '}' for u in unknown) + '. Use the list of placeholders.'
    dup = LetterTemplate.objects.filter(letter_type=t.letter_type, language=t.language, branch_id=t.branch_id).exclude(pk=t.pk)
    if dup.exists():
        err['letter_type'] = 'There is already a template for this letter type, language and branch – edit that one.'
    if err:
        return Response(err, status=400)
    t.updated_by_id = request.user.pk
    t.save()
    return Response(_tpl_json(t), status=201 if created else 200)


class LetterTemplateView(Base):
    def put(self, request, pk):
        if not S.can(request, *S.TEMPLATE_HR):
            return deny('Only HR can change letter templates.')
        t = LetterTemplate.objects.filter(pk=pk).first()
        if t is None:
            return deny('Template not found.', 404)
        return _save_tpl(request, t)

    patch = put

    def delete(self, request, pk):
        if not S.can(request, *S.TEMPLATE_HR):
            return deny('Only HR can change letter templates.')
        LetterTemplate.objects.filter(pk=pk).delete()
        return Response(status=204)


class LetterPreviewView(Base):
    """HR: preview the filled text for an employee: {employee_id, letter_type, language, addressee, purpose…}"""

    def post(self, request):
        if not S.can(request, *S.LETTER_HR, *S.TEMPLATE_HR):
            return deny('Only HR can preview letters.')
        d = _data(request)
        emp = S.employee(d.get('employee_id') or 0)
        if emp is None or not S.branch_ok(request, emp.emp_branch_id_id):
            return Response({'employee_id': 'Choose an employee of your branches.'}, status=400)
        lr = LetterRequest(employee_id=emp.pk, letter_type=d.get('letter_type') or 'salary_certificate', language=d.get('language') or 'en',
                           addressee=d.get('addressee') or '', purpose=d.get('purpose') or '', bank_name=d.get('bank_name') or '', destination=d.get('destination') or '')
        tpl = L.template_for(lr.letter_type, lr.language, emp.emp_branch_id_id)
        if tpl is None:
            return Response({'template': 'No template for this type and language.'}, status=400)
        vals = L.values(emp, lr)
        return Response({'title': L.fill(tpl.title, vals), 'body': L.fill(tpl.body, vals)})


# ================================================================================================ complaints
class MyComplaintsView(Base):
    def get(self, request):
        emp = self.emp(request)
        return Response([G.case_json(c) for c in Grievance.objects.filter(employee_id=emp.pk)] if emp else [])

    def post(self, request):
        emp = self.emp(request)
        if emp is None:
            return deny('No employee record is linked to your login.', 404)
        try:
            case, token = G.submit(request, emp, _data(request), _files(request))
        except ValidationError as exc:
            return bad(exc)
        out = G.case_json(case)
        if token:
            out['token'] = token
            out['message'] = 'Keep this token safe – it is the only way to follow your anonymous complaint. It is not shown again.'
        return Response(out, status=201)


def _case_for(request, pk):
    case = Grievance.objects.filter(pk=pk).first()
    if case is None:
        return None, False, False
    emp = S.my_employee(request)
    own = emp is not None and case.employee_id == emp.pk and not case.anonymous
    handler = G.is_handler(request, case)
    return (case, own, handler) if (own or handler) else (None, False, False)


class ComplaintView(Base):
    def get(self, request, pk):
        case, own, handler = _case_for(request, pk)
        if case is None:
            return deny('Complaint not found.', 404)
        return Response(G.case_json(case, request, handler=handler))


class ComplaintActionView(Base):
    def post(self, request, pk, act):
        case, own, handler = _case_for(request, pk)
        if case is None:
            return deny('Complaint not found.', 404)
        d = _data(request)
        try:
            if act == 'messages':
                f = (_files(request, 'attachment') or [None])[0]
                G.add_message(case, d.get('text') or '', request.user, from_employee=own and not handler, internal=handler and str(d.get('internal')).lower() in ('1', 'true'), attachment=f)
            elif act == 'feedback':
                if not own:
                    return deny('Only the employee who raised the complaint can give feedback.')
                G.feedback(case, d.get('feedback_rating') or d.get('rating'), d.get('feedback_text') or d.get('text') or '')
            elif act == 'status':
                if not handler:
                    return deny('Only the grievance officers handling this case can change its status.')
                G.transition(request, case, d.get('status'), d.get('note') or '', d.get('outcome') or '')
            elif act == 'assign':
                if not handler:
                    return deny('Only the grievance officers handling this case can assign it.')
                ids = [int(x) for x in (d.get('user_ids') or []) if str(x).isdigit()]
                mgr = G.manager_user_id(case)
                if mgr in ids:
                    return Response({'user_ids': "The employee's reporting manager cannot handle this complaint."}, status=400)
                case.assigned_user_ids = ids
                case.save(update_fields=['assigned_user_ids', 'updated_at'])
                GrievanceMessage.objects.create(grievance=case, author_user_id=request.user.pk, internal=True, event='assigned',
                                                text='Assigned to ' + ', '.join(S.uname(i) for i in ids) if ids else 'Assignment cleared.')
            else:
                return deny('Unknown action.', 404)
        except ValidationError as exc:
            return bad(exc)
        case.refresh_from_db()
        return Response(G.case_json(case, request, handler=handler))


class ComplaintFileView(Base):
    def get(self, request, pk, mid):
        case, own, handler = _case_for(request, pk)
        if case is None:
            return deny('Complaint not found.', 404)
        m = case.messages.filter(pk=mid).first()
        if m is None or (m.internal and not handler):
            return deny('File not found.', 404)
        return _file(m.attachment)


class ComplaintTrackView(Base):
    """Anonymous follow-up: POST {token} → case; {token, text} → message; {token, feedback_rating} → feedback."""

    def post(self, request):
        d = _data(request)
        tok = (d.get('token') or '').strip()
        case = Grievance.objects.filter(anonymous=True, token_hash=G.token_hash(tok)).first() if tok else None
        if case is None:
            return Response({'token': 'No complaint matches this token – check that you copied it completely.'}, status=404)
        try:
            if d.get('text') or _files(request, 'attachment'):
                G.add_message(case, d.get('text') or '', None, from_employee=True, attachment=(_files(request, 'attachment') or [None])[0])
            if d.get('feedback_rating'):
                G.feedback(case, d.get('feedback_rating'), d.get('feedback_text') or '')
        except ValidationError as exc:
            return bad(exc)
        case.refresh_from_db()
        return Response(G.case_json(case))


class ComplaintsHandlerView(Base):
    def get(self, request):
        if not S.can(request, *S.GRIEVANCE) and not Grievance.objects.filter(assigned_user_ids__contains=[request.user.pk]).exists():
            return deny('Only grievance officers can see complaints.')
        qs = G.handler_cases(request)
        if request.GET.get('status'):
            qs = qs.filter(status=request.GET['status'])
        return Response([G.case_json(c, request, handler=True) for c in qs[:500]])


class ComplaintSettingsView(Base):
    def get(self, request):
        if not S.can(request, *S.GRIEVANCE):
            return deny('Only grievance officers can see these settings.')
        rows = []
        for k, label in GRIEVANCE_CATEGORIES:
            s = G.settings_for(k)
            rows.append({'category': k, 'label': label, 'sla_days': s.sla_days, 'acknowledge_days': s.acknowledge_days,
                         'officer_user_ids': s.officer_user_ids, 'escalate_to_user_ids': s.escalate_to_user_ids})
        handlers = []
        try:
            handlers = [{'id': u.pk, 'name': S.uname(u.pk)} for u in S.users_with(None, ('handle_grievance',))]
        except Exception:
            pass
        return Response({'categories': rows, 'handlers': handlers})

    def put(self, request):
        if not (S.can(request, *S.GRIEVANCE) and S.can(request, 'change_grievancesetting', 'change_emp_master')):
            return deny('Only HR can change complaint settings.')
        rows = request.data if isinstance(request.data, list) else (request.data or {}).get('categories') or []
        for r in rows:
            if r.get('category') not in dict(GRIEVANCE_CATEGORIES):
                continue
            try:
                sla = max(1, int(r.get('sla_days') or 10))
                ack = max(1, int(r.get('acknowledge_days') or 2))
            except (TypeError, ValueError):
                return Response({'sla_days': 'Enter the number of days as a whole number.'}, status=400)
            GrievanceSetting.objects.update_or_create(category=r['category'], defaults={
                'sla_days': sla, 'acknowledge_days': ack,
                'officer_user_ids': [int(x) for x in r.get('officer_user_ids') or [] if str(x).isdigit()],
                'escalate_to_user_ids': [int(x) for x in r.get('escalate_to_user_ids') or [] if str(x).isdigit()]})
        return self.get(request)


class ComplaintEscalateView(Base):
    def post(self, request):
        if not S.can(request, *S.GRIEVANCE):
            return deny('Only grievance officers can run the escalation check.')
        return Response({'escalated': G.escalate_overdue()})


# ================================================================================================ announcements
class MyAnnouncementsView(Base):
    def get(self, request):
        emp = self.emp(request)
        return Response(S.my_announcements(emp) if emp else [])


class AnnouncementReadView(Base):
    def post(self, request, pk):
        from django.apps import apps
        emp = self.emp(request)
        if emp is None:
            return deny('No employee record is linked to your login.', 404)
        if not any(a['id'] == pk for a in S.my_announcements(emp, include_expired=True)):
            return deny('Announcement not found.', 404)
        AV = apps.get_model('OrganisationManager', 'AnnouncementView')
        av, created = AV.objects.get_or_create(announcement_id=pk, employee=emp)
        return Response({'id': pk, 'read': True, 'read_at': av.viewed_at, 'already': not created})


class AnnouncementFileView(Base):
    def get(self, request, pk):
        from django.apps import apps
        A = apps.get_model('OrganisationManager', 'Announcement')
        emp = self.emp(request)
        allowed = S.can(request, *S.ANNOUNCE_HR) or (emp is not None and any(a['id'] == pk for a in S.my_announcements(emp, include_expired=True)))
        a = A.objects.filter(pk=pk).first() if allowed else None
        if a is None:
            return deny('Announcement not found.', 404)
        return _file(a.attachment)


class AnnouncementStatsView(Base):
    def get(self, request, pk=None):
        from django.apps import apps
        if not S.can(request, *S.ANNOUNCE_HR):
            return deny('Only HR can see who read announcements.')
        A = apps.get_model('OrganisationManager', 'Announcement')
        if pk:
            a = A.objects.filter(pk=pk).first()
            if a is None:
                return deny('Announcement not found.', 404)
            return Response(S.announcement_stats(a, request))
        out = []
        for a in A.objects.order_by('-created_at')[:200]:
            s = S.announcement_stats(a, request)
            s.pop('readers', None)
            s.update({'created_at': a.created_at, 'expires_at': a.expires_at, 'schedule_at': a.schedule_at})
            out.append(s)
        return Response(out)


# ================================================================================================ payslips / documents
class MyPayslipsView(Base):
    def get(self, request):
        emp = self.emp(request)
        if emp is None:
            return Response({'payslips': [], 'ytd': None, 'next': None})
        year = request.GET.get('year')
        return Response({'payslips': [S.payslip_json(p) for p in S.my_payslips(emp)], 'ytd': S.ytd(emp, int(year) if str(year or '').isdigit() else None),
                         'next': S.next_payslip(emp)})


class MyPayslipView(Base):
    def get(self, request, pk):
        emp = self.emp(request)
        p = S.my_payslips(emp).filter(pk=pk).first() if emp else None
        if p is None:
            return deny('Payslip not found.', 404)
        return Response(S.payslip_json(p, lines=True))


class MyPayslipPdfView(Base):
    def get(self, request, pk):
        emp = self.emp(request)
        p = S.my_payslips(emp).filter(pk=pk).first() if emp else None
        if p is None:
            return deny('Payslip not found.', 404)
        if p.payslip_pdf and p.payslip_pdf.name:
            return _file(p.payslip_pdf, f'payslip-{p.payroll_run.year}-{p.payroll_run.month:02d}.pdf')
        from PayrollManagement.utils import generate_payslip_pdf
        resp = generate_payslip_pdf(request, p)
        resp['Content-Disposition'] = f'attachment; filename="payslip-{p.payroll_run.year}-{p.payroll_run.month:02d}.pdf"'
        return resp


class MyDocumentsView(Base):
    def get(self, request):
        emp = self.emp(request)
        if emp is None:
            return Response([])
        from django.apps import apps
        D = apps.get_model('EmpManagement', 'Emp_Documents')
        out = []
        for d in D.objects.filter(emp_id=emp, is_active=True).select_related('document_type').order_by('emp_doc_expiry_date'):   # v1.13.0: renewed / replaced copies are inactive
            out.append({'id': d.id, 'type': d.document_type.type_name if d.document_type_id else 'Document', 'document_type': d.document_type_id,
                        'number': d.emp_doc_number, 'issued': d.emp_doc_issued_date, 'expiry': d.emp_doc_expiry_date,
                        'badge': P.expiry_badge(d.emp_doc_expiry_date), 'has_file': bool(d.emp_doc_document),
                        'pending_request': ProfileChangeRequest.objects.filter(employee_id=emp.pk, group='documents', record_id=d.id, status='pending').exists()})
        letters = [letter_json(x) for x in LetterRequest.objects.filter(employee_id=emp.pk, status='issued')]
        return Response({'documents': out, 'letters': letters, 'policy': P.effective('documents', '', S.policy_map())})


class DocumentFileView(Base):
    def get(self, request, pk):
        from django.apps import apps
        D = apps.get_model('EmpManagement', 'Emp_Documents')
        d = D.objects.filter(pk=pk).select_related('emp_id').first()
        if d is None:
            return deny('Document not found.', 404)
        emp = self.emp(request)
        own = emp is not None and d.emp_id_id == emp.pk
        hr = S.can(request, 'view_emp_documents', 'change_emp_documents', 'change_emp_master') and S.branch_ok(request, d.emp_id.emp_branch_id_id)
        if not own and not hr:
            return deny('Document not found.', 404)
        return _file(d.emp_doc_document)
