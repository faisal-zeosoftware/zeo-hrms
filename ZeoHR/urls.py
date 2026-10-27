from django.urls import path

from . import views as V
from .app import app

urlpatterns = [
    path('api/r/<str:kind>/<int:pk>/pdf/', V.PdfView.as_view(app=app)),   # case letter, handover, exit interview summary
] + app.urls() + [
    path('api/hub/', V.HubView.as_view(app=app)),                          # document expiry hub
    path('api/sign/<int:pk>/', V.HandoverSignView.as_view(app=app)),
    path('api/sign/<int:pk>/document/', V.HandoverDocumentView.as_view(app=app)),
    path('api/exit/<int:pk>/', V.ExitTakeView.as_view(app=app)),
    path('api/my-cases/', V.MyCasesView.as_view(app=app)),
    path('api/my-tickets/', V.MyTicketsView.as_view(app=app)),               # v1.51.0 HR helpdesk
    path('api/services/', V.ServicesView.as_view(app=app)),
    path('api/assignments-report/', V.AssignmentsReportView.as_view(app=app)),   # v1.58.0
]
