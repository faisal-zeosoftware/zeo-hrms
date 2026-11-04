from django.urls import path

from . import views
from . import registers62   # v1.62.0 people
from . import requests62    # v1.62.0 people
from . import company62     # v1.62.0 people

urlpatterns = [
    path('api/transfers/', views.TransfersView.as_view()),
    path('api/transfers/preview/', views.TransferPreviewView.as_view()),
    path('api/transfers/<int:pk>/<str:action>/', views.TransferActionView.as_view()),
    path('api/warnings/', views.WarningsView.as_view()),                                   # v1.45.0
    path('api/warnings/<int:pk>/<str:action>/', views.WarningActionView.as_view()),
    path('api/rejoins/', views.RejoinsView.as_view()),
    path('api/rejoins/<int:pk>/<str:action>/', views.RejoinSettleView.as_view()),
    # v1.62.0 people: the Resignations register (stage, waiting for me, names, Leavers link)
    path('api/resignations/', registers62.ResignationsView.as_view()),
    path('api/resignations/stats/', registers62.ResignationStatsView.as_view()),
    path('api/resignations/<int:pk>/<str:action>/', registers62.ResignationActionView.as_view()),
    # v1.62.0 people: general and document requests – one row per request, stage, waiting for me
    path('api/requests/<str:kind>/', requests62.RequestsView.as_view()),
    path('api/company-policies/', company62.PoliciesView.as_view()),   # v1.62.0 people: policy register with acknowledgements
    path('api/company-policies/<int:pk>/<str:action>/', company62.PolicyActionView.as_view()),
    path('api/announcements/', company62.AnnouncementsView.as_view()),
    path('api/org-overview/', company62.OrgOverviewView.as_view()),   # v1.62.0 people: organisation overview  # v1.62.0 people: announcements register
    path('api/announcements/<int:pk>/<str:action>/', company62.AnnouncementActionView.as_view()),
    path('api/requests/<str:kind>/stats/', requests62.RequestStatsView.as_view()),
    path('api/requests/<str:kind>/<int:pk>/<str:action>/', requests62.RequestActionView.as_view()),
]
