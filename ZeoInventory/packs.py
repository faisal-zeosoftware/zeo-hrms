"""Inventory packs: the reasons for adjustments, scrap and internal use (each posts to its own account role)."""


def rs(rows):
    return [{'code': c, 'name': n, 'kind': k, 'account_role': a} for c, n, k, a in rows]


BASE = [('COUNT', 'Count correction', 'adjustment', 'count_variance'), ('FOUND', 'Found stock', 'adjustment', 'adjustment'),
        ('ERROR', 'Booking error correction', 'adjustment', 'adjustment'), ('DAMAGE', 'Damaged', 'scrap', 'scrap'),
        ('LOST', 'Lost / missing', 'scrap', 'scrap'), ('INTERNAL', 'Internal use', 'consumption', 'consumption'),
        ('SAMPLE', 'Free samples', 'consumption', 'consumption')]

PACKS = {
    'general': {'label': 'General', 'description': 'Reasons for stock adjustments, scrap and internal use, each to its own account.',
                'highlights': ['7 reasons'], 'rows': {'reason': rs(BASE)}},
    'retail': {'label': 'Retail and trading', 'description': 'Shrinkage, expiry and display damage as well as the general reasons.',
               'highlights': ['10 reasons', 'Shrinkage and expiry'],
               'rows': {'reason': rs(BASE + [('SHRINK', 'Shrinkage (theft)', 'scrap', 'scrap'), ('EXPIRED', 'Expired', 'scrap', 'write_down'),
                                             ('DISPLAY', 'Display damage', 'scrap', 'scrap')])}},
    'food': {'label': 'Food and pharmacy', 'description': 'Expiry, cold-chain break and quality rejection; lots with expiry dates.',
             'highlights': ['10 reasons', 'Expiry and quality'],
             'rows': {'reason': rs(BASE + [('EXPIRED', 'Expired', 'scrap', 'write_down'), ('COLD', 'Cold-chain break', 'scrap', 'scrap'),
                                           ('QC', 'Quality rejected', 'scrap', 'scrap')])}},
    'manufacturing': {'label': 'Manufacturing', 'description': 'Production scrap, rework and quality rejection as well as the general reasons.',
                      'highlights': ['10 reasons', 'Production scrap'],
                      'rows': {'reason': rs(BASE + [('PRODSCRAP', 'Production scrap', 'scrap', 'scrap'), ('REWORK', 'Sent to rework', 'consumption', 'consumption'),
                                                    ('QC', 'Quality rejected', 'scrap', 'scrap')])}},
}
