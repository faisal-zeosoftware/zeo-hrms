"""v1.67.27 (HSE scope) – the modules the HSE functional scope asked for that ZEO did not have yet:

* PPE management – catalogue with stock, the PPE matrix (who needs what), issue / acknowledge / return / replace / lost,
  replace-by dates, low-stock and expiry alerts.
* Emergency management – emergency response plans, emergency team (wardens, first aiders … with their competency),
  assembly points, emergency equipment register with inspections, drills with evacuation time and evaluation.
* Safety equipment and certification – lifting gear, scaffolds, harnesses, pressure vessels, gas detectors … with a
  green / yellow / red tag, certificate expiry and periodic inspection.
* Contractor workforce and performance – the contractor's own workers (induction, medical, blocked) and a calculated
  HSE score per period (A–D), which can suspend the contractor.

Every page is a register of registers.py (list, form, detail and actions are built by the frontend from the meta), so
there is no new screen code. Rules live here; registers.py imports this module at its end.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Q

from . import access as A
from . import services as S
from .models import (Action, AssemblyPoint, Audit, CompetencyType, Competency, Contractor, ContractorEvaluation, ContractorWorker,
                     EmergencyDrill, EmergencyEquipment, EmergencyPlan, EmergencyTeamMember, HSESettings, Incident, Permit, PPEIssue,
                     PPEItem, PPERequirement, SafetyEquipment)

log = logging.getLogger(__name__)
HSE = ('hse_manager', 'hse_officer')
PAGE = S.PAGE + 'r/'


def add_months(d, months):
    if not d or not months:
        return None
    y, m = divmod(d.month - 1 + months, 12)
    import calendar
    day = min(d.day, calendar.monthrange(d.year + y, m + 1)[1])
    return d.replace(year=d.year + y, month=m + 1, day=day)


def hse_users():
    return S.users_with_role('hse_officer') + S.users_with_role('hse_manager')


def _tell_hse(summary, text, model, obj_id, label, page):
    S.tell_users(hse_users(), summary, text, model, obj_id, label, page, summary)


# ================================================================== PPE
def ppe_receive(item, request, f):
    qty = f.i('qty') or 0
    if qty <= 0:
        raise S.HSEError('Enter how many were received.', 'qty')
    with transaction.atomic():
        it = PPEItem.objects.select_for_update().get(pk=item.pk)
        it.stock += qty
        it.save(update_fields=['stock'])
    S.event('ppeitem', item.pk, f'{qty} received into stock{(": " + f.s("note")) if f.s("note") else ""} – now {it.stock}', request.user)
    return it


def ppe_issue_check(o, request):
    if not o.pk:
        if bool(o.employee_id) == bool(o.worker_id):
            raise S.HSEError('Issue to an employee or to a contractor worker (one of the two).', 'employee')
        if not o.item.is_active:
            raise S.HSEError(f'{o.item.name} is no longer issued.', 'item')
        if (o.qty or 0) < 1:
            raise S.HSEError('Quantity is at least 1.', 'qty')
        sizes = [x.strip().lower() for x in (o.item.sizes or '').split(',') if x.strip()]
        if sizes and not o.size:
            raise S.HSEError(f'Choose the size ({o.item.sizes}).', 'size')
        if sizes and o.size.strip().lower() not in sizes:
            raise S.HSEError(f'Size "{o.size}" is not offered for {o.item.name} ({o.item.sizes}).', 'size')
        if o.worker_id and o.worker.status != 'active':
            raise S.HSEError(f'{o.worker.name} is blocked – PPE is not issued to blocked workers.', 'worker')


def _take_stock(item, qty, ref):
    it = PPEItem.objects.select_for_update().get(pk=item.pk)
    if it.stock < qty:
        raise S.HSEError(f'Only {it.stock} × {it.name} in stock – receive stock first.', 'qty')
    it.stock -= qty
    it.save(update_fields=['stock'])
    if it.min_stock and it.stock <= it.min_stock:
        _tell_hse(f'Low stock: {it.name} ({it.stock} left)', f'{it.code} {it.name}: {it.stock} left after {ref}; minimum {it.min_stock}.',
                  'ppeitem', it.pk, it.code, PAGE + f'ppe-items/{it.pk}')
    return it


def _person_text(o):
    return S.emp_name(o.employee) if o.employee_id else (o.worker.name if o.worker_id else '')


def ppe_issue_saved(o, request, created, old):
    if not created:
        o.save()
        return o
    with transaction.atomic():
        _take_stock(o.item, o.qty, o.number)
        if not o.issued_on:
            o.issued_on = S.today()
        if not o.expires_on:
            o.expires_on = add_months(o.issued_on, o.item.life_months)
        o.issued_by = request.user
        if o.site_id is None and o.worker_id is None and o.employee_id:
            pass
        o.save()
    if o.employee_id:
        S.tell_emp(o.employee, f'{o.number}: PPE issued to you – please acknowledge',
                   f'{o.qty} × {o.item.name} {o.size}'.strip() + (f', replace by {o.expires_on}' if o.expires_on else ''),
                   'ppe', o.pk, o.number, PAGE + f'ppe-issues/{o.pk}', f'Acknowledge PPE {o.number}')
    return o


def ppe_ack(o, request, f):
    me = A.my_emp(request)
    A.need(me is not None and o.employee_id == me.pk, 'Only the person who received the PPE acknowledges it.')
    if o.acknowledged_at:
        raise S.HSEError('Already acknowledged.')
    o.acknowledged_at = S.now()
    o.save(update_fields=['acknowledged_at'])
    S.close_todos('ppe', o.pk, 'Acknowledge')
    S.event('ppe', o.pk, f'Acknowledged by {S.emp_name(me)} – received and will use and look after it', request.user)


def ppe_return(o, request, f):
    cond = f.choice('condition', PPEIssue._meta.get_field('condition').choices, '') or 'worn'
    with transaction.atomic():
        o.status, o.returned_on, o.condition = 'returned', f.dt('returned_on') or S.today(), cond
        o.save(update_fields=['status', 'returned_on', 'condition'])
        back = o.item.reusable and cond == 'good'
        if back:
            PPEItem.objects.filter(pk=o.item_id).update(stock=o.item.stock + o.qty)
    S.close_todos('ppe', o.pk)
    S.event('ppe', o.pk, f'Returned ({o.get_condition_display()})' + (' – back in stock' if back else ''), request.user)


def ppe_replace(o, request, f):
    reason = f.s('reason')
    if not reason:
        raise S.HSEError('Say why it is replaced.', 'reason')
    with transaction.atomic():
        _take_stock(o.item, o.qty, o.number)
        n = PPEIssue.objects.create(number=S.next_number('ppe'), item=o.item, employee=o.employee, worker=o.worker, site=o.site,
                                    size=f.s('size') or o.size, qty=o.qty, issued_on=S.today(),
                                    expires_on=add_months(S.today(), o.item.life_months), issued_by=request.user, replaces=o, reason=reason)
        o.status, o.reason = 'replaced', (o.reason + '\n' if o.reason else '') + f'Replaced by {n.number}: {reason}'
        o.save(update_fields=['status', 'reason'])
    S.close_todos('ppe', o.pk)
    S.event('ppe', o.pk, f'Replaced by {n.number}: {reason}', request.user)
    S.event('ppe', n.pk, f'Issued as replacement of {o.number}', request.user)
    if n.employee_id:
        S.tell_emp(n.employee, f'{n.number}: replacement PPE issued – please acknowledge', f'{n.qty} × {n.item.name}', 'ppe', n.pk, n.number,
                   PAGE + f'ppe-issues/{n.pk}', f'Acknowledge PPE {n.number}')
    return n


def ppe_loss(o, request, f):
    kind = f.choice('kind', (('lost', 'Lost'), ('damaged', 'Damaged')), 'lost')
    reason = f.s('reason')
    if not reason:
        raise S.HSEError('Describe what happened.', 'reason')
    o.status, o.reason = kind, reason
    o.save(update_fields=['status', 'reason'])
    S.close_todos('ppe', o.pk)
    S.event('ppe', o.pk, f'Reported {kind}: {reason}', request.user)


def ppe_extra(o, request):
    return {'person': _person_text(o), 'replaced_by': [x.number for x in o.replaced_by.all()],
            'overdue': bool(o.expires_on and o.status == 'issued' and o.expires_on < S.today())}


def ppe_needed(employee_or_role):
    """PPE the PPE matrix asks for a job / activity (case-insensitive match)."""
    qs = PPERequirement.objects.filter(job_role__iexact=str(employee_or_role).strip())
    return sorted({i.name for r in qs for i in r.items.all()})


def ppe_due():
    """Daily: PPE past its replace-by date → Expired – replace (HSE and the person are told)."""
    n = 0
    for o in PPEIssue.objects.filter(status='issued', expires_on__lt=S.today()).select_related('item', 'employee'):
        o.status = 'expired'
        o.save(update_fields=['status'])
        S.event('ppe', o.pk, f'Replace-by date {o.expires_on} passed – to replace')
        _tell_hse(f'Replace PPE {o.number} ({o.item.name}) for {_person_text(o)}', f'Replace-by date was {o.expires_on}.', 'ppe', o.pk, o.number,
                  PAGE + f'ppe-issues/{o.pk}')
        if o.employee_id:
            S.tell_emp(o.employee, f'{o.number}: your {o.item.name} is due for replacement', 'Collect a replacement from HSE / the store.', 'ppe', o.pk,
                       o.number, PAGE + f'ppe-issues/{o.pk}')
        n += 1
    for it in PPEItem.objects.filter(is_active=True, min_stock__gt=0):
        if it.stock <= it.min_stock:
            _tell_hse(f'Low stock: {it.name} ({it.stock} left)', f'{it.code} {it.name}: {it.stock} in stock, minimum {it.min_stock}.', 'ppeitem', it.pk,
                      it.code, PAGE + f'ppe-items/{it.pk}')
    return n


# ================================================================== emergency
def plan_check(o, request):
    if o.status == 'approved' and not (o.procedure or '').strip():
        raise S.HSEError('The plan needs its response procedure.', 'procedure')


def plan_approve(o, request, f):
    A.need(A.has_role(request, 'hse_manager') or A.is_admin(request), 'Only the HSE manager approves emergency plans.')
    if not (o.procedure or '').strip():
        raise S.HSEError('Write the response procedure first.', 'procedure')
    if o.scenario in ('fire', 'evacuation', 'gas_leak', 'chemical_spill', 'bomb_threat') and not o.assembly_points.exists():
        raise S.HSEError('An evacuation plan needs at least one assembly point.', 'assembly_points')
    o.status, o.approved_by, o.approved_at = 'approved', request.user, S.now()
    if not o.review_date:
        o.review_date = add_months(S.today(), 12)
    o.save()
    S.event('emplan', o.pk, f'Approved by {S.user_name(request.user)} – in force, review by {o.review_date}', request.user)


def plan_revise(o, request, f):
    o.status = 'draft'
    o.save(update_fields=['status'])
    S.event('emplan', o.pk, f'Opened for revision: {f.s("note")}', request.user)


def plan_archive(o, request, f):
    o.status = 'archived'
    o.save(update_fields=['status'])
    S.event('emplan', o.pk, f'Archived: {f.s("note")}', request.user)


def valid_competency(emp, code):
    ct = CompetencyType.objects.filter(code=code, is_active=True).first()
    if ct is None:
        return True, ''   # the company does not track this competency
    ok = Competency.objects.filter(employee=emp, ctype=ct, assessed_competent=True).filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=S.today())).exists()
    return ok, ct.name


def team_check(o, request):
    code = EmergencyTeamMember.NEEDS.get(o.role)
    if code and o.employee_id:
        ok, name = valid_competency(o.employee, code)
        if not ok and HSESettings.get().block_expired_competency:
            raise S.HSEError(f'{S.emp_name(o.employee)} has no valid "{name}" competency – record the training first.', 'employee')


def team_extra(o, request):
    code = EmergencyTeamMember.NEEDS.get(o.role)
    if not code:
        return {'competency': 'Not required'}
    ok, name = valid_competency(o.employee, code)
    return {'competency': ('Valid' if ok else 'Missing or expired') + (f' – {name}' if name else '')}


def equip_saved(o, request, created, old):
    if o.inspect_days and not o.next_due:
        o.next_due = (o.last_inspected or S.today()) + timedelta(days=o.inspect_days)
    o.save()
    return o


def equip_inspect(o, request, f):
    res = f.choice('result', (('ok', 'In order'), ('defect', 'Defect')), None)
    if res is None:
        raise S.HSEError('Choose the result.', 'result')
    on = f.dt('inspected_on') or S.today()
    o.last_inspected = on
    o.next_due = on + timedelta(days=o.inspect_days or 30)
    a = None
    if res == 'defect':
        if not f.emp('owner') or not f.dt('due_date'):
            raise S.HSEError('A defect needs an action owner and a due date.', 'owner')
        o.status = 'out_of_service' if f.b('out_of_service') else 'defect'
        a = S.create_action(source='emergency', source_id=o.pk, source_ref=o.number, title=f'Repair {o.get_kind_display()} {o.number}: {f.s("note") or "defect"}'[:255],
                            description=f'{o.location}\n{f.s("note")}', owner=f.emp('owner'), due_date=f.dt('due_date'), priority='high', site=o.site,
                            user=request.user)
    else:
        o.status = 'ok'
    o.save()
    S.event('emequip', o.pk, f'Inspected {on}: {"defect – " + (f.s("note") or "") if res == "defect" else "in order"}' + (f' ({a.number})' if a else ''),
            request.user)
    return a


def equip_service(o, request, f):
    o.service_due = f.dt('service_due')
    if not o.service_due:
        raise S.HSEError('Enter the next service / expiry date.', 'service_due')
    o.save(update_fields=['service_due'])
    S.event('emequip', o.pk, f'Serviced {f.dt("serviced_on") or S.today()} – next {o.service_due}. {f.s("note")}', request.user)


def drill_record(o, request, f):
    on = f.dt('conducted_on')
    if not on:
        raise S.HSEError('Enter the date of the drill.', 'conducted_on')
    if on > S.today():
        raise S.HSEError('A drill cannot be recorded in the future.', 'conducted_on')
    o.conducted_on, o.participants = on, f.i('participants') or 0
    o.evacuation_minutes = f.n('evacuation_minutes')
    o.head_count_ok = None if f.d.get('head_count_ok') in (None, '') else f.b('head_count_ok')
    o.observations = f.s('observations')
    o.rating = f.choice('rating', EmergencyDrill.RATING, '')
    why = []
    if o.target_minutes and o.evacuation_minutes is not None and o.evacuation_minutes > o.target_minutes:
        why.append(f'evacuation took {o.evacuation_minutes} min (target {o.target_minutes})')
    if o.head_count_ok is False:
        why.append('head count did not match')
    if o.rating in ('improve', 'poor'):
        why.append(f'rated {o.get_rating_display().lower()}')
    o.improvement_needed = bool(why)
    o.status = 'done'
    o.save()
    S.event('drill', o.pk, f'Drill held {on}: {o.participants} people' + (f'; improvement needed – {"; ".join(why)}' if why else '; no improvement needed'),
            request.user)


def drill_close(o, request, f):
    if o.improvement_needed and not Action.objects.filter(source='emergency', source_ref=o.number).exists():
        raise S.HSEError('This drill showed improvements to make – raise at least one action before closing it.')
    o.status = 'closed'
    o.save(update_fields=['status'])
    S.event('drill', o.pk, f'Closed by {S.user_name(request.user)}', request.user)


def emergency_due():
    """Daily: emergency equipment inspection or service overdue → to-do for HSE; plans past their review date."""
    n, t = 0, S.today()
    for o in EmergencyEquipment.objects.exclude(status='removed').filter(Q(next_due__lt=t) | Q(service_due__lt=t)).select_related('site'):
        what = 'inspection' if (o.next_due and o.next_due < t) else 'service / refill'
        _tell_hse(f'Emergency equipment {o.number} {what} overdue', f'{o.get_kind_display()} at {o.site.name}, {o.location}.', 'emequip', o.pk, o.number,
                  PAGE + f'emergency-equipment/{o.pk}')
        n += 1
    for p in EmergencyPlan.objects.filter(status='approved', review_date__lt=t):
        _tell_hse(f'Review emergency plan {p.number}', f'{p.title}: review date {p.review_date}.', 'emplan', p.pk, p.number, PAGE + f'emergency-plans/{p.pk}')
        n += 1
    return n


# ================================================================== safety equipment
def eq_apply_tag(o):
    """Red tag or an expired certificate → out of service (never in service with a red tag)."""
    if o.status == 'retired':
        return
    expired = bool(o.cert_expires and o.cert_expires < S.today())
    if expired and o.tag != 'red':
        o.tag = 'red'
    if o.tag == 'red':
        o.status = 'out_of_service'


def eq_check(o, request):
    if o.cert_expires and o.certified_on and o.cert_expires < o.certified_on:
        raise S.HSEError('The certificate cannot expire before it was issued.', 'cert_expires')
    if o.tag == 'yellow' and not (o.restrictions or '').strip():
        raise S.HSEError('A yellow tag needs the restrictions written down.', 'restrictions')
    eq_apply_tag(o)


def eq_saved(o, request, created, old):
    if o.inspect_days and not o.next_inspection:
        o.next_inspection = (o.last_inspected or S.today()) + timedelta(days=o.inspect_days)
    o.save()
    return o


def eq_inspect(o, request, f):
    tag = f.choice('tag', SafetyEquipment.TAGS, None)
    if tag is None:
        raise S.HSEError('Choose the tag.', 'tag')
    on = f.dt('inspected_on') or S.today()
    if tag == 'yellow' and not f.s('restrictions'):
        raise S.HSEError('A yellow tag needs the restrictions.', 'restrictions')
    a = None
    if tag == 'red':
        if not f.emp('owner') or not f.dt('due_date'):
            raise S.HSEError('A red tag needs an action owner and a due date for the repair.', 'owner')
        a = S.create_action(source='equipment', source_id=o.pk, source_ref=o.number, title=f'Repair or replace {o.number} {o.name}: {f.s("note") or "failed inspection"}'[:255],
                            owner=f.emp('owner'), due_date=f.dt('due_date'), priority='high', site=o.site, user=request.user)
    o.tag, o.last_inspected = tag, on
    o.restrictions = f.s('restrictions') if tag == 'yellow' else ''
    if o.inspect_days:
        o.next_inspection = on + timedelta(days=o.inspect_days)
    o.status = 'in_service' if o.status != 'retired' else o.status
    eq_apply_tag(o)
    o.save()
    S.event('equip', o.pk, f'Inspected {on}: {o.get_tag_display()}' + (f' – {f.s("note")}' if f.s('note') else '') + (f' ({a.number})' if a else '')
            + ('; certificate expired – stays red' if (o.cert_expires and o.cert_expires < S.today()) else ''), request.user)
    return a


def eq_certify(o, request, f):
    exp = f.dt('cert_expires')
    if not exp:
        raise S.HSEError('Enter when the certificate expires.', 'cert_expires')
    if exp < S.today():
        raise S.HSEError('That certificate has already expired.', 'cert_expires')
    o.certificate_no, o.certified_by = f.s('certificate_no')[:80], f.s('certified_by')[:150]
    o.certified_on, o.cert_expires = f.dt('certified_on') or S.today(), exp
    if not o.certificate_no:
        raise S.HSEError('Enter the certificate number.', 'certificate_no')
    if o.tag == 'red' and f.b('passed'):
        o.tag = 'green'
    if o.tag != 'red' and o.status == 'out_of_service':
        o.status = 'in_service'
    o.save()
    for x in f.filelist('file'):
        from .models import HSEFile
        HSEFile.objects.create(model='equip', object_id=o.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=request.user)
    S.event('equip', o.pk, f'Certificate {o.certificate_no} by {o.certified_by or "–"}, valid to {exp}; {o.get_tag_display()}', request.user)


def eq_retire(o, request, f):
    o.status = 'retired'
    o.save(update_fields=['status'])
    S.event('equip', o.pk, f'Retired: {f.s("note")}', request.user)


def equipment_due():
    """Daily: expired certificate → red tag, out of service; certificate within 30 days or inspection overdue → to-do."""
    n, t = 0, S.today()
    for o in SafetyEquipment.objects.exclude(status='retired').select_related('owner', 'site'):
        if o.cert_expires and o.cert_expires < t and (o.tag != 'red' or o.status != 'out_of_service'):
            o.tag, o.status = 'red', 'out_of_service'
            o.save(update_fields=['tag', 'status'])
            S.event('equip', o.pk, f'Certificate expired {o.cert_expires} – red tag, out of service')
            _tell_hse(f'{o.number} {o.name}: certificate expired – out of service', f'Expired {o.cert_expires}.', 'equip', o.pk, o.number, PAGE + f'safety-equipment/{o.pk}')
            S.tell_emp(o.owner, f'{o.number} {o.name}: certificate expired – do not use', f'Expired {o.cert_expires}.', 'equip', o.pk, o.number, PAGE + f'safety-equipment/{o.pk}')
            n += 1
        elif o.cert_expires and t <= o.cert_expires <= t + timedelta(days=30):
            _tell_hse(f'Recertify {o.number} {o.name} before {o.cert_expires}', '', 'equip', o.pk, o.number, PAGE + f'safety-equipment/{o.pk}')
            n += 1
        if o.next_inspection and o.next_inspection < t and o.status == 'in_service':
            _tell_hse(f'Inspect {o.number} {o.name} (due {o.next_inspection})', '', 'equip', o.pk, o.number, PAGE + f'safety-equipment/{o.pk}')
            n += 1
    return n


def equipment_blocks(text_or_ids):
    """For a permit: equipment named on it that is out of service (red tag / expired certificate)."""
    out = []
    for o in SafetyEquipment.objects.filter(status='out_of_service'):
        if o.number and o.number.lower() in (text_or_ids or '').lower():
            out.append(f'{o.number} {o.name} is out of service ({o.get_tag_display()})')
    return out


# ================================================================== contractor workers and evaluation
def worker_cleared(w):
    why, t = [], S.today()
    if w.status != 'active':
        why.append('blocked' + (f': {w.block_reason}' if w.block_reason else ''))
    if w.contractor.status != 'approved':
        why.append(f'contractor {w.contractor.get_status_display().lower()}')
    if not w.induction_on:
        why.append('not inducted')
    elif w.induction_valid_until and w.induction_valid_until < t:
        why.append(f'induction expired {w.induction_valid_until}')
    if w.medical_valid_until and w.medical_valid_until < t:
        why.append(f'medical expired {w.medical_valid_until}')
    return not why, why


def worker_extra(w, request):
    ok, why = worker_cleared(w)
    return {'cleared': ok, 'not_cleared_because': why, 'ppe': [f'{x.number} {x.item.name} ({x.get_status_display()})' for x in w.ppe.select_related('item')[:50]]}


def worker_check(w, request):
    if w.induction_on and w.induction_valid_until and w.induction_valid_until < w.induction_on:
        raise S.HSEError('The induction cannot expire before it was given.', 'induction_valid_until')


def worker_block(w, request, f):
    if not f.s('note'):
        raise S.HSEError('Say why the worker is blocked.', 'note')
    w.status, w.block_reason = 'blocked', f.s('note')
    w.save(update_fields=['status', 'block_reason'])
    S.event('cworker', w.pk, f'Blocked: {f.s("note")}', request.user)


def worker_unblock(w, request, f):
    w.status, w.block_reason = 'active', ''
    w.save(update_fields=['status', 'block_reason'])
    S.event('cworker', w.pk, f'Unblocked: {f.s("note")}', request.user)


def evaluate(e):
    """Score out of 100 for the period: start at 100 and deduct for what went wrong; audits weigh 40 % when there are any."""
    c, a, b = e.contractor, e.period_from, e.period_to
    inc = Incident.objects.filter(contractor=c, occurred_at__date__gte=a, occurred_at__date__lte=b)
    e.incidents = inc.count()
    e.lost_time = inc.filter(Q(treatment__in=('lost_time', 'fatality')) | Q(lost_days__gt=0)).count()
    pq = Permit.objects.filter(contractor=c, created_at__date__gte=a, created_at__date__lte=b)
    e.permits = pq.count()
    e.permits_suspended = pq.exclude(suspension_reason='').count()
    av = Audit.objects.filter(contractor=c, planned_date__gte=a, planned_date__lte=b, score__isnull=False).aggregate(x=Avg('score'))['x']
    e.audit_score = Decimal(str(round(av, 1))) if av is not None else None
    t = S.today()
    e.documents_valid = not ((c.licence_expiry and c.licence_expiry < t) or (c.insurance_expiry and c.insurance_expiry < t))
    ws = list(c.workers.all())
    e.workers_cleared = Decimal(str(round(100 * sum(1 for w in ws if worker_cleared(w)[0]) / len(ws), 1))) if ws else None
    br, score = [], Decimal(100)

    def ded(label, pts):
        nonlocal score
        if pts:
            score -= Decimal(str(pts))
            br.append({'label': label, 'points': -float(pts)})
    ded(f'{e.lost_time} lost-time injur{"y" if e.lost_time == 1 else "ies"} × 15', 15 * e.lost_time)
    ded(f'{e.incidents - e.lost_time} other incident(s) × 5', 5 * max(0, e.incidents - e.lost_time))
    ded(f'{e.permits_suspended} suspended permit(s) × 5', 5 * e.permits_suspended)
    if not e.documents_valid:
        ded('Trade licence or insurance expired', 10)
    if e.workers_cleared is not None and e.workers_cleared < 100:
        ded(f'Workers cleared {e.workers_cleared} %', round((100 - float(e.workers_cleared)) / 10, 1))
    score = max(Decimal(0), score)
    if e.audit_score is not None:
        blended = (score * Decimal('0.6') + e.audit_score * Decimal('0.4')).quantize(Decimal('0.1'))
        br.append({'label': f'Audits average {e.audit_score} – weighs 40 %', 'points': float(blended - score)})
        score = blended
    e.score = score.quantize(Decimal('0.1'))
    e.rating = 'A' if e.score >= 85 else 'B' if e.score >= 70 else 'C' if e.score >= 50 else 'D'
    e.breakdown = br
    return e


def eval_check(e, request):
    if e.period_to < e.period_from:
        raise S.HSEError('The period ends before it starts.', 'period_to')


def eval_saved(e, request, created, old):
    if created:
        e.evaluated_by = request.user
    evaluate(e)
    e.save()
    S.event('ceval', e.pk, f'Calculated: {e.score} → {e.rating}', request.user)
    return e


def eval_recalc(e, request, f):
    evaluate(e)
    e.save()
    S.event('ceval', e.pk, f'Recalculated: {e.score} → {e.rating}', request.user)


def eval_finalise(e, request, f):
    A.need(A.has_role(request, 'hse_manager') or A.is_admin(request), 'Only the HSE manager finalises a contractor evaluation.')
    evaluate(e)
    e.status, e.note = 'final', f.s('note') or e.note
    e.save()
    c = e.contractor
    if f.b('suspend'):
        if e.rating not in ('C', 'D'):
            raise S.HSEError('Only a C or D rating can suspend the contractor.', 'suspend')
        c.status = 'suspended'
        c.save(update_fields=['status'])
        S.event('ceval', e.pk, f'{c.name} suspended after rating {e.rating}', request.user)
    S.event('ceval', e.pk, f'Final: {e.score} → {e.rating} by {S.user_name(request.user)}', request.user)


def contractor_workers_due():
    """Daily: workers whose induction or medical expired → HSE to-do (they stop counting as cleared)."""
    t, n = S.today(), 0
    for w in ContractorWorker.objects.filter(status='active').filter(Q(induction_valid_until__lt=t) | Q(medical_valid_until__lt=t)).select_related('contractor'):
        _tell_hse(f'{w.name} ({w.contractor.name}): induction or medical expired', '', 'cworker', w.pk, w.name, PAGE + f'contractor-workers/{w.pk}')
        n += 1
    return n


def daily():
    out = {}
    for k, fn in (('ppe', ppe_due), ('emergency', emergency_due), ('equipment', equipment_due), ('contractor_workers', contractor_workers_due)):
        try:
            out[k] = fn()
        except Exception:
            log.exception('HSE scope daily %s failed', k)
            out[k] = 'error'
    return out


# ================================================================== registers (pages)
def register_all():
    from . import registers as RG
    from .registers import NOTE, Act, Reg, reg, st, ACTION_FIELDS
    import ZeoHSE.modules as MD

    RG.OPTIONS.update({
        'ppe-items': (lambda: PPEItem.objects.filter(is_active=True), lambda x: f'{x.name} ({x.stock} in stock)', ('name', 'code')),
        'contractor-workers': (lambda: ContractorWorker.objects.filter(status='active').select_related('contractor'), lambda x: f'{x.name} – {x.contractor.name}', ('name', 'id_number')),
        'assembly-points': (lambda: AssemblyPoint.objects.filter(is_active=True).select_related('site'), lambda x: f'{x.site.name} / {x.name}', ('name',)),
        'emergency-plans': (lambda: EmergencyPlan.objects.exclude(status='archived'), lambda x: f'{x.number} {x.title}', ('number', 'title')),
        'ppe-issues': (lambda: PPEIssue.objects.all(), lambda x: x.number, ('number',)),
    })
    RG.MODEL_OPTIONS.update({'PPEItem': 'ppe-items', 'ContractorWorker': 'contractor-workers', 'AssemblyPoint': 'assembly-points',
                             'EmergencyPlan': 'emergency-plans', 'PPEIssue': 'ppe-issues'})
    RG.MODEL_KEY.update({'ppe-items': 'ppeitem', 'ppe-issues': 'ppe', 'ppe-matrix': 'ppereq', 'emergency-plans': 'emplan', 'emergency-team': 'emteam',
                         'assembly-points': 'assembly', 'emergency-equipment': 'emequip', 'drills': 'drill', 'safety-equipment': 'equip',
                         'contractor-workers': 'cworker', 'contractor-evaluations': 'ceval'})
    STOCK = HSE + ('site_manager',)
    who = lambda o, r: A.is_staff(r)
    yes = lambda o, r: True

    # ---- PPE
    reg(Reg('ppe-items', PPEItem, 'PPE item', 'PPE catalogue and stock', 'ppe',
            cols=('code', 'name', 'category', 'standard', 'sizes', 'life_months', 'stock', 'min_stock', 'is_active'),
            form=('code', 'name', 'category', 'standard', 'sizes', 'life_months', 'reusable', 'stock', 'min_stock', 'supplier', 'is_active'),
            manage=STOCK, view=('supervisor',), public=lambda req: Q(is_active=True), search=('code', 'name', 'standard'), filters=('category', 'is_active'),
            title='code', subtitle='name', help='Stock goes down when PPE is issued and back up when a reusable item is returned in good condition.',
            actions=[Act('receive', 'Receive stock', ppe_receive, lambda o, r: A.any_role(r, *STOCK),
                         (('qty', 'Quantity received', 'int', True), ('note', 'Delivery note / supplier', 'text', False)), 'primary')]))
    reg(Reg('ppe-matrix', PPERequirement, 'PPE requirement', 'PPE matrix', 'ppe',
            cols=('job_role', 'department', 'site', 'items', 'mandatory'), form=('job_role', 'department', 'site', 'items', 'mandatory', 'note'),
            manage=HSE, view=('site_manager', 'supervisor'), public=lambda req: Q(pk__isnull=False), search=('job_role', 'department'), filters=('site', 'mandatory'),
            title='job_role', subtitle='department', help='Which PPE each job or activity needs – the basis for issuing and for PPE compliance checks.'))
    reg(Reg('ppe-issues', PPEIssue, 'PPE issue', 'PPE issued', 'ppe',
            cols=('number', 'employee', 'worker', 'item', 'size', 'qty', 'issued_on', 'expires_on', 'acknowledged_at', 'status'),
            form=('item', 'employee', 'worker', 'site', 'size', 'qty', 'issued_on', 'expires_on'),
            number='ppe', manage=STOCK, view=('supervisor',), own=(('employee', 'emp'),),
            search=('number', 'employee__emp_first_name', 'employee__emp_code', 'worker__name', 'item__name'), filters=('status', 'item', 'site'),
            edit_when=lambda o: False, check=ppe_issue_check, saver=ppe_issue_saved, extra=ppe_extra, subtitle='item',
            help='Issue to an employee or a contractor worker. The person acknowledges in ZEO. Replace-by = issue date + the item\'s life.',
            actions=[Act('ack', 'I received it', ppe_ack, lambda o, r: o.status == 'issued' and o.acknowledged_at is None and bool(A.my_emp(r)) and o.employee_id == A.my_emp(r).pk,
                         style='primary'),
                     Act('return', 'Return', ppe_return, lambda o, r: o.status in ('issued', 'expired') and A.any_role(r, *STOCK),
                         (('returned_on', 'Returned on', 'date', False), ('condition', 'Condition', 'choice:good=Good – reusable,worn=Worn,damaged=Damaged', True))),
                     Act('replace', 'Replace', ppe_replace, lambda o, r: o.status in ('issued', 'expired', 'damaged') and A.any_role(r, *STOCK),
                         (('reason', 'Why', 'textarea', True), ('size', 'Size (if different)', 'text', False)), 'primary'),
                     Act('loss', 'Report lost / damaged', ppe_loss, lambda o, r: o.status == 'issued' and (A.any_role(r, *STOCK) or (bool(A.my_emp(r)) and o.employee_id == A.my_emp(r).pk)),
                         (('kind', 'What happened', 'choice:lost=Lost,damaged=Damaged', True), ('reason', 'Details', 'textarea', True)), 'danger')]))

    # ---- emergency
    reg(Reg('emergency-plans', EmergencyPlan, 'Emergency plan', 'Emergency plans', 'emergency',
            cols=('number', 'title', 'scenario', 'site', 'owner', 'review_date', 'status'),
            form=('title', 'scenario', 'site', 'alarm', 'procedure', 'assembly_points', 'external_contacts', 'owner', 'review_date'),
            number='emplan', manage=HSE, view=('site_manager', 'supervisor'), public=lambda req: Q(status='approved'),
            search=('number', 'title'), filters=('status', 'scenario', 'site'), edit_when=st('draft'), check=plan_check,
            defaults=lambda o, req: setattr(o, 'created_by', req.user), source='emergency',
            help='Approved plans are open to everyone – they are the instructions people follow in an emergency.',
            extra=lambda o, req: {'drills': [f'{d.number} {d.planned_on} {d.get_status_display()}' for d in o.drills.all()[:20]]},
            actions=[Act('approve', 'Approve – put in force', plan_approve, lambda o, r: o.status == 'draft' and A.any_role(r, 'hse_manager'), style='primary'),
                     Act('revise', 'Revise', plan_revise, lambda o, r: o.status == 'approved' and A.any_role(r, *HSE), NOTE),
                     Act('archive', 'Archive', plan_archive, lambda o, r: o.status != 'archived' and A.any_role(r, 'hse_manager'), NOTE, 'danger')]))
    reg(Reg('emergency-team', EmergencyTeamMember, 'Team member', 'Emergency team', 'emergency',
            cols=('site', 'employee', 'role', 'shift', 'phone', 'is_active'), form=('site', 'employee', 'role', 'shift', 'phone', 'is_active'),
            manage=HSE, view=('site_manager', 'supervisor'), public=lambda req: Q(is_active=True), search=('employee__emp_first_name', 'employee__emp_code', 'role'),
            filters=('site', 'role', 'is_active'), title='role', subtitle='employee', check=team_check, extra=team_extra,
            help='Fire wardens need a valid "Fire warden" competency and first aiders a valid "First aid" one (when the company tracks them).'))
    reg(Reg('assembly-points', AssemblyPoint, 'Assembly point', 'Assembly points', 'emergency',
            cols=('site', 'name', 'location', 'capacity', 'warden', 'is_active'), form=('site', 'name', 'location', 'capacity', 'warden', 'is_active'),
            manage=HSE, view=('site_manager', 'supervisor'), public=lambda req: Q(is_active=True), search=('name', 'location'), filters=('site', 'is_active'),
            title='name', subtitle='site'))
    reg(Reg('emergency-equipment', EmergencyEquipment, 'Emergency equipment', 'Emergency equipment', 'emergency',
            cols=('number', 'kind', 'site', 'location', 'spec', 'next_due', 'service_due', 'status'),
            form=('kind', 'site', 'area', 'location', 'spec', 'serial_no', 'inspect_days', 'last_inspected', 'next_due', 'service_due', 'note'),
            number='emequip', manage=HSE, view=('site_manager', 'supervisor'), search=('number', 'location', 'serial_no', 'spec'),
            filters=('kind', 'status', 'site'), edit_when=lambda o: o.status != 'removed', saver=equip_saved, subtitle='kind', source='emergency',
            extra=lambda o, req: {'actions_list': [RG.RW.action(a) for a in Action.objects.filter(source='emergency', source_ref=o.number)]},
            help='Fire extinguishers, alarms, AEDs, first-aid kits, eye wash … Inspect at the interval; a defect raises a repair action.',
            actions=[Act('inspect', 'Record inspection', equip_inspect, lambda o, r: o.status != 'removed' and (A.is_staff(r) or A.any_role(r, 'supervisor', 'site_manager')),
                         (('inspected_on', 'Inspected on', 'date', False), ('result', 'Result', 'choice:ok=In order,defect=Defect', True), ('note', 'Note / defect', 'textarea', False),
                          ('out_of_service', 'Out of service until repaired', 'bool', False), ('owner', 'Repair owner (defect)', 'fk:employees', False),
                          ('due_date', 'Repair due (defect)', 'date', False)), 'primary'),
                     Act('service', 'Record service / refill', equip_service, lambda o, r: o.status != 'removed' and A.any_role(r, *HSE),
                         (('serviced_on', 'Serviced on', 'date', False), ('service_due', 'Next service / expiry', 'date', True), ('note', 'Note', 'text', False))),
                     Act('remove', 'Remove', lambda o, r, f: (setattr(o, 'status', 'removed'), o.save(), S.event('emequip', o.pk, f'Removed: {f.s("note")}', r.user)),
                         lambda o, r: o.status != 'removed' and A.any_role(r, *HSE), NOTE, 'danger')]))
    reg(Reg('drills', EmergencyDrill, 'Drill', 'Emergency drills', 'emergency',
            cols=('number', 'scenario', 'site', 'planned_on', 'conducted_on', 'participants', 'evacuation_minutes', 'rating', 'status'),
            form=('plan', 'site', 'scenario', 'planned_on', 'coordinator', 'target_minutes'),
            number='drill', manage=HSE, view=('site_manager', 'supervisor'), own=(('coordinator', 'emp'),), search=('number',),
            filters=('status', 'scenario', 'site'), edit_when=st('planned'), subtitle='scenario', source='emergency',
            defaults=lambda o, req: setattr(o, 'created_by', req.user),
            extra=lambda o, req: {'improvement_needed': o.improvement_needed,
                                  'actions_list': [RG.RW.action(a) for a in Action.objects.filter(source='emergency', source_ref=o.number)]},
            help='Record the drill, then raise actions for what went wrong. A drill that needs improvement closes only with an action.',
            actions=[Act('record', 'Record the drill', drill_record, lambda o, r: o.status == 'planned' and (A.is_staff(r) or (bool(A.my_emp(r)) and o.coordinator_id == A.my_emp(r).pk)),
                         (('conducted_on', 'Held on', 'date', True), ('participants', 'People taking part', 'int', True),
                          ('evacuation_minutes', 'Evacuation time (min)', 'decimal', False), ('head_count_ok', 'Head count matched', 'bool', False),
                          ('rating', 'Rating', 'choice:excellent=Excellent,good=Good,improve=Needs improvement,poor=Poor', True),
                          ('observations', 'Observations', 'textarea', False)), 'primary'),
                     Act('raise-action', 'Raise action', lambda o, r, f: MD.raise_action('emergency', o, r, f), lambda o, r: o.status in ('done',) and A.is_staff(r), ACTION_FIELDS),
                     Act('close', 'Close', drill_close, lambda o, r: o.status == 'done' and A.any_role(r, *HSE), style='primary'),
                     Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), S.event('drill', o.pk, f'Cancelled: {f.s("note")}', r.user)),
                         lambda o, r: o.status == 'planned' and A.any_role(r, *HSE), NOTE, 'danger')]))

    # ---- safety equipment and certification
    reg(Reg('safety-equipment', SafetyEquipment, 'Equipment', 'Safety equipment and certificates', 'equipment',
            cols=('number', 'name', 'kind', 'site', 'capacity', 'cert_expires', 'next_inspection', 'tag', 'status'),
            form=('name', 'kind', 'site', 'area', 'location', 'serial_no', 'manufacturer', 'capacity', 'owner', 'contractor', 'certificate_no', 'certified_by',
                  'certified_on', 'cert_expires', 'inspect_days', 'last_inspected', 'next_inspection', 'tag', 'restrictions', 'note'),
            number='equip', manage=HSE, view=('site_manager', 'supervisor', 'permit_issuer'), own=(('owner', 'emp'),),
            search=('number', 'name', 'serial_no', 'certificate_no'), filters=('kind', 'tag', 'status', 'site'), edit_when=lambda o: o.status != 'retired',
            check=eq_check, saver=eq_saved, subtitle='name', source='equipment',
            extra=lambda o, req: {'actions_list': [RG.RW.action(a) for a in Action.objects.filter(source='equipment', source_id=o.pk)],
                                  'cert_valid': bool(o.cert_expires and o.cert_expires >= S.today())},
            help='Red tag or an expired certificate = out of service. Yellow = use only within the written restrictions.',
            actions=[Act('inspect', 'Record inspection (tag)', eq_inspect, lambda o, r: o.status != 'retired' and (A.is_staff(r) or A.any_role(r, 'supervisor', 'permit_issuer')),
                         (('inspected_on', 'Inspected on', 'date', False), ('tag', 'Tag', 'choice:green=Green – safe to use,yellow=Yellow – restrictions,red=Red – do not use', True),
                          ('restrictions', 'Restrictions (yellow)', 'textarea', False), ('note', 'Findings', 'textarea', False),
                          ('owner', 'Repair owner (red)', 'fk:employees', False), ('due_date', 'Repair due (red)', 'date', False)), 'primary'),
                     Act('certify', 'Record certificate', eq_certify, lambda o, r: o.status != 'retired' and A.any_role(r, *HSE),
                         (('certificate_no', 'Certificate no.', 'text', True), ('certified_by', 'Certifying body / inspector', 'text', False),
                          ('certified_on', 'Certified on', 'date', False), ('cert_expires', 'Valid until', 'date', True), ('passed', 'Passed – back to green', 'bool', False),
                          ('file', 'Certificate file', 'file', False))),
                     Act('retire', 'Retire', eq_retire, lambda o, r: o.status != 'retired' and A.any_role(r, *HSE), NOTE, 'danger')]))

    # ---- contractor workforce and performance
    reg(Reg('contractor-workers', ContractorWorker, 'Contractor worker', 'Contractor workers', 'contractors',
            cols=('contractor', 'name', 'trade', 'induction_on', 'induction_valid_until', 'medical_valid_until', 'status'),
            form=('contractor', 'name', 'id_number', 'trade', 'phone', 'induction_on', 'induction_valid_until', 'medical_valid_until', 'competencies'),
            manage=HSE, view=('site_manager', 'supervisor', 'permit_issuer'), search=('name', 'id_number', 'trade', 'contractor__name'),
            filters=('contractor', 'status'), title='name', subtitle='contractor', check=worker_check, extra=worker_extra, restricted=('id_number',),
            can_restricted=lambda req: A.any_role(req, *HSE),
            help='Cleared to work = active, inducted (induction in date), medical in date when tracked, and the contractor approved.',
            actions=[Act('block', 'Block', worker_block, lambda o, r: o.status == 'active' and A.any_role(r, *HSE), (('note', 'Reason', 'textarea', True),), 'danger'),
                     Act('unblock', 'Unblock', worker_unblock, lambda o, r: o.status == 'blocked' and A.any_role(r, 'hse_manager'), NOTE)]))
    reg(Reg('contractor-evaluations', ContractorEvaluation, 'Contractor evaluation', 'Contractor performance', 'contractors',
            cols=('number', 'contractor', 'period_from', 'period_to', 'incidents', 'lost_time', 'permits_suspended', 'audit_score', 'score', 'rating', 'status'),
            form=('contractor', 'period_from', 'period_to', 'note'),
            number='ceval', manage=HSE, view=('site_manager', 'senior_mgmt'), search=('number', 'contractor__name'), filters=('rating', 'status', 'contractor'),
            edit_when=st('draft'), check=eval_check, saver=eval_saved, subtitle='contractor',
            extra=lambda o, req: {'breakdown': o.breakdown},
            help='100 points less 15 per lost-time injury, 5 per other incident, 5 per suspended permit, 10 for expired licence or insurance and a share '
                 'for uncleared workers; audits weigh 40 % when there are any. A ≥ 85, B ≥ 70, C ≥ 50, D below.',
            actions=[Act('recalculate', 'Recalculate', eval_recalc, lambda o, r: o.status == 'draft' and A.any_role(r, *HSE)),
                     Act('finalise', 'Finalise', eval_finalise, lambda o, r: o.status == 'draft' and A.any_role(r, 'hse_manager'),
                         (('suspend', 'Suspend the contractor (C or D only)', 'bool', False), ('note', 'Conclusion', 'textarea', False)), 'primary')]))
