from django.urls import path

from .app import app
from . import views as V
from . import quick as Q   # v1.73.5

urlpatterns = app.urls() + [
    # v1.44.0 accounting-ready
    path('api/partners/<int:pk>/statement/', V.StatementView.as_view()),
    path('api/valuation/', V.ValuationView.as_view()),
    path('api/balances/refresh/', V.BalancesView.as_view()),
    path('api/quick/meta/', Q.QuickMetaView.as_view()),     # v1.73.5 "+ Create new …" in every master drop-down
    path('api/quick/names/', Q.QuickNamesView.as_view()),   # v1.73.5 the names the masters are known by
]
