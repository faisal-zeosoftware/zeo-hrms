"""Ask ZEO – the question router. Without an AI key (1.71.0) every answer comes from rules: the intent (words.py), then the
handler for it. With the company's AI key (1.72.0, ai.py) free questions go to the AI with ZEO's tools; the rule answers
stay first for what they cover, so the figures are always ZEO's own.

An answer: {text (markdown), source (data / document / general / help / check), intent, cards [...], links [...],
            follow [suggested questions]}
"""
import logging
import re

from Translations.i18n import tr

from . import changes, checks, explain, guided, help as H, ready
from . import scope as S
from .words import canon, has_arabic, intent_of, month_in

log = logging.getLogger(__name__)
MS = '/main-sidebar/'

EXAMPLES = {
    'en': ['What is my leave balance?', 'Explain my last payslip', 'How do I add a new employee?', 'Whose documents expire in the next 30 days?',
           'Total net pay by department for last month', 'Check the payroll before approval', 'Am I ready to go live?', 'Check data quality',
           'Run payroll', 'I need a salary certificate', 'Which tickets are open, by priority?', 'Occupancy by building',
           'What is waiting for me?', 'Is CRM ready?'],   # 1.73.0 every app
    'ar': ['ما هو رصيد اجازتي؟', 'اشرح قسيمة راتبي', 'كيف اضيف موظف جديد؟', 'من تنتهي وثائقه هذا الشهر؟', 'مجموع صافي الراتب حسب القسم الشهر الماضي',
           'فحص الرواتب قبل الاعتماد', 'هل أنا جاهز للتشغيل الفعلي؟', 'فحص جودة البيانات', 'شغل الرواتب', 'أحتاج شهادة راتب', 'ماذا ينتظرني؟'],
}


def examples(c, lang):
    """Example questions this user can ask (the HR ones only for HR users)."""
    hr_only = {'Check the payroll before approval', 'Am I ready to go live?', 'Check data quality', 'Run payroll', 'Total net pay by department for last month',
               'Whose documents expire in the next 30 days?', 'فحص الرواتب قبل الاعتماد', 'هل أنا جاهز للتشغيل الفعلي؟', 'فحص جودة البيانات', 'شغل الرواتب',
               'مجموع صافي الراتب حسب القسم الشهر الماضي', 'من تنتهي وثائقه هذا الشهر؟'}
    active = H.active_apps()
    out = {}
    for lg, xs in EXAMPLES.items():
        keep = []
        for x in xs:
            if x in hr_only and not S.is_hr(c):
                continue
            if 'tickets' in x and not active('projects'):
                continue
            if 'Occupancy' in x and not active('property'):
                continue
            if 'CRM' in x and not (active('crm') and S.is_hr(c)):
                continue
            keep.append(x)
        out[lg] = keep
    return out


def _who(c, question, cq):
    """The employee the question is about: 'my' → the user; a name / code → that employee (HR); else the user."""
    if re.search(r'\b(my|me|i|mine)\b', cq):
        return c.emp, None
    if not S.is_hr(c):
        # v1.73.2: a colleague's payslip / leave / settlement was answered with the user's own, silently
        if re.search(r'\b(of|for|about)\s+(?!me\b|myself\b)\S+', cq) or re.search(r'\b[a-z]{1,5}-?\d{2,6}\b', cq):
            return None, tr('I can explain your own payslip, leave balance and settlement. For a colleague\'s, ask HR.')
        return c.emp, None
    e = S.find_employee(c, question)
    if e is not None:
        return e, None
    if c.emp is not None:
        return c.emp, None
    return None, tr('Name the employee (name or employee code), for example: "Explain the payslip of E0002".')


def _src(d, source, intent):
    d.setdefault('source', source)
    d.setdefault('intent', intent)
    return d


def answer(request, conv, question, uploads=(), suite='', branch='', lang='en'):
    c = S.ctx(request)
    cq = canon(question)
    intent = intent_of(cq)
    if uploads:
        intent = upload_intent(cq, intent, uploads)
    elif intent in ('data', 'howto', 'general', 'guide'):   # 1.73.0: a record number of any app ("explain WO-00012") – read from ZEO itself
        from .allapps import find_record, number_in
        if number_in(question) and find_record(request, c, question) is not None:
            intent = 'record'
    # 1.72.0: the company's AI (ai.py) takes free questions and the work it is needed for; the rules keep what they answer best
    from . import ai
    note = ''
    if ai.ready(request) and intent in ai.AI_INTENTS:
        try:
            got = ai.answer(request, c, conv, question, uploads, suite, branch, lang, intent)
            if got is not None:
                return got
        except ai.Unavailable as e:
            log.info('Ask ZEO AI not used: %s', e)
            note = tr("The company's AI could not be used ({0}) – this answer comes from ZEO without it.", str(e), lang=lang)
    try:
        out = route(request, c, conv, question, cq, intent, uploads, suite, branch, lang)
    except changes.Refused as e:
        out = {'text': str(e), 'source': 'check', 'intent': intent}
    if note:
        out['text'] = f'_{note}_\n\n' + (out.get('text') or '')
    return out


AI_WORK = {'write': 'Writing letters and messages', 'translate': 'Translating', 'quiz': 'Quizzes and test scripts', 'bulk': 'Changes from a sentence',
           'map': 'Mapping a file in any layout to the data workbook', 'sop': 'Turning an SOP into ZEO steps and a Word guide',
           'policy_setup': 'Setting up from a policy document', 'read_doc': 'Answering from an attached document',
           'sop_app': 'Making an app from an SOP', 'write_script': 'Writing a ZEO Developer script',   # v1.73.4
           'write_module': 'Drafting a full code module'}   # v1.73.5


def upload_intent(cq, intent, uploads):
    kinds = {u.kind for u in uploads}
    if 'workbook' in kinds:
        return 'template'
    if 'spreadsheet' in kinds:
        return 'map' if intent not in ('translate',) else intent
    if 'video' in kinds:   # v1.73.4 – a filmed SOP: only for the app builder
        return 'sop_app'
    if 'document' in kinds or 'image' in kinds:
        if intent in ('translate', 'write', 'quiz'):
            return intent
        if re.search(r'\b(app|module|register|form|system)\b', cq) and re.search(r'\b(make|build|create|turn|design|set up|develop)\b', cq):
            return 'sop_app'   # v1.73.4 (A10) the app itself from the SOP
        if re.search(r'\b(sop|procedure|workflow|process|steps|guide)\b', cq):
            return 'sop'
        if re.search(r'\b(set ?up|configure|apply|policy|policies|leave days|allowances?)\b', cq):
            return 'policy_setup'
        return 'read_doc'
    return intent


def route(request, c, conv, question, cq, intent, uploads, suite, branch, lang):
    L = lang
    if intent == 'map' and any(u.kind == 'spreadsheet' for u in uploads or []):
        # without the AI: say the file is not a ZEO data workbook, with the template (as in 1.71.0) – mapping it needs the AI
        return _src(ready.template(request, c, conv, cq, uploads, L), 'check', 'map')
    if intent in AI_WORK:
        return {'text': tr("{0} needs the company's AI. An administrator connects it in Settings › AI settings (with the "
                           "company's own key) and switches it on for Ask ZEO.", tr(AI_WORK[intent], lang=L), lang=L)
                + ('\n\n' + tr('Without it, Ask ZEO can still check a ZEO data workbook – download the template from Settings › Data workbook.', lang=L)
                   if intent == 'map' else ''),
                'source': 'check', 'intent': intent, 'links': [{'label': tr('Settings › AI settings', lang=L), 'url': MS + 'settings/ai-settings'}]}
    if intent == 'load_workbook':
        return _src(ready.load_workbook(request, c, conv, L), 'data', intent)
    if intent == 'build_report':   # 1.73.2 reports and dashboards from a sentence (report builder)
        from . import reportbuild
        return reportbuild.build(request, c, conv, question, L)
    if intent in ('build_module', 'module_field'):   # 1.74.0 development without programming
        from . import developer
        got = developer.build(request, c, conv, question, L) if intent == 'build_module' else developer.add_field(request, c, conv, question, L)
        if got is not None:
            return got
        intent = 'howto'
    if intent in ('record', 'app_setup', 'my_work'):   # 1.73.0 every app: a record by its number, the setup check of an app
        from . import allapps
        got = allapps.route(request, c, conv, question, cq, intent, suite, L)
        if got is not None:
            return got
        intent = 'data'
    if intent in ('howto', 'data', 'guide', 'general'):   # 1.72.0: a company document that clearly answers it comes first
        from . import docs
        if docs.strong(c, question):
            intent = 'document'
    if intent == 'document':
        from . import docs
        got = docs.answer_builtin(c, question, L)
        if got:
            return got
        intent = 'product'
    if intent in ('product', 'law'):
        from . import product
        got = product.answer_builtin(c, question, L)
        if intent == 'law':
            note = tr("Labour-law questions are answered with the company's AI (with two sources). What ZEO applies is in its guides:", lang=L)
            txt = note + ('\n\n' + got['text'] if got else '') + '\n\n_' + tr('This is general information, not legal advice. Check with your legal adviser.', lang=L) + '_'
            return {'text': txt, 'source': 'help', 'intent': 'law', 'links': (got or {}).get('links') or []}
        if got:
            return got
        intent = 'howto'
    if intent == 'data' and re.search(r'\b(payslip|leave balance|gratuity|settlement)\b', cq) and not re.search(r'\b(total|sum|by|per|all|how many|list|who|which|whose)\b', cq):
        if re.search(r'\b(my|me|i)\b', cq) or (S.is_hr(c) and S.find_employee(c, question) is not None):
            intent = {'payslip': 'explain_payslip', 'leave balance': 'explain_leave', 'gratuity': 'explain_gratuity',
                      'settlement': 'explain_settlement'}[re.search(r'\b(payslip|leave balance|gratuity|settlement)\b', cq).group(1)]
    if intent == 'greeting':
        ex = examples(c, L)
        return {'text': tr('I am Ask ZEO. I answer from your company\'s data with your own rights, explain payslips, leave and settlements, '
                           'show where things are in ZEO, check the setup and the data, and guide you through the payroll. Changes are always '
                           'shown to you first and can be undone.', lang=L),
                'source': 'help', 'intent': intent, 'follow': ex.get(L) or ex['en']}
    if intent == 'undo':
        ch = changes.Change.objects.filter(user=request.user, state='applied', undoable=True).order_by('-applied_at').first()
        if ch is None:
            return {'text': tr('There is no change of yours to undo.', lang=L), 'source': 'check', 'intent': intent}
        return {'text': tr('Your last change was "{0}". Press Undo to put it back.', ch.title, lang=L), 'source': 'check', 'intent': intent,
                'cards': [dict(changes.card(ch, L), state='applied')]}
    if intent == 'guided_payroll':
        if not S.can(c, {'view_payrollrun', 'add_payrollrun', 'change_payrollrun'}):
            return {'text': tr('The guided payroll is for payroll users. You can ask about your own payslip, e.g. "Explain my last payslip".', lang=L), 'source': 'check', 'intent': intent}
        ym = month_in(question) or guided.default_month(c)
        st = guided.state(request, c, ym[0], ym[1], L)
        if st.get('error'):
            return {'text': st['error'], 'source': 'check', 'intent': intent}
        nxt = st['next'] or {}
        txt = tr('**Guided payroll – {0}**', st['month_name'], lang=L) + '\n\n' + (
            tr('Next step: {0}.', nxt['label'], lang=L) if nxt.get('label') else (nxt.get('reason') or ''))
        return {'text': txt, 'source': 'data', 'intent': intent, 'cards': [{'type': 'guided', 'flow': 'payroll', **st}]}
    if intent == 'precheck':
        if not S.can(c, {'view_payrollrun', 'change_payrollrun', 'view_payslip', 'add_payrollrun'}):
            return {'text': tr('The payroll pre-check is for payroll users.', lang=L), 'source': 'check', 'intent': intent}
        run = checks.pick_run(c, month_in(question), ['processed', 'Processed', 'pending']) or checks.pick_run(c, month_in(question))
        if run is None:
            return {'text': tr('No payroll run to check. Create the run first (or say "run payroll").', lang=L), 'source': 'check', 'intent': intent}
        from . import apicall
        try:
            d = checks.precheck(request, c, run, L)
        except apicall.ApiError as e:
            return {'text': e.detail, 'source': 'check', 'intent': intent}
        cn = d['counts']
        txt = tr('**Pre-check of {0}** ({1}/{2}): {3} critical, {4} warning(s), {5} for information.', run.name or run.pk, run.month, run.year,
                 cn.get('critical', 0), cn.get('warning', 0), cn.get('info', 0), lang=L)
        if d['summary']:
            txt += '\n\n' + d['summary']
        g = d.get('gate')
        g_state, g_msg = (g[0], g[1] if len(g) > 1 else '') if isinstance(g, (list, tuple)) else ((g or {}).get('state'), (g or {}).get('message'))
        if g_state not in (None, 'ok'):
            txt += '\n\n' + tr('Approval is held: {0}', tr(g_msg or '', lang=L), lang=L)
        return {'text': txt, 'source': 'check', 'intent': intent, 'cards': [{'type': 'findings', 'title': tr('Findings to look at', lang=L), 'items': d['findings'], 'more': d['more']}],
                'links': [{'label': tr('Open the payroll run', lang=L), 'url': d['link']}]}
    if intent == 'find_file':   # v1.73.10 the Document Center search in words, with the user's rights
        from ZeoDocs.phase5 import smart_search
        from ZeoDocs.services import Who
        r = smart_search(Who(request), question, 8)
        if not r['results']:
            return {'text': tr('No document found for "{0}" in the folders you may open.', question[:120], lang=L), 'source': 'data', 'intent': intent,
                    'links': [{'label': tr('Open the Document Center', lang=L), 'url': '/main-sidebar/documents?tab=search'}]}
        lines = [f"- **{x['number']}** {x['title']}" + (f" – {x['category']}" if x['category'] else '') + (f" ({tr('expires', lang=L)} {x['expiry_date']:%d/%m/%Y})" if x['expiry_date'] else '')
                 for x in r['results']]
        txt = tr('**{0} document(s) found:**', r['count'], lang=L) + '\n' + '\n'.join(lines)
        return {'text': txt, 'source': 'data', 'intent': intent,
                'links': [{'label': f"{x['number']} {x['title']}"[:80], 'url': f"/main-sidebar/documents?doc={x['id']}"} for x in r['results'][:5]]}
    if intent == 'payroll_health':
        if not S.can(c, {'view_payrollrun', 'change_payrollrun', 'view_payslip', 'add_payrollrun', 'view_salarycomponent'}):
            return {'text': tr('The payroll health check is for payroll users.', lang=L), 'source': 'check', 'intent': intent}
        from PayrollExtras.health import check as health_check
        emps = list(S.employees(c).filter(is_active=True).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id'))
        issues = health_check(emps)
        want = re.search(r'\b(basic)\b', cq) and 'no_basic' or (re.search(r'\b(salary|salaries)\b', cq) and re.search(r'\b(no|without|missing)\b', cq) and 'no_salary') \
            or (re.search(r'\b(rule)\b', cq) and 'no_rule') or ''
        if want:
            issues = [i for i in issues if i['code'] == want]
        counts = {}
        for i in issues:
            counts[i['code']] = counts.get(i['code'], 0) + 1
        if not issues:
            txt = tr('**Payroll health check**: no problems found for {0} employee(s).', len(emps), lang=L)
        else:
            txt = tr('**Payroll health check**: {0} problem(s) for {1} employee(s).', len(issues), len({i['employee_id'] for i in issues}), lang=L)
        items = [{'severity': 'warning', 'title': i['employee'] or tr('Payroll setup', lang=L), 'text': tr(i['text'], lang=L), 'link': i['link']} for i in issues[:20]]
        return {'text': txt, 'source': 'check', 'intent': intent, 'counts': counts,
                'cards': [{'type': 'findings', 'title': tr('Findings to look at', lang=L), 'items': items, 'more': max(0, len(issues) - 20)}],
                'links': [{'label': tr('Open the payroll health check', lang=L), 'url': '/main-sidebar/salary-options/payroll-health'}]}
    if intent == 'payroll_category':
        from PayrollExtras import categories as PC
        rows = PC.rows()
        rows = [r for r in rows if getattr(r, 'active', True)]
        names = ', '.join(((r.name_ar or r.name) if L == 'ar' else r.name) for r in rows[:30])
        txt = tr('**Payroll categories** ({0}): {1}.', len(rows), names, lang=L) + '\n\n' + \
            tr('Each salary item belongs to one category; reports can show columns by category (e.g. one Basic column even when the Basic item differs).', lang=L)
        if S.can(c, {'manage_payrollcategory', 'add_salarycomponent', 'change_salarycomponent'}):
            txt += '\n\n' + tr('To add one, open Payroll categories and press New category.', lang=L)
        return {'text': txt, 'source': 'data', 'intent': intent,
                'links': [{'label': tr('Open Payroll categories', lang=L), 'url': '/main-sidebar/salary-options/payroll-categories'}]}
    if intent == 'setup_check':
        if not (c.admin or S.can(c, {'change_emp_master', 'view_emp_master'})):
            return {'text': tr('The setup check is for the company administrator and HR.', lang=L), 'source': 'check', 'intent': intent}
        d = checks.setup_check(c, L)
        if not d['items']:
            txt = tr('Nothing is missing – the setup is ready.', lang=L)
        else:
            txt = tr('**Setup check:** {0} item(s) block go-live, {1} warning(s).', d['blocking'], d['warnings'], lang=L)
        if d['live_at']:
            txt += ' ' + tr('The company went live on {0}.', d['live_at'][:10], lang=L)
        return {'text': txt, 'source': 'check', 'intent': intent, 'cards': [{'type': 'checks', 'title': tr('Before go-live', lang=L), 'items': d['items'][:40]}],
                'links': [{'label': tr('Settings › Setup guide', lang=L), 'url': MS + 'settings/setup-guide'}]}
    if intent in ('data_quality', 'fix_eid'):
        if not (c.admin or S.can(c, {'change_emp_master', 'view_emp_master'})):
            return {'text': tr('The data quality check is for HR.', lang=L), 'source': 'check', 'intent': intent}
        d = checks.data_quality(c, L)
        total = sum(g['count'] for g in d['groups'])
        txt = tr('**Data quality** of {0} employee(s): {1} finding(s).', d['employees'], total, lang=L) if total else tr('No problems found in {0} employee(s).', d['employees'], lang=L)
        cards = [{'type': 'quality', 'groups': d['groups']}]
        if d['fixable_eid']:
            ops = [{'op': 'update', 'model': x['model'], 'id': x['id'], 'values': {x['field']: x['value']}, 'label': tr('Emirates ID of {0}', x['employee'], lang=L)}
                   for x in d['fixable_eid']]
            ch = changes.preview(request, conv, 'data_fix', tr('Write {0} Emirates ID number(s) as 784-YYYY-NNNNNNN-N', len(ops), lang=L), {'records': ops},
                                 tr('Only the way the number is written changes; the digits stay the same.', lang=L), group='setup')
            cards.append(changes.card(ch, L))
        return {'text': txt, 'source': 'check', 'intent': intent, 'cards': cards,
                'links': [{'label': tr('Expiring documents', lang=L), 'url': MS + 'sub-sidebar/document-expired'},
                          {'label': tr('WPS salary file data', lang=L), 'url': MS + 'report-options/r/wps'}]}
    if intent in ('explain_payslip', 'my_payslip', 'explain_leave', 'my_leave', 'explain_settlement', 'explain_gratuity'):
        if intent == 'my_leave' and not re.search(r'explain|why|how', cq):
            intent = 'explain_leave'
        emp, err = _who(c, question, cq)
        if emp is None:
            return {'text': err or tr('Your login is not linked to an employee.', lang=L), 'source': 'check', 'intent': intent}
        fn = {'explain_payslip': lambda: explain.payslip(c, emp, month_in(question), L), 'my_payslip': lambda: explain.payslip(c, emp, month_in(question), L),
              'explain_leave': lambda: explain.leave_balance(request, c, emp, L), 'my_leave': lambda: explain.leave_balance(request, c, emp, L),
              'explain_settlement': lambda: explain.settlement(request, c, emp, L), 'explain_gratuity': lambda: explain.gratuity(request, c, emp, L)}[intent]
        d = fn()
        if d.get('error'):
            return {'text': d['error'], 'source': 'data', 'intent': intent}
        return {'text': d['text'], 'source': 'data', 'intent': intent, 'cards': [d['card']] if d.get('card') else [], 'links': d.get('links') or [], 'figures': d.get('figures')}
    if intent == 'my_documents':
        d = explain.my_documents(c, L)
        if d.get('error'):
            return {'text': d['error'], 'source': 'data', 'intent': intent}
        return {'text': d['text'], 'source': 'data', 'intent': intent, 'cards': [d['card']] if d.get('card') else [], 'links': d.get('links') or []}
    if intent == 'letter':
        return _src(ready.letter(request, c, conv, cq, question, L), 'data', intent)
    if intent == 'template':
        return _src(ready.template(request, c, conv, cq, list(uploads), L), 'help', intent)
    if intent == 'form':
        if re.search(r'leave|vacation|day off', cq):
            return _src(ready.leave_form(request, c, L), 'help', intent)
        intent = 'howto'
    if intent == 'guide':
        gs = H.find_guides(c, question)
        if gs:
            return guide_answer(gs, L)
        intent = 'howto'
    if intent == 'howto':
        return howto(c, question, L)
    # 1.73.0: explain a record of any app by its name ("tell me about the lease of Marina Tower 1204")
    from . import allapps
    got = allapps.route(request, c, conv, question, cq, intent, suite, L)
    if got is not None:
        return got
    return data_answer(request, c, question, cq, suite, branch, L)


def guide_answer(gs, L):
    g = gs[0]
    steps = [{'text': s.get(L) or s['en'], 'link': s.get('link')} for s in g['steps']]
    return {'text': tr('**{0}** – {1} steps:', g['title'].get(L) or g['title']['en'], len(steps), lang=L), 'source': 'help', 'intent': 'guide',
            'cards': [{'type': 'guide', 'title': g['title'].get(L) or g['title']['en'], 'steps': steps}],
            'follow': [x['title'].get(L) or x['title']['en'] for x in gs[1:]]}


def howto(c, question, L):
    sc = H.find_screens(c, question)
    gs = H.find_guides(c, question, 1)
    news = H.find_news(question)
    if not sc and not gs:
        return {'text': tr('I did not find a screen for that. Try other words, or open the menu search (Ctrl+K).', lang=L), 'source': 'help', 'intent': 'howto'}
    lines = []
    if L != 'en':   # 1.70.4: the menu path in the user's language (each part as the menu shows it), not in English
        sc = [dict(s, where=' › '.join(tr(p, lang=L) for p in s['where'].split(' › ')), hint=tr(s['hint'], lang=L) if s.get('hint') else '') for s in sc]
    if sc:
        top = sc[0]
        lines.append(tr('Open **{0}**.', top['where'], lang=L) + (' ' + top['hint'] if top['hint'] else ''))
        for s in sc[1:]:
            lines.append('- ' + tr('or **{0}**', s['where'], lang=L) + (f" – {s['hint']}" if s['hint'] else ''))
    for n in news:
        lines.append(tr('New in {0}: {1} – {2}', n['version'], n['title'], n['text'], lang=L))
    if gs:
        lines.insert(0, tr('Follow the guide **{0}** below.', gs[0]['title'].get(L) or gs[0]['title']['en'], lang=L))
    out = {'text': '\n'.join(lines), 'source': 'help', 'intent': 'howto', 'links': [{'label': s['where'], 'url': s['link']} for s in sc]}
    if gs:
        g = guide_answer(gs, L)
        out['cards'] = g['cards']
    return out


SUITE_WORDS = [('projects', r'\b(support )?tickets?\b(?!.*\bair\b)|\bsla\b|\bamc\b|\bmilestones?\b|\bwork ?logs?\b|\butili[sz]ation\b|\bprojects?\b'),
               ('crm', r'\bpipeline\b|\bopportunit|\bleads?\b|\bdeals?\b|\bquotations?\b|\bsales orders?\b|\bfollow-?ups?\b'),
               ('property', r'\boccupan|\brent\b|\bleases?\b|\btenants?\b|\bunits?\b|\bbuildings?\b|\bcheques?\b'),
               ('maintenance', r'\bwork ?orders?\b|\bpreventive\b|\btechnicians?\b|\binspections?\b'),
               ('assets', r'\bdepreciation\b|\bspare parts?\b|\basset counts?\b|\bstock\b|\bwarrant(y|ies)\b|\basset register\b'),   # v1.73.2: + warranty
               ('hse', r'\bincidents?\b|\bnear miss|\bpermits?\b|\bcorrective actions?\b|\bhazards?\b|\bsafety\b')]
SUITE_APP = {'projects': 'projects', 'crm': 'crm', 'property': 'property', 'maintenance': 'maintenance', 'assets': 'assets', 'hse': 'hse'}


def guess_suite(cq):
    """The app a question is about, among the active apps (air tickets stay HR)."""
    if re.search(r'\bair ?tickets?\b', cq):
        return ''
    active = H.active_apps()
    for suite, rx in SUITE_WORDS:
        if re.search(rx, cq) and active(SUITE_APP[suite]):
            return suite
    return ''


def data_answer(request, c, question, cq, suite, branch, L):
    """The report centre of every app, with the user's rights (DashboardManagement.assistant – the built-in mode)."""
    from DashboardManagement.assistant import ask_basic
    suite = suite or guess_suite(cq)
    try:
        text, links = ask_basic(request, cq if has_arabic(question) else question, branch, suite or '')
    except Exception:
        log.exception('Ask ZEO data answer failed')
        text, links = tr('I could not answer that from the reports. Try asking it another way, or open Reports.', lang=L), []
    if text.startswith('I can answer questions about'):
        hs = H.find_screens(c, question, 2)
        if hs:
            return howto(c, question, L)
    return {'text': text, 'source': 'data', 'intent': 'data', 'links': links}
