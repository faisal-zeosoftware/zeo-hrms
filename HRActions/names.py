"""v1.62.0 people – people by name, not by login.

Approvers, managers and "waiting for" columns store a user (CustomUser). Screens showed the login name (gdis_samuel).
`user_names(ids)` gives "Samuel Otieno (GD-0021)" for a user linked to an employee, else the user's full name, else the
login name; `user_name(id)` is the same for one user. Used by the serializers / registers of People & Time.
"""
from django.apps import apps

M = apps.get_model


def _emp_label(e):
    n = ' '.join(x for x in [(e.emp_first_name or '').strip(), (e.emp_last_name or '').strip()] if x)
    return f'{n} ({e.emp_code})' if n and e.emp_code else (n or e.emp_code or '')


def user_names(ids, with_code=True):
    ids = {int(i) for i in ids if i not in (None, '') and str(i).isdigit()}
    if not ids:
        return {}
    out = {}
    try:
        for e in M('EmpManagement', 'emp_master').objects.filter(users_id__in=ids).only('users_id', 'emp_first_name', 'emp_last_name', 'emp_code').order_by('-is_active', 'id'):
            if e.users_id not in out:
                lab = _emp_label(e)
                if not with_code:
                    lab = lab.rsplit(' (', 1)[0]
                out[e.users_id] = lab
    except Exception:
        pass
    rest = ids - set(out)
    if rest:
        for u in M('UserManagement', 'CustomUser').objects.filter(pk__in=rest):
            full = ''
            try:
                full = (u.get_full_name() or '').strip()
            except Exception:
                full = ''
            out[u.pk] = full or u.username
    return out


def user_name(uid, with_code=True):
    if uid in (None, ''):
        return ''
    return user_names([uid], with_code).get(int(uid), '')


def user_obj_name(u, with_code=True):
    return user_name(getattr(u, 'pk', None), with_code) if u is not None else ''
