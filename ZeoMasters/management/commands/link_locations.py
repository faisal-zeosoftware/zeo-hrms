"""v1.67.4 (masters) – link the existing Asset Management locations / stores and Maintenance locations to the shared stores and
locations. DRY RUN unless --apply (see ZeoMasters/linking.py)."""
from .link_partners import Command as _Base


class Command(_Base):
    help = 'Link existing Asset Management and Maintenance locations to shared stores / locations. Dry run unless --apply.'
    what = 'locations'
