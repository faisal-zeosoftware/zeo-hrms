from django.urls import path

from . import views as V

urlpatterns = [
    path('api/settings/', V.SettingsView.as_view()),
    path('api/settings/test/', V.TestView.as_view()),
    path('api/options/', V.OptionsView.as_view()),
    path('api/accounts/', V.AccountsView.as_view()),
    path('api/accounts/create/', V.CreateAccountsView.as_view()),
    path('api/dimensions/', V.DimensionsView.as_view()),
    path('api/dimensions/auto/', V.AutoMapView.as_view()),
    path('api/partners/auto/', V.PartnersView.as_view()),
    path('api/sources/', V.SourcesView.as_view()),
    path('api/journals/', V.JournalsView.as_view()),
    path('api/journals/build/', V.BuildView.as_view()),
    path('api/journals/export/', V.ExportView.as_view()),
    path('api/journals/<int:pk>/', V.JournalView.as_view()),
    path('api/journals/<int:pk>/missing/', V.MissingView.as_view()),
    path('api/journals/<int:pk>/<str:action>/', V.JournalActionView.as_view()),
    path('api/clients/', V.ClientsView.as_view()),
    path('api/clients/<int:pk>/', V.ClientView.as_view()),
    path('api/hooks/', V.HooksView.as_view()),
    path('api/hooks/<int:pk>/', V.HookView.as_view()),
    # ZEO API v1 – other applications
    path('v1/token/', V.TokenView.as_view()),
    path('v1/journals/', V.PublicJournals.as_view()),
    path('v1/journals/<int:pk>/', V.PublicJournal.as_view()),
    path('v1/journals/<int:pk>/ack/', V.PublicAck.as_view()),
]
