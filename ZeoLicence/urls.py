from django.urls import path
from . import views as V

urlpatterns = [
    path('api/', V.StatusView.as_view()),
    path('api/banner/', V.BannerView.as_view()),
    path('api/upload/', V.UploadView.as_view()),
    path('api/enterprise-request/', V.EnterpriseRequestView.as_view()),
    path('api/console/requests/', V.ConsoleRequestsView.as_view()),
    path('api/console/requests/<int:pk>/', V.ConsoleRequestsView.as_view()),
    path('api/console/licences/', V.ConsoleLicencesView.as_view()),
    path('api/console/licences/<int:pk>/<str:what>/', V.ConsoleLicencesView.as_view()),
]
