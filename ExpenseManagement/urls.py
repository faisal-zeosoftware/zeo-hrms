from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'api/categories', views.CategoryViewSet, basename='expense-category')
router.register(r'api/policies', views.PolicyViewSet, basename='expense-policy')
router.register(r'api/policy-limits', views.LimitViewSet, basename='expense-policy-limit')
router.register(r'api/cost-centers', views.CostCenterViewSet, basename='expense-cost-center')
router.register(r'api/trips', views.TripViewSet, basename='expense-trip')
router.register(r'api/advances', views.AdvanceViewSet, basename='expense-advance')
router.register(r'api/expenses', views.ExpenseViewSet, basename='expense')
router.register(r'api/reports', views.ReportViewSet, basename='expense-report')

urlpatterns = [
    path('', include(router.urls)),
    path('api/home/', views.HomeView.as_view()),
    path('api/export/', views.ExportView.as_view()),
    path('api/analytics/', views.AnalyticsView.as_view()),
    path('api/settings/', views.SettingsView.as_view()),
]
