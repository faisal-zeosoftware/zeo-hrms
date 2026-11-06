"""v1.67.4 (masters) – link the existing expense categories, HR asset types and work order cost lines (and any spare part, charge
type, service product or chemical still without one) to the item master. DRY RUN unless --apply (see ZeoMasters/linking.py)."""
from .link_partners import Command as _Base


class Command(_Base):
    help = 'Link existing app records to items: code → exact name → new item (cost lines: name only). Dry run unless --apply.'
    what = 'items'
