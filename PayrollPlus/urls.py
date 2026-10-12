"""v1.18.0 – PayrollPlus API (see views.py)."""
from django.urls import path

from . import views as V

urlpatterns = [
    path('api/settings/', V.SettingsView.as_view()),
    path('api/runs/<int:run_id>/control/', V.RunControlView.as_view()),
    path('api/encashments/<int:pk>/steps/', V.EncashmentStepsView.as_view()),
    path('api/settlements/', V.SettlementsView.as_view()),
    path('api/settlements/eligible/', V.EligibleView.as_view()),
    path('api/settlements/<int:pk>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/', V.SettlementView.as_view()),
]
