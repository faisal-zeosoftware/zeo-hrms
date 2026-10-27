from django.urls import path

from . import views
from . import registers62   # v1.62.0 people

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
]
