from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.views import TokenObtainPairView
# from . import views
from .views import (RegisterUserAPIView, CompanyViewSet, DomainViewset, TenantUserListView, CustomTokenObtainPairView, UserDetailView,NoEssUerListView,GroupPermTenantUserListView)
from . import views
router = DefaultRouter()

router.register(r'user', RegisterUserAPIView)

router.register(r'company', CompanyViewSet)
router.register(r'domain', DomainViewset)


urlpatterns = [
    path('api/', include(router.urls)),
    path('api/user/<str:tenant_id>/', UserDetailView.as_view(), name='user-detail'),  # New path
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path('tenant-users/', TenantUserListView.as_view(), name='tenant-user-list'),
    path('tenant-non-ess-users/', NoEssUerListView.as_view(), name='tenant-user-list'),
    path('group-perm-tenant-users/', GroupPermTenantUserListView.as_view(), name='group-perm-tenant-users-list')
    
]