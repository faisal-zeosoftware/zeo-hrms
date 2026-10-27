"""v1.56.0 – Excel templates of the setup guide: download (filled with the current setup) and upload.

Upload never saves a bad row: every row is checked, the good rows are saved and the bad ones are listed with the reason
(the list can be downloaded, corrected and uploaded again).
"""
import datetime
import io
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from Translations.i18n import tr

M = apps.get_model
KINDS = ('numbering', 'golive', 'matrix', 'branches')


class TemplateError(ValueError):
    pass


def _sheet(wb, title, header, rows, help_text=''):
    ws = wb.create_sheet(title[:31])
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='5B4FE0')
    for r in rows:
        ws.append(r)
    for i, h in enumerate(header, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(14, len(str(h)) + 4)
    if help_text:
        ws.cell(1, len(header) + 2, help_text).font = Font(italic=True, color='6B7185')
    return ws


def download(kind):
    wb = Workbook()
    wb.remove(wb.active)
    if kind == 'numbering':
        from .numbering import AREAS, SUGGESTED, type_labels
        P = M('ZeoAdmin', 'NumberingPattern').get()
        labels = type_labels()
        rows = [[t, tr(labels.get(t, t)), (P.codes or {}).get(t) or SUGGESTED[t], tr(a)] for a, ts in AREAS for t in ts]
        _sheet(wb, 'Document types', ['type', 'name', 'code', 'area'], rows, tr('Change only the code column: 1 to 6 letters or digits, no two types the same.'))
    elif kind == 'golive':
        E = M('EmpManagement', 'emp_master')
        G = {g.employee_id: g for g in M('PayrollPlus', 'GoLiveEmployee').objects.all()}
        UP = dict(M('EmployeeProfile', 'EmploymentInfo').objects.filter(unpaid_days_before_golive__gt=0).values_list('employee_id', 'unpaid_days_before_golive'))
        rows = []
        for e in E.objects.filter(is_active=True).order_by('emp_code'):
            g = G.get(e.pk)
            rows.append([e.emp_code, f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip(), e.emp_joined_date,
                         g.paid_up_to if g else None, float(UP[e.pk]) if e.pk in UP else None, g.gratuity_from if g else None, g.note if g else ''])
        _sheet(wb, 'Go-live data', ['emp_code', 'name', 'joined', 'salary_paid_up_to', 'unpaid_days_before_go_live', 'gratuity_from', 'note'], rows,
               tr('Leave a cell empty to use the company value (paid up to) or the joining date (gratuity).'))
    elif kind == 'matrix':
        from .category_matrix import options, rows as mrows
        o = options()
        lab = {k: {x['value']: x['label'] for x in v} for k, v in o.items()}
        rows = [[r['name'], lab['weekend'].get(r['weekend'], ''), r['start'], r['end'], lab['roster'].get(r['roster'], ''),
                 lab['leave'].get(r['leave'], ''), r['pay'] or ''] for r in mrows()]
        _sheet(wb, 'Categories', ['category', 'weekend_calendar', 'hours_from', 'hours_to', 'roster_pattern', 'leave_policy', 'pay_structure_id'], rows,
               tr('Names as in ZEO; empty = not set for the category (the company setting applies).'))
    elif kind == 'branches':
        BW = {x.branch_id: x for x in M('PayrollPlus', 'BranchWPS').objects.all()}
        rows = [[b.branch_name, b.branch_code or '', str(b.br_state_id or '') if b.br_state_id_id else '',
                 getattr(BW.get(b.pk), 'employer_unique_id', ''), getattr(BW.get(b.pk), 'bank_routing_code', '')]
                for b in M('OrganisationManager', 'brnch_mstr').objects.order_by('id')]
        _sheet(wb, 'Branches', ['branch', 'code', 'emirate_or_state', 'wps_employer_id', 'bank_routing_code'], rows,
               tr('Existing branches are updated by name; add new branches in Settings › Branches.'))
    else:
        raise TemplateError(tr('Not found.'))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows(f):
    try:
        wb = load_workbook(f, data_only=True, read_only=True)
    except Exception:
        raise TemplateError(tr('The file is not an Excel workbook (.xlsx).'))
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    try:
        head = [str(h or '').strip().lower() for h in next(it)]
    except StopIteration:
        raise TemplateError(tr('The file is empty.'))
    out = []
    for i, r in enumerate(it, start=2):
        if r is None or all(v in (None, '') for v in r):
            continue
        out.append((i, {head[j]: r[j] for j in range(min(len(head), len(r))) if head[j]}))
    return out


def _d(v):
    if v in (None, ''):
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        try:
            return datetime.datetime.strptime(str(v).strip(), '%d/%m/%Y').date()
        except ValueError:
            raise ValueError(tr('{0} is not a date (YYYY-MM-DD or DD/MM/YYYY).', v))


def upload(kind, f, user):
    rows = _rows(f)
    ok, errors = 0, []

    def bad(i, msg):
        errors.append({'row': i, 'error': msg})

    if kind == 'numbering':
        from .numbering import SUGGESTED, validate
        P = M('ZeoAdmin', 'NumberingPattern').get()
        codes = dict(P.codes or {})
        for i, r in rows:
            t = str(r.get('type') or '').strip()
            if t not in SUGGESTED:
                bad(i, tr('Unknown document type {0}.', t))
                continue
            codes[t] = str(r.get('code') or '').strip().upper()
            ok += 1
        full = {t: codes.get(t) or c for t, c in SUGGESTED.items()}
        problems = validate(P.pattern, full)
        if problems:
            raise TemplateError(' '.join(problems))
        P.codes = full
        P.save(update_fields=['codes', 'updated_at'])
    elif kind == 'golive':
        E = M('EmpManagement', 'emp_master')
        G = M('PayrollPlus', 'GoLiveEmployee')
        by_code = {e.emp_code.lower(): e for e in E.objects.all()}
        with transaction.atomic():
            for i, r in rows:
                e = by_code.get(str(r.get('emp_code') or '').strip().lower())
                if e is None:
                    bad(i, tr('No employee with the code {0}.', r.get('emp_code')))
                    continue
                try:
                    paid, gfrom = _d(r.get('salary_paid_up_to')), _d(r.get('gratuity_from'))
                    u = r.get('unpaid_days_before_go_live')
                    unpaid = Decimal(str(u)) if u not in (None, '') else Decimal('0')
                    if unpaid < 0 or unpaid > 3650:
                        raise ValueError(tr('Unpaid days must be 0 to 3650.'))
                    if gfrom and e.emp_joined_date and gfrom < e.emp_joined_date - datetime.timedelta(days=3650):
                        raise ValueError(tr('Gratuity start is too far before the joining date.'))
                except (ValueError, InvalidOperation) as ex:
                    bad(i, str(ex))
                    continue
                if not (paid or gfrom or r.get('note')):
                    G.objects.filter(employee_id=e.pk).delete()
                else:
                    G.objects.update_or_create(employee_id=e.pk, defaults={'paid_up_to': paid, 'gratuity_from': gfrom, 'note': str(r.get('note') or '')[:200]})
                EI = M('EmployeeProfile', 'EmploymentInfo')   # the unpaid days before go-live live in Employment info (since 1.20.2)
                info = EI.objects.filter(employee_id=e.pk).first()
                if info is None and unpaid:
                    info = EI(employee_id=e.pk)
                if info is not None and info.unpaid_days_before_golive != unpaid:
                    info.unpaid_days_before_golive = unpaid
                    info.save()
                ok += 1
        try:
            from PayrollPlus.golive import forget
            forget()
        except ImportError:
            pass
    elif kind == 'matrix':
        from .category_matrix import MatrixError, options, save
        o = options()
        rev = {k: {str(x['label']).strip().lower(): x['value'] for x in v} for k, v in o.items()}
        C = M('OrganisationManager', 'ctgry_master')
        out = []
        for i, r in rows:
            c = C.objects.filter(ctgry_title__iexact=str(r.get('category') or '').strip()).first()
            if c is None:
                bad(i, tr('No category {0}.', r.get('category')))
                continue
            row = {'category': c.pk}
            try:
                for col, k in (('weekend_calendar', 'weekend'), ('roster_pattern', 'roster'), ('leave_policy', 'leave')):
                    v = str(r.get(col) or '').strip().lower()
                    if v and v not in rev[k]:
                        raise ValueError(tr('{0}: {1} not found.', col, r.get(col)))
                    row[k] = rev[k].get(v) if v else None
                pid = r.get('pay_structure_id')
                row['pay'] = int(pid) if pid not in (None, '') else None
                row['start'] = str(r.get('hours_from') or '')[:5]
                row['end'] = str(r.get('hours_to') or '')[:5]
            except (ValueError, TypeError) as ex:
                bad(i, str(ex))
                continue
            out.append((i, row))
        for i, row in out:
            try:
                save({'rows': [row]}, user)
                ok += 1
            except MatrixError as ex:
                bad(i, str(ex))
    elif kind == 'branches':
        import re
        B = M('OrganisationManager', 'brnch_mstr')
        BW = M('PayrollPlus', 'BranchWPS')
        S = M('Core', 'state_mstr')
        for i, r in rows:
            b = B.objects.filter(branch_name__iexact=str(r.get('branch') or '').strip()).first()
            if b is None:
                bad(i, tr('No branch {0} – add it in Settings › Branches first.', r.get('branch')))
                continue
            eid, rc = str(r.get('wps_employer_id') or '').strip(), str(r.get('bank_routing_code') or '').strip()
            if eid and not re.fullmatch(r'\d{13}', eid) or rc and not re.fullmatch(r'\d{9}', rc):
                bad(i, tr('The WPS employer ID has 13 digits and the routing code 9 digits.'))
                continue
            st = str(r.get('emirate_or_state') or '').strip()
            if st:
                s = S.objects.filter(state_name__iexact=st).first()
                if s is None:
                    bad(i, tr('No emirate or state {0}.', st))
                    continue
                b.br_state_id = s
            code = str(r.get('code') or '').strip()
            if code:
                b.branch_code = code[:20]
            b.save()
            if eid or rc:
                BW.objects.update_or_create(branch_id=b.pk, defaults={'employer_unique_id': eid, 'bank_routing_code': rc})
            ok += 1
    else:
        raise TemplateError(tr('Not found.'))
    return {'saved': ok, 'errors': errors}
