"""People packs – awards, competencies, benefit plan templates, forms and a survey. General for every company; the other
industries add a few items. Packs only add; everything can be changed or deleted."""

AWARDS = [('STAR', 'Star of the month', 'star', 50, 'Outstanding work this month', 'managers', 1),
          ('THANKS', 'Thank you', 'favorite', 5, 'A small thank you from a colleague', 'all', 0),
          ('TEAM', 'Team player', 'groups', 10, 'Helped the team beyond the job', 'all', 0),
          ('CUSTOMER', 'Customer hero', 'support_agent', 20, 'Went the extra mile for a customer', 'managers', 0),
          ('SAFETY', 'Safety champion', 'health_and_safety', 20, 'Prevented an incident or improved safety', 'managers', 0),
          ('INNOVATION', 'Bright idea', 'lightbulb', 20, 'An improvement that was put to use', 'all', 0),
          ('SERVICE5', '5 years of service', 'military_tech', 100, 'Service award', 'hr', 0),
          ('SERVICE10', '10 years of service', 'workspace_premium', 200, 'Service award', 'hr', 0)]

COMPETENCIES = [('COMM', 'Communication', 'core', 'Shares information clearly, listens, adapts the message to the audience'),
                ('TEAM', 'Teamwork', 'core', 'Works with others, shares credit, helps colleagues'),
                ('CUST', 'Customer focus', 'core', 'Understands internal and external customers and delivers for them'),
                ('QUAL', 'Quality of work', 'core', 'Accurate, complete, on time'),
                ('PROB', 'Problem solving', 'core', 'Finds causes, proposes workable solutions'),
                ('ADAPT', 'Adaptability', 'core', 'Handles change and pressure constructively'),
                ('INTEG', 'Integrity', 'values', 'Honest, keeps commitments, follows the rules'),
                ('SAFE', 'Safety awareness', 'values', 'Works safely and speaks up about risks'),
                ('LEAD', 'Leading people', 'leadership', 'Sets direction, coaches, gives feedback'),
                ('DECIDE', 'Decision making', 'leadership', 'Decides in time with the facts at hand'),
                ('PLAN', 'Planning and organising', 'leadership', 'Plans work, resources and priorities'),
                ('DEVEL', 'Developing others', 'leadership', 'Grows the skills of the team')]
LEVELS = ['Needs development', 'Developing', 'Meets expectations', 'Exceeds expectations', 'Role model']

PLANS = [('MED-BASIC', 'Medical insurance – basic', 'medical', 0, 250, 0, True, 2),
         ('MED-ENH', 'Medical insurance – enhanced', 'medical', 150, 450, 6, True, 4),
         ('LIFE', 'Group life insurance', 'life', 0, 25, 0, False, 0),
         ('PHONE', 'Mobile phone allowance', 'allowance', 0, 150, 3, False, 0),
         ('GYM', 'Gym membership', 'wellness', 100, 100, 6, False, 0)]

FORMS = [('ADDRESS', 'Change of address', 'Tell HR your new address. Attach a tenancy contract or utility bill.',
          [{'name': 'address', 'label': 'New address', 'type': 'textarea', 'required': True}, {'name': 'from', 'label': 'From date', 'type': 'date', 'required': True},
           {'name': 'proof', 'label': 'Proof (tenancy contract / bill)', 'type': 'file', 'required': False}], 'all', True, 'hr', False),
         ('IT-ACCESS', 'IT access request', 'Ask for a system, e-mail group or device.',
          [{'name': 'system', 'label': 'System / access', 'type': 'choice', 'options': 'E-mail group|Shared drive|ERP user|VPN|Laptop|Other', 'required': True},
           {'name': 'reason', 'label': 'Why', 'type': 'textarea', 'required': True}, {'name': 'urgent', 'label': 'Urgent', 'type': 'yesno', 'required': False}], 'all', True, 'manager', False),
         ('WFH', 'Work-from-home request', '', [{'name': 'from', 'label': 'From', 'type': 'date', 'required': True}, {'name': 'to', 'label': 'To', 'type': 'date', 'required': True},
                                                {'name': 'reason', 'label': 'Reason', 'type': 'textarea', 'required': False}], 'all', True, 'manager', False),
         ('NDA', 'Confidentiality undertaking', 'I undertake to keep the company\'s information confidential during and after my employment.',
          [{'name': 'agree', 'label': 'I have read and agree', 'type': 'yesno', 'required': True}], 'all', False, 'hr', True),
         ('TRAVEL', 'Business travel request', '', [{'name': 'destination', 'label': 'Destination', 'type': 'text', 'required': True},
                                                   {'name': 'from', 'label': 'From', 'type': 'date', 'required': True}, {'name': 'to', 'label': 'To', 'type': 'date', 'required': True},
                                                   {'name': 'purpose', 'label': 'Purpose', 'type': 'textarea', 'required': True}, {'name': 'budget', 'label': 'Estimated cost', 'type': 'number', 'required': False}],
          'all', True, 'manager', False)]

SURVEY_Q = [('I know what is expected of me at work.', 'rating'), ('I have the tools and information I need to do my job.', 'rating'),
            ('My manager gives me useful feedback.', 'rating'), ('My work is recognised when I do it well.', 'rating'),
            ('I can see myself working here in two years.', 'rating'), ('Would you recommend the company as a place to work?', 'yesno'),
            ('What one thing should we improve?', 'text')]


def _rows(extra_awards=(), extra_comp=()):
    return {
        'award': [{'code': c, 'name': n, 'icon': i, 'points': p, 'description': d, 'who_can_give': w, 'monthly_limit': m} for c, n, i, p, d, w, m in list(AWARDS) + list(extra_awards)],
        'competency': [{'code': c, 'name': n, 'group': g, 'description': d, 'levels': LEVELS, 'for_managers': g == 'leadership'} for c, n, g, d in list(COMPETENCIES) + list(extra_comp)],
        'benefit_plan': [{'code': c, 'name': n, 'kind': k, 'employee_cost': ec, 'employer_cost': er, 'eligible_after_months': m, 'covers_dependants': dep, 'max_dependants': md}
                         for c, n, k, ec, er, m, dep, md in PLANS],
        'form_template': [{'code': c, 'name': n, 'description': d, 'fields': f, 'audience': a, 'needs_approval': ap, 'approver': apr, 'sign': s} for c, n, d, f, a, ap, apr, s in FORMS],
        'survey': [{'number': 'SRV-TEMPLATE', 'title': 'Employee engagement survey (template)', 'kind': 'engagement', 'anonymous': True,
                    'questions': [{'text': t, 'type': k, 'required': True} for t, k in SURVEY_Q], 'status': 'draft'}],
    }


PACKS = {
    'general': {'label': 'General', 'description': '8 awards, 12 competencies with 5 levels, 5 benefit plan templates, 5 forms, an engagement survey template.',
                'highlights': ['8 awards', '12 competencies', '5 benefit plans', '5 forms', 'Engagement survey'], 'rows': _rows()},
    'construction': {'label': 'Construction and oil & gas', 'description': 'General plus site awards (zero-harm, best site) and competencies (site discipline, permit compliance).',
                     'highlights': ['Site awards', 'Site competencies'],
                     'rows': _rows([('ZEROHARM', 'Zero-harm week', 'verified', 30, 'A week without incidents on site', 'managers', 0), ('BESTSITE', 'Best site', 'construction', 50, 'Best site of the month', 'hr', 1)],
                                   [('SITE', 'Site discipline', 'functional', 'Follows site rules, PPE, housekeeping'), ('PERMIT', 'Permit compliance', 'functional', 'Works within the permit to work')])},
    'healthcare': {'label': 'Healthcare', 'description': 'General plus patient-care award and clinical competencies.', 'highlights': ['Patient-care award', 'Clinical competencies'],
                   'rows': _rows([('CARE', 'Patient care', 'medical_services', 30, 'Exceptional patient care', 'managers', 0)],
                                 [('CLIN', 'Clinical competence', 'functional', 'Applies clinical protocols correctly'), ('EMPATH', 'Empathy', 'values', 'Treats patients and families with respect')])},
    'retail': {'label': 'Retail and hospitality', 'description': 'General plus sales and service awards.', 'highlights': ['Top seller', 'Guest delight'],
               'rows': _rows([('TOPSELL', 'Top seller', 'storefront', 40, 'Highest sales of the month', 'managers', 1), ('GUEST', 'Guest delight', 'sentiment_very_satisfied', 15, 'A guest praised this person', 'all', 0)],
                             [('SELL', 'Selling skills', 'functional', 'Understands needs and closes the sale'), ('SERVICE', 'Service standards', 'functional', 'Delivers the service standard every time')])},
}
