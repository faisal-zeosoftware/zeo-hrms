# v1.67.12 (effective): new module
"""v1.67.12 (effective) – API of the dated setup versions and of the GPSSA first-insured date.

GET  api/rule-versions/?kind=leave_policy&id=3      the versions of a record (oldest first) with what changed in each
GET  api/rule-versions/?kind=gratuity               the versions of the gratuity table (one record)
GET  api/pension/<employee id>/                     first insured date, scheme, law (old / new) and why
PUT  api/pension/<employee id>/                     {first_insured: yyyy-mm-dd | null, scheme: gpssa|adpf|gcc, note}
GET  api/pension-settings/                          {gcc_counts} (legal rule to confirm)
PUT  api/pension-settings/                          {gcc_counts: true|false}
"""
from datetime import date

from django.apps import apps
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from Translations.i18n import tr

from . import effective as EFF
from . import legal as LG
from . import services as S
from .views import Base

M = apps.get_model
EMP_READ = {'view_emp_master', 'change_emp_master', 'view_payrollrun', 'change_payrollrun'}
EMP_WRITE = {'change_emp_master'}
SET_WRITE = {'change_payrollplussettings'}
LAW = [
    {'topic': 'gpssa_cut_off', 'text': 'Federal Decree-Law 57/2023 applies to UAE nationals first insured (first joining work with a GPSSA employer) '
                                       'from 31/10/2023; those insured before keep Federal Law 7/1999, also after changing employer.'},
    {'topic': 'gpssa_rates', 'text': 'New law: employee 11 %, employer 12.5 % (the government pays 2.5 %) below AED 20,000 and 15 % from AED 20,000, '
                                     'contribution salary up to AED 70,000. Old law: employee 5 %, employer 12.5 %, up to AED 50,000.'},
    {'topic': 'gcc', 'text': 'Insurance in another GCC scheme before 31/10/2023: not confirmed by two sources – a company setting (legal rule to confirm).'},
]


class RuleVersionsView(Base):
    def get(self, request):
        kind = request.query_params.get('kind') or ''
        if kind not in EFF.KINDS:
            raise S.Refused(tr('Unknown kind of record.'))
        try:
            oid = 0 if kind in EFF.TABLE_KINDS else int(request.query_params.get('id') or 0)
        except ValueError:
            oid = 0
        if kind not in EFF.TABLE_KINDS and not oid:
            raise S.Refused(tr('Give the record.'))
        rows = EFF.history(kind, oid)
        for r in rows:   # the field names and Yes / No / Added are translated in effective._diff
            r['note'] = tr(r['note']) if r['note'] else ''
        return Response({'kind': kind, 'object_id': oid, 'versions': rows, 'today': date.today().isoformat()})


def _emp(pk):
    e = M('EmpManagement', 'emp_master').objects.filter(pk=pk).first()
    if e is None:
        raise S.Refused(tr('Employee not found.'), status=404)
    return e


def _pension_json(e):
    row = LG.pension_row(e)
    try:
        from PayrollManagement.signals import is_uae_national
        nat = bool(is_uae_national(e))
    except Exception:
        nat = False
    law = LG.pension_law(e, row.scheme if row is not None and row.scheme == 'adpf' else 'gpssa')
    return {'employee_id': e.pk, 'joined_date': e.emp_joined_date, 'is_national': nat,
            'first_insured': row.first_insured if row else None, 'scheme': row.scheme if row else 'gpssa', 'note': row.note if row else '',
            'law': law['law'], 'law_from': law['from'], 'law_basis': law['basis'], 'cut_off': law['cut_off'],
            'settings': LG.pension_settings()}


class PensionView(Base):
    def get(self, request, pk):
        if not self.can(EMP_READ):
            raise S.Refused(tr('You may not see the pension details.'), status=403)
        e = _emp(pk)
        return Response({**_pension_json(e), 'can_edit': bool(self.c.admin or EMP_WRITE & self.c.codes)})

    def put(self, request, pk):
        if not self.can(EMP_WRITE):
            raise S.Refused(tr('You may not change the pension details.'), status=403)
        e = _emp(pk)
        d = request.data or {}
        raw = d.get('first_insured')
        day = parse_date(str(raw)[:10]) if raw else None
        if raw and day is None:
            return Response({'first_insured': tr('Enter a valid date.')}, status=400)
        if day and day > date.today():
            return Response({'first_insured': tr('The date first insured cannot be in the future.')}, status=400)
        scheme = d.get('scheme') or 'gpssa'
        if scheme not in dict(M('PayrollPlus', 'EmployeePension').SCHEMES):
            return Response({'scheme': tr('Choose the pension scheme.')}, status=400)
        P = M('PayrollPlus', 'EmployeePension')
        P.objects.update_or_create(employee_id=e.pk, defaults={'first_insured': day, 'scheme': scheme, 'note': str(d.get('note') or '')[:255],
                                                               'updated_by_id': getattr(request.user, 'pk', None)})
        try:
            from Chatter.models import Message
            Message.objects.create(model='EmpManagement.emp_master', object_id=str(e.pk), author_id=getattr(request.user, 'pk', None),
                                   body=f"Pension: first insured {day:%d/%m/%Y} ({scheme})." if day else 'Pension: first insured date cleared (the joining date decides).')
        except Exception:
            pass
        return Response({**_pension_json(e), 'can_edit': True})


class PensionSettingsView(Base):
    def get(self, request):
        return Response({**LG.pension_settings(), 'law': [{**x, 'text': tr(x['text'])} for x in LAW],
                         'can_edit': bool(self.c.admin or SET_WRITE & self.c.codes)})

    def put(self, request):
        if not (self.c.admin or SET_WRITE & self.c.codes):
            raise S.Refused(tr('Only the company administrator can change the pension settings.'), status=403)
        return Response({**LG.save_pension_settings(request.data or {}), 'law': [{**x, 'text': tr(x['text'])} for x in LAW], 'can_edit': True})
