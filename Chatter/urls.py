from django.urls import path
from . import views as v
from . import collab_views as cv   # v1.45.0
from . import nav_views as nv      # v1.47.0
from . import msg_views as mv      # v1.51.0

urlpatterns = [
    path('api/record/', v.RecordView.as_view(), name='chatter-record'),
    path('api/notes/', v.NotesView.as_view(), name='chatter-notes'),
    path('api/notes/<int:pk>/', v.NoteDetailView.as_view(), name='chatter-note'),
    path('api/notes/<int:pk>/react/', v.NoteReactView.as_view()),       # v1.46.0 threads
    path('api/notes/<int:pk>/resolve/', v.NoteResolveView.as_view()),
    path('api/attachments/', v.AttachmentsView.as_view(), name='chatter-attachments'),
    path('api/attachments/<int:pk>/download/', v.AttachmentDetailView.as_view(), name='chatter-attachment-download'),
    path('api/attachments/<int:pk>/', v.AttachmentDetailView.as_view(), name='chatter-attachment'),
    path('api/activities/', v.ActivitiesView.as_view(), name='chatter-activities'),
    path('api/activities/<int:pk>/', v.ActivityDetailView.as_view(), name='chatter-activity'),
    path('api/todo/', v.TodoView.as_view(), name='chatter-todo'),
    path('api/users/', v.UsersView.as_view(), name='chatter-users'),
    path('api/fields/screens/', v.FieldScreensView.as_view(), name='chatter-field-screens'),
    path('api/fields/', v.FieldsView.as_view(), name='chatter-fields'),
    path('api/layout/', v.FormLayoutView.as_view(), name='chatter-layout'),   # v1.39.0 form layout designer
    path('api/values/', v.ValuesView.as_view(), name='chatter-values'),
    path('api/values/check/', v.ValuesCheckView.as_view(), name='chatter-values-check'),
    path('api/layout/dashboard/', v.DashboardLayoutView.as_view(), name='chatter-dashboard-layout'),
    path('api/layout/list/', v.ListLayoutView.as_view(), name='chatter-list-layout'),
    path('api/orgchart/', v.OrgChartView.as_view(), name='chatter-orgchart'),
    # v1.45.0 followers, messages, inbox, starred / recent, tags, mass actions, reply mailbox
    path('api/followers/', cv.FollowersView.as_view()),
    path('api/inbox/', cv.InboxView.as_view()),
    path('api/stars/', cv.StarsView.as_view()),
    path('api/tags/', cv.TagsView.as_view()),
    path('api/tags/<int:pk>/', cv.TagView.as_view()),
    path('api/record-tags/', cv.RecordTagsView.as_view()),
    path('api/tag-values/', cv.TagValuesView.as_view()),
    path('api/mass/', cv.MassView.as_view()),
    path('api/mail-settings/', cv.MailSettingsView.as_view()),
    path('api/mail-settings/check/', cv.MailCheckView.as_view()),
    path('api/audit/', cv.AuditView.as_view()),     # v1.46.0 company-wide audit log
    path('api/print/', cv.PrintView.as_view()),     # v1.46.0 print any record
    # v1.47.0 global search, quick create, company wall, My team with 1:1s, flags (tours), what's new
    path('api/search/', nv.SearchView.as_view()),
    path('api/quick/', nv.QuickView.as_view()),
    path('api/wall/', nv.WallView.as_view()),
    path('api/wall/react/', nv.WallReactView.as_view()),
    path('api/wall/comment/', nv.WallCommentView.as_view()),
    path('api/wall/comment/<int:pk>/', nv.WallCommentView.as_view()),
    path('api/my-team/', nv.MyTeamView.as_view()),
    path('api/one-on-ones/', nv.OneOnOnesView.as_view()),
    path('api/one-on-ones/<int:pk>/', nv.OneOnOneView.as_view()),
    path('api/flags/', nv.FlagsView.as_view()),
    path('api/whats-new/', nv.WhatsNewView.as_view()),
    path('api/messaging/', mv.MessagingView.as_view()),           # v1.51.0 WhatsApp / SMS
    path('api/messaging/test/', mv.MessagingTestView.as_view()),
    path('api/messaging/log/', mv.MessageLogView.as_view()),
    path('api/push-settings/', mv.PushSettingsView.as_view()),    # v1.51.0 company Firebase project
    path('api/push-settings/test/', mv.PushTestView.as_view()),
]
