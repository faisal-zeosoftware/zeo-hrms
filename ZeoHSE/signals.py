"""HSE industry (v1.17.0): HSE is no longer filled automatically when a company is created or its industry changes.
The industry is chosen per app when HSE is activated (views.ActivateView), suggested from the company master
(company.industry_type → pack). This file only keeps the mapping helper."""
import logging

log = logging.getLogger(__name__)


def industry_of(tenant):
    """The HSE pack suggested by the company master industry (General when there is no matching pack)."""
    from .models import INDUSTRY_FROM_COMPANY
    return INDUSTRY_FROM_COMPANY.get(getattr(tenant, 'industry_type', None) or '', 'general')


def connect_company_change():
    """Kept for compatibility with v1.16 apps.py: a change of the company industry no longer changes HSE (HSE keeps its own choice)."""
    return None
