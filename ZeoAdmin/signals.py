"""v1.52.0 – record events → webhooks; new approvals → out-of-office colleague."""
import logging

from django.apps import apps
from django.db.models.signals import post_delete, post_save, pre_save

log = logging.getLogger(__name__)
SKIP_APPS = {'ZeoAdmin', 'Chatter', 'sessions', 'admin', 'contenttypes', 'auth', 'django_celery_beat', 'ZeoMobile', 'ZeoCloud', 'ZeoLicence'}
_done = False


def _saved(sender, instance, created, raw=False, **kw):
    if raw or sender._meta.app_label in SKIP_APPS or sender.__module__ == '__fake__':   # v1.62.0: not inside migrations
        return
    try:
        from .webhooks import fire
        fire(instance, 'created' if created else 'updated')
    except Exception:
        log.debug('webhook', exc_info=True)


def _deleted(sender, instance, **kw):
    if sender._meta.app_label in SKIP_APPS or sender.__module__ == '__fake__':
        return
    try:
        from .webhooks import fire
        fire(instance, 'deleted')
    except Exception:
        log.debug('webhook', exc_info=True)


def _approval(sender, instance, created, raw=False, **kw):
    if raw or not created:
        return
    try:
        from .delegation import on_new_approval
        on_new_approval(instance)
    except Exception:
        log.exception('out of office')


def _step(sender, instance, created, raw=False, **kw):
    if raw:
        return
    try:
        from .delegation import on_new_step
        on_new_step(instance)
    except Exception:
        log.exception('out of office step')


def _policy(key):
    """v1.56.0 – an HR policy default of the setup guide (None when not set)."""
    try:
        G = apps.get_model('ZeoAdmin', 'SetupGuide')
        g = G.objects.order_by('id').only('policies').first()
        return (g.policies or {}).get(key) if g else None
    except Exception:
        return None


def _resignation_notice(sender, instance, raw=False, **kw):
    if raw or instance.pk or instance.notice_period not in (None, ''):
        return
    v = _policy('notice_days')
    if v is not None:
        instance.notice_period = v


def _identity_contract(sender, instance, raw=False, **kw):
    if raw or instance.pk or (instance.mohre_contract_type or ''):
        return
    v = _policy('contract_type')
    if v in ('limited', 'unlimited'):
        instance.mohre_contract_type = v


def install():
    global _done
    if _done:
        return
    _done = True
    post_save.connect(_saved, dispatch_uid='zeoadmin_webhook_save')
    post_delete.connect(_deleted, dispatch_uid='zeoadmin_webhook_delete')
    try:
        from DashboardManagement.services import REQUESTS
        for key, label, rm, am, fk, dfield, summ in REQUESTS:
            try:
                post_save.connect(_approval, sender=apps.get_model(*am.split('.')), dispatch_uid=f'zeoadmin_ooo_{am}')
            except LookupError:
                continue
    except Exception:
        log.exception('out of office signals')
    try:
        post_save.connect(_step, sender=apps.get_model('ZeoFlow', 'ApprovalStep'), dispatch_uid='zeoadmin_ooo_flowstep')
    except LookupError:
        pass
    # v1.56.0 HR policy defaults of the setup guide: notice period of a resignation, contract type of a new employee
    for label, fn in (('EmpManagement.EmployeeResignation', _resignation_notice), ('EmployeeProfile.EmployeeIdentity', _identity_contract)):
        try:
            pre_save.connect(fn, sender=apps.get_model(*label.split('.')), dispatch_uid=f'zeoadmin_policy_{label}')
        except LookupError:
            pass
