from django.urls import path

from .app import app
from .pdf import DocPdfView
from .views import CaptureView
from .marketing import HookView, TrackView   # v1.67.29

urlpatterns = app.urls() + [
    path('api/quotation/<int:pk>/pdf/', DocPdfView.as_view(app=app, kind='quotation')),          # v1.33.0
    path('api/sales-order/<int:pk>/pdf/', DocPdfView.as_view(app=app, kind='sales_order')),
    path('api/capture/<str:key>/', CaptureView.as_view()),
    path('api/hooks/<str:key>/', HookView.as_view()),                    # v1.67.29 lead connectors, WhatsApp, telephony
    path('api/m/<str:kind>/<str:token>/', TrackView.as_view()),           # v1.67.29 open / click / unsubscribe
]
