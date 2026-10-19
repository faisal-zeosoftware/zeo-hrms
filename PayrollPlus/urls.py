"""v1.18.0 – PayrollPlus API (see views.py)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V

router = DefaultRouter()
router.register(r'statutory', V.StatutoryViewSet, basename='statutory')   # v1.31.0

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/settings/', V.SettingsView.as_view()),
    path('api/runs/<int:run_id>/control/', V.RunControlView.as_view()),
    path('api/encashments/<int:pk>/steps/', V.EncashmentStepsView.as_view()),
    path('api/settlements/', V.SettlementsView.as_view()),
    path('api/settlements/eligible/', V.EligibleView.as_view()),
    path('api/settlements/<int:pk>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/', V.SettlementView.as_view()),
]
