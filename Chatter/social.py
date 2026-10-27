"""v1.45.0 – starred records, recently viewed records and colour tags on any record (keyed like the rest of the chatter by
'app_label.model_name' + object id)."""
from collections import defaultdict

from django.utils import timezone

from .models import RecentRecord, Star, Tag, TagLink

KEEP_RECENT = 30
APP_TITLES = {'ZeoCRM': 'CRM', 'ZeoProperty': 'Property', 'ZeoMaintenance': 'Maintenance', 'ZeoAssets': 'Asset Management',
              'ZeoProjects': 'Projects', 'ZeoHSE': 'HSE', 'ZeoPeople': 'People', 'ZeoMasters': 'Masters', 'ZeoAccounting': 'Accounting',
              'RecruitmentManagement': 'Recruitment', 'PerformanceManagement': 'Performance', 'LearningManagement': 'Learning',
              'LearningPlus': 'Learning', 'ExpenseManagement': 'Expenses', 'PayrollManagement': 'Payroll', 'PayrollPlus': 'Payroll',
              'calendars': 'Leave', 'LeavePolicy': 'Leave', 'AttendancePlus': 'Attendance', 'ShiftPlanner': 'Attendance',
              'AssetPlus': 'Assets (HR)', 'ProjectControl': 'Projects (HR)', 'ProjectManagement': 'Projects (HR)', 'SelfService': 'Self service'}


def app_of(model_key):
    return APP_TITLES.get((model_key or '').split('.')[0], 'HRMS')


# ------------------------------------------------------------------ recent and starred
def seen(user, rec, label, page):
    if not page or user is None or not user.is_authenticated:
        return
    RecentRecord.objects.update_or_create(user=user, model=rec.key, object_id=rec.id,
                                          defaults={'label': label[:255], 'page': page[:255], 'endpoint': rec.endpoint[:255], 'at': timezone.now()})
    Star.objects.filter(user=user, model=rec.key, object_id=rec.id).exclude(label=label[:255]).update(label=label[:255])
    old = list(RecentRecord.objects.filter(user=user).order_by('-at').values_list('pk', flat=True)[KEEP_RECENT:])
    if old:
        RecentRecord.objects.filter(pk__in=old).delete()


def is_starred(user, key, oid):
    return Star.objects.filter(user=user, model=key, object_id=str(oid)).exists()


def toggle_star(user, rec, label, page):
    s = Star.objects.filter(user=user, model=rec.key, object_id=rec.id).first()
    if s:
        s.delete()
        return False
    Star.objects.create(user=user, model=rec.key, object_id=rec.id, label=label[:255], page=page[:255], endpoint=rec.endpoint[:255])
    return True


def row(x, at):
    return {'id': x.id, 'model': x.model, 'object_id': x.object_id, 'label': x.label, 'page': x.page, 'endpoint': x.endpoint,
            'app': app_of(x.model), 'at': at}


def lists(user):
    return {'starred': [row(s, s.created_at) for s in Star.objects.filter(user=user)[:50]],
            'recent': [row(r, r.at) for r in RecentRecord.objects.filter(user=user)[:KEEP_RECENT]]}


# ------------------------------------------------------------------ tags
def tag_json(t, n=None):
    d = {'id': t.id, 'name': t.name, 'color': t.color}
    if n is not None:
        d['count'] = n
    return d


def tags_of(key, ids):
    out = defaultdict(list)
    for l in TagLink.objects.filter(model=key, object_id__in=[str(i) for i in ids]).select_related('tag').order_by('tag__name'):
        out[l.object_id].append(tag_json(l.tag))
    return dict(out)


def get_or_make(name, user=None, color=None):
    name = ' '.join(str(name or '').split())[:60]
    if not name:
        return None
    t = Tag.objects.filter(name__iexact=name).first()
    if t is None:
        colours = [c for c, _ in Tag._meta.get_field('color').choices]
        t = Tag.objects.create(name=name, color=color if color in colours else colours[(Tag.objects.count() + 6) % len(colours)],
                               created_by=user if getattr(user, 'pk', None) else None)
    return t


def add_tags(key, oid, names_or_ids, user=None):
    added = []
    for x in names_or_ids or []:
        t = Tag.objects.filter(pk=x).first() if str(x).isdigit() else get_or_make(x, user)
        if t is not None:
            TagLink.objects.get_or_create(tag=t, model=key, object_id=str(oid), defaults={'created_by': user if getattr(user, 'pk', None) else None})
            added.append(t.name)
    return added


def remove_tags(key, oid, names_or_ids):
    for x in names_or_ids or []:
        q = TagLink.objects.filter(model=key, object_id=str(oid))
        q = q.filter(tag_id=x) if str(x).isdigit() else q.filter(tag__name__iexact=str(x))
        q.delete()
