from ZeoCloud import security_views as _SV   # v1.52.0
from ZeoCloud import sso_views as _SSO   # v1.54.0
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
    path("token/refresh/", _SV.RefreshView.as_view(), name="token_refresh"),   # v1.52.0 refused for a signed-out session (was TokenRefreshView)
    path("2fa/login/", _SV.TwoFactorLoginView.as_view()),                    # v1.52.0 sign-in security
    path("sso/discover/", _SSO.DiscoverView.as_view()),                      # v1.54.0 single sign-on
    path("sso/start/<int:pk>/", _SSO.StartView.as_view()),
    path("sso/oidc/callback/", _SSO.OidcCallbackView.as_view()),
    path("sso/saml/acs/<int:pk>/", _SSO.SamlAcsView.as_view()),
    path("sso/saml/metadata/<int:pk>/", _SSO.SamlMetadataView.as_view()),
    path("sso/exchange/", _SSO.ExchangeView.as_view()),
    path("sso/device/confirm/", _SSO.DeviceConfirmView.as_view()),           # the phone app: confirm in the browser, the app polls
    path("sso/device/poll/<str:poll>/", _SSO.DevicePollView.as_view()),
    path("sso/providers/", _SSO.ProvidersView.as_view()),
    path("sso/providers/<int:pk>/", _SSO.ProviderView.as_view()),
    path("2fa/", _SV.TwoFactorView.as_view()),
    path("sessions/", _SV.SessionsView.as_view()),
    path("security/policy/", _SV.PolicyView.as_view()),
    path("security/users/", _SV.CompanyUsersView.as_view()),
    path("security/alerts/", _SV.SignInAlertsView.as_view()),   # v1.61.0
    path("security/dashboard/", _SV.SecurityDashboardView.as_view()),   # v1.62.0
    path("step-up/", _SV.StepUpView.as_view()),                        # v1.62.0 password asked again
    path("activate/send/", _SV.ActivateSendView.as_view()),            # v1.62.0 code to the mobile (no e-mail)
    path("activate/", _SV.ActivateView.as_view()),                     # v1.62.0 slip / message code + own password
    path("first-check/", _SV.FirstCheckView.as_view()),                # v1.62.0 company default password: extra check
    path("impersonate/", _SV.ImpersonateView.as_view()),
    path("impersonate/stop/", _SV.ImpersonateStopView.as_view()),
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
    path("sign-in-link/", __import__('ZeoAdmin.views', fromlist=['SignInLinkView']).SignInLinkView.as_view(), name="sign-in-link"),   # v1.56.0
    path("media-cookie/", __import__('ZeoAdmin.views', fromlist=['MediaCookieView']).MediaCookieView.as_view(), name="media-cookie"),   # v1.56.1 files need a login


]