"""v1.54.0 SCIM 2.0 (mounted at /scim/v2/)."""
from django.urls import path, re_path

from . import scim as V

urlpatterns = [
    path('ServiceProviderConfig', V.ServiceProviderConfigView.as_view()),
    path('ResourceTypes', V.ResourceTypesView.as_view()),
    path('Schemas', V.SchemasView.as_view()),
    path('Users', V.UsersView.as_view()),
    path('Users/<str:pk>', V.UserView.as_view()),
    path('Groups', V.GroupsView.as_view()),
    path('Groups/<str:pk>', V.GroupView.as_view()),
]
