"""ZEO People – the screens that are not plain registers: signing, survey taking and results, form filling, chat,
the 360° reviewer sheet, the recognition wall and points."""
import base64
import io
import json
import logging
import secrets
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from zeo.appkit.engine import AppError, In, d, emp_name, now, today, user_name
from zeo.appkit.views import Base, guard

from .app import HR, MANAGE, app, audience, emp_key, is_manager_of, me, points_of, summarise
from .models import ChatMessage, Conversation, FormSubmission, FormTemplate, Recognition, Review360, Reviewer, SignRequest, Survey, SurveyResponse

log = logging.getLogger(__name__)


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))[:60]


# ====================================================================== e-signature
def sign_payload(s, public=False):
    out = {'id': s.pk, 'number': s.number, 'title': s.title, 'kind': s.get_kind_display(), 'status': s.status, 'status_label': s.get_status_display(), 'text': s.text,
           'message': s.message, 'signer': emp_name(s.employee) or s.signer_name, 'has_document': bool(s.document), 'otp_needed': bool(app.settings().esign_otp),
           'expires_at': d(s.expires_at), 'signed_at': d(s.signed_at), 'signed_file': bool(s.signed_document), 'audit': s.audit or [],
           'document_url': (f'/zeo-people/api/sign/{s.pk}/document/' if not public else f'/zeo-people/api/public-sign/{s.token}/document/') if s.document else '',
           'signed_url': (f'/zeo-people/api/sign/{s.pk}/document/?signed=1' if not public else f'/zeo-people/api/public-sign/{s.token}/document/?signed=1') if s.signed_document else ''}
    return out


def stamp_pdf(s):
    """The signed document: the original PDF (or the text as a PDF) plus a signature page with the audit trail."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    try:
        from Translations.pdf import fonts, shape
        reg, bold = fonts()
    except Exception:
        reg, bold, shape = 'Helvetica', 'Helvetica-Bold', (lambda t: t)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    if not s.document and s.text:
        y = h - 25 * mm
        c.setFont(bold, 14)
        c.drawString(20 * mm, y, shape(s.title)[:90])
        y -= 10 * mm
        c.setFont(reg, 10.5)
        for para in s.text.split('\n'):
            words, line = para.split(' '), ''
            for wd in words + ['\n']:
                if wd == '\n' or c.stringWidth(line + ' ' + wd, reg, 10.5) > w - 40 * mm:
                    c.drawString(20 * mm, y, shape(line.strip()))
                    y -= 5.5 * mm
                    line = wd if wd != '\n' else ''
                    if y < 25 * mm:
                        c.showPage()
                        c.setFont(reg, 10.5)
                        y = h - 25 * mm
                else:
                    line += ' ' + wd
        c.showPage()
    c.setFont(bold, 14)
    c.drawString(20 * mm, h - 25 * mm, 'Electronic signature')
    c.setFont(reg, 10.5)
    y = h - 36 * mm
    for k, v in (('Document', f'{s.number} – {s.title}'), ('Signer', emp_name(s.employee) or s.signer_name), ('Signed on', d(s.signed_at)), ('From', f'{s.signed_ip}  {s.signed_agent[:70]}'),
                 ('Verification', 'One-time code sent by e-mail' if app.settings().esign_otp else 'ZEO login'), ('Reference', s.token[:16])):
        c.setFont(bold, 10.5)
        c.drawString(20 * mm, y, k)
        c.setFont(reg, 10.5)
        c.drawString(55 * mm, y, shape(str(v or '')))
        y -= 7 * mm
    if s.signature.startswith('data:image'):
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(io.BytesIO(base64.b64decode(s.signature.split(',', 1)[1])))
            c.drawImage(img, 20 * mm, y - 32 * mm, width=70 * mm, height=28 * mm, preserveAspectRatio=True, mask='auto')
            y -= 36 * mm
        except Exception:
            log.exception('signature image')
    elif s.signature:
        c.setFont('Helvetica-Oblique', 22)
        c.drawString(20 * mm, y - 10 * mm, s.signature[6:][:40] if s.signature.startswith('typed:') else s.signature[:40])
        y -= 18 * mm
    c.setFont(bold, 10.5)
    c.drawString(20 * mm, y - 4 * mm, 'Audit trail')
    c.setFont(reg, 9)
    yy = y - 10 * mm
    for a in (s.audit or [])[-14:]:
        c.drawString(20 * mm, yy, f'{a.get("at", "")[:19].replace("T", " ")}  {a.get("what", "")}  {a.get("by", "")}  {a.get("ip", "")}'[:120])
        yy -= 5 * mm
    c.showPage()
    c.save()
    sig_pdf = buf.getvalue()
    if s.document:
        try:
            from pypdf import PdfReader, PdfWriter
            wr = PdfWriter()
            s.document.open('rb')
            for p in PdfReader(s.document).pages:
                wr.add_page(p)
            for p in PdfReader(io.BytesIO(sig_pdf)).pages:
                wr.add_page(p)
            out = io.BytesIO()
            wr.write(out)
            return out.getvalue()
        except Exception:
            log.exception('merge signed pdf')
    return sig_pdf


def do_sign(s, request, signature, otp, user=None):
    settings_ = app.settings()
    if s.status != 'sent':
        raise AppError('This document is not waiting for a signature.')
    if s.expires_at and s.expires_at < now():
        s.status = 'expired'
        s.save(update_fields=['status'])
        raise AppError('The request has expired – ask HR to send it again.')
    if settings_.esign_otp:
        if not s.otp or not s.otp_sent_at or s.otp_sent_at < now() - timedelta(minutes=30):
            raise AppError('Ask for a new one-time code first.', 'otp')
        if (otp or '').strip() != s.otp:
            s.audit = (s.audit or []) + [{'at': now().isoformat(), 'what': 'wrong code', 'ip': _ip(request)}]
            s.save(update_fields=['audit'])
            raise AppError('The one-time code is wrong.', 'otp')
    if not signature:
        raise AppError('Draw or type your signature.', 'signature')
    s.signature = signature[:200000]
    s.status, s.signed_at, s.signed_ip, s.signed_agent = 'signed', now(), _ip(request), request.META.get('HTTP_USER_AGENT', '')[:200]
    s.audit = (s.audit or []) + [{'at': now().isoformat(), 'what': 'signed', 'by': user_name(user) if user else (s.signer_name or s.signer_email), 'ip': s.signed_ip}]
    s.otp = ''
    s.save()
    try:
        s.signed_document.save(f'{s.number}-signed.pdf', ContentFile(stamp_pdf(s)), save=True)
    except Exception:
        log.exception('stamp')
    app.event('sign_request', s.pk, f'Signed from {s.signed_ip}', user)
    app.close_todos('sign_request', s.pk)
    if s.requested_by_id:
        app.todo(s.requested_by, f'Signed: {s.title}', 'sign_request', s.pk, s.number, app.page + f'r/sign_request/{s.pk}')
    if s.source_model == 'ZeoPeople.FormSubmission' and s.source_id:
        app.event('form_submission', s.source_id, f'Signed ({s.number})', user)
    try:   # other modules that asked for the signature
        from zeo.appkit.engine import REGISTRY
        for label, other in REGISTRY.items():
            fn = other.hooks.get('signed') if label != 'ZeoPeople' else None
            if fn:
                fn(sign_request_id=s.pk, source_model=s.source_model, source_id=s.source_id)
    except Exception:
        log.exception('signed hook')


def send_otp(s):
    s.otp = ''.join(secrets.choice('0123456789') for _ in range(6))
    s.otp_sent_at = now()
    s.audit = (s.audit or []) + [{'at': now().isoformat(), 'what': 'code sent'}]
    s.save(update_fields=['otp', 'otp_sent_at', 'audit'])
    to = s.signer_email or (s.employee.emp_company_email or s.employee.emp_personal_email or (s.employee.users.email if s.employee.users_id else '') if s.employee_id else '')
    sent = app.send_mail([to], f'Your code to sign {s.title}', f'Your one-time code is {s.otp}. It is valid for 30 minutes.\n\nIf you did not ask to sign a document, ignore this e-mail.')
    return to, sent


class SignView(Base):
    """GET the document to sign; POST {otp: 'send'} to get a code; POST {signature, otp} to sign; POST {decline: reason}."""
    @guard
    def get(self, request, pk):
        s = SignRequest.objects.filter(pk=pk).first()
        e = me(request)
        if s is None or not (app.any(request, *HR) or (e and s.employee_id == e.pk)):
            raise Http404
        return Response(sign_payload(s))

    @guard
    def post(self, request, pk):
        s = SignRequest.objects.filter(pk=pk).first()
        e = me(request)
        if s is None or not (e and s.employee_id == e.pk):
            raise Http404
        f = In(request)
        if f.s('otp') == 'send':
            to, sent = send_otp(s)
            return Response({'sent': sent, 'to': (to[:3] + '…' + to[to.find('@'):]) if to and '@' in to else '', 'demo_code': s.otp if not sent else ''})
        if f.s('decline'):
            s.status, s.decline_reason = 'declined', f.s('decline')[:200]
            s.audit = (s.audit or []) + [{'at': now().isoformat(), 'what': 'declined', 'by': user_name(request.user), 'note': s.decline_reason}]
            s.save()
            app.event('sign_request', s.pk, f'Declined – {s.decline_reason}', request.user)
            app.close_todos('sign_request', s.pk)
            if s.requested_by_id:
                app.todo(s.requested_by, f'Declined: {s.title}', 'sign_request', s.pk, s.number, app.page + f'r/sign_request/{s.pk}', s.decline_reason)
            return Response(sign_payload(s))
        do_sign(s, request, f.s('signature'), f.s('otp'), request.user)
        return Response(sign_payload(s))


class SignDocumentView(Base):
    @guard
    def get(self, request, pk):
        s = SignRequest.objects.filter(pk=pk).first()
        e = me(request)
        if s is None or not (app.any(request, *HR) or (e and s.employee_id == e.pk)):
            raise Http404
        f = s.signed_document if request.query_params.get('signed') and s.signed_document else s.document
        if not f:
            raise Http404
        resp = HttpResponse(f.open('rb').read(), content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{s.number}{"-signed" if request.query_params.get("signed") else ""}.pdf"'
        return resp


@method_decorator(csrf_exempt, name='dispatch')
class PublicSignView(APIView):
    """Outside signer with the e-mailed link: /zeo-people/api/public-sign/<token>/?schema=<company>."""
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def _get(self, token):
        s = SignRequest.objects.filter(token=token).exclude(token='').first()
        if s is None or not s.signer_email:
            raise Http404
        return s

    def get(self, request, token):
        return Response(sign_payload(self._get(token), public=True))

    def post(self, request, token):
        s = self._get(token)
        f = In(request)
        try:
            if f.s('otp') == 'send':
                to, sent = send_otp(s)
                return Response({'sent': sent, 'to': (to[:3] + '…' + to[to.find('@'):]) if to and '@' in to else ''})
            if f.s('decline'):
                s.status, s.decline_reason = 'declined', f.s('decline')[:200]
                s.audit = (s.audit or []) + [{'at': now().isoformat(), 'what': 'declined', 'by': s.signer_name, 'note': s.decline_reason, 'ip': _ip(request)}]
                s.save()
                app.event('sign_request', s.pk, f'Declined – {s.decline_reason}', None)
                return Response(sign_payload(s, public=True))
            do_sign(s, request, f.s('signature'), f.s('otp'))
        except AppError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(sign_payload(s, public=True))


class PublicSignDocumentView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def get(self, request, token):
        s = SignRequest.objects.filter(token=token).exclude(token='').first()
        if s is None or not s.signer_email:
            raise Http404
        f = s.signed_document if request.query_params.get('signed') and s.signed_document else s.document
        if not f:
            raise Http404
        return HttpResponse(f.open('rb').read(), content_type='application/pdf')


# ====================================================================== surveys
class SurveyTakeView(Base):
    """GET the questions (and whether already answered); POST {answers: [...]}."""
    @guard
    def get(self, request, pk):
        s = Survey.objects.filter(pk=pk).first()
        e = me(request)
        if s is None:
            raise Http404
        done = bool(e and SurveyResponse.objects.filter(survey=s, employee_key=emp_key(s, e)).exists())
        return Response({'id': s.pk, 'number': s.number, 'title': s.title, 'description': s.description, 'anonymous': s.anonymous, 'status': s.status,
                         'questions': s.questions or [], 'done': done, 'can_take': s.status == 'open' and e is not None and not done, 'end': s.end})

    @guard
    def post(self, request, pk):
        s = Survey.objects.filter(pk=pk).first()
        e = me(request)
        if s is None or e is None:
            raise Http404
        if s.status != 'open':
            raise AppError('The survey is closed.')
        key = emp_key(s, e)
        if SurveyResponse.objects.filter(survey=s, employee_key=key).exists():
            raise AppError('You have already answered this survey.')
        answers = In(request).j('answers') or []
        if not isinstance(answers, list):
            raise AppError('Answers are not valid.')
        for i, q in enumerate(s.questions or []):
            if q.get('required', True) and (i >= len(answers) or answers[i] in (None, '', [])):
                raise AppError(f'Question {i + 1} needs an answer.')
        SurveyResponse.objects.create(survey=s, employee=None if s.anonymous else e, employee_key=key, department=getattr(getattr(e, 'emp_dept_id', None), 'dept_name', '') or '',
                                      answers=[str(a)[:2000] if not isinstance(a, (int, float)) else a for a in answers])
        app.close_todos('survey', s.pk)
        return Response({'ok': True, 'message': 'Thank you – your answers are saved.' + (' They are anonymous.' if s.anonymous else '')})


class SurveyResultsView(Base):
    @guard
    def get(self, request, pk):
        s = Survey.objects.filter(pk=pk).first()
        if s is None or not app.any(request, *HR):
            raise Http404
        rs = list(s.responses.all())
        total = audience(s).count()
        by_dept = request.query_params.get('department') or ''
        if by_dept:
            rs = [r for r in rs if r.department == by_dept]
        out = []
        for i, q in enumerate(s.questions or []):
            vals = [r.answers[i] for r in rs if i < len(r.answers or []) and r.answers[i] not in (None, '')]
            row = {'text': q.get('text'), 'type': q.get('type', 'rating'), 'answered': len(vals)}
            if q.get('type') == 'rating':
                nums = [float(v) for v in vals if str(v).replace('.', '').isdigit()]
                row['avg'] = round(sum(nums) / len(nums), 2) if nums else None
                row['dist'] = [sum(1 for n in nums if int(n) == k) for k in range(1, 6)]
                row['favourable'] = round(100 * sum(1 for n in nums if n >= 4) / len(nums)) if nums else 0
            elif q.get('type') == 'yesno':
                yes = sum(1 for v in vals if str(v).lower() in ('yes', 'true', '1'))
                row['yes'], row['no'] = yes, len(vals) - yes
                row['favourable'] = round(100 * yes / len(vals)) if vals else 0
            elif q.get('type') == 'choice':
                cnt = {}
                for v in vals:
                    cnt[str(v)] = cnt.get(str(v), 0) + 1
                row['choices'] = sorted(({'label': k, 'n': n} for k, n in cnt.items()), key=lambda x: -x['n'])
            else:
                row['texts'] = [str(v) for v in vals][:200]
            out.append(row)
        depts = sorted({r.department for r in s.responses.all() if r.department})
        ratings = [q for q in out if q['type'] == 'rating' and q.get('avg') is not None]
        return Response({'id': s.pk, 'title': s.title, 'anonymous': s.anonymous, 'status': s.status, 'responses': len(rs), 'invited': total,
                         'rate': round(100 * len(rs) / total, 1) if total else 0, 'questions': out, 'departments': depts, 'department': by_dept,
                         'engagement': round(sum(q['favourable'] for q in ratings) / len(ratings)) if ratings else None,
                         'min_group': 3 if s.anonymous else 0, 'hidden': s.anonymous and len(rs) < 3})


# ====================================================================== forms
class FormFillView(Base):
    """GET a template's fields; POST {data} makes the submission through the register (same rules)."""
    @guard
    def get(self, request, pk):
        t = FormTemplate.objects.filter(pk=pk, is_active=True).first()
        if t is None:
            raise Http404
        e = me(request)
        if t.audience == 'managers' and not (app.any(request, *HR) or (e and e.__class__.objects.filter(emp_reporting_manager=request.user).exists())):
            raise AppError('This form is for managers.')
        if t.audience == 'hr' and not app.any(request, *HR):
            raise AppError('This form is for HR.')
        already = bool(e and t.once and FormSubmission.objects.filter(template=t, employee=e).exclude(status='rejected').exists())
        return Response({'id': t.pk, 'name': t.name, 'description': t.description, 'fields': t.fields or [], 'needs_approval': t.needs_approval, 'sign': t.sign, 'already': already})

    @guard
    def post(self, request, pk):
        from zeo.appkit.engine import detail, save
        t = FormTemplate.objects.filter(pk=pk, is_active=True).first()
        if t is None:
            raise Http404
        r = app.regs['form_submission']
        f = In(request)
        data = f.j('data') or {}
        for f_ in f.filelist('files'):   # file fields: stored as attachments of the submission; the data keeps the file name
            data[f_.name.split('__', 1)[0]] = f_.name.split('__', 1)[-1] if '__' in f_.name else f_.name
        f.d = {'template': t.pk, 'data': json.dumps(data)}
        obj = save(r, FormSubmission(), f, request, True)
        return Response(detail(r, FormSubmission.objects.get(pk=obj.pk), request), status=status.HTTP_201_CREATED)


class MyFormsView(Base):
    """Forms the employee may fill, with the number of own submissions."""
    @guard
    def get(self, request):
        e = me(request)
        is_mgr = bool(e and e.__class__.objects.filter(emp_reporting_manager=request.user).exists())
        qs = FormTemplate.objects.filter(is_active=True)
        out = []
        for t in qs:
            if t.audience == 'managers' and not (is_mgr or app.any(request, *HR)):
                continue
            if t.audience == 'hr' and not app.any(request, *HR):
                continue
            mine = FormSubmission.objects.filter(template=t, employee=e).count() if e else 0
            out.append({'id': t.pk, 'name': t.name, 'description': t.description, 'fields': len(t.fields or []), 'needs_approval': t.needs_approval, 'sign': t.sign,
                        'mine': mine, 'once': t.once, 'can_fill': not (t.once and mine)})
        return Response(out)


# ====================================================================== recognition wall
class WallView(Base):
    @guard
    def get(self, request):
        e = me(request)
        qs = Recognition.objects.filter(status='published').select_related('employee', 'given_by', 'award')
        q = request.query_params
        if q.get('employee'):
            qs = qs.filter(employee_id=q['employee'])
        rows = [{'id': r.pk, 'number': r.number, 'employee': emp_name(r.employee), 'employee_id': r.employee_id, 'given_by': emp_name(r.given_by), 'award': r.award.name, 'icon': r.award.icon,
                 'points': r.points, 'message': r.message, 'at': d(r.created_at), 'likes': len(r.likes or []), 'liked': bool(e and e.pk in (r.likes or []))} for r in qs[:100]]
        t = today()
        top = (Recognition.objects.filter(status='published', created_at__gte=t - timedelta(days=90)).values('employee_id', 'employee__emp_first_name', 'employee__emp_last_name')
               .annotate(p=Sum('points'), n=Count('id')).order_by('-p')[:10])
        return Response({'rows': rows, 'my_points': points_of(e.pk) if e else 0, 'points_name': app.settings().points_currency, 'point_value': float(app.settings().points_value),
                         'leaders': [{'employee_id': x['employee_id'], 'name': f'{x["employee__emp_first_name"] or ""} {x["employee__emp_last_name"] or ""}'.strip(), 'points': x['p'], 'n': x['n']} for x in top],
                         'can_give': e is not None})

    @guard
    def post(self, request):
        """{like: id} toggles a like."""
        e = me(request)
        r = Recognition.objects.filter(pk=In(request).i('like'), status='published').first()
        if r is None or e is None:
            raise Http404
        likes = list(r.likes or [])
        likes.remove(e.pk) if e.pk in likes else likes.append(e.pk)
        r.likes = likes
        r.save(update_fields=['likes'])
        return Response({'likes': len(likes), 'liked': e.pk in likes})


# ====================================================================== 360° reviewer sheet
class ReviewSheetView(Base):
    @guard
    def get(self, request, pk):
        r = Review360.objects.filter(pk=pk).first()
        e = me(request)
        rv = Reviewer.objects.filter(review=r, reviewer=e).first() if r and e else None
        if r is None or rv is None:
            raise Http404
        comps = [{'id': c.pk, 'name': c.name, 'group': c.get_group_display(), 'description': c.description, 'levels': c.levels or []} for c in r.competencies.filter(is_active=True)]
        return Response({'id': r.pk, 'number': r.number, 'employee': emp_name(r.employee), 'cycle': r.cycle_name, 'due': r.due, 'relation': rv.get_relation_display(), 'relation_code': rv.relation,
                         'status': rv.status, 'review_status': r.status, 'competencies': comps, 'questions': r.open_questions or [], 'ratings': rv.ratings or {}, 'answers': rv.answers or [],
                         'strengths': rv.strengths, 'improve': rv.improve, 'overall': rv.overall, 'anonymous': r.anonymous})

    @guard
    def post(self, request, pk):
        r = Review360.objects.filter(pk=pk).first()
        e = me(request)
        rv = Reviewer.objects.filter(review=r, reviewer=e).first() if r and e else None
        if r is None or rv is None:
            raise Http404
        if r.status != 'open':
            raise AppError('This review is not collecting answers.')
        if rv.status == 'done':
            raise AppError('You have already submitted this review.')
        f = In(request)
        ratings = f.j('ratings') or {}
        missing = [c.name for c in r.competencies.filter(is_active=True) if not str(ratings.get(str(c.pk)) or '').isdigit()]
        if missing and not f.b('draft'):
            raise AppError('Rate: ' + ', '.join(missing))
        rv.ratings = {str(k): int(v) for k, v in ratings.items() if str(v).isdigit() and 1 <= int(v) <= 5}
        rv.answers = [str(a)[:2000] for a in (f.j('answers') or [])]
        rv.strengths, rv.improve = f.s('strengths')[:4000], f.s('improve')[:4000]
        rv.overall = f.i('overall') if f.i('overall') and 1 <= f.i('overall') <= 5 else None
        if not f.b('draft'):
            rv.status, rv.done_at = 'done', now()
            app.close_todos('review_360', r.pk)
            app.event('review_360', r.pk, f'{rv.get_relation_display()} review submitted', request.user)
            if not r.reviewers.filter(status='pending').exists():
                summarise(r)
                r.save(update_fields=['summary', 'overall'])
                for u in app.users_with_role('ppl_hr'):
                    app.todo(u, f'360° review complete: {emp_name(r.employee)}', 'review_360', r.pk, r.number, app.page + f'r/review_360/{r.pk}', 'All reviewers have answered – close and share.')
        rv.save()
        return Response({'ok': True, 'status': rv.status})


# ====================================================================== chat
def _user_label(u):
    return user_name(u)


def conv_payload(c, user):
    last = c.messages.order_by('-at').first()
    unread = c.messages.exclude(sender=user).exclude(read_by__contains=[user.pk]).count()
    others = [m for m in c.members.all() if m.pk != user.pk]
    name = c.name if c.kind != 'direct' else (_user_label(others[0]) if others else 'Me')
    return {'id': c.pk, 'kind': c.kind, 'name': name, 'members': [{'id': m.pk, 'name': _user_label(m)} for m in c.members.all()],
            'last': {'text': (last.text or last.file_name)[:80], 'at': d(last.at), 'by': _user_label(last.sender)} if last else None, 'unread': unread, 'last_at': d(c.last_at)}


class ChatView(Base):
    """GET conversations (+ people to start one); POST {user: id} direct, or {name, members: [ids], kind: group}."""
    @guard
    def get(self, request):
        u = request.user
        convs = Conversation.objects.filter(members=u).prefetch_related('members').order_by('-last_at', '-id')[:100]
        out = [conv_payload(c, u) for c in convs]
        people = []
        if request.query_params.get('people'):
            from EmpManagement.models import emp_master
            from AccessControl.access import company_perms
            users = {x.pk: x for x in company_perms().select_related('profile')}
            for e in emp_master.objects.filter(users__in=list(users)).exclude(users=u).select_related('users', 'emp_dept_id')[:500]:
                people.append({'id': e.users_id, 'name': emp_name(e), 'sub': getattr(e.emp_dept_id, 'dept_name', '') or ''})
            people.sort(key=lambda x: x['name'])
        return Response({'conversations': out, 'people': people, 'me': u.pk, 'unread': sum(c['unread'] for c in out)})

    @guard
    def post(self, request):
        u = request.user
        f = In(request)
        from django.contrib.auth import get_user_model
        U = get_user_model()
        if f.i('user'):
            other = U.objects.filter(pk=f.i('user')).first()
            if other is None:
                raise AppError('Unknown person.')
            key = f'u{min(u.pk, other.pk)}-u{max(u.pk, other.pk)}'
            c = Conversation.objects.filter(kind='direct', key=key).first()
            if c is None:
                c = Conversation.objects.create(kind='direct', key=key, created_by=u, last_at=now())
                c.members.set([u, other])
        else:
            ids = [i for i in f.ids('members') if i != u.pk]
            if not f.s('name') or not ids:
                raise AppError('Give the group a name and members.')
            c = Conversation.objects.create(kind=f.s('kind') if f.s('kind') in ('group', 'channel') else 'group', name=f.s('name')[:100], created_by=u, last_at=now())
            c.members.set(list(U.objects.filter(pk__in=ids)) + [u])
        return Response(conv_payload(c, u), status=status.HTTP_201_CREATED)


class ChatMessagesView(Base):
    """GET ?since=<id> messages of a conversation (marks them read); POST {text} or multipart with file."""
    def _conv(self, request, pk):
        c = Conversation.objects.filter(pk=pk, members=request.user).first()
        if c is None:
            raise Http404
        return c

    @guard
    def get(self, request, pk):
        c = self._conv(request, pk)
        since = int(request.query_params.get('since') or 0)
        qs = c.messages.filter(pk__gt=since).select_related('sender').order_by('at', 'id')
        rows = []
        for m in qs[:200]:
            if request.user.pk not in (m.read_by or []) and m.sender_id != request.user.pk:
                m.read_by = list(m.read_by or []) + [request.user.pk]
                m.save(update_fields=['read_by'])
            rows.append({'id': m.pk, 'text': m.text, 'file': f'/zeo-people/api/chat/{c.pk}/file/{m.pk}/' if m.file else '', 'file_name': m.file_name, 'at': d(m.at),
                         'by': _user_label(m.sender), 'mine': m.sender_id == request.user.pk, 'sender': m.sender_id})
        return Response({'id': c.pk, 'messages': rows, 'conversation': conv_payload(c, request.user)})

    @guard
    def post(self, request, pk):
        c = self._conv(request, pk)
        f = In(request)
        text = f.s('text')[:4000]
        files = f.filelist('file') or f.filelist('files')
        if not text and not files:
            raise AppError('Write a message or attach a file.')
        m = ChatMessage.objects.create(conversation=c, sender=request.user, text=text, read_by=[request.user.pk])
        if files:
            m.file = files[0]
            m.file_name = files[0].name[:255]
            m.save()
        c.last_at = now()
        c.save(update_fields=['last_at'])
        return Response({'id': m.pk}, status=status.HTTP_201_CREATED)


class ChatFileView(Base):
    @guard
    def get(self, request, pk, mid):
        c = Conversation.objects.filter(pk=pk, members=request.user).first()
        m = ChatMessage.objects.filter(pk=mid, conversation=c).first() if c else None
        if m is None or not m.file:
            raise Http404
        resp = HttpResponse(m.file.open('rb').read(), content_type='application/octet-stream')
        resp['Content-Disposition'] = f'attachment; filename="{m.file_name}"'
        return resp


class ChatUnreadView(Base):
    @guard
    def get(self, request):
        n = ChatMessage.objects.filter(conversation__members=request.user).exclude(sender=request.user).exclude(read_by__contains=[request.user.pk]).count()
        return Response({'unread': n})
