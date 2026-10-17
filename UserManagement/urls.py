from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.views import TokenObtainPairView
# from . import views
from .views import (MyCompaniesView, DuplicateCompanyView, DuplicateStatusView, RegisterUserAPIView, CompanyViewSet, DomainViewset, TenantUserListView, CustomTokenObtainPairView, UserDetailView,NoEssUerListView,GroupPermTenantUserListView,
                    ValidateCredentialsView,SendOTPView,VerifyOTPView,SendResetPasswordOTP,VerifyResetOTP,ResetPassword,ChangePasswordView,ESSSendResetPasswordOTP,ESSVerifyResetOTP,
                    ESSResetPassword)
from . import views
router = DefaultRouter()

router.register(r'user', RegisterUserAPIView)

router.register(r'company', CompanyViewSet)
router.register(r'domain', DomainViewset)


urlpatterns = [
    path('api/my-companies/', MyCompaniesView.as_view(), name='my-companies'),   # v1.22.2 company list after login
    path('api/my-companies/<int:pk>/duplicate/', DuplicateCompanyView.as_view(), name='duplicate-company'),   # v1.22.3
    path('api/my-companies/duplicate/<str:job>/', DuplicateStatusView.as_view(), name='duplicate-company-status'),
    path('api/company-jobs/<str:job>/', views.CompanyJobView.as_view(), name='company-job'),   # v1.26.0
    path('api/invites/', views.InvitesView.as_view(), name='invites'),                          # v1.26.0
    path('api/invites/<int:pk>/', views.InvitesView.as_view(), name='invite'),
    path('api/', include(router.urls)),
    path('api/user/<str:tenant_id>/', UserDetailView.as_view(), name='user-detail'),  # New path
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path('tenant-users/', TenantUserListView.as_view(), name='tenant-user-list'),
    path('tenant-non-ess-users/', NoEssUerListView.as_view(), name='tenant-user-list'),
    path('group-perm-tenant-users/', GroupPermTenantUserListView.as_view(), name='group-perm-tenant-users-list'),
    path('validate-credentials/', ValidateCredentialsView.as_view(), name='validate-credentials'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('send-otp/', SendOTPView.as_view(), name='send-otp'),
    path("send-reset-otp/", SendResetPasswordOTP.as_view(),name='send-reset-otp'),
    path("verify-reset-otp/", VerifyResetOTP.as_view(),name='verify-reset-otp/'),
    path("reset-password/", ResetPassword.as_view(),name='reset-password'),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("ess-send-reset-otp/",ESSSendResetPasswordOTP.as_view(),name="ess-send-reset-otp",),
    path("ess-verify-reset-otp/",ESSVerifyResetOTP.as_view(),name="ess-verify-reset-otp",),
    path("ess-reset-password/",ESSResetPassword.as_view(),name="ess-reset-password",),


]