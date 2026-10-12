from django.urls import path

from . import portal as P
from .app import app

urlpatterns = app.urls() + [
    path('api/portal/me/', P.MeView.as_view()),
    path('api/portal/tenant/', P.TenantView.as_view()),
    path('api/portal/request/', P.RequestView.as_view()),
    path('api/portal/renewal/', P.RenewalAnswerView.as_view()),
    path('api/portal/owner/', P.OwnerView.as_view()),
    path('api/portal/owner/decision/', P.OwnerDecisionView.as_view()),
    path('api/portal/buyer/', P.BuyerView.as_view()),
    path('api/portal/files/<int:pk>/', P.PortalFileView.as_view()),
]
