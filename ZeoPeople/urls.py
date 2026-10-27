from django.urls import path

from . import views as V
from .app import app

urlpatterns = app.urls() + [
    path('api/sign/<int:pk>/', V.SignView.as_view(app=app)),
    path('api/sign/<int:pk>/document/', V.SignDocumentView.as_view(app=app)),
    path('api/public-sign/<str:token>/', V.PublicSignView.as_view()),
    path('api/public-sign/<str:token>/document/', V.PublicSignDocumentView.as_view()),
    path('api/survey/<int:pk>/take/', V.SurveyTakeView.as_view(app=app)),
    path('api/survey/<int:pk>/results/', V.SurveyResultsView.as_view(app=app)),
    path('api/forms/', V.MyFormsView.as_view(app=app)),
    path('api/forms/<int:pk>/', V.FormFillView.as_view(app=app)),
    path('api/wall/', V.WallView.as_view(app=app)),
    path('api/review/<int:pk>/', V.ReviewSheetView.as_view(app=app)),
    path('api/chat/', V.ChatView.as_view(app=app)),
    path('api/chat/unread/', V.ChatUnreadView.as_view(app=app)),
    path('api/chat/<int:pk>/', V.ChatMessagesView.as_view(app=app)),
    path('api/chat/<int:pk>/file/<int:mid>/', V.ChatFileView.as_view(app=app)),
    path('api/insurer-file/', V.InsurerFileView.as_view(app=app)),           # v1.61.0
    path('api/insurer-file/plans/', V.InsurerPlansView.as_view(app=app)),
]
