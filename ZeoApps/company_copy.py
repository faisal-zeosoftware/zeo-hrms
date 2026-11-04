"""v1.62.1 (user request 2 Nov) – a new company starts with the setup of a branch of an existing company.

"Also i need option to copy setup on from existing company -Branch when a new company creating time if already company
there" – on the Create company form the user picks "Copy from an existing company", the company, one of its branches and
the name and code of the new company's first branch (prefilled from the source branch, both editable).

The company is created as before (standard defaults, country pack, standard approval chains); then copy() reads the source
company in its own schema and writes the new one, mapping every link by code / name to the new rows (ids are never copied):

  branch setup        departments, designations, categories, grades; weekend and holiday calendars (with the holidays) of
                      the branch; leave types with entitlements, reset policies, pay rules and approval workflows; leave
                      policies; salary components, salary / pay structures; document types; loan and request types; the
                      approval chains of every request type; e-mail templates; document numbering (the prefix follows the
                      new branch code) and the numbering pattern; attendance rules and shifts
  company settings    payroll controls, leave year / HR policy defaults of the setup guide, HR settings, expense
                      settings / categories / policies, organisation settings, asset settings, country pack choice, country
                      rule set, AI mode (never the key), print templates and print colours / fonts
  never               company name, legal profile (licence, TRN, registrations, bank accounts), WPS employer IDs, logo,
                      stamp, signature, letterhead texts, e-mail server and passwords, API keys, users, employees,
                      transactions, balances, payslips, files, setup-guide progress (each step is still completed by hand)

A chain that names a person of the source company (approver / escalation user) becomes "Reporting manager".
A row that already exists in the new company (a standard default with the same name / code) gets the source's settings.
Every table is copied in its own savepoint: one that fails is logged and skipped, the company is made anyway.
The result {key: count} is shown on the "company is ready" card and stored as CompanyOption 'setup_copied_from'.
"""
import logging
import re

from django.apps import apps
from django.db import connection, models, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# key → label (English; the screens translate it) in the order shown on the ready card
LABELS = [
    ('departments', 'departments'), ('designations', 'designations'), ('categories', 'categories'), ('grades', 'grades'),
    ('weekend_calendars', 'weekend calendars'), ('holiday_calendars', 'holiday calendars'), ('holidays', 'holidays'),
    ('leave_types', 'leave types'), ('leave_entitlements', 'leave entitlements'), ('leave_reset_policies', 'leave reset policies'),
    ('leave_pay_rules', 'leave pay rules'), ('leave_workflows', 'leave approval workflows'), ('leave_policies', 'leave policies'),
    ('salary_components', 'salary components'), ('salary_structures', 'salary structures'), ('pay_structures', 'pay structures'),
    ('document_types', 'document types'), ('loan_types', 'loan types'), ('request_types', 'request types'),
    ('approval_workflows', 'approval chains'), ('email_templates', 'e-mail templates'), ('document_numbering', 'document numbers'),
    ('numbering_pattern', 'numbering pattern'), ('attendance_rules', 'attendance rules'), ('shifts', 'shifts'),
    ('expense_categories', 'expense categories'), ('expense_policies', 'expense policies'), ('print_templates', 'print templates'),
    ('settings', 'settings'),
]
LABEL = dict(LABELS)
ONE = {'departments': 'department', 'designations': 'designation', 'categories': 'category', 'grades': 'grade',
       'weekend_calendars': 'weekend calendar', 'holiday_calendars': 'holiday calendar', 'holidays': 'holiday', 'leave_types': 'leave type',
       'leave_entitlements': 'leave entitlement', 'leave_reset_policies': 'leave reset policy', 'leave_pay_rules': 'leave pay rule',
       'leave_workflows': 'leave approval workflow', 'leave_policies': 'leave policy', 'salary_components': 'salary component',
       'salary_structures': 'salary structure', 'pay_structures': 'pay structure', 'document_types': 'document type', 'loan_types': 'loan type',
       'request_types': 'request type', 'approval_workflows': 'approval chain', 'email_templates': 'e-mail template',
       'document_numbering': 'document number', 'numbering_pattern': 'numbering pattern', 'attendance_rules': 'attendance rule',
       'shifts': 'shift', 'expense_categories': 'expense category', 'expense_policies': 'expense policy', 'print_templates': 'print template',
       'settings': 'setting'}
CREATOR_FIELDS = re.compile(r'(^|_)(created_by|updated_by)$')
# never copied from any row: identity, secrets, files, the source's people
NEVER = {'logo', 'branch_logo', 'stamp', 'signature', 'api_key_enc', 'key_last4', 'password', 'smtp_password', 'secret',
         'client_secret', 'token', 'iban', 'iban_number', 'trn', 'employer_unique_id', 'paid_up_to', 'applied_at', 'applied_by_id',
         'activated_at', 'activated_by', 'activated_by_id', 'updated_by_id', 'created_by_id_int'}


def M(label):
    try:
        return apps.get_model(*label.split('.'))
    except (LookupError, ValueError):
        return None


# ====================================================================== who may copy what
def can_administer(user, comp):
    """The user may open the company and administers it (system administrator, or company administrator of it)."""
    if user is None or comp is None or not getattr(user, 'is_authenticated', False):
        return False
    if not user.tenants.filter(pk=comp.pk).exists():
        return False
    if user.is_superuser:
        return True
    try:
        from django_tenants.utils import schema_context
        from tenant_users.tenants.models import UserTenantPermissions
        with schema_context(comp.schema_name):
            return UserTenantPermissions.objects.filter(profile=user, is_superuser=True).exists()
    except Exception:
        log.exception('admin check of %s', getattr(comp, 'schema_name', '?'))
        return False


def sources(user):
    """[{id, name, schema_name}] – the companies the user may copy a setup from."""
    out = []
    for c in user.tenants.exclude(schema_name='public').order_by('name'):
        if c.schema_name.startswith('zeo_template'):
            continue
        if can_administer(user, c):
            out.append({'id': c.pk, 'name': c.name, 'schema_name': c.schema_name})
    return out


def branches_of(comp):
    from django_tenants.utils import schema_context
    with schema_context(comp.schema_name):
        B = M('OrganisationManager.brnch_mstr')
        return [{'id': b.pk, 'name': b.branch_name, 'code': b.branch_code} for b in B.objects.order_by('id')]


class CopyRefused(Exception):
    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message, self.status, self.field = message, status, field


def check_request(user, data):
    """The copy fields of the Create company form → None (standard defaults) or the plan {source, branch, name, code}.
    Raises CopyRefused (403 / 400) when the user may not copy from that company or a field is wrong."""
    mode = str(data.get('setup_mode') or '').strip()
    src_id = data.get('copy_from')
    if mode != 'copy' and src_id in (None, '', 'null'):
        return None
    from UserManagement.models import company as Company
    try:
        src = Company.objects.filter(pk=int(src_id)).exclude(schema_name='public').first()
    except (TypeError, ValueError):
        src = None
    if src is None:
        raise CopyRefused('Choose the company to copy the setup from.', 400, 'copy_from')
    if not can_administer(user, src):
        raise CopyRefused('You can copy the setup only from a company you administer.', 403, 'copy_from')
    try:
        bid = int(data.get('copy_branch'))
    except (TypeError, ValueError):
        raise CopyRefused('Choose the branch to copy the setup from.', 400, 'copy_branch')
    brs = {b['id']: b for b in branches_of(src)}
    if bid not in brs:
        raise CopyRefused('Choose the branch to copy the setup from.', 400, 'copy_branch')
    name = ' '.join(str(data.get('copy_branch_name') or '').split())
    code = str(data.get('copy_branch_code') or '').strip().upper()
    if not name:
        raise CopyRefused('Enter the name of the new branch.', 400, 'copy_branch_name')
    if len(name) > 100:
        raise CopyRefused('The branch name has at most 100 characters.', 400, 'copy_branch_name')
    if not code:
        raise CopyRefused('Enter the branch code.', 400, 'copy_branch_code')
    if len(code) > 20 or not re.fullmatch(r'[A-Z0-9][A-Z0-9\-_/]*', code):
        raise CopyRefused('The branch code has up to 20 letters, digits and - _ /.', 400, 'copy_branch_code')
    return {'source_id': src.pk, 'source_schema': src.schema_name, 'source_name': src.name, 'branch_id': bid,
            'branch_source_name': brs[bid]['name'], 'name': name, 'code': code}


# ====================================================================== the first branch of the new company
def rename_first_branch(name, code):
    """In the new company's schema: the first branch gets the name and code of the form; its document numbers (none issued
    yet) take prefixes from the new code."""
    B = M('OrganisationManager.brnch_mstr')
    b = B.objects.order_by('id').first()
    if b is None:
        return None
    b.branch_name, b.branch_code = name[:100], code[:50]
    b.save(update_fields=['branch_name', 'branch_code'])
    from OrganisationManager.signals import branch_doc_prefix
    DN = M('OrganisationManager.DocumentNumbering')
    for row in DN.objects.filter(branch_id=b, current_number=0):
        row.prefix = branch_doc_prefix(b, row.type)
        row.save(update_fields=['prefix'])
    return b


# ====================================================================== generic cross-schema copy
class Ctx:
    def __init__(self, src_schema, dst_schema, src_branch_id, user=None):
        self.src, self.dst = src_schema, dst_schema
        self.sb = src_branch_id
        self.nb = None             # the new branch (set in the new schema)
        self.user = user
        self.maps = {}             # model label → {source id: new id}
        self.counts = {}
        self.failed = {}
        self.named_users = 0       # chains that named a person and became "Reporting manager"

    def add(self, key, n=1):
        if n:
            self.counts[key] = self.counts.get(key, 0) + n

    def map(self, model, old, new):
        self.maps.setdefault(model._meta.label, {})[old] = new

    def mapped(self, model, old):
        return self.maps.get(model._meta.label, {}).get(old)

    def ids(self, model, olds):
        return [x for x in (self.mapped(model, o) for o in (olds or [])) if x is not None]


def _src(ctx):
    from django_tenants.utils import schema_context
    return schema_context(ctx.src)


def _dst(ctx):
    from django_tenants.utils import schema_context
    return schema_context(ctx.dst)


def snap(objs):
    """Model instances (source schema) → plain rows {pk, f: {attname: value}, m2m: {name: [ids]}} – read while in the source."""
    out = []
    for o in objs:
        f = {x.attname: getattr(o, x.attname) for x in o._meta.concrete_fields}
        m2m = {x.name: list(getattr(o, x.name).values_list('pk', flat=True)) for x in o._meta.many_to_many}
        out.append({'pk': o.pk, 'f': f, 'm2m': m2m, 'model': type(o)})
    return out


def read(ctx, model, **flt):
    if model is None:
        return []
    with _src(ctx):
        qs = model.objects.filter(**flt).order_by('pk') if flt else model.objects.order_by('pk')
        return snap(list(qs.distinct()))


def _is_user(m):
    return m is not None and m._meta.label in ('UserManagement.CustomUser',)


def _fk(ctx, f, v):
    """(ok, new value) of a foreign key of a source row."""
    if v is None:
        return True, None
    R = f.related_model
    label = R._meta.label
    if label == 'OrganisationManager.brnch_mstr':
        return (True, ctx.nb.pk) if v == ctx.sb else (False, None)
    if _is_user(R):
        if CREATOR_FIELDS.search(f.name) or not f.null:
            return True, getattr(ctx.user, 'pk', None) if ctx.user is not None else (v if not f.null else None)
        ctx.named_users += 1
        return True, None     # approver / escalation / owner – a person of the source company
    if label == 'EmpManagement.emp_master':
        return True, None
    if R._meta.app_label in ('Core', 'auth', 'contenttypes'):   # shared tables: the same rows
        return True, v
    new = ctx.mapped(R, v)
    return (new is not None), new


def build(ctx, row, exclude=(), only=None):
    """The field values for the new schema, or None when a required link cannot be mapped."""
    model = row['model']
    data = {}
    for f in model._meta.concrete_fields:
        if f.primary_key or f.name in exclude or f.attname in exclude or f.name in NEVER or f.attname in NEVER:
            continue
        if only is not None and f.name not in only and f.attname not in only:
            continue
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            continue
        if isinstance(f, models.FileField):
            continue
        v = row['f'].get(f.attname)
        if f.is_relation:
            ok, v = _fk(ctx, f, v)
            if not ok:
                if f.null:
                    v = None
                else:
                    return None
        data[f.attname] = v
    return data


def find(model, data, key):
    if not key:
        return None
    flt = {}
    for k in key:
        f = model._meta.get_field(k)
        v = data.get(f.attname)
        if v is None:
            flt[f'{f.attname}__isnull'] = True
        elif isinstance(v, str):
            flt[f'{f.name}__iexact'] = v.strip()
        else:
            flt[f.attname] = v
    return model.objects.filter(**flt).order_by('pk').first()


def _unique_ok(model, data, exclude_pk=None):
    """A unique value that the new company already uses gets a suffix (or is left empty when it may be)."""
    for f in model._meta.concrete_fields:
        if not f.unique or f.primary_key or f.attname not in data or data[f.attname] in (None, ''):
            continue
        qs = model.objects.filter(**{f.attname: data[f.attname]})
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        if not qs.exists():
            continue
        if f.null:
            data[f.attname] = None
            continue
        base, n = str(data[f.attname]), 2
        while model.objects.filter(**{f.attname: f'{base}-{n}'[:f.max_length or 50]}).exists():
            n += 1
        data[f.attname] = f'{base}-{n}'[:f.max_length or 50]


def set_m2m(ctx, obj, row, adding):
    """Many-to-many links: the source branch → the new branch; mapped masters → their copies; people are left out."""
    for f in obj._meta.many_to_many:
        olds = row['m2m'].get(f.name) or []
        R = f.related_model
        if R._meta.label == 'OrganisationManager.brnch_mstr':
            vals = [ctx.nb.pk] if ctx.sb in olds else []
        elif _is_user(R) or R._meta.label == 'EmpManagement.emp_master':
            continue
        elif R._meta.app_label in ('Core',):
            vals = olds
        else:
            vals = ctx.ids(R, olds)
        rel = getattr(obj, f.name)
        if adding:
            rel.set(vals)
        elif vals:
            rel.add(*vals)


def put(ctx, row, key=None, update=True, exclude=(), fix=None, only=None, m2m=True):
    """Copy one source row into the new schema: match by key (update it) or create it. Returns (obj, created) or (None, False)."""
    model = row['model']
    data = build(ctx, row, exclude, only)
    if data is None:
        return None, False
    if fix is not None:
        data = fix(data, row)
        if data is None:
            return None, False
    obj = find(model, data, key)
    if obj is not None:
        if update:
            keyset = {model._meta.get_field(k).attname for k in (key or [])}
            for k, v in data.items():
                f = next((x for x in model._meta.concrete_fields if x.attname == k), None)
                if k in keyset or (f is not None and f.unique):
                    continue
                setattr(obj, k, v)
            obj.save()
        created = False
    else:
        _unique_ok(model, data)
        obj = model(**data)
        obj.save()
        created = True
    ctx.map(model, row['pk'], obj.pk)
    if m2m:
        set_m2m(ctx, obj, row, created)
    return obj, created


def step(ctx, key, fn):
    """Run one area of the copy in its own savepoint in the new schema; a failure is logged and skipped."""
    try:
        with _dst(ctx):
            with transaction.atomic():
                fn(ctx)
    except Exception as e:   # noqa: BLE001 – the company is made anyway
        log.exception('copy setup %s → %s: %s', ctx.src, ctx.dst, key)
        ctx.failed[key] = str(e)[:200]


def _in_branch(rows, ctx, field='branch'):
    """Rows whose branch m2m contains the source branch, or company-wide rows (no branch at all)."""
    return [r for r in rows if ctx.sb in (r['m2m'].get(field) or []) or not r['m2m'].get(field)]


# ====================================================================== the areas
def c_masters(ctx):
    for key, label, name_f in (('departments', 'OrganisationManager.dept_master', 'dept_name'),
                               ('designations', 'OrganisationManager.desgntn_master', 'desgntn_job_title'),
                               ('categories', 'OrganisationManager.ctgry_master', 'ctgry_title')):
        Mo = M(label)
        rows = [r for r in read(ctx, Mo) if ctx.sb in r['m2m'].get('branch', [])]
        n = 0
        for r in rows:
            obj, _c = put(ctx, r, key=[name_f])
            n += obj is not None
        ctx.add(key, n)


def c_grades(ctx):
    G = M('OrgStructure.Grade')
    if G is None:
        return
    rows = [r for r in read(ctx, G) if not r['f'].get('branch_ids') or ctx.sb in (r['f'].get('branch_ids') or [])]
    D = M('OrganisationManager.desgntn_master')

    def fix(d, r):
        d['branch_ids'] = [ctx.nb.pk] if r['f'].get('branch_ids') else []
        d['designation_ids'] = ctx.ids(D, r['f'].get('designation_ids') or [])
        return d
    ctx.add('grades', sum(1 for r in rows if put(ctx, r, key=['code'], fix=fix)[0] is not None))


def _drop_unassigned(model, assign_model, fk):
    """Calendars of the new company that no assignment uses any more (the standard ones replaced by the copy)."""
    used = set(assign_model.objects.values_list(fk, flat=True))
    for c in model.objects.exclude(pk__in=used):
        try:
            with transaction.atomic():
                c.delete()
        except Exception:
            pass


def c_calendars(ctx):
    nb = ctx.nb
    # weekend
    W, WD, AW = M('calendars.weekend_calendar'), M('calendars.weekenddetail'), M('calendars.assign_weekend')
    with _src(ctx):
        cal_ids = list(AW.objects.filter(branch=ctx.sb).values_list('weekend_model_id', flat=True).distinct())
    if cal_ids:
        for a in AW.objects.filter(branch=nb):
            a.branch.remove(nb)
            if not a.branch.exists() and not a.department.exists() and not a.category.exists() and not a.designation.exists() and not a.employee.exists():
                a.delete()
        for r in read(ctx, W, pk__in=cal_ids):
            obj, _c = put(ctx, r, key=['calendar_code'])
            if obj is None:
                continue
            WD.objects.filter(weekend_calendar=obj).delete()
            for d in read(ctx, WD, weekend_calendar_id=r['pk']):
                put(ctx, d, m2m=False)
            a = AW.objects.create(related_to='branch', weekend_model=obj, created_by=ctx.user)
            a.branch.add(nb)
            ctx.add('weekend_calendars')
        _drop_unassigned(W, AW, 'weekend_model_id')
    # holidays
    HC, H, AH = M('calendars.holiday_calendar'), M('calendars.holiday'), M('calendars.assign_holiday')
    with _src(ctx):
        cal_ids = list(AH.objects.filter(branch=ctx.sb).values_list('holiday_model_id', flat=True).distinct())
    if cal_ids:
        for a in AH.objects.filter(branch=nb):
            a.branch.remove(nb)
            if not a.branch.exists() and not a.department.exists() and not a.category.exists() and not a.designation.exists() and not a.employee.exists():
                a.delete()
        for r in read(ctx, HC, pk__in=cal_ids):
            obj, _c = put(ctx, r, key=['calendar_title', 'year'])
            if obj is None:
                continue
            H.objects.filter(calendar=obj).delete()
            n = 0
            for h in read(ctx, H, calendar_id=r['pk']):
                n += put(ctx, h, m2m=False)[0] is not None
            a = AH.objects.create(related_to='branch', holiday_model=obj, created_by=ctx.user)
            a.branch.add(nb)
            ctx.add('holiday_calendars')
            ctx.add('holidays', n)
        _drop_unassigned(HC, AH, 'holiday_model_id')


def _chain(ctx, wf_row, Lv, fk, new_wf, key='workflows'):
    """Levels of a copied workflow. A level that names a person of the source company makes the chain "Reporting manager"."""
    levels = read(ctx, Lv, **{f'{fk}_id': wf_row['pk']})
    person = any(lv['f'].get('approver_id') or lv['f'].get('escalate_to_id') for lv in levels)
    Lv.objects.filter(**{fk: new_wf}).delete()
    if person:
        new_wf.approval_type = 'reporting_manager'
        new_wf.save(update_fields=['approval_type'])
        ctx.named_users += 1
        if any(f.name == 'role' for f in Lv._meta.concrete_fields):
            from ZeoAdmin.approval_defaults import ROLE
            Lv.objects.create(**{fk: new_wf, 'level': 1, 'role': ROLE})
        return
    for lv in levels:
        put(ctx, lv, fix=lambda d, r: dict(d, **{f'{fk}_id': new_wf.pk}), m2m=False)


def _code(ctx, code):
    """SL-Q7I (source schema in the code) → SL-NEW."""
    if not code:
        return code
    s, d = ctx.src[:3].upper(), ctx.dst[:3].upper()
    return re.sub(rf'-{re.escape(s)}$', f'-{d}', code) if s != d else code


def c_leave(ctx):
    nb = ctx.nb
    LT, Ent = M('calendars.leave_type'), M('calendars.leave_entitlement')
    Reset, Pay = M('calendars.LeaveResetPolicy'), M('calendars.LeavePayRule')
    WF, LV = M('calendars.LVApprovalWorkflow'), M('calendars.LeaveApprovalLevels')
    rows = read(ctx, LT, branch_id=ctx.sb) + read(ctx, LT, branch__isnull=True)
    for r in rows:
        own = r['f'].get('branch_id') is not None
        def fix(d, r, own=own):
            d['branch_id'] = nb.pk if own else None
            d['code'] = _code(ctx, d.get('code'))
            return d
        t, _c = put(ctx, r, key=['name', 'branch'], fix=fix)
        if t is None:
            continue
        ctx.add('leave_types')
        # entitlements / reset / pay rules / workflows of this type that cover the new branch: replaced by the source's
        for e in Ent.objects.filter(leave_type=t):
            if not e.branches.exists() or e.branches.filter(pk=nb.pk).exists():
                if Reset is not None:
                    Reset.objects.filter(leave_entitlement=e).delete()
                e.delete()
        if Reset is not None:
            Reset.objects.filter(leave_type=t, leave_entitlement__isnull=True).delete()
        if Pay is not None:
            Pay.objects.filter(leave_type=t).delete()
        for w in WF.objects.filter(request_type=t, branch=nb):
            w.branch.remove(nb)
            if not w.branch.exists():
                LV.objects.filter(workflow=w).delete()
                w.delete()
        src_ents = [e for e in read(ctx, Ent, leave_type_id=r['pk']) if own or ctx.sb in e['m2m'].get('branches', []) or not e['m2m'].get('branches')]
        for e in src_ents:
            ne, _c = put(ctx, e, fix=lambda d, r: dict(d, leave_type_id=t.pk))
            if ne is None:
                continue
            ctx.add('leave_entitlements')
            if Reset is not None:
                for rp in read(ctx, Reset, leave_entitlement_id=e['pk']):
                    if put(ctx, rp, fix=lambda d, r: dict(d, leave_type_id=t.pk, leave_entitlement_id=ne.pk))[0] is not None:
                        ctx.add('leave_reset_policies')
        if Reset is not None:
            for rp in read(ctx, Reset, leave_type_id=r['pk'], leave_entitlement__isnull=True):
                if put(ctx, rp, fix=lambda d, r: dict(d, leave_type_id=t.pk))[0] is not None:
                    ctx.add('leave_reset_policies')
        if Pay is not None:
            for p in read(ctx, Pay, leave_type_id=r['pk']):
                if put(ctx, p, fix=lambda d, r: dict(d, leave_type_id=t.pk))[0] is not None:
                    ctx.add('leave_pay_rules')
        for w in read(ctx, WF, request_type_id=r['pk'], branch=ctx.sb):
            nw = WF(request_type=t, approval_type=w['f'].get('approval_type') or 'reporting_manager', created_by=ctx.user)
            nw.save()
            nw.branch.set([nb])
            ctx.map(WF, w['pk'], nw.pk)
            _chain(ctx, w, LV, 'workflow', nw)
            ctx.add('leave_workflows')


def c_leave_policies(ctx):
    P, PL = M('LeavePolicy.LeavePolicy'), M('LeavePolicy.LeavePolicyLine')
    if P is None:
        return
    C, LT = M('OrganisationManager.ctgry_master'), M('calendars.leave_type')
    for r in read(ctx, P):
        p, _c = put(ctx, r, key=['code'], fix=lambda d, r: dict(d, category_ids=ctx.ids(C, r['f'].get('category_ids') or [])))
        if p is None:
            continue
        ctx.add('leave_policies')
        PL.objects.filter(policy=p).delete()
        for ln in read(ctx, PL, policy_id=r['pk']):
            def fix(d, r):
                new = ctx.mapped(LT, r['f'].get('leave_type_id'))
                if new is None:
                    return None
                return dict(d, policy_id=p.pk, leave_type_id=new)
            put(ctx, ln, fix=fix, m2m=False)


def c_payroll(ctx):
    nb = ctx.nb
    SC, SS, PS = M('PayrollManagement.SalaryComponent'), M('PayrollManagement.SalaryStructure'), M('PayrollManagement.PayStructure')
    n = 0
    for r in read(ctx, SC, branch_id=ctx.sb) + read(ctx, SC, branch__isnull=True):
        own = r['f'].get('branch_id') is not None
        obj, _c = put(ctx, r, key=['code', 'branch'], fix=lambda d, r, own=own: dict(d, branch_id=nb.pk if own else None))
        n += obj is not None
    ctx.add('salary_components', n)
    ctx.add('salary_structures', sum(1 for r in _in_branch(read(ctx, SS), ctx) if put(ctx, r, key=['name'])[0] is not None))
    n = 0
    for r in _in_branch(read(ctx, PS), ctx):
        company_wide = not any(r['m2m'].get(k) for k in ('branch', 'department', 'category', 'designation'))
        if company_wide:
            from ZeoAdmin.setup_guide import company_paystructure
            cur = company_paystructure()
            if cur is not None and not cur.branch.exists() and not cur.category.exists():
                data = build(ctx, r)
                for k, v in data.items():
                    setattr(cur, k, v)
                cur.save()
                ctx.map(PS, r['pk'], cur.pk)
                n += 1
                continue
        n += put(ctx, r)[0] is not None
    ctx.add('pay_structures', n)


def c_types(ctx):
    nb = ctx.nb
    ctx.add('document_types', sum(1 for r in _in_branch(read(ctx, M('EmpManagement.document_type')), ctx)
                                   if put(ctx, r, key=['type_name'])[0] is not None))
    LoT = M('PayrollManagement.LoanType')
    if LoT is not None:
        ctx.add('loan_types', sum(1 for r in _in_branch(read(ctx, LoT), ctx) if put(ctx, r, key=['loan_type'])[0] is not None))
    RT = M('EmpManagement.RequestType')
    if RT is not None:
        ctx.add('request_types', sum(1 for r in _in_branch(read(ctx, RT), ctx) if put(ctx, r, key=['name'])[0] is not None))
    DRT = M('EmpManagement.DocRequestType')
    if DRT is not None:
        try:
            name_f = 'type_name' if any(f.name == 'type_name' for f in DRT._meta.concrete_fields) else 'name'
            rows = read(ctx, DRT)
            if any(f.name == 'branch' for f in DRT._meta.many_to_many):
                rows = _in_branch(rows, ctx)
            for r in rows:
                put(ctx, r, key=[name_f])
        except Exception:
            log.exception('document request types')
    AT = M('OrganisationManager.AssetType')
    if AT is not None:
        for r in _in_branch(read(ctx, AT), ctx):
            put(ctx, r, key=['name'])


# the other approval chains (leave chains are copied with the leave types)
CHAINS = [
    ('calendars.LatinEarlyApprovalWorkflow', 'calendars.LateinEarlyoutApprovalLevel', None),
    ('PayrollManagement.PayslipApprovalWorkflow', 'PayrollManagement.PayslipCommonWorkflow', None),
    ('PayrollManagement.LoanApprovalWorkflow', 'PayrollManagement.LoanApprovalLevels', 'loan_type'),
    ('PayrollManagement.AdvanceApprovalWorkflow', 'PayrollManagement.AdvanceCommonWorkflow', None),
    ('PayrollManagement.AirticketApprovalWorkflow', 'PayrollManagement.AirticketWorkflow', None),
    ('EmpManagement.ApprovalWorkflow', 'EmpManagement.ApprovalLevel', 'request_type'),
    ('EmpManagement.DocumentApprovalWorkflow', 'EmpManagement.DocumentApprovalLevel', 'request_type'),
    ('EmpManagement.ResignationApprovalWorkflow', 'EmpManagement.ResignationApprovalLevel', None),
    ('OrganisationManager.AssetApprovalWorkflow', 'OrganisationManager.AssetApprovalLevel', 'asset_type'),
]


def c_chains(ctx):
    nb = ctx.nb
    for wf_l, lv_l, type_f in CHAINS:
        W, L = M(wf_l), M(lv_l)
        if W is None or L is None:
            continue
        fk = next((f.name for f in L._meta.concrete_fields if f.is_relation and f.related_model is W), None)
        if fk is None:
            continue
        src = [r for r in read(ctx, W, branch=ctx.sb)]
        if not src:
            continue
        # the standard chains of the new branch for the same request types are replaced by the source's
        for r in src:
            flt = {'branch': nb}
            if type_f:
                tv = r['f'].get(f'{type_f}_id')
                new_t = ctx.mapped(W._meta.get_field(type_f).related_model, tv) if tv else None
                if tv and new_t is None:
                    continue
                flt[f'{type_f}_id'] = new_t
            for w in W.objects.filter(**flt).distinct():
                w.branch.remove(nb)
                if not w.branch.exists():
                    L.objects.filter(**{fk: w}).delete()
                    w.delete()
            nw, _c = put(ctx, r)
            if nw is None:
                continue
            _chain(ctx, r, L, fk, nw)
            ctx.add('approval_workflows')


def _template_models():
    """Every e-mail template model: subject + body + a branch many-to-many."""
    out = []
    for m in apps.get_models():
        names = {f.name for f in m._meta.concrete_fields}
        if {'subject', 'body'} <= names and any(f.name == 'branch' for f in m._meta.many_to_many) and \
                m._meta.app_label not in ('UserManagement', 'ZeoCloud', 'LanguagePack'):
            kf = 'template_type' if 'template_type' in names else ('template_name' if 'template_name' in names else None)
            if kf:
                out.append((m, kf))
    return out


def c_templates(ctx):
    nb = ctx.nb
    n = 0
    for m, kf in _template_models():
        for r in read(ctx, m, branch=ctx.sb):
            data = build(ctx, r)
            if data is None:
                continue
            obj = m.objects.filter(branch=nb, **{f'{kf}__iexact': data.get(kf) or ''}).order_by('pk').first()
            if obj is not None:
                obj.subject, obj.body = data.get('subject') or obj.subject, data.get('body') or obj.body
                obj.save()
            else:
                obj = m(**data)
                obj.save()
                set_m2m(ctx, obj, r, True)
            n += 1
    ctx.add('email_templates', n)


def c_numbering(ctx):
    nb = ctx.nb
    DN = M('OrganisationManager.DocumentNumbering')
    from OrganisationManager.signals import branch_doc_prefix
    n = 0
    for r in read(ctx, DN, branch_id_id=ctx.sb):
        t = r['f']['type']
        row = DN.objects.filter(branch_id=nb, type=t).first() or DN(branch_id=nb, type=t)
        std = branch_doc_prefix(nb, t)     # e.g. ABU-LEA from the new code
        sp = r['f'].get('prefix') or ''
        # the source's own part after its branch part is kept (Q7I-LEAVE → ABU-LEAVE); otherwise the standard prefix
        tail = sp.split('-', 1)[1] if '-' in sp else ''
        row.prefix = (f"{std.split('-', 1)[0]}-{tail}" if tail else std)[:50]
        if DN.objects.filter(type=t, prefix__iexact=row.prefix).exclude(branch_id=nb).exists():
            row.prefix = std
        row.suffix = r['f'].get('suffix') or ''
        row.total_length = r['f'].get('total_length') or row.total_length or 12
        row.automatic_numbering = r['f'].get('automatic_numbering', True)
        row.current_number = 0
        row.start_date = row.start_date or timezone.localdate()
        row.end_date = row.end_date or r['f'].get('end_date')
        row.user = None
        row.created_by = ctx.user
        row.save()
        n += 1
    ctx.add('document_numbering', n)
    NP = M('ZeoAdmin.NumberingPattern')
    if NP is not None:
        for r in read(ctx, NP):
            cur = NP.objects.order_by('id').first()
            data = build(ctx, r)
            if cur is None:
                NP.objects.create(**data)
            else:
                for k, v in data.items():
                    setattr(cur, k, v)
                cur.save()
            ctx.add('numbering_pattern')
            break


def c_attendance(ctx):
    nb = ctx.nb
    R = M('AttendancePlus.AttendanceRule')
    if R is not None:
        D, C = M('OrganisationManager.dept_master'), M('OrganisationManager.ctgry_master')
        n = 0
        for r in read(ctx, R):
            f = r['f']
            sc = f.get('scope')
            if sc == 'employee' or f.get('employee_id'):
                continue
            if sc == 'branch' and f.get('branch_id') != ctx.sb:
                continue

            def fix(d, r):
                d['branch_id'] = nb.pk if r['f'].get('branch_id') == ctx.sb else None
                d['department_id'] = ctx.mapped(D, r['f'].get('department_id')) if r['f'].get('department_id') else None
                d['category_id'] = ctx.mapped(C, r['f'].get('category_id')) if r['f'].get('category_id') else None
                if (r['f'].get('department_id') and not d['department_id']) or (r['f'].get('category_id') and not d['category_id']):
                    return None
                d['late_policy_id'] = d['early_policy_id'] = None
                d['exempt_employee_ids'] = []
                d['exempt_category_ids'] = ctx.ids(C, r['f'].get('exempt_category_ids') or [])
                return d
            n += put(ctx, r, key=['name', 'scope'], fix=fix)[0] is not None
        ctx.add('attendance_rules', n)
    S = M('calendars.Shift')
    if S is not None:
        ctx.add('shifts', sum(1 for r in read(ctx, S) if put(ctx, r, key=['name'])[0] is not None))


def c_expenses(ctx):
    EC, EP, EL = M('ExpenseManagement.ExpenseCategory'), M('ExpenseManagement.ExpensePolicy'), M('ExpenseManagement.ExpensePolicyLimit')
    if EC is None:
        return
    ctx.add('expense_categories', sum(1 for r in read(ctx, EC) if put(ctx, r, key=['code'])[0] is not None))
    G = M('OrgStructure.Grade')
    n = 0
    for r in read(ctx, EP):
        bids = r['f'].get('branch_ids') or []
        if bids and ctx.sb not in bids:
            continue

        def fix(d, r):
            d['branch_ids'] = [ctx.nb.pk] if r['f'].get('branch_ids') else []
            d['category_ids'] = ctx.ids(EC, r['f'].get('category_ids') or [])
            d['grade_ids'] = ctx.ids(G, r['f'].get('grade_ids') or []) if G is not None else []
            return d
        p, _c = put(ctx, r, key=['name'], fix=fix)
        if p is None:
            continue
        n += 1
        EL.objects.filter(policy=p).delete()
        for ln in read(ctx, EL, policy_id=r['pk']):
            put(ctx, ln, fix=lambda d, r: dict(d, policy_id=p.pk), m2m=False)
    ctx.add('expense_policies', n)


def c_print(ctx):
    PT, PB = M('PrintTemplates.PrintTemplate'), M('PrintTemplates.PrintBranding')
    if PT is None:
        return
    n = 0
    for r in read(ctx, PT):
        if put(ctx, r, key=['doc_type', 'name', 'language'], exclude=('copied_from', 'created_by_id', 'updated_by_id'))[0] is not None:
            n += 1
    ctx.add('print_templates', n)
    if PB is not None:
        keep = ('logo_position', 'primary_color', 'accent_color', 'text_color', 'font', 'font_arabic', 'show_stamp',
                'show_signature', 'show_watermark', 'watermark_text')
        for r in read(ctx, PB):
            cur = PB.objects.order_by('id').first() or PB()
            for k in keep:
                if k in r['f']:
                    setattr(cur, k, r['f'][k])
            cur.save()
            ctx.add('settings')
            break


# company-wide one-row settings: (model, fields copied – None = all except NEVER / identity)
SINGLETONS = [
    ('PayrollPlus.PayrollPlusSettings', None),
    ('ExpenseManagement.ExpenseSettings', None),
    ('OrgStructure.OrgSettings', None),
    ('AssetPlus.AssetPlusSettings', None),
    ('CountryPack.CountryPackSettings', None),
    ('ZeoHR.HRSettings', ('require_review', 'terms', 'renewal_days', 'renewal_auto', 'fine_max_days', 'suspension_max_days',
                          'show_cause_days', 'exit_auto', 'exit_days_before', 'handover_signature', 'ticket_ai', 'ticket_ai_min')),
    ('OrgStructure.CompanyProfile', ('fiscal_year_start_month', 'week_start')),      # identity fields stay empty
    ('AISettings.AISettings', ('mode', 'use_ask', 'use_documents', 'use_tickets', 'monthly_limit')),   # never the key
]


def c_settings(ctx):
    for label, only in SINGLETONS:
        Mo = M(label)
        if Mo is None:
            continue
        rows = read(ctx, Mo)
        if not rows:
            continue
        r = rows[0]
        data = build(ctx, r, only=only)
        if data is None:
            continue
        if label == 'AISettings.AISettings' and data.get('mode') not in (None, '', 'off', 'zeo', 'builtin', 'local'):
            data.pop('mode', None)      # a mode that needs the source's own key / server is not copied
        cur = Mo.objects.order_by('id').first()
        if cur is None:
            cur = Mo(**data)
        else:
            for k, v in data.items():
                setattr(cur, k, v)
        cur.save()
        ctx.add('settings')
    CP = M('OrgStructure.CountryPolicy')
    if CP is not None:
        for r in read(ctx, CP):
            put(ctx, r, key=['code'])
    # HR policy defaults of the setup guide (notice, contract type, leave year …) – never the guide's progress
    G = M('ZeoAdmin.SetupGuide')
    with _src(ctx):
        sg = G.objects.order_by('id').first()
        pol = dict(sg.policies or {}) if sg is not None else {}
    pol = {k: v for k, v in pol.items() if k not in ('test_mail_ok', 'paid_none', 'test_to')}
    if pol:
        g = G.get()
        g.policies = dict(g.policies or {}, **pol)
        g.save(update_fields=['policies', 'updated_at'])
        ctx.add('settings')
    # probation of the source branch
    B = M('OrganisationManager.brnch_mstr')
    with _src(ctx):
        sb = B.objects.filter(pk=ctx.sb).first()
        prob, st, country = (sb.probation_period_days, sb.br_state_id_id, sb.br_country_id) if sb else (None, None, None)
    if prob is not None:
        ctx.nb.probation_period_days = prob
        if st and (not country or country == ctx.nb.br_country_id):
            ctx.nb.br_state_id_id = st
        ctx.nb.save(update_fields=['probation_period_days', 'br_state_id'])


AREAS = [('masters', c_masters), ('grades', c_grades), ('calendars', c_calendars), ('types', c_types),
         ('leave', c_leave), ('leave policies', c_leave_policies), ('payroll', c_payroll), ('approval chains', c_chains),
         ('e-mail templates', c_templates), ('numbering', c_numbering), ('attendance', c_attendance),
         ('expenses', c_expenses), ('print', c_print), ('settings', c_settings)]


def copy(plan, dst_schema, user=None, progress=None):
    """Copy the setup of plan['branch_id'] of plan['source_schema'] into the new company dst_schema (its first branch).
    Returns {source, branch, counts: {key: n}, items: [{key, label, count}], failed: {area: error}, named_users}."""
    ctx = Ctx(plan['source_schema'], dst_schema, plan['branch_id'], user)
    with _dst(ctx):
        ctx.nb = M('OrganisationManager.brnch_mstr').objects.order_by('id').first()
    if ctx.nb is None:
        return {'failed': {'branch': 'The new company has no branch.'}, 'counts': {}, 'items': []}
    for i, (name, fn) in enumerate(AREAS):
        if progress is not None:
            try:
                progress(i / len(AREAS), name)
            except Exception:
                pass
        step(ctx, name, fn)
    try:   # every request type of the new branch has a chain (types the source had none for keep the standard one)
        with _dst(ctx):
            from ZeoAdmin.approval_defaults import ensure
            ensure(user=user)
    except Exception:
        log.exception('approval chains after the copy')
    items = [{'key': k, 'label': lbl if ctx.counts[k] != 1 else ONE.get(k, lbl), 'count': ctx.counts[k]} for k, lbl in LABELS if ctx.counts.get(k)]
    res = {'source': plan.get('source_name'), 'source_schema': plan['source_schema'], 'branch': plan.get('branch_source_name'),
           'new_branch': plan.get('name'), 'new_code': plan.get('code'), 'counts': ctx.counts, 'items': items,
           'failed': ctx.failed, 'named_users': ctx.named_users, 'at': timezone.now().isoformat()}
    try:
        with _dst(ctx):
            from .models import CompanyOption
            CompanyOption.objects.update_or_create(key='setup_copied_from', defaults={'value': res})
    except Exception:
        log.exception('remember the copied setup')
    return res


def copied_from():
    """The note of the current company ({source, branch, items …}) or None."""
    try:
        from .models import CompanyOption
        o = CompanyOption.objects.filter(key='setup_copied_from').first()
        return o.value if o is not None else None
    except Exception:
        return None
