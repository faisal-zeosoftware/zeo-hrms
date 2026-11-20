"""Manufacturing packs: work centres to start from (rates are set by the company)."""
PACKS = {
    'general': {'label': 'General', 'description': 'One work centre – add the operations when you need them.', 'highlights': ['1 work centre'],
                'rows': {'workcentre': [{'code': 'WC-MAIN', 'name': 'Main workshop', 'hours_per_day': 8}]}},
    'assembly': {'label': 'Assembly and fabrication', 'description': 'Cutting, assembly, and quality check work centres.', 'highlights': ['3 work centres'],
                 'rows': {'workcentre': [{'code': 'WC-CUT', 'name': 'Cutting', 'hours_per_day': 8}, {'code': 'WC-ASM', 'name': 'Assembly', 'hours_per_day': 8},
                                         {'code': 'WC-QC', 'name': 'Quality check', 'hours_per_day': 8}]}},
    'process': {'label': 'Process (food, chemicals, cosmetics)', 'description': 'Mixing, filling and packing work centres.', 'highlights': ['3 work centres'],
                'rows': {'workcentre': [{'code': 'WC-MIX', 'name': 'Mixing', 'hours_per_day': 16}, {'code': 'WC-FILL', 'name': 'Filling', 'hours_per_day': 16},
                                        {'code': 'WC-PACK', 'name': 'Packing', 'hours_per_day': 8}]}},
}
