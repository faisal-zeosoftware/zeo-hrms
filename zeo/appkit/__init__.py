"""ZEO app kit (v1.18.0) – shared engine for the business apps that are sold separately but connect when installed
together (CRM, Property, Maintenance …).

Every app built on the kit gets:
* activation per app: the app asks for its industry when it is first opened; the company master industry is the
  default and the manager is asked whether to change it (General = no industry pack),
* industry packs (configuration rows marked "to review" until a manager reviews them; packs only ADD),
* roles as permissions on the app's Settings model (groups "<App> – <role>"; company administrators have all),
* a generic register API (list / create / detail / change / workflow actions / files / history / options / export),
* links to the other kit apps: a link works only when the other app is installed AND activated in the company,
  and the other app's records are kept as plain ids, never foreign keys.

The kit lives in the project package (zeo/) so it is always present; each app still installs and works alone.
"""
