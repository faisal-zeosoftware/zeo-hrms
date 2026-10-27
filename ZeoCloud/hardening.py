"""v1.62.0 – security hooks installed at start-up (ZeoCloud.apps.ready):

* every upload through the API is checked (zeo.uploadguard) – type, size, virus scan;
* a masked value (••••1234) sent back by a screen is never saved over the real number;
* a new password ends every other sign-in of the user (release checklist "Tokens");
* administrator rights given, permission groups changed, API keys and single sign-on changed → security events (dashboard,
  e-mail to the administrators).
"""
import logging

from django.db import connection
from django.db.models.signals import m2m_changed, post_save, pre_save

log = logging.getLogger(__name__)
MASK = '••••'
_done = False


# ------------------------------------------------------------------ parsers
def _strip_masked(data):
    """Drop fields whose value is a masked number (a screen that shows ••••1234 and saves the whole form)."""
    try:
        if hasattr(data, 'lists'):   # QueryDict
            bad = [k for k, vals in data.lists() if any(isinstance(v, str) and MASK in v for v in vals)]
            if bad:
                data = data.copy()
                for k in bad:
                    del data[k]
            return data
        if isinstance(data, dict):
            return {k: _strip_masked(v) for k, v in data.items() if not (isinstance(v, str) and MASK in v)}
        if isinstance(data, list):
            return [_strip_masked(x) for x in data]
    except Exception:
        log.exception('masked values')
    return data


def _check_files(files):
    from rest_framework.exceptions import ParseError
    from zeo import uploadguard
    for key in list(getattr(files, 'keys', lambda: [])()):
        for f in files.getlist(key) if hasattr(files, 'getlist') else [files[key]]:
            try:
                uploadguard.check(f, key)
            except uploadguard.Refused as e:
                try:
                    from Chatter.tracking import current_request
                    from .netguard import client_ip
                    from .security import event
                    r = current_request()
                    u = getattr(r, 'user', None)
                    event('upload_refused', user=u if getattr(u, 'is_authenticated', False) else None,
                          schema=getattr(connection, 'schema_name', '') if getattr(connection, 'schema_name', '') != 'public' else '',
                          ip=client_ip(r), detail=str(e)[:400], notify='virus' in str(e))
                except Exception:
                    pass
                err = ParseError(str(e), code='upload_refused')
                err.field = key   # the form shows the reason under the right field (careers CV, documents …)
                raise err


def _wrap_parsers():
    from rest_framework import parsers as P

    def wrap_multi(cls):
        orig = cls.parse

        def parse(self, stream, media_type=None, parser_context=None):
            res = orig(self, stream, media_type, parser_context)
            try:
                files = getattr(res, 'files', None)
                if files:
                    _check_files(files)
                if getattr(res, 'data', None) is not None:
                    res.data = _strip_masked(res.data)
            except Exception as e:
                from rest_framework.exceptions import ParseError
                if isinstance(e, ParseError):
                    raise
                log.exception('upload check')
            return res
        cls.parse = parse

    def wrap_file(cls):
        orig = cls.parse

        def parse(self, stream, media_type=None, parser_context=None):
            res = orig(self, stream, media_type, parser_context)
            files = getattr(res, 'files', None)
            if files:
                _check_files(files)
            return res
        cls.parse = parse

    def wrap_plain(cls):
        orig = cls.parse

        def parse(self, stream, media_type=None, parser_context=None):
            return _strip_masked(orig(self, stream, media_type, parser_context))
        cls.parse = parse

    wrap_multi(P.MultiPartParser)
    wrap_file(P.FileUploadParser)
    wrap_plain(P.JSONParser)
    wrap_plain(P.FormParser)


# ------------------------------------------------------------------ password changed → other sign-ins end
def _user_pre(sender, instance, raw=False, **kw):
    if raw or not instance.pk:
        return
    try:
        old = sender.objects.filter(pk=instance.pk).values_list('password', flat=True).first()
        instance._zeo_pw_changed = bool(old and old != instance.password)
    except Exception:
        instance._zeo_pw_changed = False


def _user_post(sender, instance, created=False, raw=False, **kw):
    if raw or created or not getattr(instance, '_zeo_pw_changed', False):
        return
    instance._zeo_pw_changed = False
    try:
        from .security import end_other_sessions, event
        n = end_other_sessions(instance)
        event('password_changed', user=instance, detail=f'{n} other sign-in(s) ended', notify=False)
    except Exception:
        log.exception('password change – sessions')


# ------------------------------------------------------------------ rights
def _who():
    try:
        from Chatter.tracking import current_request
        r = current_request()
        u = getattr(r, 'user', None)
        return (u if getattr(u, 'is_authenticated', False) else None), r
    except Exception:
        return None, None


def _schema():
    s = getattr(connection, 'schema_name', '') or ''
    return '' if s == 'public' else s


def _utp_pre(sender, instance, raw=False, **kw):
    if raw:
        return
    try:
        old = sender.objects.filter(pk=instance.pk).values_list('is_superuser', flat=True).first() if instance.pk else False
        instance._zeo_new_admin = bool(instance.is_superuser and not old)
    except Exception:
        instance._zeo_new_admin = False


def _utp_post(sender, instance, created=False, raw=False, **kw):
    if raw or not getattr(instance, '_zeo_new_admin', False):
        return
    instance._zeo_new_admin = False
    by, r = _who()
    if by is None:   # the owner made by the company set-up, a script – not an action of a person
        return
    try:
        from .netguard import client_ip
        from .security import event, forget_rules
        u = instance.profile
        forget_rules([u.pk])
        event('admin_granted', user=u, by=by, schema=_schema(), ip=client_ip(r),
              detail=f'{u.get_full_name() or u.username} was made an administrator of the company.')
    except Exception:
        log.exception('admin granted event')


def _utp_groups(sender, instance, action, pk_set=None, **kw):
    if action not in ('post_add', 'post_remove') or not pk_set:
        return
    by, r = _who()
    if by is None:
        return
    try:
        from django.contrib.auth.models import Group
        from .netguard import client_ip
        from .security import event
        names = ', '.join(Group.objects.filter(pk__in=pk_set).values_list('name', flat=True))
        u = getattr(instance, 'profile', None)
        if u is None:
            return
        event('rights_changed', user=u, by=by, schema=_schema(), ip=client_ip(r),
              detail=f'Permission group(s) {"given" if action == "post_add" else "taken away"}: {names}.')
    except Exception:
        log.exception('rights event')


def _api_key(sender, instance, created=False, raw=False, **kw):
    if raw or not created:
        return
    by, r = _who()
    try:
        from .netguard import client_ip
        from .security import event
        event('api_key', user=by, by=by, schema=_schema(), ip=client_ip(r),
              detail=f'API key "{getattr(instance, "name", "")}" created ({"read only" if getattr(instance, "read_only", False) else "read and write"}).')
    except Exception:
        log.exception('api key event')


def _sso(sender, instance, created=False, raw=False, **kw):
    if raw:
        return
    by, r = _who()
    if by is None:
        return
    try:
        from .netguard import client_ip
        from .security import event
        for sch in (instance.schemas or [])[:10]:
            event('sso_changed', user=by, by=by, schema=sch, ip=client_ip(r),
                  detail=f'Single sign-on "{instance.name}" {"added" if created else "changed"} (domains: {", ".join(instance.domains or [])}).')
    except Exception:
        log.exception('sso event')


def install():
    global _done
    if _done:
        return
    _done = True
    try:
        _wrap_parsers()
    except Exception:
        log.exception('parsers')
    from django.contrib.auth import get_user_model
    U = get_user_model()
    pre_save.connect(_user_pre, sender=U, dispatch_uid='zeo62-user-pre')
    post_save.connect(_user_post, sender=U, dispatch_uid='zeo62-user-post')
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        pre_save.connect(_utp_pre, sender=UserTenantPermissions, dispatch_uid='zeo62-utp-pre')
        post_save.connect(_utp_post, sender=UserTenantPermissions, dispatch_uid='zeo62-utp-post')
        m2m_changed.connect(_utp_groups, sender=UserTenantPermissions.groups.through, dispatch_uid='zeo62-utp-groups')
    except Exception:
        log.exception('rights signals')
    try:
        from ZeoAdmin.models import ApiKey
        post_save.connect(_api_key, sender=ApiKey, dispatch_uid='zeo62-apikey')
    except Exception:
        log.exception('api key signal')
    from .models import SSOProvider
    post_save.connect(_sso, sender=SSOProvider, dispatch_uid='zeo62-sso')
