import logging

logger = logging.getLogger(__name__)


def warn_if_email_not_configured():
    """Log (do not block) when there is no usable email configuration for the current company.

    Requests (leave, loan, asset, document, air ticket ...) must still be saved; the e-mail step is
    skipped by the senders when no configuration exists and the in-app notification is still created.
    """
    try:
        from EmpManagement.models import EmailConfiguration
        cfg = EmailConfiguration.objects.filter(is_active=True).first()
        if not cfg or not cfg.email_host_user or not cfg.email_host_password:
            logger.warning("Email configuration missing or incomplete - request saved without sending e-mail.")
            return False
        return True
    except Exception:  # never break a request because of the e-mail check
        logger.exception("Email configuration check failed")
        return False
