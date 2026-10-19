from django.urls import path

from . import views as V
from . import views2 as V2
from .app import app

urlpatterns = app.urls() + [
    path('api/scan/', V.ScanView.as_view(app=app)),
    path('api/labels/', V.LabelsView.as_view(app=app)),
    path('api/audit/<int:pk>/scan/', V.AuditScanView.as_view(app=app)),
    path('api/floorplan/<int:pk>/', V.FloorPlanView.as_view(app=app)),
    path('api/floorplan/<int:pk>/image/', V.FloorPlanImageView.as_view(app=app)),
    path('api/depreciation/<int:pk>/journal/', V.JournalView.as_view(app=app)),
    path('api/sync/', V.SyncView.as_view(app=app)),
    # v1.34.0 release 2
    path('api/calendar/', V2.CalendarView.as_view(app=app)),
    path('api/mailbox/', V2.MailboxView.as_view(app=app)),
    path('api/field/', V2.FieldView.as_view(app=app)),
    path('api/import/<str:kind>/', V2.ImportView.as_view(app=app)),
]
