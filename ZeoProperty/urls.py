from django.urls import path

from . import portal as P
from .app import app
from .pdf import RecordPdfView

urlpatterns = [
    path('api/r/<str:kind>/<int:pk>/pdf/', RecordPdfView.as_view(app=app)),           # v1.40.0 printed documents
    path('api/public/listings/', P.PublicListingsView.as_view()),                      # v1.40.0 public listing page data
    path('api/public/listings/feed.xml', P.ListingFeedView.as_view()),                 # v1.40.0 Bayut / Property Finder feed
    path('api/public/listings/photo/<int:pk>/', P.PublicPhotoView.as_view()),
    path('public/listings/', P.public_listings_page),                                  # v1.40.0 public listing page (HTML)
    path('api/portal/announcements/', P.AnnouncementsView.as_view()),
    path('api/portal/requests/', P.PortalRequestsView.as_view()),
    path('api/portal/deposit/', P.DepositView.as_view()),
    path('api/portal/accept/', P.AcceptLeaseView.as_view()),
    path('api/portal/pay/', P.PayLinkView.as_view()),
    path('api/portal/pdf/<str:kind>/<int:pk>/', P.PortalPdfView.as_view()),
] + app.urls() + [
    path('api/portal/me/', P.MeView.as_view()),
    path('api/portal/tenant/', P.TenantView.as_view()),
    path('api/portal/request/', P.RequestView.as_view()),
    path('api/portal/renewal/', P.RenewalAnswerView.as_view()),
    path('api/portal/owner/', P.OwnerView.as_view()),
    path('api/portal/owner/decision/', P.OwnerDecisionView.as_view()),
    path('api/portal/buyer/', P.BuyerView.as_view()),
    path('api/portal/files/<int:pk>/', P.PortalFileView.as_view()),
]
