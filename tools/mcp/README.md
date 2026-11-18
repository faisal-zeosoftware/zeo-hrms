# API for AI tools (ZEO HRMS 1.73.17)

An AI tool – an assistant, an agent, or any app that speaks MCP (Model Context Protocol) – can read ZEO and **propose**
changes to it. ZEO never lets the tool change anything by itself:

1. The tool sends the change in plain words, for example *"Make a vehicle request module with vehicle type (car, van or bus),
   from date, to date and purpose, approved by the line manager then HR"*.
2. ZEO works it out the way Ask ZEO does and keeps it as a **draft**, with every value before and after.
3. A company administrator opens **Settings › Ask ZEO › Waiting for approval** and presses **Approve** or **Reject**.
4. Every draft, approval, rejection and undo is listed in **Settings › Ask ZEO › Changes made via Ask ZEO** with the API key's
   name and who approved it. An applied change can be undone there.

An API key can never approve or undo a change, also not through ZEO's other addresses.

## 1. Make an API key

Settings › API keys › New key. Choose the user the key works for (a staff user; for modules, a company administrator) and
the scopes:

| Scope | The tool may |
|---|---|
| `employees:read`, `org:read`, `leave:read`, `attendance:read`, `payroll:read` | read with API v1 (as before) |
| `modules:write` | propose custom modules and new fields |
| `reports:write` | propose reports |
| `workflow:write` | propose ZEO Developer scripts: reminders and automations |
| `settings:write` | propose setup (e.g. leave policy lines), data fixes and changes from a sentence |

A proposal outside the key's scope is kept as *cancelled – outside the key's scope*, so it still shows in the log.
Ask ZEO must be switched on (Settings › Ask ZEO).

Keep the key in the AI tool's own secret settings. Do not put it in a shared file or a chat.

## 2. The API

Every call: header `Authorization: Api-Key zeo_…` and `?schema=<company>`.

| Call | What it does |
|---|---|
| `POST /api/v1/ai/changes/` `{"scope": "modules:write", "request": "…", "language": "en"}` | Works out the change and keeps it as a draft. **201** with the draft(s) (`changes[]`: id, title, state `preview`, rows of before / after), or **422** when no change came out of the request (the answer says why). |
| `GET /api/v1/ai/changes/?state=preview` | The drafts and changes of this key, newest first (paged). States: `preview` (waiting), `applied`, `undone`, `cancelled` (rejected or withdrawn), `failed`. |
| `GET /api/v1/ai/changes/<id>/` | One change: state, `approved_by`, `rejected_by`, before / after. |
| `POST /api/v1/ai/changes/<id>/withdraw/` | The key takes back a draft that was not approved yet. |

Example:

```bash
curl -s -X POST "https://hr.example.com/api/v1/ai/changes/?schema=acme" \
  -H "Authorization: Api-Key $ZEO_API_KEY" -H "Content-Type: application/json" \
  -d '{"scope": "reports:write", "request": "Make a report of employees with their visa expiry date, by branch"}'
```

## 3. The MCP connector

`tools/mcp/zeo_mcp.py` is a small MCP server (Python 3.8+, no packages) that runs on the computer of the AI tool and talks
to ZEO with the key. Its tools: `zeo_about`, `zeo_read`, `zeo_propose_change`, `zeo_changes`, `zeo_change`,
`zeo_withdraw_change`.

Add it to the AI tool's MCP settings, for example:

```json
{
  "mcpServers": {
    "zeo": {
      "command": "python3",
      "args": ["/path/to/zeo_mcp.py"],
      "env": {"ZEO_URL": "https://hr.example.com", "ZEO_COMPANY": "acme", "ZEO_API_KEY": "zeo_…"}
    }
  }
}
```

Try it without an AI tool:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | ZEO_URL=https://hr.example.com ZEO_COMPANY=acme ZEO_API_KEY=zeo_… python3 zeo_mcp.py
```

## 4. What the administrator sees

Settings › Ask ZEO › **Waiting for approval**: the request in the tool's words, the key, the scope, and each record and
value that will change. **Approve** makes the change as the administrator (the screens' own checks run as usual);
**Reject** keeps it in the log as rejected. After approval the change is in *Changes made via Ask ZEO* with **Undo**.
