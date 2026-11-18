"""v1.73.17 – API for AI tools (Ask ZEO A9): an AI tool (an assistant, an agent, an MCP client) proposes changes to ZEO with an API key.

    POST /api/v1/ai/changes/            {scope, request, language?}   the change is worked out by Ask ZEO and kept as a DRAFT
    GET  /api/v1/ai/changes/            the drafts and changes of this key  ?state=preview|applied|undone|cancelled|failed
    GET  /api/v1/ai/changes/<id>/       one of them, with its before / after
    POST /api/v1/ai/changes/<id>/withdraw/   the key takes back a draft that was not approved yet

Scopes (Settings › API keys):
    modules:write    custom modules and new fields                     (Ask ZEO "make a … module", "add a field …")
    reports:write    reports                                           ("make a report of …")
    workflow:write   ZEO Developer scripts – reminders and automations ("every day remind … when …")
    settings:write   setup – leave policy lines, data fixes, bulk changes from a sentence

Rules
* Nothing is changed by the API: every proposal is a draft (preview) that a company administrator approves or rejects in
  Ask ZEO › Changes › "Waiting for approval". The key itself can never approve, even through ZEO's other endpoints.
* A proposal outside the key's scope is kept as cancelled ("outside the key's scope"), so it shows in the log.
* Every proposal, approval, rejection and undo is in Ask ZEO › Changes with "API key <name>" and who approved it;
  an applied change can be undone there as any Ask ZEO change.
* The key's user is the user the proposal is worked out for (their rights; modules need a company administrator).
"""
import logging

from django.db.models import Max
from rest_framework.response import Response

from .api_v1 import V1

log = logging.getLogger(__name__)
AI_SCOPES = {   # scope → the Ask ZEO change kinds it may propose
    'modules:write': {'module'},
    'reports:write': {'report'},
    'workflow:write': {'script'},
    'settings:write': {'setup', 'data_fix', 'bulk', 'records'},
}
AI_SCOPE_LIST = (
    ('modules:write', 'Custom modules and fields – propose (an administrator approves)'),
    ('reports:write', 'Reports – propose (an administrator approves)'),
    ('workflow:write', 'Scripts, reminders and automations – propose (an administrator approves)'),
    ('settings:write', 'Setup and data fixes – propose (an administrator approves)'),
)
STATES = ('preview', 'applied', 'undone', 'cancelled', 'failed')


def key_label(ch):
    v = (ch.plan or {}).get('via') or {}
    return v.get('key') or ''


def change_json(ch, lang=None):
    from AskZeo import changes
    out = changes.as_json(ch, lang)
    v = (ch.plan or {}).get('via') or {}
    out['via'] = f"API key {v.get('key')}" if v else 'Ask ZEO'
    out['scope'] = v.get('scope', '')
    out['request'] = v.get('request', '')
    out['approved_by'] = v.get('approved_by', '')
    out['rejected_by'] = v.get('rejected_by', '')
    out['waiting_for_approval'] = ch.state == 'preview'
    card = changes.card(ch, lang)
    out['rows'] = card.get('rows') or []
    return out


class AIChangesView(V1):
    def scope_for(self, request):
        if request.method == 'POST':
            s = str((request.data or {}).get('scope') or '').strip()
            return s if s in AI_SCOPES else 'modules:write'
        return None

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from rest_framework.exceptions import PermissionDenied
        if request.method != 'POST' and not set(self.key.scopes or []) & set(AI_SCOPES):
            raise PermissionDenied('This API key has none of the scopes of AI tools (modules:write, reports:write, workflow:write, settings:write).')

    def get(self, request):
        from AskZeo.models import Change
        qs = Change.objects.filter(plan__via__api_key=self.key.pk).select_related('user', 'undone_by')
        st = request.query_params.get('state')
        if st:
            if st not in STATES:
                return self.bad(f'state is one of {", ".join(STATES)}.')
            qs = qs.filter(state=st)
        return self.page(request, qs, change_json)

    def post(self, request):
        from AskZeo import engine
        from AskZeo import scope as S
        from AskZeo.models import AskSettings, AskUsage, Change, Conversation, Message
        from AskZeo.words import has_arabic
        d = request.data or {}
        scope = str(d.get('scope') or '').strip()
        if scope not in AI_SCOPES:
            return self.bad(f'scope is one of {", ".join(AI_SCOPES)}.')
        text = str(d.get('request') or '').strip()
        if not text:
            return self.bad('request: say the change, e.g. "Make a vehicle request module with vehicle type, from date and to date, approved by HR".')
        if len(text) > 2000:
            return self.bad('request: at most 2000 characters.')
        st = AskSettings.get()
        if not st.enabled:
            return self.bad('Ask ZEO is switched off in this company (Settings › Ask ZEO).', 403)
        lang = 'ar' if has_arabic(text) or d.get('language') == 'ar' else 'en'
        conv = Conversation.objects.create(user=request.user, title=f'API key {self.key.name}: {text}'[:200], suite='api')
        Message.objects.create(conversation=conv, role='user', text=text, language=lang, data={'api_key': self.key.name, 'scope': scope})
        last = Change.objects.aggregate(m=Max('id'))['m'] or 0
        try:
            a = engine.answer(request, conv, text, (), '', '', lang)
        except S.NoCompany:
            return self.bad('Add ?schema=<company> to the call.')
        except Exception:   # noqa: BLE001
            log.exception('API for AI tools: Ask ZEO failed')
            return self.bad('Something went wrong while working out the change. Say it another way.', 500)
        Message.objects.create(conversation=conv, role='assistant', text=a.get('text') or '', intent=a.get('intent') or '',
                               source=a.get('source') or '', mode=a.get('mode') or 'builtin', language=lang,
                               data={k: a[k] for k in ('cards', 'links') if a.get(k)})
        if not a.get('usage_logged'):
            AskUsage.objects.create(user=request.user, feature='api_ai', mode=a.get('mode') or 'builtin')
        made = list(Change.objects.filter(conversation=conv, pk__gt=last, state='preview'))
        allowed = AI_SCOPES[scope]
        out = []
        for ch in made:
            ch.plan = dict(ch.plan or {}, via={'api_key': self.key.pk, 'key': self.key.name, 'scope': scope, 'request': text[:500]})
            if ch.kind not in allowed:
                ch.state, ch.error = 'cancelled', f'Outside the key\'s scope {scope} (this is a {ch.kind} change).'
            ch.save(update_fields=['plan', 'state', 'error'])
            out.append(change_json(ch, lang))
        waiting = [x for x in out if x['state'] == 'preview']
        if not waiting:
            why = 'No change was proposed.' if not out else f'The change asked for is outside the key\'s scope {scope}.'
            return Response({'detail': why, 'answer': a.get('text') or '', 'changes': out}, status=422)
        return Response({'detail': f'{len(waiting)} change(s) waiting for an administrator to approve them in Ask ZEO › Changes.',
                         'answer': a.get('text') or '', 'changes': out,
                         'approve_in': '/main-sidebar/ask-zeo/changes'}, status=201)


class AIChangeView(V1):
    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from rest_framework.exceptions import PermissionDenied
        if not set(self.key.scopes or []) & set(AI_SCOPES):
            raise PermissionDenied('This API key has none of the scopes of AI tools (modules:write, reports:write, workflow:write, settings:write).')

    def _one(self, pk):
        from AskZeo.models import Change
        return Change.objects.filter(pk=pk, plan__via__api_key=self.key.pk).first()

    def get(self, request, pk):
        ch = self._one(pk)
        if ch is None:
            return self.bad('Not found.', 404)
        return Response(change_json(ch))

    def post(self, request, pk, action):
        ch = self._one(pk)
        if ch is None or action != 'withdraw':
            return self.bad('Not found.', 404)
        if ch.state != 'preview':
            return self.bad(f'Only a draft can be withdrawn; this change is {ch.state}.', 409)
        ch.state, ch.error = 'cancelled', 'Withdrawn by the API key.'
        ch.save(update_fields=['state', 'error'])
        return Response(change_json(ch))
