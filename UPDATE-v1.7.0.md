# ZEO HRMS – update package, version 1.7.0 (7 October 2026)

This package holds **every change made so far (versions 1.1.0 to 1.7.0)**. Apply it to the code as received (1.0.0).
After deploying, the version is shown at the bottom of the main menu, under Logout: **"Version 1.7.0"**.

## Version history

| Version | Date | What |
|---|---|---|
| 1.0.0 | – | Code as received |
| 1.1.0 | Sep 2026 | Performance, Recruitment and Learning modules |
| 1.2.0 | Sep 2026 | 24 fixes from the "all modules" test |
| 1.3.0 | Oct 2026 | Manager and ESS dashboards with drill-down |
| 1.4.0 | Oct 2026 | User rights by company / branch, one list screen everywhere, import / export, duplicate checks (sections 1–4) |
| 1.5.0 | Oct 2026 | Employee form designer fixes (section 6) |
| 1.6.0 | 7 Oct 2026 | Menu rearranged, Dashboard freeze fix, Logout visible, version shown (section 7) |
| **1.7.0** | **7 Oct 2026** | **Notes / activities / files / history on every form, form designer for every screen, list views and column chooser, dashboards, org chart, approval and report fixes (section 8)** |

The version is set in `frontend/hrms-project/src/app/shared-ui/version.ts` and in the `VERSION` file of this package. Raise both with every new update.

1. **New modules:** Performance, Recruitment and Learning.
2. **Runtime fixes:** the 24 fixes from the "all modules" test.
3. **Dashboards:** Manager and ESS dashboards with drill-down.
4. **Rights and lists (1.4.0):**
   - user rights at company, branch and role level;
   - the same list screen everywhere: search, filters, group by, footer, Excel / CSV / PDF export and import;
   - duplicate checks;
   - picking employees by Branch / Department / Designation / Category.
5. **Form designer (1.5.0)** and **menu (1.6.0):** sections 6 and 7.
6. **Every screen like one app (1.7.0):** section 8.

Folders:

| Path | What |
|---|---|
| `backend/` | Changed and new Django files, in their project paths (copy over the project) |
| `frontend/hrms-project/` | Changed and new Angular files, in their project paths |
| `zeo-hrms-backend-full.patch`, `zeo-frontend-full.patch` | The same changes as `git apply` patches |

Test-only files are **not** included: the local settings file, locally generated `0001/0002_initial` migrations of the existing apps, and `media/`.

---

## 1. User rights – what was wrong and what is fixed

Tested on a local copy with two companies, two branches and five kinds of user:

- company admin;
- HR limited to the Sharjah branch;
- HR limited to the Dubai branch;
- payroll user;
- ESS employee or manager.

| Check | Before | After |
|---|---|---|
| No login | 94 lists answered without any token (employees, salaries, payslips, loans, users) | 401 everywhere except login / OTP / password reset |
| Another company | A user of company B read all employees and payslips of company A by changing `?schema=` | 403 "You do not have access to this company." |
| Branch limits | "User branch access" was saved but never applied – Dubai HR saw all 21 employees and 42 payslips | Dubai HR sees 5 employees / 10 payslips; Sharjah HR 16 / 32 |
| ESS employee | Could read every payslip, salary, the user list and permission groups | Own records only; approvals assigned to them; team leave / attendance for managers |
| ESS writes | Could raise a request for a colleague, and approve someone else's approval | 403 "You can only raise requests for yourself."; approvals only by the approver (others get 404) |
| Branch writes | Dubai HR could move an employee to Sharjah or create records in Sharjah | 403 "You can only work with records of your own branches." |
| Master data | ESS could edit leave types and other settings through the API | Needs the add / change / delete permission of that screen |
| Company / branch picker | Every user (even without login) got all companies and branches | Only the user's companies, and only their branches |
| Main menu | Every module shown to every user | Modules without any view permission are hidden (admins see all) |

**How it works:** a new app, `AccessControl`, puts one check in front of every API.

- It is added to each view's own permission classes, and every list / detail / update / delete is filtered.
- Company admins (superuser or company superuser) keep full access.
- HR users with a view permission see the branches set in **Settings → Branch permissions** (User branch access).
  - With no branch set, an HR user sees nothing. This matches how the branch picker already worked.
  - ESS users fall back to their own branch.
- Users without the view permission see:
  - their own records;
  - rows where they are the approver, delegate or user;
  - for managers, their team's leave and attendance.
- Reference lists (leave types, departments …) stay readable for everyone, limited to their branches.
- A view can opt out with `zeo_access = False` / `zeo_scope = False` when it applies its own rules.

The before / after run on all 345 list APIs is in the PPT. The company admin's results are **identical** before and after.

## 2. One list screen everywhere

`shared-ui/z-list.directive.ts` attaches itself to every data table, so each screen keeps its own buttons, forms and actions. A table can opt out with the `zPlain` attribute.

**Search**
- Type a word, then choose "Search *column* for …" (any column, or Branch / Department / Designation / Category).
- Each search becomes a removable chip.

**Filters**
- Branch, Department, Designation and Category on every list with employees. They are worked out from the employee code or name, even when the table has no such column.
- Status-like columns, with counts.
- Date periods: Today, This week, This month, Last month, This year, or a custom range.
- A custom filter: contains / does not contain / = / > / < / is set / is not set.

**Group by**
- One or two levels.
- Groups start collapsed and show a row count and totals of the number columns.
- Expand all / Collapse all.

**Sorting:** click a column title.

**Footer**
- Select all (every filtered row, not just the page).
- Shows how many rows are selected, with Export selected and Clear.
- **Rows per page 50 (default) / 100 / 500 / 1000**, remembered per screen.
- Previous / Next.

**Export**
- **Excel, CSV (UTF-8) and PDF**, of the filtered rows or only the selected rows.
- Employee lists also get Branch / Department / Designation / Category columns.

**Import**
- Download the template, in Excel (with a "How to fill" sheet) or CSV.
- Choose a file. Every row is **checked first**: duplicates in the system or in the file, unknown branch or department, required columns.
- Only valid rows are imported, through the screen's own API, so rights, workflows and numbering still apply.
- Linked columns accept the code or the name.
- Screens that already had their own bulk upload keep it, inside the same Import menu.
- Employees without HR rights do not see Import.

**Same look**
- Purple header row, the same pill colours for status words (approved / pending / rejected …), and the same toolbar and footer, as on the Performance and Learning screens.
- 59 screens that paged themselves 4–10 rows at a time now hand paging to the footer.
- The employee list opens in table view; the card view is still one click away.

## 3. Duplicate records

`DataTools/duplicates.py` checks every save of master data.

- Matching ignores upper / lower case and extra spaces.
- Scope is per company or per branch. The same department name is allowed in a different branch.
- The message names the record and branch, for example *Department name "SALES" already exists in Adept Business Solutions FZE.* It shows on the screen's own error message and as a notice at the bottom right.
- Covered: employee code and e-mails, department / designation / category names and codes, branches, leave types, shifts, calendars, policies, salary components and structures, loan / asset / request / document types, asset serial numbers, bank account and document numbers, projects, KPIs, courses, candidates, job codes.
- To add more, add a line to `RULES`.

## 4. Assign by Branch / Department / Designation / Category

- Every multi-select dropdown that lists employees gets a **"Pick employees by"** bar. Choose Branch, Department, Designation and / or Category, then **Add matching** or **Only matching**.
- This applies to shift, holiday and weekend assignment, policies, allocations, project members, nominations and so on.
- Screens that already had these four filters keep them.

## 5. Dashboards

See the previous package. Included here unchanged: the ESS "My dashboard", the manager "My team" view and the HR "Manager Dashboard" with drill-down.

## 6. Employee form designer – review and fixes

| Problem found | Fix |
|---|---|
| Labels, Mandatory ticks, hide/show and dropdown values of the standard fields were saved **only in the browser that made them**. Other HR users, other PCs and other companies never saw the design, and clearing the browser lost it. | Saved per company on the server (`DataTools.FormSetting`, `GET/PUT /tools/api/form-settings/`). Every browser loads it when the app opens; the designer saves on every change. The first time an admin opens the app after the upgrade, a design that exists only in their browser is moved to the server automatically. |
| "Mandatory field" on a custom field was ignored: the database had no column for it. | Stored and returned as `mandatory`. The create form, the edit dialog and the details page now block saving until it is filled. |
| Renaming a custom field disconnected the values already entered; deleting one left orphan values that came back when a field with the same name was added again. | A rename moves the values to the new name, and a delete removes them (all 5 forms: employee, family, qualification, job history, documents). |
| A value for a field that does not exist crashed the server (500). | Clear 400 message. |
| "Has Car" and "has car" could both be created. | Field names are unique, ignoring case. |
| Checkbox fields could not be created from the designer (the database needs an option list). | Yes / No added automatically; "Checkbox (Yes / No)" added to the designer's data types. |
| Edit dialog: custom values were posted to a URL that does not exist (404, error only in the console). Fields added after the employee was created never appeared. Dropdowns showed as free text. | Correct URL. Every designed field is shown with the right input (dropdown, radio, date, checkbox, text). |
| Details page (inline edit): employee custom fields were shown but **never saved**. Qualification, job history and document custom values were written to the **family** table. The page reloaded before the tab saves finished, so family / qualification / bank / job history / document edits could be lost silently. | Each value goes to its own table and the employee's custom fields are saved. The page reloads only after every save has finished, and any failure is shown. |
| Server: updating a qualification, job history or document from the details page never worked. The handler passed itself instead of the record, and qualification / job history wrote the employee into the qualification name. Each update also needed fields the screen does not send. | Fixed in `EmpManagement/views.py`. The record from the URL is updated with only the fields sent. |
| Typos: "Catogory", "Comfirmation Date", "mother Name", "Feild Name". | Corrected. |

Still as before (suggestions):
- The hide/show setting applies to the create and edit forms. The details page still lists every field.
- Family / qualification / job history / document custom fields show only values already entered. The employee-level fields now show all designed fields.
- The designer has no field order or sections, no preview, and no number / e-mail / file field types. Adding these needs a database change.

## 7. Menu rearranged (1.6.0)

The main menu and all module menus now come from **one file**, `src/app/shared-ui/menu-config.ts`. To move, rename or hide an item, edit that file; the main menu and every module menu follow. **No screen address changed**, so bookmarks and notification links still work.

**Main menu:** 18 links became 15, under 7 headings.

| Heading | Modules |
|---|---|
| Overview | Dashboard, Manager Dashboard |
| People | Employees, Requests |
| Time | Time & Attendance, Leave |
| Pay | Payroll, Loans & Benefits |
| Operations | Assets, Projects |
| Talent | Performance, Recruitment, Learning |
| Admin | Reports, Settings |

- Merged: Shift Management → Time & Attendance; Air Ticket → Loans & Benefits; Document Management → Employees; General Management → Requests.
- Each link opens the first **daily** screen the user may see (Leave requests, Payroll runs, Loan requests, Asset register …), no longer a master such as Leave Type or Loan Type.

**Module menus:** each module is split into **Daily work**, **Approvals**, **Planning** (Time & Attendance only) and **Setup**.
- Setup is folded and shows how many screens it holds. It opens by itself when you are on one of its screens.
- 13 screens moved to the module where they are used:
  - weekend / holiday calendars and their assignment → Time & Attendance;
  - employee document types, the employee form designer, company policy and announcements → Employees;
  - the asset form designer → Assets;
  - advance salary requests, approvals, levels and escalation → Loans & Benefits.
- Settings keeps only company-wide setup: Company, Branches, States, Document numbering, Expiry alerts, Users & rights, E-mail (10 templates folded).

**Rights:** every item keeps the view permission the old menus checked. Headings and modules with nothing the user may open are hidden; company admins see everything.

**Tested:** all 136 menu pages opened, each with the right module menu and the item highlighted; 0 page errors. Also checked as Dubai branch HR and as an employee (ESS).

**Also fixed in 1.6.0 (both were in the code as received):**

| Problem | Fix |
|---|---|
| The Dashboard updated its greeting every 0.1 s (the comment said once a minute) and never stopped after you left it, so the whole app kept redrawing. If attendance figures arrived after you left, its chart retried on every frame. Going from the Dashboard straight to Reports froze the browser. | Greeting once a minute. Timers, subscriptions and the chart are stopped when the Dashboard is left (`dashboard-contents.component.ts`). |
| The main menu was as tall as the whole screen although it starts below the 64 px top bar, so Logout was partly hidden. | Main menu height fixed (`main-sidebar.component.css`). |

Files:
- **New:** `shared-ui/menu-config.ts`, `shared-ui/z-module-menu.component.ts`, `shared-ui/version.ts`.
- **Changed:**
  - `main-sidebar` (`.html` / `.ts` / `.css`);
  - the 12 module menus: sub-sidebar, leave-options, salary-options, loan-sidebar, asset-options, general-sidebar, attendance-sidebar, project-options, air-ticket-options, shift-options, report-options, settings. Their menu list was replaced by `<z-module-menu>`;
  - `app.module.ts`;
  - `dashboard-contents.component.ts`.

The old permission flags in the module menu components are no longer used by their templates. They were left in place, so nothing else breaks.

## 8. Every screen works like one app (1.7.0)

### On every form (create and edit)
A panel at the bottom of each screen's own form, above Save:
- **Log note** – notes on the record, with files.
- **Schedule activity** – to-do, call, meeting, e-mail, upload document, approval, follow-up; due date, assignee, note. *Mark done* writes a note to the history.
- **Files** – drop or pick (25 MB each); a file downloads only for users who may see its record.
- **History** – every create, change and delete, with user, time, IP and old → new values. Recorded by the server on every save (also imports and API calls).
- **My To-do** (main menu and the tick in the top bar) – activities assigned to me, planned by me and done, grouped as overdue / today / planned; *Open* goes to the record.

Checked on 134 form pages (87 create and 69 edit forms show the panel). New records show it after the first save.

### Form designer on every screen
- *Design this form* on any form: extra fields with **20 types** (text, long text, whole / decimal number, amount, percentage, date, date-time, time, yes/no, dropdown, multi-select, radio, e-mail, phone, web link, file, employee, rating, colour), sections, order (drag or arrows), required, help text, default, list column, active, and a live preview.
- Extra fields show in the form, as list columns, in exports and in the import template. Required fields are checked before the screen saves.
- **Employee form designer:** the same 20 types in the employee, family, qualification, job history and document designers; fields hidden in the designer no longer show on the details page; child tabs show every custom field, also when empty; sections and order are followed on create, edit and details.

### Lists
- **Columns** – show / hide columns on every grid; saved per user and screen; export leaves hidden columns out.
- **Views** – List, Kanban, Calendar, Graph and Pivot wherever the data allows (Calendar needs a date column). Pivot exports to Excel.
- **One search and one Export per screen** – screens with their own search box or Export button (Employee master and others) keep only the list bar's.
- **Import templates** – every Import offers an Excel / CSV template with all columns, including form designer fields; required columns end with `*`.

### Dashboards, menu and people
- **Dashboard** – *Company at a glance*: people, leave, attendance, payroll, loans and benefits, assets, documents, requests, exits, recruitment, performance, learning. Each card only for users with that right. **My space** for employees: leave balances, **my assets**, pay, documents, requests.
- **Customize** – hide cards from the dashboard itself, show them again from the bar; saved per user (main and ESS dashboards).
- **Main menu folded** to icons by default; the button at its top unfolds it and the choice is remembered. The dashboard now uses the full width.
- **Organisation chart** – built from each employee's reporting manager; search, branch / department filter, expand / collapse, zoom, print.

### Reports checked with data
11 reports opened with the test data. Fixed: the **Asset transaction report** was empty and showed a stray “>” before every value; the Designation / Department reports got the list bar; saved report layouts are now kept **per company and report** in `media/reports/<company>/…` (before, all companies shared one file and some modules overwrote each other's).

### Approval hierarchy
Fixed:
| Where | Problem | Now |
|---|---|---|
| All modules | Anyone with access could approve a step, also their own request, or set `status` directly | Only the approver or delegate (admins too), never one's own request; status changes only through Approve / Reject |
| Leave | 2-level leave approved after level 1; reject crashed | Pending until the last level; reject works |
| Leave | Compensatory-leave steps in the leave approval list | Leave requests only |
| Loans | Common workflow ignored; no levels = instant approval | Common workflow, level by level |
| Advance salary | No escalation from level 2 on | Scheduled for every level |
| General request | Workflow not found by branch; common workflow ignored | Branch first, then common workflow |
| Payslips | Bulk approve / reject touched slips not waiting for the user | Only the user's slips |
| Escalation jobs | Wrong filters; crash when a level had no escalation days | Fixed / skipped |

Open points (not changed – decide first):
- No workflow set up → the request is approved at once without notice.
- Some modules stay pending forever when a level has no approver.
- The approval "role" is a label; the approver is always a named user.
- Leave and general requests never schedule escalation.
- Status values in mixed case (Approved / approved / APPROVED).
- Approving late-in / early-out does not change attendance or payroll.
- A payroll run has no maker-checker step.

Suggested new approval flows: overtime; attendance recheck / manual entry; shift override; employee data, bank and salary changes; salary revision; end of service / exit clearance; leave encashment; employee-initiated leave cancellation; rejoin; payroll run release; asset return; probation confirmation; contract / visa renewal.

### Files
- **Backend new:** `Chatter/` (app with migration), `DashboardManagement/overview.py`, `AccessControl/approvals.py`, `zeo/report_files.py`.
- **Backend changed:** `zeo/settings.py`, `zeo/urls.py`, `AccessControl/access.py`, `DataTools/views.py`, `DataTools/forms.py`, `DashboardManagement/views.py`, `EmpManagement/models.py` / `views.py`, `calendars/models.py` / `views.py` / `tasks.py`, `PayrollManagement/models.py` / `views.py` / `utils.py` / `tasks.py`, `OrganisationManager/views.py` / `tasks.py`.
- **Frontend new (`src/app/shared-ui/`):** `field-types.ts`, `z-field-input`, `z-field-designer`, `z-record.service`, `z-record-panel`, `z-record-injector.service`, `z-todo`, `z-org-chart`, `z-dash`, `z-emp-fields`, `emp-field-adapter.ts`, `z-views.ts`; `src/zrecord.css`.
- **Frontend changed:** `app.module.ts`, `app-routing.module.ts`, `app.component.ts`, `main-sidebar`, `dashboard-contents`, `ess-dashboard`, `from-designer`, `employee-edit`, `create-employee`, `employee-details`, `asset-transaction-report`, `z-list.directive.ts`, `z-list.service.ts`, `menu-config.ts`, `version.ts`, `angular.json`.

---

## Deploy

### Backend
1. Copy `backend/` over the project, or apply `zeo-hrms-backend-full.patch`.
2. `zeo/settings.py` → add these to `TENANT_APPS` (already in the copied file):
   - `'PerformanceManagement', 'RecruitmentManagement', 'LearningManagement', 'DashboardManagement', 'AccessControl', 'DataTools', 'Chatter'`
   - `MIDDLEWARE`: `'Chatter.tracking.CurrentRequestMiddleware'` after the tenant timezone middleware (records who made each change).
3. `zeo/urls.py` → `performance/`, `recruitment/`, `learning/`, `dashboard/`, `tools/`, `chatter/` (already in the copied file).
4. Run migrations: `python manage.py migrate_schemas`. The three new modules and `DataTools` (form designer settings) have tables; `Chatter` (notes, activities, files, history, extra fields, layouts) has tables; `AccessControl` and `DashboardManagement` have none. The new field types in `EmpManagement` are choices only – no table change (`makemigrations` is optional).
   - Saved report layouts now go to `media/reports/<company schema>/`. Old files are not used; each report creates its file again on first open.
5. Once: `python manage.py create_default_email_templates`.
6. Restart the API (gunicorn / uwsgi) and Celery.
7. **Before go-live:** give each HR user their branches in **Settings → Branch permissions**. HR users without any branch see empty lists. Company admins are not affected.

New endpoints:

| Endpoint | Use |
|---|---|
| `GET /tools/api/directory/` | Light employee list with branch / department / designation / category, filtered by the user's rights |
| `GET /tools/api/fields/?endpoint=<list API>` | Import template columns |
| `POST /tools/api/import/` | `{endpoint, rows, dry_run}` – check (`dry_run: true`) or import |

### Frontend
1. Copy `frontend/hrms-project/` over the project, or apply `zeo-frontend-full.patch`.
2. `npm install`. This adds `jspdf-autotable`, which is used for PDF export.
3. `ng build`. `angular.json` now also loads `src/zlist.css` and `src/zrecord.css`.

### Check after deploying
- Log in as an HR user limited to one branch: the employee list shows only that branch, and the branch picker offers only that branch.
- Open any list, then:
  - search;
  - add a Department filter;
  - group by Department;
  - set rows per page to 100;
  - select all;
  - export to Excel, CSV and PDF.
- Department → Create with an existing name: you should see the duplicate message.
- Department → Import → template → fill two rows → check → import.
- The bottom of the main menu shows **Version 1.7.0**.
- Open a leave request: the panel shows under the form. Add a note, an activity for yourself and a file; the tick in the top bar counts the activity; My To-do lists it.
- *Design this form* → add a dropdown field → it shows in the form, the list (Columns) and the import template.
- On the Dashboard press Customize, hide a card, Done, reload: it stays hidden.
- Organisation chart opens with the managers at the top.
- Open Leave, Payroll and Time & Attendance: each opens on its daily screen, and Setup unfolds when clicked.
- Go from the Dashboard to Reports: the page opens normally.

## Still to do (not changed)
- **Security:** uploaded files and reports under `/media/` are served without login. Serve media through an authenticated view or the web server with access checks.
- **Security:** `zeo/settings.py` still has a personal Gmail address and app password as the fallback mail sender. Move them to environment variables and change that password.
- The approval inboxes in the ESS portal are card lists, not tables. They keep their current layout, without the shared list bar.
- The PDF export uses a standard font, so Arabic text is exported correctly to Excel and CSV but not to PDF.

## Test accounts (local copy only)
`tester` (admin), `hr.sharjah`, `hr.dubai` (branch HR), `payroll.clerk`, `rahul.pillai` (manager), `priya.nair` (employee), `gulf.admin` (second company).
