from django.urls import path

from . import views

urlpatterns = [
    path('api/transfers/', views.TransfersView.as_view()),
    path('api/transfers/preview/', views.TransferPreviewView.as_view()),
    path('api/transfers/<int:pk>/<str:action>/', views.TransferActionView.as_view()),
    path('api/rejoins/', views.RejoinsView.as_view()),
    path('api/rejoins/<int:pk>/<str:action>/', views.RejoinSettleView.as_view()),
]
