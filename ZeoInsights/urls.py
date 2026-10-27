from django.urls import path

from . import views as V

urlpatterns = [
    path('api/meta/', V.MetaView.as_view()),
    path('api/fields/', V.FieldsView.as_view()),
    path('api/reports/', V.ReportsView.as_view()),
    path('api/reports/preview/', V.PreviewView.as_view()),
    path('api/reports/<int:pk>/', V.ReportView.as_view()),
    path('api/dashboards/', V.DashboardsView.as_view()),
    path('api/dashboards/card-preview/', V.CardPreviewView.as_view()),
    path('api/dashboards/<int:pk>/', V.DashboardView.as_view()),
    path('api/dashboards/<int:pk>/add-card/', V.AddCardView.as_view()),
    path('api/dashboards/<int:pk>/cards/<str:cid>/', V.CardView.as_view()),
]
