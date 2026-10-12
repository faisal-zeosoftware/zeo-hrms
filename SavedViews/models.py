"""
v1.16.0 – saved views of the shared list tools (z-list).

A view is everything the user set up on a list: search text, filters, group by levels, sort,
visible columns with their order and widths, rows per page and the view mode (list / kanban / ...).
Views are personal unless shared with everyone (company admins or users holding
SavedViews.share_savedview). Each user may pick one view per screen as their default; it is
applied when the screen opens.

Tables live in the company schema; users are referenced by id (users live in the public schema).
"""
from django.db import models


class SavedView(models.Model):
    screen = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=80)
    owner_id = models.IntegerField(db_index=True)
    owner_name = models.CharField(max_length=150, blank=True, default='')
    shared = models.BooleanField(default=False)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'saved view'
        permissions = [('share_savedview', 'Can share saved list views with everyone')]

    def __str__(self):
        return f'{self.screen}: {self.name}'


class SavedViewDefault(models.Model):
    """The view a user wants applied when a screen opens (one per user and screen)."""
    user_id = models.IntegerField()
    screen = models.CharField(max_length=255)
    view_id = models.IntegerField()

    class Meta:
        unique_together = [('user_id', 'screen')]
        verbose_name = 'default saved view'
