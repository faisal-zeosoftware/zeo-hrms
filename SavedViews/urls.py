from django.urls import path
from .views import SavedViewListView, SavedViewDetailView, SavedViewDefaultView, SavedViewGroupsView

urlpatterns = [
    path('api/views/', SavedViewListView.as_view(), name='savedviews-list'),
    path('api/views/default/', SavedViewDefaultView.as_view(), name='savedviews-default'),
    path('api/views/groups/', SavedViewGroupsView.as_view(), name='savedviews-groups'),   # v1.46.0
    path('api/views/<int:pk>/', SavedViewDetailView.as_view(), name='savedviews-detail'),
]
