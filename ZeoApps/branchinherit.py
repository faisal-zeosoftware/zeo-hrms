"""v1.67.6 (setup) item 2 – the default branch takes everything from the company master.

A new company's first branch was made with placeholders ("Sample City", "123456", "BN-…", branch@example.com) and nothing of
the company: no logo, address, contact, TRN / licence, financial year. Now, at company creation (setup_zeo, Create company,
ZEO Cloud sign-up, copy from a company, the template copy – all go through `ZeoApps.services.setup_new_company`, the copy step
runs it again at its end) every company field that also exists on the branch is filled:

  company (public row)          → branch (brnch_mstr)                 company profile (OrgStructure) → branch profile
  logo                          → branch_logo                         licence_no / licence_expiry   → licence_no / licence_expiry
  address_line1 + 2             → branch_address                      establishment_no / _expiry    → establishment_no / _expiry
  city, pincode, state, country → br_city, br_pincode, br_state_id,  tax_id (TRN)                  → tax_id
                                  br_country                          po_box, bank_accounts         → po_box, bank_accounts
  financial_year                → FiscalYear of the branch            phone, email                  → br_branch_nmbr_1, br_branch_mail
  timezone                      → branch profile timezone             (head office, default branch)

The branch currency is the company currency: shown read only on the branch (`br_currency`) and refused by the API when a
different one is sent (the branch has no currency of its own – it can never differ).

Existing companies: `python manage.py inherit_branch_fields [--schema X] [--apply]` (dry run first) fills only the EMPTY fields
of the default branch (placeholders count as empty); nothing filled by hand is changed.
"""
import datetime
import logging

from django.apps import apps as djapps
from django.db import connection, transaction

log = logging.getLogger(__name__)
PLACEHOLDERS = {'br_city': {'Sample City'}, 'br_pincode': {'123456'}, 'br_branch_mail': {'branch@example.com'}}


def M(lab):
    return djapps.get_model(lab)


def _empty(field, v):
    if v in (None, '', [], {}):
        return True
    if isinstance(v, str) and (v.strip() in PLACEHOLDERS.get(field, set()) or (field == 'br_branch_nmbr_1' and v.startswith('BN-'))):
        return True
    return False


def company_row():
    from UserManagement.models import company
    return company.objects.filter(schema_name=connection.schema_name).select_related('currency', 'country').first()


def default_branch():
    B = M('OrganisationManager.brnch_mstr')
    try:
        P = M('OrgStructure.BranchProfile')
        bid = P.objects.filter(is_default=True).values_list('branch_id', flat=True).first()
        if bid:
            b = B.objects.filter(pk=bid).first()
            if b is not None:
                return b
    except LookupError:
        pass
    return B.objects.order_by('id').first()


def plan(branch=None):
    """[(target, field, current, new)] for the empty fields of the default branch that the company master can fill."""
    co = company_row()
    b = branch or default_branch()
    if co is None or b is None:
        return [], b
    try:
        cp = M('OrgStructure.CompanyProfile').get()
    except Exception:   # noqa: BLE001
        cp = None
    out = []
    addr = ', '.join(x for x in [co.address_line1, co.address_line2] if x)
    src = {
        'branch_logo': co.logo.name if co.logo else '', 'branch_address': addr, 'br_city': co.city or '', 'br_pincode': co.pincode or '',
        'br_state_id': co.state_id, 'br_country': co.country_id,
        'br_branch_nmbr_1': (cp.phone if cp else '') or '', 'br_branch_mail': (cp.email if cp else '') or '',
    }
    for f, v in src.items():
        if v in (None, ''):
            continue
        cur = getattr(b, f'{f}_id' if f in ('br_state_id', 'br_country') and not f.endswith('_id') else f, None)
        if f == 'br_country':
            cur = b.br_country_id
        elif f == 'br_state_id':
            cur = b.br_state_id_id
        elif f == 'branch_logo':
            cur = b.branch_logo.name if b.branch_logo else ''
        if f == 'br_country' and cur not in (None, '') and cur != v and str(cur) == '1':   # the model default (1) is a placeholder
            cur = None
        if _empty(f, cur) and cur != v:
            out.append(('branch', f, cur, v))
    try:
        BP = M('OrgStructure.BranchProfile')
        bp = BP.objects.filter(branch_id=b.pk).first()
        psrc = {'is_default': True, 'branch_type': 'head', 'timezone': co.timezone or ''}
        if cp is not None:
            psrc.update(licence_no=cp.licence_no, licence_expiry=cp.licence_expiry, establishment_no=cp.establishment_no,
                        establishment_expiry=cp.establishment_expiry, tax_id=cp.tax_id, po_box=cp.po_box, bank_accounts=cp.bank_accounts or [])
        for f, v in psrc.items():
            if v in (None, '', []):
                continue
            cur = getattr(bp, f, None) if bp is not None else None
            if f == 'is_default':
                if not cur and not BP.objects.filter(is_default=True).exclude(branch_id=b.pk).exists():
                    out.append(('profile', f, cur, True))
                continue
            if _empty(f, cur):
                out.append(('profile', f, cur, v))
    except LookupError:
        pass
    FY = M('OrganisationManager.FiscalYear')
    if co.financial_year and not FY.objects.filter(branch_id=b).exists():
        st = co.financial_year
        try:
            end = st.replace(year=st.year + 1) - datetime.timedelta(days=1)
        except ValueError:
            end = st + datetime.timedelta(days=364)
        out.append(('fiscal_year', 'start_date', None, (st, end)))
    return out, b


def apply(rows, b, user=None):
    if not rows:
        return 0
    with transaction.atomic():
        bf = {}
        for target, f, cur, v in rows:
            if target == 'branch':
                if f == 'br_country':
                    bf['br_country_id'] = v
                elif f == 'br_state_id':
                    bf['br_state_id_id'] = v
                else:
                    bf[f] = v
        if bf:
            M('OrganisationManager.brnch_mstr').objects.filter(pk=b.pk).update(**bf)
        prof = {f: v for t, f, c, v in rows if t == 'profile'}
        if prof:
            BP = M('OrgStructure.BranchProfile')
            bp, _ = BP.objects.get_or_create(branch_id=b.pk)
            for f, v in prof.items():
                setattr(bp, f, v)
            bp.save()
        for t, f, c, v in rows:
            if t == 'fiscal_year':
                st, end = v
                M('OrganisationManager.FiscalYear').objects.create(branch_id=b, name=f'FY {st.year}' + (f'/{end.year % 100:02d}' if end.year != st.year else ''),
                                                                    start_date=st, end_date=end, created_by=user if getattr(user, 'pk', None) else None)
    return len(rows)


def inherit(user=None, apply_now=True):
    """Fill the empty fields of the default branch from the company master. Returns the rows (target, field, old, new)."""
    rows, b = plan()
    if apply_now and b is not None:
        apply(rows, b, user)
    return rows


def currency_of_branch():
    co = company_row()
    cur = getattr(co, 'currency', None)
    return {'id': getattr(cur, 'pk', None), 'name': getattr(cur, 'currency_name', '') or '', 'code': getattr(cur, 'currency_code', '') or ''}
