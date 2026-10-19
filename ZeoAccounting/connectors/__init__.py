"""Connectors to accounting applications. Each one offers:

test()                              → {'ok': True, 'detail': '…'}
accounts()                          → [{'id', 'code', 'name', 'type'}]
analytics(kind)                     → [{'id', 'name', 'code'}]   kind: department / employee / branch
create_analytic(kind, name, code)   → {'id', 'name', 'code'}
create_account(code, name, parent)  → {'id', 'code', 'name'}     parent: the base account (its type is copied)
partner(name, code, create)         → (id or '', made)
post(batch)                         → (external id, external number) – never makes a second entry for the same batch
"""


class ConnectorError(Exception):
    pass
