"""AssetPlus API – include under /asset-plus/ (see INTEGRATION.md)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'api/assets', views.AssetViewSet, basename='assetplus-asset')
router.register(r'api/allocations', views.AllocationViewSet, basename='assetplus-allocation')
router.register(r'api/transfers', views.TransferViewSet, basename='assetplus-transfer')
router.register(r'api/maintenance', views.MaintenanceViewSet, basename='assetplus-maintenance')
router.register(r'api/schedules', views.ScheduleViewSet, basename='assetplus-schedule')
router.register(r'api/returns', views.ReturnViewSet, basename='assetplus-return')
router.register(r'api/damages', views.DamageViewSet, basename='assetplus-damage')
router.register(r'api/losses', views.LossViewSet, basename='assetplus-loss')
router.register(r'api/disposals', views.DisposalViewSet, basename='assetplus-disposal')

urlpatterns = [
    path('', include(router.urls)),
    path('api/recoveries/', views.RecoveryView.as_view()),
    path('api/clearance/', views.ClearanceView.as_view()),
    path('api/clearance/<int:emp_id>/', views.ClearanceView.as_view()),
    path('api/my/', views.MyAssetsView.as_view()),
    path('api/types/', views.TypeConfigView.as_view()),
    path('api/settings/', views.SettingsView.as_view()),
    path('api/events/', views.EventsView.as_view()),
    path('api/lookups/', views.LookupsView.as_view()),
]
