"""v1.73.6 – Document Center, DMS SOP phase 3: expiry and renewal, retention (review, authorised disposal with evidence, legal hold),
the archive and disposal registers, and correspondence (incoming / outgoing registers, reference numbers, approval, dispatch,
deadlines).

Expiry: every category may have an expiry. The renewal owner (the document's, else the category rule: the owner or a named user)
gets a to-do 90 / 60 / 30 / 15 / 7 days before (the category may set other days); past the expiry without a renewal for the
category's escalation days, the escalation user (or the Document Center administrators) is told. A renewal is a new document in the
same folder with the same category and details, linked to the one it renews; when the renewal is in force the old one is Superseded
and its alerts stop.

Retention: retention_until = the category's start event (registered, effective, expiry or archived) + its retention years. When it is
reached the Document Center administrators get the retention review. Disposal needs the "Dispose" right, is refused under legal hold
and before the end of retention (an administrator may dispose earlier with a reason); it writes the disposal register (rule, who,
when, method, certificate, evidence file) and deletes the files – the register row and the audit trail stay.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr

from . import models as Mo
from . import services as S

ACTIVE = ('draft', 'submitted', 'under_review', 'revision_required', 'pending_approval', 'approved', 'published', 'expired')


# ================================================================== retention
def retention_until(doc):
    cat = doc.category
    if cat is None or not cat.retention_years:
        return None
    start = {'created': (doc.created_at.date() if doc.created_at else None), 'effective': doc.effective_date or (doc.created_at.date() if doc.created_at else None),
             'expiry': doc.expiry_date, 'archived': (doc.archived_at.date() if doc.archived_at else None)}.get(cat.retention_start or 'effective')
    if start is None:
        return None
    try:
        return start.replace(year=start.year + cat.retention_years)
    except ValueError:   # 29 February
        return start + timedelta(days=365 * cat.retention_years)


def refresh_retention(doc):
    v = retention_until(doc)
    if v != doc.retention_until:
        Mo.Document.objects.filter(pk=doc.pk).update(retention_until=v)
        doc.retention_until = v
    return v


def is_held(doc):
    """a legal hold on the document, or on its employee (ZeoAdmin legal hold)"""
    if doc.legal_hold:
        return True
    return bool(S._held([doc]))


def set_hold(who, doc, on, reason=''):
    if not (who.dms_admin or 'dispose' in S.doc_rights(doc, who)):
        raise S.Refused(tr('Only a Document Center administrator or a user with the "Dispose" right may set a legal hold.'), 403)
    if on and not str(reason or '').strip():
        raise S.Refused(tr('Give the reason for the legal hold.'), problems={'reason': tr('Required.')})
    doc.legal_hold = bool(on)
    doc.legal_hold_reason = str(reason or '').strip()[:255] if on else ''
    doc.legal_hold_by_id = who.uid if on else None
    doc.legal_hold_at = timezone.now() if on else None
    doc.save(update_fields=['legal_hold', 'legal_hold_reason', 'legal_hold_by_id', 'legal_hold_at'])
    S.log_act(who, 'legal_hold' if on else 'legal_hold_released', doc.legal_hold_reason, document=doc)


def archive(who, doc, location=''):
    S.need(S.doc_rights(doc, who), 'archive')
    if doc.status in ('submitted', 'under_review', 'pending_approval'):
        raise S.Refused(tr('The document is waiting for approval – withdraw it first.'))
    doc.status, doc.archived_at, doc.archived_by_id = 'archived', timezone.now(), who.uid
    doc.archive_location = str(location or '').strip()[:120]
    doc.save(update_fields=['status', 'archived_at', 'archived_by_id', 'archive_location'])
    refresh_retention(doc)
    S.log_act(who, 'archived', doc.archive_location, document=doc)


def review(who, doc, decision, note=''):
    """the retention review: keep (another review later) or dispose / archive by the category's action"""
    if not who.dms_admin:
        raise S.Refused(tr('Only a Document Center administrator reviews retention.'), 403)
    if decision not in ('keep', 'dispose', 'archive'):
        raise S.Refused(tr('Choose keep, archive or dispose.'))
    doc.retention_reviewed_at, doc.retention_review_note = timezone.now(), str(note or '')[:255]
    if decision == 'keep':
        doc.retention_until = (doc.retention_until or _zt_today()) + timedelta(days=365)   # looked at again in a year
        doc.save(update_fields=['retention_reviewed_at', 'retention_review_note', 'retention_until'])
    else:
        doc.save(update_fields=['retention_reviewed_at', 'retention_review_note'])
    S.log_act(who, 'retention_review', f'{decision} {note}'.strip(), document=doc)
    if decision == 'archive':
        archive(who, doc)


def dispose(who, doc, method='deleted', certificate='', reason='', evidence=None):
    r = S.doc_rights(doc, who)
    S.need(r, 'dispose', tr('Only a user with the "Dispose" right may dispose of documents.'))
    if is_held(doc):
        raise S.Refused(tr('The document is under legal hold.'), 403)
    if doc.status in ('submitted', 'under_review', 'pending_approval'):
        raise S.Refused(tr('The document is waiting for approval – withdraw it first.'))
    if doc.status == 'disposed':
        raise S.Refused(tr('The document is disposed already.'))
    today = _zt_today()
    early = not doc.retention_until or doc.retention_until > today
    if early:
        if not who.dms_admin:
            raise S.Refused(tr('The retention period has not ended ({0}). Only a Document Center administrator may dispose of it earlier, with a reason.',
                               f'{doc.retention_until:%d/%m/%Y}' if doc.retention_until else tr('no retention set')), 409)
        if not str(reason or '').strip():
            raise S.Refused(tr('Give the reason for disposing before the end of retention.'), problems={'reason': tr('Required.')})
    if method not in dict(Mo.DisposalRecord.METHODS):
        raise S.Refused(tr('Choose how it was disposed.'), problems={'method': tr('Invalid.')})
    with transaction.atomic():
        rec = Mo.DisposalRecord(document=doc, number=doc.number, title=doc.title, category=str(doc.category) if doc.category_id else '',
                                retention_until=doc.retention_until, early=early, reason=str(reason or '').strip()[:255], method=method,
                                certificate=str(certificate or '').strip()[:60], authorised_by_id=who.uid)
        if evidence is not None:
            S.check_file(evidence)
            rec.evidence_name = str(getattr(evidence, 'name', '') or '')[:255]
            rec.evidence.save(rec.evidence_name or 'evidence', evidence, save=False)
        rec.save()
        S.set_status(who, doc, 'disposed')
    return rec


# ================================================================== renewal
def renewal_owner(doc):
    if doc.renewal_owner_id:
        return doc.renewal_owner_id
    cat = doc.category
    if cat is not None and cat.renewal_owner == 'user' and cat.renewal_user_id:
        return cat.renewal_user_id
    return doc.owner_id or doc.created_by_id


def renew(who, doc, upload, data):
    """the renewed copy: a new document in the same folder, category and details, linked to the one it renews"""
    S.need(S.doc_rights(doc, who), 'upload', tr('You may not renew this document.'))
    if doc.renewals.filter(deleted_at__isnull=True).exclude(status__in=('rejected', 'disposed')).exists():
        raise S.Refused(tr('This document has a renewal already.'), 409)
    exp = S._date(data.get('expiry_date'), 'expiry_date')
    if doc.expiry_date and not exp:
        raise S.Refused(tr('Enter the new expiry date.'), problems={'expiry_date': tr('Required.')})
    if exp and doc.expiry_date and exp <= doc.expiry_date:
        raise S.Refused(tr('The new expiry date must be after the old one ({0}).', f'{doc.expiry_date:%d/%m/%Y}'), problems={'expiry_date': tr('Too early.')})
    base = {'title': data.get('title') or doc.title, 'category': doc.category_id, 'confidentiality': doc.confidentiality, 'tags': doc.tags,
            'remarks': data.get('remarks') or doc.remarks, 'fields': doc.fields, 'received_date': data.get('received_date') or str(_zt_today()),
            'effective_date': data.get('effective_date') or (str(doc.expiry_date + timedelta(days=1)) if doc.expiry_date else ''),
            'expiry_date': str(exp) if exp else '', 'department': doc.department_id, 'branch': doc.branch_id, 'owner': doc.owner_id,
            'employee': doc.employee_id, 'project': doc.project_id, 'partner': doc.partner_id, 'change_note': data.get('change_note') or 'Renewal'}
    new, _dup = S.register(who, doc.folder, upload, {k: v for k, v in base.items() if v not in (None, '')})
    Mo.Document.objects.filter(pk=new.pk).update(renewal_of=doc, renewal_owner_id=doc.renewal_owner_id)
    new.refresh_from_db()
    refresh_retention(new)
    S.log_act(who, 'renewed', f'{new.number}', document=doc)
    S.log_act(who, 'renewal_of', f'{doc.number}', document=new)
    if new.status == 'published':
        superseded_by_renewal(who, new)
    S._close_todos(doc)
    return new


def superseded_by_renewal(who, new):
    """the renewal is in force: the document it renews is Superseded (its alerts stop)"""
    old = new.renewal_of
    if old is None or old.status in ('superseded', 'disposed'):
        return
    Mo.Document.objects.filter(pk=old.pk).update(status='superseded')
    S.log_act(who, 'status', f'{old.status} → superseded (renewed by {new.number})', document=old)
    S._close_todos(old)


# ================================================================== the daily job
def alert_days(doc):
    days = [int(x) for x in ((doc.category.alert_days if doc.category_id else None) or Mo.ALERT_DAYS) if str(x).lstrip('-').isdigit() and int(x) >= 0]
    return sorted(set(days), reverse=True) or list(Mo.ALERT_DAYS)


def admin_users():
    """the company's administrators and the users with "Configure the Document Center" (manage_dms)"""
    ids = set()
    try:
        from ZeoCloud.companyadmin import admin_ids
        ids |= set(admin_ids(None))
    except Exception:   # noqa: BLE001
        pass
    try:
        from AccessControl.access import company_perms
        ids |= set(company_perms().filter(groups__permissions__codename='manage_dms').values_list('profile_id', flat=True))
    except Exception:   # noqa: BLE001
        pass
    return {i for i in ids if i}


def daily(today=None):
    today = today or _zt_today()
    out = {'alerts': 0, 'escalations': 0, 'retention': 0, 'corr_reminders': 0, 'corr_escalations': 0}
    live = Mo.Document.objects.filter(deleted_at__isnull=True, status__in=ACTIVE).select_related('category')
    # 1) expiry alerts and escalation (documents not renewed yet)
    for d in live.filter(expiry_date__isnull=False).exclude(renewals__deleted_at__isnull=True, renewals__status__in=ACTIVE):
        left = (d.expiry_date - today).days
        if left >= 0:
            due = [x for x in alert_days(d) if left <= x]
            if not due:
                continue
            step = min(due)   # the nearest threshold reached (a missed day is caught up once, not every threshold)
            if Mo.ExpiryAlert.objects.filter(document=d, kind='alert', days=step).exists():
                continue
            u = renewal_owner(d)
            S._todo(u, S.en('{0} {1} expires on {2} ({3} days) – renew it', d.number, d.title, f'{d.expiry_date:%d %b %Y}', left), d)
            Mo.ExpiryAlert.objects.create(document=d, kind='alert', days=step, to_user_id=u)
            out['alerts'] += 1
        else:
            esc = d.category.escalate_days if d.category_id else 7
            if -left < esc or Mo.ExpiryAlert.objects.filter(document=d, kind='escalation').exists():
                continue
            to = [d.category.escalate_user_id] if d.category_id and d.category.escalate_user_id else sorted(admin_users())
            for u in to:
                S._todo(u, S.en('{0} {1} expired on {2} and has not been renewed', d.number, d.title, f'{d.expiry_date:%d %b %Y}'), d)
            Mo.ExpiryAlert.objects.create(document=d, kind='escalation', days=left, to_user_id=to[0] if to else None)
            out['escalations'] += 1
    # 2) retention reached: the administrators review it (once per retention date)
    for d in Mo.Document.objects.filter(deleted_at__isnull=True, retention_until__lte=today, legal_hold=False).exclude(status='disposed').select_related('category'):
        key = (d.retention_until - date(2000, 1, 1)).days
        if Mo.ExpiryAlert.objects.filter(document=d, kind='retention', days=key).exists():
            continue
        for u in sorted(admin_users()):
            S._todo(u, S.en('{0} {1}: retention ended on {2} – review it (keep, archive or dispose)', d.number, d.title, f'{d.retention_until:%d %b %Y}'), d)
        Mo.ExpiryAlert.objects.create(document=d, kind='retention', days=key)
        out['retention'] += 1
    # 3) correspondence deadlines: a reminder 2 days before, an escalation when overdue
    open_ = Mo.Correspondence.objects.filter(deadline__isnull=False).exclude(status__in=('replied', 'closed', 'dispatched', 'rejected'))
    for c in open_.filter(deadline__lte=today + timedelta(days=2), deadline__gte=today, reminded_at__isnull=True):
        if c.assigned_to_id or c.created_by_id:
            corr_todo(c.assigned_to_id or c.created_by_id, S.en('{0} {1} – due on {2}', c.number, c.subject, f'{c.deadline:%d %b %Y}'), c)
        Mo.Correspondence.objects.filter(pk=c.pk).update(reminded_at=timezone.now())
        out['corr_reminders'] += 1
    for c in open_.filter(deadline__lt=today, escalated_at__isnull=True):
        for u in {x for x in (c.created_by_id,) if x} | (admin_users() if not c.created_by_id else set()):
            corr_todo(u, S.en('{0} {1} – overdue since {2}', c.number, c.subject, f'{c.deadline:%d %b %Y}'), c)
        Mo.Correspondence.objects.filter(pk=c.pk).update(escalated_at=timezone.now())
        out['corr_escalations'] += 1
    return out


# ================================================================== correspondence
def corr_todo(user_id, summary, c):
    try:
        from Chatter.models import Activity as CA
        from django.apps import apps
        u = apps.get_model('UserManagement', 'CustomUser').objects.filter(pk=user_id).first()
        if u is None:
            return
        if CA.objects.filter(model='ZeoDocs.correspondence', object_id=str(c.pk), assigned_to=u, summary=summary[:255], state='open').exists():
            return
        CA.objects.create(model='ZeoDocs.correspondence', object_id=str(c.pk), record_label=f'{c.number} {c.subject}'[:255],
                          page=f'/main-sidebar/documents?tab=corr&corr={c.pk}'[:255], activity_type='todo', summary=summary[:255],
                          due_date=c.deadline or _zt_today(), assigned_to=u)
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('correspondence to-do')


def corr_close_todos(c):
    try:
        from Chatter.models import Activity as CA
        CA.objects.filter(model='ZeoDocs.correspondence', object_id=str(c.pk), state='open').update(state='done', done_at=timezone.now())
    except Exception:   # noqa: BLE001
        pass


def corr_may(who, action='view'):
    if who.dms_admin or who.has('manage_correspondence'):
        return True
    return action == 'view' and who.has('view_correspondence')


def corr_visible(who):
    qs = Mo.Correspondence.objects.all()
    if who.dms_admin or who.has('manage_correspondence', 'view_correspondence'):
        if who.branches is not None:
            qs = qs.filter(Q(branch_id__in=list(who.branches)) | Q(branch_id__isnull=True))
        return qs
    return qs.filter(Q(assigned_to_id=who.uid) | Q(approver_id=who.uid) | Q(created_by_id=who.uid))


def corr_number(direction, on):
    pre = 'IN' if direction == 'in' else 'OUT'
    key = f'corr:{pre}:{on.year}'
    with transaction.atomic():
        c, _ = Mo.DocCounter.objects.select_for_update().get_or_create(key=key)
        c.value += 1
        c.save(update_fields=['value'])
        return f'{pre}-{on.year}-{c.value:05d}'


def corr_save(who, data, c=None):
    if not corr_may(who, 'manage'):
        raise S.Refused(tr('You may not register correspondence.'), 403)
    new = c is None
    direction = data.get('direction', getattr(c, 'direction', None))
    if direction not in ('in', 'out'):
        raise S.Refused(tr('Choose incoming or outgoing.'), problems={'direction': tr('Required.')})
    if c is not None and c.status in ('dispatched', 'closed') and direction == 'out':
        raise S.Refused(tr('A dispatched letter is not changed – register a new one.'), 409)
    c = c or Mo.Correspondence(direction=direction, created_by_id=who.uid, status='registered' if direction == 'in' else 'draft')
    subject = ' '.join(str(data.get('subject', c.subject) or '').split())[:255]
    party = ' '.join(str(data.get('party', c.party) or '').split())[:200]
    probs = {}
    if not subject:
        probs['subject'] = tr('Required.')
    if not party:
        probs['party'] = tr('Required.')
    on = S._date(data.get('date'), 'date') if data.get('date') else (c.date or _zt_today())
    if on > _zt_today():
        probs['date'] = tr('Not in the future.')
    dl = S._date(data.get('deadline'), 'deadline') if data.get('deadline') else (None if 'deadline' in data else c.deadline)
    if dl and dl < on:
        probs['deadline'] = tr('Before the date of the letter.')
    if probs:
        raise S.Refused(tr('Check the marked fields.'), problems=probs)
    ch = data.get('channel', c.channel) or 'email'
    if ch not in dict(Mo.Correspondence.CHANNELS):
        raise S.Refused(tr('Choose the channel.'), problems={'channel': tr('Invalid.')})
    c.subject, c.party, c.date, c.deadline, c.channel = subject, party, on, dl, ch
    c.their_reference = str(data.get('their_reference', c.their_reference) or '')[:80]
    c.partner_id = S._int(data.get('partner', c.partner_id))
    c.branch_id = S._int(data.get('branch', c.branch_id))
    c.department_id = S._int(data.get('department', c.department_id))
    c.remarks = str(data.get('remarks', c.remarks) or '')[:4000]
    conf = data.get('confidentiality', c.confidentiality) or 'internal'
    c.confidentiality = conf if conf in dict(Mo.CONFIDENTIALITY) else 'internal'
    if 'document' in data:
        did = S._int(data.get('document'))
        if did:
            d = Mo.Document.objects.filter(pk=did, deleted_at__isnull=True).first()
            if d is None or 'view' not in S.doc_rights(d, who):
                raise S.Refused(tr('Choose a document you may see.'), problems={'document': tr('Not found.')})
            c.document = d
        else:
            c.document = None
    if 'reply_to' in data:
        rid = S._int(data.get('reply_to'))
        c.reply_to = Mo.Correspondence.objects.filter(pk=rid).first() if rid else None
    if 'assigned_to' in data:
        aid = S._int(data.get('assigned_to'))
        if aid != c.assigned_to_id:
            c.assigned_to_id = aid
            if aid and c.direction == 'in' and c.status == 'registered':
                c.status = 'assigned'
    with transaction.atomic():
        if new:
            c.number = corr_number(direction, on)
        c.save()
    if c.assigned_to_id and c.direction == 'in':
        corr_todo(c.assigned_to_id, S.en('{0} {1} from {2}', c.number, c.subject, c.party) + (S.en(' – reply by {0}', f'{c.deadline:%d %b %Y}') if c.deadline else ''), c)
    return c


def corr_action(who, c, act, data):
    """incoming: replied / closed / reopen; outgoing: submit for approval, approve / reject (the approver), dispatch, close"""
    note = str(data.get('note') or '')[:255]
    if act == 'submit':
        if c.direction != 'out' or c.status not in ('draft', 'rejected'):
            raise S.Refused(tr('Only a draft outgoing letter is sent for approval.'))
        if not corr_may(who, 'manage'):
            raise S.Refused(tr('You may not register correspondence.'), 403)
        aid = S._int(data.get('approver'))
        if not aid:
            raise S.Refused(tr('Choose the approver.'), problems={'approver': tr('Required.')})
        if aid == who.uid and not who.dms_admin:
            raise S.Refused(tr('Another user approves your letter.'), problems={'approver': tr('Not yourself.')})
        c.approver_id, c.status, c.decided_at, c.decision_note = aid, 'pending_approval', None, ''
        c.save(update_fields=['approver_id', 'status', 'decided_at', 'decision_note'])
        corr_todo(aid, S.en('{0} {1} to {2} – waiting for your approval', c.number, c.subject, c.party), c)
    elif act in ('approve', 'reject'):
        if c.status != 'pending_approval':
            raise S.Refused(tr('The letter is not waiting for approval.'))
        if who.uid != c.approver_id and not who.dms_admin:
            raise S.Refused(tr('Only the approver decides.'), 403)
        if act == 'reject' and not note:
            raise S.Refused(tr('Say why it is rejected.'), problems={'note': tr('Required.')})
        c.status, c.decided_at, c.decision_note = ('approved' if act == 'approve' else 'rejected'), timezone.now(), note
        c.save(update_fields=['status', 'decided_at', 'decision_note'])
        corr_close_todos(c)
        if c.created_by_id:
            corr_todo(c.created_by_id, S.en('{0} {1} was {2}', c.number, c.subject, 'approved – dispatch it' if act == 'approve' else 'rejected'), c)
    elif act == 'dispatch':
        if c.direction != 'out' or c.status != 'approved':
            raise S.Refused(tr('Only an approved outgoing letter is dispatched.'))
        if not corr_may(who, 'manage'):
            raise S.Refused(tr('You may not register correspondence.'), 403)
        m = data.get('dispatch_method') or c.channel
        if m not in dict(Mo.Correspondence.CHANNELS):
            raise S.Refused(tr('Choose how it is sent.'), problems={'dispatch_method': tr('Invalid.')})
        c.status, c.dispatch_method, c.tracking, c.dispatched_at = 'dispatched', m, str(data.get('tracking') or '')[:80], timezone.now()
        c.save(update_fields=['status', 'dispatch_method', 'tracking', 'dispatched_at'])
        corr_close_todos(c)
        if c.reply_to_id:   # the reply answers the incoming letter
            Mo.Correspondence.objects.filter(pk=c.reply_to_id).exclude(status='closed').update(status='replied', closed_at=timezone.now())
            corr_close_todos(c.reply_to)
    elif act in ('replied', 'close'):
        if not (corr_may(who, 'manage') or who.uid == c.assigned_to_id):
            raise S.Refused(tr('You may not change this letter.'), 403)
        if c.direction == 'out' and c.status not in ('dispatched',):
            raise S.Refused(tr('Dispatch the letter first.'))
        c.status, c.closed_at = ('replied' if act == 'replied' else 'closed'), timezone.now()
        c.decision_note = note or c.decision_note
        c.save(update_fields=['status', 'closed_at', 'decision_note'])
        corr_close_todos(c)
    elif act == 'reopen':
        if not corr_may(who, 'manage'):
            raise S.Refused(tr('You may not change this letter.'), 403)
        if c.status not in ('replied', 'closed'):
            raise S.Refused(tr('Only a closed letter is reopened.'))
        c.status, c.closed_at = ('assigned' if c.assigned_to_id else 'registered') if c.direction == 'in' else 'dispatched', None
        c.save(update_fields=['status', 'closed_at'])
    else:
        raise S.Refused(tr('Unknown action.'))
    return c
