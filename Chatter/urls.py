from django.urls import path
from . import views as v

urlpatterns = [
    path('api/record/', v.RecordView.as_view(), name='chatter-record'),
    path('api/notes/', v.NotesView.as_view(), name='chatter-notes'),
    path('api/notes/<int:pk>/', v.NoteDetailView.as_view(), name='chatter-note'),
    path('api/attachments/', v.AttachmentsView.as_view(), name='chatter-attachments'),
    path('api/attachments/<int:pk>/download/', v.AttachmentDetailView.as_view(), name='chatter-attachment-download'),
    path('api/attachments/<int:pk>/', v.AttachmentDetailView.as_view(), name='chatter-attachment'),
    path('api/activities/', v.ActivitiesView.as_view(), name='chatter-activities'),
    path('api/activities/<int:pk>/', v.ActivityDetailView.as_view(), name='chatter-activity'),
    path('api/todo/', v.TodoView.as_view(), name='chatter-todo'),
    path('api/users/', v.UsersView.as_view(), name='chatter-users'),
    path('api/fields/screens/', v.FieldScreensView.as_view(), name='chatter-field-screens'),
    path('api/fields/', v.FieldsView.as_view(), name='chatter-fields'),
    path('api/values/', v.ValuesView.as_view(), name='chatter-values'),
    path('api/values/check/', v.ValuesCheckView.as_view(), name='chatter-values-check'),
    path('api/layout/dashboard/', v.DashboardLayoutView.as_view(), name='chatter-dashboard-layout'),
    path('api/layout/list/', v.ListLayoutView.as_view(), name='chatter-list-layout'),
    path('api/orgchart/', v.OrgChartView.as_view(), name='chatter-orgchart'),
]
