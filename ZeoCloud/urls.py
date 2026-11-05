from django.urls import path

from . import views as V
from . import lifecycle_views as LV   # v1.67.1 (companies)

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
    path('api/accounts/<int:pk>/databases/', V.AccountDatabasesView.as_view()),                  # v1.26.0
    path('api/accounts/<int:pk>/databases/<str:key>/', V.AccountDatabaseView.as_view()),
    path('api/accounts/<int:pk>/moves/', V.AccountMovesView.as_view()),
    path('api/db-jobs/<str:job>/', V.DbJobView.as_view()),
    path('api/console/me/', V.ConsoleMeView.as_view()),
    path('api/console/customers/', V.ConsoleCustomersView.as_view()),
    path('api/console/customers/<int:pk>/', V.ConsoleCustomerView.as_view()),
    path('api/console/customers/<int:pk>/action/', V.ConsoleActionView.as_view()),
    path('api/console/customers/<int:pk>/apps/', V.ConsoleAppsView.as_view()),
    path('api/console/customers/<int:pk>/invoices/', V.ConsoleInvoicesView.as_view()),
    path('api/console/customers/<int:pk>/companies/', V.ConsoleCompaniesView.as_view()),
    path('api/console/invoices/<int:pk>/paid/', V.ConsoleInvoicePaidView.as_view()),
    path('api/console/invoices/<int:pk>/odoo/', V.ConsoleInvoiceOdooView.as_view()),   # v1.28.0
    path('api/console/unlinked/', V.ConsoleUnlinkedView.as_view()),
    path('api/console/plans/', V.ConsolePlansView.as_view()),
    path('api/console/databases/', V.ConsoleDatabasesView.as_view()),                         # v1.24.0
    path('api/console/customers/<int:pk>/moves/', V.ConsoleMovesView.as_view()),
    path('api/console/customers/<int:pk>/change-plan/', V.ConsoleChangePlanView.as_view()),
    path('api/console/moves/<int:pk>/', V.ConsoleMoveView.as_view()),
    # v1.67.1 (companies) make inactive / active, delete (kept 30 days), the backup download, restore (Adept)
    path('api/companies/<int:pk>/life/', LV.CompanyLifeView.as_view()),
    path('api/companies/<int:pk>/delete/', LV.CompanyDeleteView.as_view()),
    path('api/deleted-backups/<str:token>/', LV.DeletedBackupView.as_view()),
    path('api/console/deleted/', LV.ConsoleDeletedView.as_view()),
]
