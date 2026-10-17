"""v1.25.0 – /mobile-co/…?schema=<company> (the company's own data)."""
from django.urls import path

from . import views as V

urlpatterns = [
    path('api/home/', V.HomeView.as_view()),
    path('api/approvals/', V.ApprovalsView.as_view()),
    path('api/approvals/<slug:module>/<int:pk>/<str:act>/', V.ApprovalActionView.as_view()),
    path('api/requests/', V.RequestsView.as_view()),
]
