from django.urls import path

from . import views as V

urlpatterns = [
    path('api/signup/plans/', V.SignupPlansView.as_view()),
    path('api/signup/', V.SignupView.as_view()),
    path('api/jobs/<str:job>/', V.JobView.as_view()),
    path('api/status/', V.StatusView.as_view()),
    path('api/notices/seen/', V.NoticesSeenView.as_view()),
    path('api/accounts/<int:pk>/subscription/', V.SubscriptionView.as_view()),
    path('api/accounts/<int:pk>/companies/', V.AccountCompanyCreateView.as_view()),
    path('api/accounts/<int:pk>/request/', V.RequestView.as_view()),
    path('api/accounts/<int:pk>/security/', V.SecurityView.as_view()),
    path('api/console/me/', V.ConsoleMeView.as_view()),
    path('api/console/customers/', V.ConsoleCustomersView.as_view()),
    path('api/console/customers/<int:pk>/', V.ConsoleCustomerView.as_view()),
    path('api/console/customers/<int:pk>/action/', V.ConsoleActionView.as_view()),
    path('api/console/customers/<int:pk>/apps/', V.ConsoleAppsView.as_view()),
    path('api/console/customers/<int:pk>/invoices/', V.ConsoleInvoicesView.as_view()),
    path('api/console/customers/<int:pk>/companies/', V.ConsoleCompaniesView.as_view()),
    path('api/console/invoices/<int:pk>/paid/', V.ConsoleInvoicePaidView.as_view()),
    path('api/console/unlinked/', V.ConsoleUnlinkedView.as_view()),
    path('api/console/plans/', V.ConsolePlansView.as_view()),
    path('api/console/databases/', V.ConsoleDatabasesView.as_view()),                         # v1.24.0
    path('api/console/customers/<int:pk>/moves/', V.ConsoleMovesView.as_view()),
    path('api/console/customers/<int:pk>/change-plan/', V.ConsoleChangePlanView.as_view()),
    path('api/console/moves/<int:pk>/', V.ConsoleMoveView.as_view()),
]
