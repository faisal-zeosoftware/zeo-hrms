"""AI Recruitment Assistant – CV extraction, skill match, summaries and missing qualifications, job descriptions and interview
questions, interview scheduling help, time-to-hire. Assists the recruiter; never decides."""
import re
from collections import Counter
from datetime import date, datetime, timedelta

from django.apps import apps
from django.db.models import Avg, Count, F

M = apps.get_model
STOP = set('and or the a an of to in for with on at by from as is are be this that we you our your will can shall must have has had not no yes per all any'
           ' years year experience experienced skills skill required requirement requirements responsibilities responsibility role job work working ability able'
           ' good strong excellent knowledge etc including including also such well team company candidate candidates position preferred plus'.split())
SKILL_HINTS = ['excel', 'word', 'powerpoint', 'sap', 'odoo', 'oracle', 'tally', 'quickbooks', 'autocad', 'revit', 'primavera', 'ms project', 'python', 'java', 'sql', 'javascript',
               'angular', 'react', 'django', '.net', 'php', 'linux', 'aws', 'azure', 'networking', 'cisco', 'firewall', 'crm', 'erp', 'hr', 'payroll', 'wps', 'recruitment', 'ifrs', 'vat',
               'audit', 'budgeting', 'forecasting', 'procurement', 'logistics', 'supply chain', 'sales', 'marketing', 'customer service', 'project management', 'pmp', 'prince2',
               'nebosh', 'iosh', 'osha', 'iso 9001', 'iso 45001', 'iso 14001', 'first aid', 'hse', 'safety', 'quality', 'qa', 'qc', 'mep', 'hvac', 'electrical', 'plumbing', 'civil',
               'structural', 'surveying', 'estimation', 'tendering', 'contracts', 'fidic', 'arabic', 'english', 'hindi', 'urdu', 'french', 'driving licence', 'uae driving license',
               'nursing', 'dha', 'moh', 'haad', 'bls', 'acls', 'pharmacy', 'laboratory', 'radiology', 'teaching', 'curriculum', 'cambridge', 'ib', 'accounting', 'cpa', 'acca', 'cma',
               'mba', 'bachelor', 'master', 'diploma', 'phd', 'degree', 'certified', 'chartered', 'photoshop', 'illustrator', 'seo', 'social media', 'retail', 'hospitality', 'f&b',
               'food safety', 'haccp', 'barista', 'cashier', 'pos', 'warehouse', 'forklift', 'inventory', 'maintenance', 'cctv', 'security', 'sira', 'documentation', 'negotiation']
MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
EDU_WORDS = {'mba', 'bachelor', 'master', 'diploma', 'phd', 'degree', 'certified', 'chartered'}
NOISE = {'email', 'phone', 'mobile', 'nationality', 'address', 'profile', 'summary', 'objective', 'present', 'current', 'date', 'birth', 'status', 'visa', 'references', 'available', 'request'}


def cv_text(candidate):
    """Text of the candidate's CV (PDF text or OCR through AISettings.docread) + the fields typed on the candidate."""
    txt = ''
    try:
        if candidate.cv:
            from AISettings.docread import extract_text
            candidate.cv.open('rb')
            data = candidate.cv.read()
            name = candidate.cv.name
            low = name.lower()
            if low.endswith('.docx'):
                try:
                    import io
                    from docx import Document
                    txt = '\n'.join(p.text for p in Document(io.BytesIO(data)).paragraphs)
                except Exception:
                    txt = ''
            else:
                txt, _ = extract_text(name, data)
    except Exception:
        txt = ''
    typed = ' '.join(str(x or '') for x in (candidate.skills, candidate.languages, candidate.current_designation, candidate.current_employer, candidate.notes))
    return (txt or '') + '\n' + typed


def extract(text):
    """Fields a recruiter wants from a CV: contact, experience years, education, skills, employers, languages, certifications."""
    t = text or ''
    low = t.lower()
    out = {}
    m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', t)
    out['email'] = m.group(0) if m else ''
    m = re.search(r'(\+?\d[\d\s().-]{7,}\d)', t)
    out['phone'] = re.sub(r'[^\d+]', '', m.group(1))[:16] if m else ''
    yrs = [int(x) for x in re.findall(r'(\d{1,2})\+?\s*(?:years?|yrs)', low) if int(x) < 45]
    out['experience_years'] = max(yrs) if yrs else _years_from_dates(low)
    edu = []
    for pat, label in ((r'ph\.?d|doctorate', 'PhD'), (r'\bmba\b', 'MBA'), (r"master'?s?\b|\bm\.?sc\b|\bm\.?a\b|\bm\.?com\b|\bm\.?tech\b|\bm\.?e\b", 'Master'),
                       (r"bachelor'?s?\b|\bb\.?sc\b|\bb\.?a\b|\bb\.?com\b|\bb\.?tech\b|\bb\.?e\b|\bbba\b|\bbe\b", 'Bachelor'), (r'\bdiploma\b', 'Diploma'), (r'high school|secondary', 'High school')):
        if re.search(pat, low):
            edu.append(label)
    out['education'] = edu[:1] or []
    out['education_all'] = edu
    out['skills'] = sorted({h for h in SKILL_HINTS if h not in EDU_WORDS and re.search(r'(?<![a-z])' + re.escape(h) + r'(?![a-z])', low)})
    out['certifications'] = sorted({h for h in ('pmp', 'prince2', 'nebosh', 'iosh', 'osha', 'cpa', 'acca', 'cma', 'dha', 'moh', 'haad', 'bls', 'acls', 'haccp', 'sira', 'iso 9001', 'iso 45001', 'iso 14001', 'first aid') if h in out['skills']})
    out['languages'] = sorted({h.title() for h in ('arabic', 'english', 'hindi', 'urdu', 'french', 'tagalog', 'malayalam', 'tamil', 'bengali', 'russian', 'chinese') if re.search(r'(?<![a-z])' + h + r'(?![a-z])', low)})
    out['nationality'] = ''
    m = re.search(r'nationality\s*[:\-]?\s*([A-Za-z ]{3,25})', t, re.I)
    if m:
        out['nationality'] = m.group(1).strip().split('\n')[0][:25]
    m = re.search(r'(?:visa|visa status)\s*[:\-]?\s*([A-Za-z ,]{3,40})', t, re.I)
    out['visa'] = m.group(1).strip()[:40] if m else ''
    m = re.search(r'notice period\s*[:\-]?\s*(\d{1,3})\s*(?:days?|months?)', low)
    out['notice_days'] = int(m.group(1)) * (30 if 'month' in (m.group(0) if m else '') else 1) if m else None
    m = re.search(r'(?:driving licen[cs]e)[^.\n]{0,30}', low)
    out['driving_licence'] = bool(m)
    out['employers'] = _employers(t)
    words = [w for w in re.findall(r'[a-z][a-z+#]{2,}', re.sub(r'\S+@\S+|www\.\S+|https?://\S+', ' ', low)) if w not in STOP and w not in NOISE]
    out['keywords'] = [w for w, _ in Counter(words).most_common(25)]
    out['chars'] = len(t)
    return out


def _years_from_dates(low):
    ys = [int(y) for y in re.findall(r'(?:19|20)\d{2}', low)]
    ys = [y for y in ys if 1980 <= y <= date.today().year]
    return max(0, min(40, (date.today().year if re.search(r'present|current|till date|to date', low) else max(ys)) - min(ys))) if len(ys) >= 2 else 0


def _employers(t):
    out = []
    for m in re.finditer(r'(?:[–\-:,]\s*|^\s*)([A-Z][A-Za-z&.\- ]{2,60}?(?:LLC|L\.L\.C|FZE|FZCO|Ltd|Limited|Inc|Group|Company|Co\.|Contracting|Trading|Hospital|School|Bank|Holding|International|Technologies|Solutions|Onshore|University))\b', t, re.M):
        name = m.group(1).strip()
        if name not in out:
            out.append(name)
    return out[:8]


def job_requirements(job):
    txt = ' '.join(str(x or '') for x in (job.title, job.description, job.requirements))
    low = txt.lower()
    req = {'skills': sorted({h for h in SKILL_HINTS if h not in EDU_WORDS and re.search(r'(?<![a-z])' + re.escape(h) + r'(?![a-z])', low)}),
           'experience_min_years': float(job.experience_min_years or 0), 'education': ''}
    for pat, label in ((r'\bmba\b', 'MBA'), (r"master'?s?\b", 'Master'), (r"bachelor'?s?\b|\bdegree\b", 'Bachelor'), (r'\bdiploma\b', 'Diploma')):
        if re.search(pat, low):
            req['education'] = label
            break
    words = [w for w in re.findall(r'[a-z][a-z+#]{3,}', low) if w not in STOP and w not in NOISE]
    req['keywords'] = [w for w, _ in Counter(words).most_common(20)]
    return req


EDU_RANK = {'High school': 1, 'Diploma': 2, 'Bachelor': 3, 'Master': 4, 'MBA': 4, 'PhD': 5}


def match(candidate, job, cv=None):
    """Score 0–100 with the reasons: skills in the job found in the CV, experience, education, keyword overlap. Missing items listed."""
    cv = cv if cv is not None else extract(cv_text(candidate))
    req = job_requirements(job)
    parts, missing, reasons = [], [], []
    if req['skills']:
        have = [s for s in req['skills'] if s in cv['skills']]
        miss = [s for s in req['skills'] if s not in cv['skills']]
        sc = 100 * len(have) / len(req['skills'])
        parts.append((sc, 0.5))
        reasons.append(f'Skills: {len(have)} of {len(req["skills"])} asked for ({", ".join(have[:8]) or "none"}).')
        missing += [f'Skill: {s}' for s in miss]
    exp_need = req['experience_min_years'] or float(getattr(job, 'experience_min_years', 0) or 0)
    exp = float(cv.get('experience_years') or candidate.experience_years or 0)
    if exp_need:
        sc = min(100, 100 * exp / exp_need)
        parts.append((sc, 0.25))
        reasons.append(f'Experience: {exp:g} years against {exp_need:g} required.')
        if exp < exp_need:
            missing.append(f'Experience: {exp_need - exp:g} year(s) short')
    if req['education']:
        best = max((EDU_RANK.get(e, 0) for e in cv.get('education_all', [])), default=0)
        sc = 100 if best >= EDU_RANK.get(req['education'], 0) else 40 if best else 0
        parts.append((sc, 0.15))
        reasons.append(f'Education: {req["education"]} asked, CV shows {", ".join(cv.get("education_all", [])) or "nothing readable"}.')
        if sc < 100:
            missing.append(f'Education: {req["education"]}')
    kw = set(req['keywords']) & set(cv.get('keywords', []))
    if req['keywords']:
        parts.append((100 * len(kw) / len(req['keywords']), 0.10))
        reasons.append(f'Wording overlap with the job: {len(kw)} of {len(req["keywords"])} key terms.')
    score = round(sum(s * w for s, w in parts) / sum(w for _, w in parts)) if parts else 0
    return {'score': score, 'reasons': reasons, 'missing': missing, 'cv': cv, 'requirements': req}


def summary(candidate, cv=None):
    cv = cv if cv is not None else extract(cv_text(candidate))
    bits = [f'{candidate.first_name} {candidate.last_name}'.strip()]
    if candidate.current_designation or candidate.current_employer:
        bits.append(f'currently {candidate.current_designation or "working"}{" at " + candidate.current_employer if candidate.current_employer else ""}')
    if cv.get('experience_years'):
        bits.append(f'about {cv["experience_years"]} years of experience')
    if cv.get('education'):
        bits.append(f'{cv["education"][0]} level education')
    if cv.get('skills'):
        bits.append('skills: ' + ', '.join(cv['skills'][:10]))
    if cv.get('certifications'):
        bits.append('certified: ' + ', '.join(c.upper() for c in cv['certifications']))
    if cv.get('languages'):
        bits.append('languages: ' + ', '.join(cv['languages']))
    if cv.get('employers'):
        bits.append('employers seen: ' + ', '.join(cv['employers'][:4]))
    if candidate.notice_period_days:
        bits.append(f'notice {candidate.notice_period_days} days')
    if candidate.expected_salary:
        bits.append(f'expects {float(candidate.expected_salary):,.0f}')
    return '; '.join(bits) + '.'


def job_description(job):
    """A job description from the opening's fields – the recruiter edits it. Optional external AI polishes the wording."""
    req = job_requirements(job)
    dept = getattr(job.department, 'dept_name', '') if job.department_id else ''
    loc = job.location or (getattr(job.branch, 'branch_name', '') if job.branch_id else '')
    lines = [f'# {job.title}', '',
             f'{dept + " · " if dept else ""}{loc}{" · " + job.get_employment_type_display() if hasattr(job, "get_employment_type_display") else ""}'.strip(' ·'), '',
             '## About the role', job.description or f'We are looking for a {job.title} to join our {dept or "team"}.', '',
             '## What you will do',
             *([f'- {x.strip()}' for x in re.split(r'[.\n;]+', job.description or '') if len(x.strip()) > 20][:6] or ['- Deliver the responsibilities of the role to the agreed standards', '- Work with the team and other departments', '- Report progress to the hiring manager']), '',
             '## What we need',
             *([f'- {req["experience_min_years"]:g}+ years of relevant experience'] if req['experience_min_years'] else []),
             *([f'- {req["education"]} degree or equivalent'] if req['education'] else []),
             *[f'- {s.title()}' for s in req['skills'][:10]],
             *[f'- {x.strip()}' for x in re.split(r'[.\n;]+', job.requirements or '') if len(x.strip()) > 15][:6], '',
             '## What we offer',
             f'- Salary {float(job.min_salary):,.0f} – {float(job.max_salary):,.0f}' if job.min_salary and job.max_salary else '- Competitive salary',
             '- Annual leave, medical insurance and air ticket as per company policy',
             *(['- This position is part of our Emiratisation programme'] if getattr(job, 'is_emiratisation', False) else []), '',
             f'Closing date: {job.closing_date}' if job.closing_date else '']
    text = '\n'.join(lines)
    return polish('job_description', text, f'Rewrite this job description in clear, professional English. Keep every fact; do not invent requirements or benefits.')


def interview_questions(job, candidate=None):
    req = job_requirements(job)
    qs = [('Opening', f'Walk us through your experience most relevant to the {job.title} role.'), ('Opening', 'Why are you interested in this role and in our company?')]
    for s in req['skills'][:6]:
        qs.append(('Skills', f'Describe a situation where you used {s.title()} – what was the task, what did you do, what was the result?'))
    if req['experience_min_years']:
        qs.append(('Experience', f'The role needs {req["experience_min_years"]:g}+ years; which part of your experience best matches, and where would you need support?'))
    qs += [('Behaviour', 'Tell us about a conflict with a colleague or customer and how you resolved it.'),
           ('Behaviour', 'Describe a time you had to deliver under a tight deadline. What did you prioritise?'),
           ('Behaviour', 'Give an example of a mistake you made at work and what you changed afterwards.'),
           ('Fit', 'What do you expect from your manager? What kind of team do you work best in?'),
           ('Practical', f'Notice period, expected salary and earliest joining date? (job range {float(job.min_salary or 0):,.0f} – {float(job.max_salary or 0):,.0f})')]
    if candidate is not None:
        m = match(candidate, job)
        for miss in m['missing'][:3]:
            qs.append(('Gap', f'The CV does not show "{miss.split(": ", 1)[-1]}" – how would you cover this?'))
    return [{'area': a, 'question': q} for a, q in qs]


def polish(feature, text, instruction):
    """External AI only when the company switched it on for 'ask'; otherwise the template text is returned as it is."""
    try:
        from AISettings import service as A
        r = A.chat(None, 'ask', instruction, [{'role': 'user', 'content': text}], max_tokens=1500)
        out = ''
        for b in (r.get('content') or []):
            if b.get('type') == 'text':
                out += b.get('text', '')
        return out.strip() or text
    except Exception:
        return text


def drafts(application, kind):
    """Candidate communications: invitation, regret, offer-stage note. Placeholders filled from the application."""
    c, j = application.candidate, application.job
    name = f'{c.first_name}'.strip()
    iv = application.interviews.order_by('-scheduled_at').first() if hasattr(application, 'interviews') else None
    when = iv.scheduled_at.strftime('%A %d %B %Y at %H:%M') if iv and iv.scheduled_at else '[date and time]'
    where = (iv.location_or_link if iv else '') or '[location or video link]'
    T = {
        'invite': (f'Interview invitation – {j.title}', f'Dear {name},\n\nThank you for applying for the position of {j.title}. We would like to invite you to an interview on {when}.\nVenue / link: {where}\nPlease bring your passport copy, certificates and a copy of your CV, and confirm your attendance by replying to this e-mail.\n\nKind regards,\nRecruitment team'),
        'regret': (f'Your application – {j.title}', f'Dear {name},\n\nThank you for the time you invested in applying for {j.title}. After careful consideration we will not be taking your application further at this stage. We will keep your CV on file for 12 months and contact you if a suitable role comes up.\n\nWe wish you every success.\n\nKind regards,\nRecruitment team'),
        'hold': (f'Your application – {j.title}', f'Dear {name},\n\nThank you for your application for {j.title}. The process is taking a little longer than planned; we expect to come back to you within two weeks.\n\nKind regards,\nRecruitment team'),
        'offer_note': (f'Offer – {j.title}', f'Dear {name},\n\nWe are pleased to move to the offer stage for {j.title}. Our HR team will send the offer letter for your review; please let us know your earliest joining date and any questions.\n\nKind regards,\nRecruitment team'),
    }
    s, b = T.get(kind, T['hold'])
    return {'subject': s, 'body': b}


def slots(job, days=7, per_day=('10:00', '11:00', '14:00', '15:00')):
    """Free interview slots for the hiring manager: working days, skipping times with an interview already."""
    Interview = M('RecruitmentManagement', 'Interview')
    taken = set(Interview.objects.filter(application__job=job, scheduled_at__gte=datetime.now()).values_list('scheduled_at', flat=True))
    taken = {t.replace(second=0, microsecond=0) for t in taken}
    out = []
    d = date.today() + timedelta(days=1)
    while len(out) < 12 and (d - date.today()).days <= days + 3:
        if d.weekday() < 5:
            for hm in per_day:
                h, mi = map(int, hm.split(':'))
                dt = datetime(d.year, d.month, d.day, h, mi)
                if dt not in taken:
                    out.append(dt)
        d += timedelta(days=1)
    return out[:12]


def metrics(days=180):
    """Pipeline and time-to-hire per job: applications by stage, days from opening to offer accepted, conversion."""
    Job = M('RecruitmentManagement', 'JobOpening')
    App = M('RecruitmentManagement', 'Application')
    Offer = M('RecruitmentManagement', 'Offer')
    since = date.today() - timedelta(days=days)
    rows = []
    for j in Job.objects.filter(created_at__date__gte=since).select_related('department'):
        apps_ = App.objects.filter(job=j)
        by = {x['stage']: x['n'] for x in apps_.values('stage').annotate(n=Count('id'))}
        offers = Offer.objects.filter(application__job=j)
        accepted = offers.filter(status__in=('accepted', 'joined')).order_by('responded_on')
        tth = None
        if accepted.exists() and accepted.first().responded_on:
            tth = (accepted.first().responded_on - j.created_at.date()).days
        rows.append({'job': j.title, 'code': j.job_code, 'status': j.status, 'openings': j.openings, 'opened': j.created_at.date(), 'department': getattr(j.department, 'dept_name', '') if j.department_id else '',
                     'applied': by.get('applied', 0), 'screening': by.get('screening', 0), 'interview': by.get('interview', 0), 'offer': by.get('offer', 0), 'hired': by.get('hired', 0) + by.get('joined', 0),
                     'rejected': by.get('rejected', 0), 'total': apps_.count(), 'offers': offers.count(), 'accepted': accepted.count(), 'time_to_hire_days': tth,
                     'days_open': (date.today() - j.created_at.date()).days if j.status in ('open', 'published') else None})
    tths = [r['time_to_hire_days'] for r in rows if r['time_to_hire_days'] is not None]
    return {'jobs': rows, 'avg_time_to_hire': round(sum(tths) / len(tths), 1) if tths else None, 'open_jobs': sum(1 for r in rows if r['days_open'] is not None),
            'applications': sum(r['total'] for r in rows), 'offer_acceptance': round(100 * sum(r['accepted'] for r in rows) / max(1, sum(r['offers'] for r in rows)))}
