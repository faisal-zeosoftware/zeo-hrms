from django.urls import path
from . import views as V

urlpatterns = [
    path('api/', V.GroupsView.as_view()),
    path('api/<str:key>/dashboard/', V.DashboardView.as_view()),
    path('api/<str:key>/consolidation/', V.ConsolidationView.as_view()),   # v1.53.0 one currency
    path('api/<str:key>/rates/', V.RatesView.as_view()),
    path('api/<str:key>/reports/', V.ReportsView.as_view()),
    path('api/<str:key>/reports/<str:rkey>/', V.ReportsView.as_view()),
    path('api/<str:key>/inbox/', V.InboxView.as_view()),
    path('api/<str:key>/feed/', V.FeedView.as_view()),
    path('api/<str:key>/copy/', V.CopyView.as_view()),
    path('api/<str:key>/transfer/', V.TransferView.as_view()),
    path('api/<str:key>/transfer/<str:what>/', V.TransferView.as_view()),
    path('api/<str:key>/roles/', V.RolesView.as_view()),
    path('api/<str:key>/roles/<int:pk>/', V.RolesView.as_view()),
]
