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
    path('api/notifications/', v.NotificationView.as_view()),
    path('api/audit/', v.AuditView.as_view()),
    path('api/record/', v.RecordFlowView.as_view()),     # v1.38.0 workflow strip of a record page
] + router.urls
