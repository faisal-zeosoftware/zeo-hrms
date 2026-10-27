"""v1.57.0 – public careers page, apply form, job-board feeds (XML / JSON) and talent pools.

The careers page is public: /careers/<company> in the web app reads /recruitment/api/careers/…?schema=<company>.
Nothing here needs a login; what a visitor may see is decided by CareersSettings.enabled and JobOpening.on_careers_page.
"""
import logging
import re
from datetime import timedelta
from xml.sax.saxutils import escape

from django.conf import settings as dj
from django.db import connection, transaction
from django.utils import timezone

from .models import (Application, ApplicationStageHistory, Candidate, CandidateDocument, CareersSettings, JobOpening, TalentCampaign,
                     TalentPool, TalentPoolMember)

from Translations.i18n import tr

log = logging.getLogger(__name__)

CV_EXT = ('.pdf', '.doc', '.docx')
CV_MAX = 5 * 1024 * 1024
SOURCES = {'linkedin': 'linkedin', 'indeed': 'indeed', 'bayt': 'bayt', 'naukrigulf': 'naukrigulf', 'nafis': 'nafis', 'pool': 'talent_pool',
           'talent_pool': 'talent_pool', 'referral': 'referral'}
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class CareersError(Exception):
    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.field, self.status = field, status


# ---------------------------------------------------------------------------------------------- helpers
import contextvars
_BASE = contextvars.ContextVar('zeo_careers_base', default='')


def use_request(request):
    """Without ZEO_APP_URL the links (careers page, feed, re-engage e-mails) use the address the request came in on."""
    try:
        _BASE.set(request.build_absolute_uri('/').rstrip('/'))
    except Exception:
        pass


def app_url():
    return (getattr(dj, 'ZEO_APP_URL', '') or '').rstrip('/') or _BASE.get()


def company():
    from UserManagement.models import company as Company
    return Company.objects.filter(schema_name=connection.schema_name).first()


def page_url(job_code=None, src=None):
    base = f'{app_url()}/careers/{connection.schema_name}'
    if job_code:
        base += f'/job/{job_code}'
    return base + (f'?src={src}' if src else '')


def send_mail(to, subject, body):
    """Best effort, the company's own mail server when set (as the app kit does)."""
    to = [x for x in dict.fromkeys(t.strip() for t in (to or []) if t and '@' in t)]
    if not to:
        return False
    try:
        from django.core.mail import EmailMessage, get_connection
        conn, sender = None, dj.DEFAULT_FROM_EMAIL
        try:
            from EmpManagement.models import EmailConfiguration
            cfg = EmailConfiguration.objects.filter(is_active=True).first()
            if cfg and cfg.email_host_user:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                      password=cfg.email_host_password, use_tls=cfg.email_use_tls, fail_silently=False)
                sender = cfg.email_host_user
        except Exception:
            conn = None
        EmailMessage(subject=subject[:200], body=body, from_email=sender, to=to, connection=conn).send(fail_silently=False)
        return True
    except Exception:
        log.exception('careers e-mail failed')
        return False


def open_jobs():
    today = timezone.localdate()
    qs = JobOpening.objects.filter(status__in=('published', 'interviewing')).select_related('department', 'designation', 'branch')
    return [j for j in qs.order_by('-published_on', '-created_at') if 'Careers page' in (j.channels or []) and (j.closing_date is None or j.closing_date >= today)]


def public_questions(job):
    """The questions without the expected answers (those stay on the server)."""
    out = []
    for i, q in enumerate(job.questions or []):
        if not isinstance(q, dict) or not str(q.get('text') or '').strip():
            continue
        out.append({'id': str(q.get('id') or i + 1), 'text': str(q['text']).strip(), 'type': q.get('type') or 'text',
                    'options': [str(o) for o in (q.get('options') or [])], 'required': bool(q.get('required'))})
    return out


def _summary(text, n=220):
    t = re.sub(r'\s+', ' ', text or '').strip()
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0] + '…'


def job_card(job, s, full=False):
    d = {'code': job.job_code, 'title': job.title, 'department': str(job.department) if job.department else '',
         'location': job.location or (str(job.branch) if job.branch else ''), 'employment_type': job.employment_type,
         'employment_type_label': job.get_employment_type_display(), 'workplace': job.workplace, 'workplace_label': job.get_workplace_display(),
         'experience_min_years': job.experience_min_years, 'openings': job.openings, 'closing_date': job.closing_date,
         'published_on': job.published_on, 'summary': _summary(job.description)}
    if s.show_salary and (job.min_salary or job.max_salary):
        d['salary'] = {'min': job.min_salary, 'max': job.max_salary, 'currency': currency()}
    if full:
        d.update({'description': job.description, 'requirements': job.requirements or '', 'questions': public_questions(job),
                  'is_emiratisation': job.is_emiratisation})
    return d


def currency():
    try:
        from CountryPack.services import current_pack
        return (current_pack().get('currency') or {}).get('code') or 'AED'
    except Exception:
        return 'AED'


def page(request):
    s = CareersSettings.get()
    if not s.enabled:
        raise CareersError(tr('This company has no careers page.'), status=404)
    c = company()
    jobs = open_jobs()
    from Core.models import Nationality
    try:
        nats = [{'id': n.pk, 'name': str(n)} for n in Nationality.objects.all().order_by('pk')[:300]]
    except Exception:
        nats = []
    return {
        'company': {'name': c.name if c else '', 'logo': bool(c and c.logo), 'schema': connection.schema_name},
        'settings': {'page_title': s.page_title, 'headline': s.headline, 'intro': s.intro, 'about': s.about, 'brand_color': s.brand_color,
                     'require_cv': s.require_cv, 'general_applications': s.general_applications, 'privacy_text': s.privacy_text,
                     'retention_months': s.retention_months},
        'jobs': [job_card(j, s) for j in jobs],
        'filters': {'departments': sorted({str(j.department) for j in jobs if j.department}),
                    'locations': sorted({j.location or str(j.branch) for j in jobs if j.location or j.branch}),
                    'types': [{'value': k, 'label': v} for k, v in JobOpening._meta.get_field('employment_type').choices if any(j.employment_type == k for j in jobs)]},
        'nationalities': nats,
        'visa_choices': [{'value': k, 'label': v} for k, v in Candidate.VISA_CHOICES],
    }


def job_detail(code):
    s = CareersSettings.get()
    if not s.enabled:
        raise CareersError(tr('This company has no careers page.'), status=404)
    job = JobOpening.objects.filter(job_code=code).select_related('department', 'branch').first()
    if job is None or not job.on_careers_page:
        raise CareersError(tr('This job is no longer open.'), status=404)
    return job_card(job, s, full=True)


# ---------------------------------------------------------------------------------------------- apply
def _rate_limited(ip, email):
    """8 applications an hour from one address, 5 a day from one e-mail."""
    try:
        from django.core.cache import cache
        k1, k2 = f'zcar:{connection.schema_name}:ip:{ip}', f'zcar:{connection.schema_name}:em:{email}'
        n1, n2 = cache.get(k1, 0), cache.get(k2, 0)
        if n1 >= 8 or n2 >= 5:
            return True
        cache.set(k1, n1 + 1, 3600)
        cache.set(k2, n2 + 1, 86400)
    except Exception:
        return False
    return False


def check_answers(job, raw):
    """[{id, answer}] → (answers kept on the application, list of failed must-have questions). Raises for missing required ones."""
    given = {}
    for a in raw or []:
        if isinstance(a, dict):
            given[str(a.get('id'))] = a.get('answer')
    kept, failed = [], []
    for i, q in enumerate(job.questions or []):
        if not isinstance(q, dict) or not str(q.get('text') or '').strip():
            continue
        qid, kind = str(q.get('id') or i + 1), q.get('type') or 'text'
        ans = given.get(qid)
        ans = ans.strip() if isinstance(ans, str) else ans
        if ans in (None, '', []):
            if q.get('required'):
                raise CareersError(tr('Answer the question "{0}".', q['text']), field=f'q_{qid}')
            continue
        if kind == 'number':
            try:
                ans = float(ans)
            except (TypeError, ValueError):
                raise CareersError(tr('Give a number for "{0}".', q['text']), field=f'q_{qid}')
        if kind == 'yes_no':
            ans = 'yes' if str(ans).lower() in ('yes', 'true', '1', 'y') else 'no'
        if kind == 'choice' and q.get('options') and str(ans) not in [str(o) for o in q['options']]:
            raise CareersError(tr('Choose one of the answers for "{0}".', q['text']), field=f'q_{qid}')
        must = q.get('must')
        ok = True
        if must not in (None, '', []):
            if kind == 'yes_no':
                ok = ans == str(must).lower()
            elif kind == 'number':
                try:
                    ok = ans >= float(must)
                except (TypeError, ValueError):
                    ok = True
            elif kind == 'choice':
                ok = str(ans) in ([str(m) for m in must] if isinstance(must, list) else [str(must)])
        if not ok:
            failed.append(q['text'])
        kept.append({'id': qid, 'question': q['text'], 'answer': ans, 'ok': ok})
    return kept, failed


def apply(data, files, ip=''):
    """The apply form (a job code) or "send us your CV" (no job). Returns {'ok', 'message', 'candidate', 'application'}."""
    s = CareersSettings.get()
    if not s.enabled:
        raise CareersError(tr('This company has no careers page.'), status=404)
    g = lambda k: (str(data.get(k) or '')).strip()
    if g('website'):                       # honeypot filled in by bots: answer as if it worked, save nothing
        return {'ok': True, 'message': tr('Thank you – your application was sent.')}
    job = None
    if g('job'):
        job = JobOpening.objects.filter(job_code=g('job')).first()
        if job is None or not job.on_careers_page:
            raise CareersError(tr('This job is no longer open.'), status=404)
    elif not s.general_applications:
        raise CareersError(tr('Choose a job to apply for.'), field='job')
    first, last, email, phone = g('first_name'), g('last_name'), g('email').lower(), g('phone')
    if not first:
        raise CareersError(tr('Enter your first name.'), field='first_name')
    if not EMAIL_RE.match(email):
        raise CareersError(tr('Enter a valid e-mail address.'), field='email')
    if phone and len(re.sub(r'\D', '', phone)) < 7:
        raise CareersError(tr('Enter a valid phone number.'), field='phone')
    if str(data.get('privacy')).lower() not in ('1', 'true', 'yes', 'on'):
        raise CareersError(tr('Please confirm that you have read how we use your details.'), field='privacy')
    cv = files.get('cv') if files else None
    if cv is not None:
        if not cv.name.lower().endswith(CV_EXT):
            raise CareersError(tr('The CV must be a PDF or Word file.'), field='cv')
        if cv.size > CV_MAX:
            raise CareersError(tr('The CV is larger than 5 MB.'), field='cv')
    import json
    raw = data.get('answers')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or '[]')
        except ValueError:
            raw = []
    answers, failed = check_answers(job, raw) if job else ([], [])

    def num(k, kind=float):
        v = g(k)
        if not v:
            return None
        try:
            n = kind(v)
        except ValueError:
            raise CareersError(tr('Enter a number.'), field=k)
        if n < 0:
            raise CareersError(tr('Enter a number of 0 or more.'), field=k)
        return n

    exp, notice, expected = num('experience_years'), num('notice_period_days', int), num('expected_salary')
    consent = str(data.get('consent_to_retain')).lower() in ('1', 'true', 'yes', 'on')
    src = SOURCES.get(g('src').lower(), 'careers')
    if _rate_limited(ip or '-', email):
        raise CareersError(tr('Too many applications were sent. Please try again later.'), status=429)

    with transaction.atomic():
        cand = Candidate.objects.filter(email__iexact=email).order_by('pk').first()
        new = cand is None
        if new:
            cand = Candidate(first_name=first, last_name=last or None, email=email, source=src)
        else:
            cand.first_name, cand.last_name = first, last or cand.last_name
        if not cv and new and s.require_cv:
            raise CareersError(tr('Attach your CV.'), field='cv')
        if not cv and not new and s.require_cv and not cand.cv:
            raise CareersError(tr('Attach your CV.'), field='cv')
        if job and not new and Application.objects.filter(candidate=cand, job=job).exists():
            raise CareersError(tr('You have already applied for this job. We will contact you if your profile matches.'), status=409)
        for f, v in (('phone', phone), ('current_location', g('current_location')), ('current_employer', g('current_employer')),
                     ('current_designation', g('current_designation')), ('skills', g('skills')), ('languages', g('languages')),
                     ('linkedin', g('linkedin'))):
            if v:
                setattr(cand, f, v[:300])
        if exp is not None:
            cand.experience_years = exp
        if notice is not None:
            cand.notice_period_days = notice
        if expected is not None:
            cand.expected_salary = expected
        if g('visa_status') in dict(Candidate.VISA_CHOICES):
            cand.visa_status = g('visa_status')
        if g('nationality').isdigit():
            from Core.models import Nationality
            cand.nationality = Nationality.objects.filter(pk=int(g('nationality'))).first() or cand.nationality
        if consent:
            cand.consent_to_retain = True
            cand.consent_until = timezone.localdate() + timedelta(days=30 * (s.retention_months or 24))
        if cv is not None:
            if cand.cv and not new:
                CandidateDocument.objects.create(candidate=cand, name=f'Earlier CV ({timezone.localdate():%d/%m/%Y})', file=cand.cv)
            cand.cv = cv
        cand.save()
        app = None
        if job:
            app = Application.objects.create(candidate=cand, job=job, stage='applied', source=src, answers=answers,
                                             knockout_failed=bool(failed), cover_letter=g('cover_letter')[:5000])
            note = f'Applied on the careers page' + (f' (from {src})' if src != 'careers' else '')
            if failed:
                note += ' – must-have answers not met: ' + '; '.join(failed)
            ApplicationStageHistory.objects.create(application=app, from_stage=None, to_stage='applied', note=note[:255])
            TalentPoolMember.objects.filter(candidate=cand, pool__campaigns__job=job).exclude(status='hired').update(status='applied')
        else:
            pool = general_pool(s)
            m, made = TalentPoolMember.objects.get_or_create(pool=pool, candidate=cand, defaults={'note': 'Sent a CV on the careers page'})
            if not made and g('cover_letter'):
                m.note = g('cover_letter')[:255]
                m.save(update_fields=['note'])
            if g('cover_letter'):
                cand.notes = ((cand.notes or '') + '\n' + g('cover_letter')).strip()
                cand.save(update_fields=['notes'])
    _tell(s, cand, job, app, failed)
    return {'ok': True, 'message': tr('Thank you – your application was sent.') if job else tr('Thank you – we have your CV and will contact you when a matching job opens.'),
            'candidate': cand.pk, 'application': app.pk if app else None}


def general_pool(s=None):
    s = s or CareersSettings.get()
    if s.general_pool_id:
        return s.general_pool
    pool = TalentPool.objects.filter(is_general=True).first() or TalentPool.objects.filter(name='General applications').first()
    if pool is None:
        pool = TalentPool.objects.create(name='General applications', is_general=True,
                                         description='CVs sent from the careers page without a job.')
    s.general_pool = pool
    s.save(update_fields=['general_pool'])
    return pool


def _tell(s, cand, job, app, failed):
    what = f'{job.job_code} – {job.title}' if job else 'general application (talent pool)'
    try:
        if job and job.hiring_manager_id:
            from zeo.module_helpers import notify
            notify(user=job.hiring_manager, title='New application', notification_type='recruitment',
                   message=f'{cand.full_name} applied for {job.title} on the careers page.' + (' Must-have answers not met.' if failed else ''))
    except Exception:
        log.exception('careers notify')
    if s.notify_emails:
        body = (f'{cand.full_name} <{cand.email}> – {what}.\n'
                + (f'Must-have answers not met: {"; ".join(failed)}\n' if failed else '')
                + (f'\nOpen ZEO: {app_url()}/main-sidebar/recruitment-options/candidates' if app_url() else ''))
        send_mail([x for x in re.split(r'[,;\s]+', s.notify_emails) if x], f'New application: {what}', body)
    if s.acknowledge:
        c = company()
        send_mail([cand.email], f'{c.name if c else "Careers"}: your application' + (f' for {job.title}' if job else ''),
                  f'Dear {cand.first_name},\n\n{s.acknowledge_text}\n\n' + (f'Job: {job.title} ({job.job_code})\n' if job else '') + (c.name if c else ''))


# ---------------------------------------------------------------------------------------------- feeds
def _feed_open(key):
    s = CareersSettings.get()
    if not (s.enabled and s.feed_enabled):
        raise CareersError(tr('The job feed is off.'), status=404)
    if s.feed_key and key != s.feed_key:
        raise CareersError(tr('Unknown feed key.'), status=403)
    return s


JOB_TYPES = {'full_time': 'fulltime', 'part_time': 'parttime', 'contract': 'contract', 'intern': 'internship'}


def feed_json(key=''):
    s = _feed_open(key)
    c = company()
    out = []
    for j in open_jobs():
        d = job_card(j, s, full=True)
        d.pop('questions', None)
        d.update({'url': page_url(j.job_code), 'apply_url': page_url(j.job_code, 'feed'), 'company': c.name if c else '',
                  'country': getattr(getattr(c, 'country', None), 'country_name', '') if c else ''})
        out.append(d)
    return {'company': c.name if c else '', 'updated': timezone.now(), 'jobs': out}


def feed_xml(key=''):
    """Indeed-style XML (also read by LinkedIn job wrapping, Bayt, Naukrigulf and most aggregators)."""
    s = _feed_open(key)
    c = company()
    name = escape(c.name if c else '')
    country = escape(str(getattr(getattr(c, 'country', None), 'country_name', '') or '')) if c else ''
    cdata = lambda v: '<![CDATA[' + str(v or '').replace(']]>', ']]&gt;') + ']]>'
    parts = ['<?xml version="1.0" encoding="utf-8"?>', '<source>', f'<publisher>{name}</publisher>',
             f'<publisherurl>{escape(page_url())}</publisherurl>', f'<lastBuildDate>{timezone.now():%a, %d %b %Y %H:%M:%S %z}</lastBuildDate>']
    for j in open_jobs():
        desc = (j.description or '') + ('\n\nRequirements:\n' + j.requirements if j.requirements else '')
        sal = ''
        if s.show_salary and (j.min_salary or j.max_salary):
            n = lambda x: f'{x:,.0f}' if x == int(x) else f'{x:,.2f}'
            sal = f'<salary>{cdata(" – ".join(n(x) for x in (j.min_salary, j.max_salary) if x) + " " + currency() + " per month")}</salary>'
        remote = {"remote": "Fully remote", "hybrid": "Hybrid remote"}.get(j.workplace, "")
        parts.append('<job>' + ''.join([
            f'<title>{cdata(j.title)}</title>',
            f'<date>{cdata((j.published_on or j.created_at).strftime("%a, %d %b %Y %H:%M:%S %z"))}</date>',
            f'<referencenumber>{cdata(j.job_code)}</referencenumber>',
            f'<url>{cdata(page_url(j.job_code, "feed"))}</url>',
            f'<company>{cdata(c.name if c else "")}</company>',
            f'<city>{cdata(j.location or (str(j.branch) if j.branch else ""))}</city>',
            f'<country>{cdata(country)}</country>',
            f'<description>{cdata(desc)}</description>',
            sal,
            f'<jobtype>{cdata(JOB_TYPES.get(j.employment_type, "fulltime"))}</jobtype>',
            f'<category>{cdata(str(j.department) if j.department else "")}</category>',
            (f'<experience>{cdata(f"{j.experience_min_years}+ years")}</experience>' if j.experience_min_years else ''),
            (f'<remotetype>{cdata(remote)}</remotetype>' if remote else ''),
            (f'<expirationdate>{cdata(j.closing_date.strftime("%Y-%m-%d"))}</expirationdate>' if j.closing_date else ''),
        ]) + '</job>')
    parts.append('</source>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------------------------- talent pools
def add_to_pool(pool, candidates, user=None, note=''):
    added = 0
    for c in candidates:
        _, made = TalentPoolMember.objects.get_or_create(pool=pool, candidate=c, defaults={'added_by': user, 'note': note[:255]})
        added += int(made)
    return added


def reengage(pool, user, subject, message, job=None, member_ids=None):
    """E-mail the pool's members (or the chosen ones) – only those who agreed that we keep their CV, with an e-mail address."""
    subject, message = (subject or '').strip(), (message or '').strip()
    if not subject:
        raise CareersError(tr('Enter a subject.'), field='subject')
    if not message:
        raise CareersError(tr('Enter the message.'), field='message')
    if job is not None and not job.on_careers_page:
        raise CareersError(tr('The job is not open on the careers page: publish it with the "Careers page" channel first.'), field='job')
    qs = pool.members.select_related('candidate')
    if member_ids:
        qs = qs.filter(pk__in=member_ids)
    sent, skipped, to = 0, [], []
    now = timezone.now()
    c = company()
    for m in qs:
        cand = m.candidate
        if m.status in ('not_interested', 'hired'):
            skipped.append({'candidate': cand.pk, 'name': cand.full_name, 'reason': m.get_status_display()})
            continue
        if not cand.may_contact:
            skipped.append({'candidate': cand.pk, 'name': cand.full_name, 'reason': 'No consent to keep the CV (or it ran out)'})
            continue
        if job is not None and Application.objects.filter(candidate=cand, job=job).exists():
            skipped.append({'candidate': cand.pk, 'name': cand.full_name, 'reason': 'Already applied for this job'})
            continue
        body = f'Dear {cand.first_name},\n\n{message}\n'
        if job is not None:
            body += f'\n{job.title} ({job.job_code}): {page_url(job.job_code, "pool")}\n'
        body += f'\n{c.name if c else ""}\nYou receive this because you agreed that we keep your CV. Reply to this e-mail to be removed.'
        ok = send_mail([cand.email], subject, body)
        if not ok:
            skipped.append({'candidate': cand.pk, 'name': cand.full_name, 'reason': 'The e-mail could not be sent'})
            continue
        sent += 1
        to.append(cand.pk)
        m.status = 'contacted' if m.status == 'active' else m.status
        m.last_contacted = now
        m.save(update_fields=['status', 'last_contacted'])
        cand.last_contacted = now
        cand.save(update_fields=['last_contacted'])
    camp = TalentCampaign.objects.create(pool=pool, job=job, subject=subject[:200], message=message, sent_by=user, sent_count=sent,
                                         skipped=skipped, recipients=to)
    return camp


def suggest_for_job(job, limit=50):
    """Pool members and earlier candidates whose tags / skills match the job title or its requirements and who may be contacted."""
    words = {w for w in re.findall(r'[a-zA-Z][a-zA-Z+#.]{2,}', f'{job.title} {job.requirements or ""}'.lower())
             if w not in ('and', 'the', 'with', 'for', 'years', 'experience', 'year', 'able', 'good', 'work', 'must', 'have', 'will')}
    out = []
    applied = set(job.applications.values_list('candidate_id', flat=True))
    for cand in Candidate.objects.exclude(pk__in=applied).order_by('-created_at')[:2000]:
        if not cand.may_contact:
            continue
        hay = ' '.join([' '.join(str(t) for t in (cand.tags or [])), cand.skills or '', cand.current_designation or '']).lower()
        hits = sorted(w for w in words if w in hay)
        if hits:
            out.append({'id': cand.pk, 'name': cand.full_name, 'email': cand.email, 'tags': cand.tags or [], 'matches': hits,
                        'score': len(hits), 'experience_years': cand.experience_years})
    out.sort(key=lambda r: (-r['score'], r['name']))
    return out[:limit]
