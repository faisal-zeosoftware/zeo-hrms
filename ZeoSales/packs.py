"""Sales packs: a sales team and a few price rules to start from."""
PACKS = {
    'general': {'label': 'General', 'description': 'One sales team (no commission) – add price rules when you need them.', 'highlights': ['1 team'],
                'rows': {'team': [{'name': 'Sales team', 'commission': 0}]}},
    'trading': {'label': 'Trading and distribution', 'description': 'Field and counter sales teams with commission; a volume discount rule from 100 units.',
                'highlights': ['2 teams', '1 volume discount'],
                'rows': {'team': [{'name': 'Field sales', 'commission': 2}, {'name': 'Counter sales', 'commission': 1}],
                         'rule': [{'name': 'Volume discount 5 % from 100 units', 'kind': 'percent', 'min_qty': 100, 'value': 5, 'priority': 50}]}},
}
