"""
v1.13.0 – fill the employee master extras from existing data.

    python manage.py profile_backfill                 # dry run for every company: what would be created / copied, conflicts
    python manage.py profile_backfill --apply         # save
    python manage.py profile_backfill --schema adepttest --apply --json /tmp/backfill.json

* creates EmploymentInfo for every employee (status / probation worked out from joining date, employment type or
  branch probation days, confirmation date, inactive flag, approved resignations and end of service);
* copies passport / visa / Emirates ID / labour card / driving licence numbers and dates from EmpManagement.Emp_Documents (matched by
  document type name: Passport; Visa / Residence visa; Emirates ID / EID; Labour card / Work permit) into
  EmployeeIdentity, and links the document so later changes keep it up to date;
* lists conflicts: number already on another employee, identity already holding a different number, several
  documents of one kind (the latest expiry is used), numbers that fail the format / check-digit test.
"""
import json

from django.core.management.base import BaseCommand
from django.db import transaction


class _DryRun(Exception):
    pass


def backfill_company(apply=False, out=None):
    from django.apps import apps
    from EmployeeProfile import services as S
    from EmployeeProfile import validators as V
    from EmployeeProfile.models import EmployeeIdentity, EmploymentInfo
    Emp = apps.get_model('EmpManagement', 'emp_master')
    Doc = apps.get_model('EmpManagement', 'Emp_Documents')
    rep = {'employment_created': 0, 'identity_created': 0, 'identity_updated': 0, 'copied': 0, 'conflicts': [], 'warnings': [], 'changes': []}
    cleaners = {'passport': V.clean_passport, 'emirates_id': V.clean_emirates_id,
                'visa': lambda v: V.clean_code(v, 'Visa number', 4, 30, '/-'), 'labour_card': lambda v: V.clean_code(v, 'Labour card number', 4, 20, '-'),
                'driving_licence': lambda v: V.clean_code(v, 'Driving licence number', 4, 20, '-/')}
    try:
        with transaction.atomic():
            have = set(EmploymentInfo.objects.values_list('employee_id', flat=True))
            emps = list(Emp.objects.select_related('emp_branch_id').order_by('emp_code'))
            for e in emps:
                if e.pk not in have:
                    i = S.ensure_info(e)
                    rep['employment_created'] += 1
                    rep['changes'].append({'employee': e.emp_code, 'what': 'employment', 'status': i.status, 'probation': i.probation_status,
                                           'probation_end': str(i.probation_end_date or '')})
            types = {kind: [t.pk for t in S.doc_types_for(kind)] for kind, *_ in S.DOC_KINDS}
            taken = {}   # (field, number) -> employee code
            for ident in EmployeeIdentity.objects.all():
                code = Emp.objects.filter(pk=ident.employee_id).values_list('emp_code', flat=True).first()
                for kind, tname, nf, *_ in S.DOC_KINDS:
                    v = getattr(ident, nf)
                    if v:
                        taken[(nf, v.upper())] = code
            for e in emps:
                ident = EmployeeIdentity.objects.filter(employee_id=e.pk).first()
                new = ident is None
                ident = ident or EmployeeIdentity(employee_id=e.pk)
                changed = False
                links = dict(ident.doc_links or {})
                for kind, tname, nf, isf, exf, _ in S.DOC_KINDS:
                    if not types[kind]:
                        continue
                    docs = list(Doc.objects.filter(emp_id=e, document_type_id__in=types[kind]).order_by('-is_active', '-emp_doc_expiry_date', '-id'))
                    if not docs:
                        continue
                    d = docs[0]
                    if len(docs) > 1:
                        rep['warnings'].append({'employee': e.emp_code, 'kind': tname, 'message': f'{len(docs)} {tname} documents – the one expiring {d.emp_doc_expiry_date} ({d.emp_doc_number}) is used.'})
                    raw = d.emp_doc_number or ''
                    try:
                        num = cleaners[kind](raw)
                    except ValueError as ex:
                        digits = ''.join(ch for ch in raw if ch.isdigit())
                        if kind == 'emirates_id' and len(digits) == 15 and digits.startswith('784'):
                            num = f'{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}'
                            rep['warnings'].append({'employee': e.emp_code, 'kind': tname, 'message': f'{raw}: {ex} Copied – please check the card.'})
                        else:
                            rep['conflicts'].append({'employee': e.emp_code, 'kind': tname, 'number': raw, 'message': f'Not copied: {ex}'})
                            continue
                    current = getattr(ident, nf)
                    if current and current.upper() != num.upper():
                        rep['conflicts'].append({'employee': e.emp_code, 'kind': tname, 'number': num,
                                                 'message': f'The identity already has {current}; the document has {num}. Kept {current}.'})
                        continue
                    other = taken.get((nf, num.upper()))
                    if other and other != e.emp_code:
                        rep['conflicts'].append({'employee': e.emp_code, 'kind': tname, 'number': num, 'message': f'Not copied: the same number belongs to employee {other}.'})
                        continue
                    taken[(nf, num.upper())] = e.emp_code
                    vals = {nf: num, isf: d.emp_doc_issued_date, exf: d.emp_doc_expiry_date}
                    for f, v in vals.items():
                        if getattr(ident, f) in (None, '') and v not in (None, ''):
                            setattr(ident, f, v)
                            changed = True
                    if links.get(kind) != d.pk:
                        links[kind] = d.pk
                        changed = True
                    rep['copied'] += 1
                    rep['changes'].append({'employee': e.emp_code, 'what': tname, 'number': num, 'expiry': str(d.emp_doc_expiry_date)})
                if changed:
                    ident.doc_links = links
                    ident.save()
                    rep['identity_created' if new else 'identity_updated'] += 1
            if not apply:
                raise _DryRun()
    except _DryRun:
        pass
    rep['applied'] = apply
    return rep


class Command(BaseCommand):
    help = 'Create employment info and copy identity numbers from employee documents (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='only this company schema')
        parser.add_argument('--apply', action='store_true', help='save (default: dry run)')
        parser.add_argument('--json', help='write the report to this file')

    def handle(self, *args, **o):
        from django_tenants.utils import get_tenant_model, schema_context
        from EmployeeProfile.services import table_ready
        result = {}
        tenants = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            tenants = tenants.filter(schema_name=o['schema'])
        for t in tenants:
            with schema_context(t.schema_name):
                if not table_ready():
                    self.stdout.write(f'{t.schema_name}: EmployeeProfile is not migrated – run migrate_schemas first.')
                    continue
                rep = backfill_company(apply=o['apply'])
                result[t.schema_name] = rep
                self.stdout.write(f"{t.schema_name}: {'SAVED' if o['apply'] else 'DRY RUN'} – employment info {rep['employment_created']}, "
                                  f"identity new {rep['identity_created']} / updated {rep['identity_updated']}, numbers copied {rep['copied']}, "
                                  f"conflicts {len(rep['conflicts'])}, warnings {len(rep['warnings'])}")
                for c in rep['conflicts']:
                    self.stdout.write(f"  CONFLICT {c['employee']} {c['kind']} {c.get('number', '')}: {c['message']}")
                for w in rep['warnings']:
                    self.stdout.write(f"  check    {w['employee']} {w['kind']}: {w['message']}")
        if o.get('json'):
            with open(o['json'], 'w') as f:
                json.dump(result, f, indent=1, default=str)
        return None
