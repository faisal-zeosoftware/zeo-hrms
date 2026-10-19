"""python manage.py zeoassets_setup [--schema X] [--roles] [--activate [--industry general]] [--daily]"""
from zeo.appkit.command import make_command

Command = make_command('ZeoAssets')
