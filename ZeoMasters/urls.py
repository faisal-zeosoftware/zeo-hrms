from django.urls import path

from .app import app
from . import views as V

urlpatterns = app.urls() + [
    # v1.44.0 accounting-ready
    path('api/partners/<int:pk>/statement/', V.StatementView.as_view()),
    path('api/valuation/', V.ValuationView.as_view()),
    path('api/balances/refresh/', V.BalancesView.as_view()),
]
