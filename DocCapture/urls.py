from django.urls import path

from . import views as V

urlpatterns = [
    path('api/meta/', V.MetaView.as_view()),
    path('api/read/', V.ReadView.as_view()),
    path('api/jobs/<int:pk>/', V.JobView.as_view()),
    path('api/jobs/<int:pk>/file/', V.FileView.as_view()),
    path('api/jobs/<int:pk>/rematch/', V.RematchView.as_view()),
    path('api/jobs/<int:pk>/create/', V.CreateView.as_view()),
    path('api/search/<str:what>/', V.SearchView.as_view()),
    path('api/item-image/<int:pid>/', V.ItemImageView.as_view()),
    path('api/jobs/<int:pk>/photo/', V.PhotoView.as_view()),
]
