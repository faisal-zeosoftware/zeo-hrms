"""Purchase packs: the starting set-up. Purchase keeps its records on the shared suppliers and items, so the packs add no rows."""
PACKS = {
    'general': {'label': 'General', 'description': 'Purchase orders, receipts and bills – switch on requests, quotations and approvals when you need them.',
                'highlights': ['no extra records'], 'rows': {}},
    'trading': {'label': 'Trading and distribution', 'description': 'Supplier price lists and supplier returns for buying stock to sell.',
                'highlights': ['supplier prices', 'returns'], 'rows': {}},
    'projects': {'label': 'Projects and contracting', 'description': 'Purchase requests from the sites and quotations from several suppliers.',
                 'highlights': ['requests', 'quotations'], 'rows': {}},
}
