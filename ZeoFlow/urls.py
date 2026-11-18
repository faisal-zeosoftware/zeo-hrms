from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views as v

router = DefaultRouter()
router.register(r'api/workflows', v.WorkflowViewSet, basename='flow-workflows')
router.register(r'api/runs', v.RunViewSet, basename='flow-runs')
router.register(r'api/scheduled', v.ScheduledViewSet, basename='flow-scheduled')
router.register(r'api/templates', v.TemplateViewSet, basename='flow-templates')
router.register(r'api/approval-flows', v.ApprovalFlowViewSet, basename='flow-approval-flows')
router.register(r'api/approvals', v.ApprovalViewSet, basename='flow-approvals')

urlpatterns = [
    path('api/meta/', v.MetaView.as_view()),
    path('api/meta/fields/', v.FieldsView.as_view()),
    path('api/dashboard/', v.DashboardView.as_view()),
    path('api/by-app/', v.ByAppView.as_view()),          # v1.67.21 (item 1.62.8) notifications and approval rules per app
    path('api/notifications/', v.NotificationView.as_view()),
    path('api/audit/', v.AuditView.as_view()),
    path('api/record/', v.RecordFlowView.as_view()),     # v1.38.0 workflow strip of a record page
    # v1.67.26 custom modules and Blueprint
    path('api/custom-modules/', v.CustomModulesView.as_view()),
    path('api/custom-modules/<slug:key>/', v.CustomModuleView.as_view()),
    path('api/custom/', v.CustomMyModulesView.as_view()),
    path('api/custom/<slug:key>/records/', v.CustomRecordsView.as_view()),
    path('api/custom/<slug:key>/records/<int:pk>/', v.CustomRecordView.as_view()),
    path('api/custom/<slug:key>/records/<int:pk>/move/', v.CustomMoveView.as_view()),
] + router.urls
