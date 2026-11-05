"""v1.61.0 – python manage.py zeo_backup: a backup of the database(s) and the uploaded files.

    python manage.py zeo_backup                       every company + the files, into ZEO_BACKUP_DIR (or <project>/backups)
    python manage.py zeo_backup --dir /srv/backups    another folder
    python manage.py zeo_backup --schema acme         one company only (its schema; no files)
    python manage.py zeo_backup --no-media            the database only
    python manage.py zeo_backup --keep 14             delete backups made by this command older than 14 days (default 14, 0 = keep all)
    python manage.py zeo_backup --check <folder>      check a backup: checksums and the database archive can be read

Each run makes a folder backup-YYYYMMDD-HHMMSS with
    db-<alias>.dump      pg_dump custom format (-Fc) of each PostgreSQL database in DATABASES (own databases of ZEO Cloud too)
    media.tar.gz         the uploaded files (MEDIA_ROOT)
    manifest.json        ZEO version, time, sizes, SHA-256 of every file
and writes last-backup.json in the backup folder (System health › Last backup reads it; set ZEO_BACKUP_DIR).
Schedule it nightly with cron, e.g.  30 1 * * *  cd /srv/zeo && venv/bin/python manage.py zeo_backup >> backup.log 2>&1
Copy the folder to another machine or to cloud storage – a backup on the same disk does not survive a disk failure.

v1.62.0 – encrypted and off the server:
    python manage.py zeo_backup --new-key             print a new key → ZEO_BACKUP_KEY in .env (keep a copy OUTSIDE the server)
    with ZEO_BACKUP_KEY set every backup is encrypted (AES-256-GCM): db-*.dump.enc, media.tar.gz.enc; the plain files are
    removed after the check. --no-encrypt makes a plain backup anyway.
    python manage.py zeo_backup --decrypt <folder> [--out <dir>]   the plain files back (for a restore), checked against the manifest
    --copy-to <dir> (or ZEO_BACKUP_COPY_TO)  copy the new backup to another disk / a mounted network share, and check the copy
    ZEO_BACKUP_COPY_CMD="rclone copy {folder} offsite:zeo/{name}"  any copy command (rclone to S3 / Azure / B2 with object lock,
    rsync over SSH …) run after each backup; {folder} and {name} are filled in. The result is in last-backup.json.

v1.67.1 (companies) – the backup made before a company or an own database is deleted:
    --db <key>          one own database only (with --schema: the company schema in that database)
    --company-files     with --schema / --db: also the uploaded files of the company(ies) (files.tar.gz)
    --tag <text>        added to the folder name (backup-YYYYMMDD-HHMMSS-<tag>)
    --no-status         do not write last-backup.json (System health keeps showing the nightly backup)

Restore (by hand, on purpose): stop gunicorn and Celery; createdb a new empty database; pg_restore --no-owner -d <newdb> db-default.dump;
point .env to it; tar xzf media.tar.gz -C <project>; start again. See README-UPDATE section 82.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

PREFIX = 'backup-'


def _tool(name):
    for d in (getattr(settings, 'ZEO_PG_BIN', ''), '/usr/lib/postgresql/16/bin', '/usr/bin', '/usr/local/bin'):
        if d and os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return name


def _pg(p):
    a, env = [], dict(os.environ)
    if p.get('HOST'):
        a += ['-h', str(p['HOST'])]
    if p.get('PORT'):
        a += ['-p', str(p['PORT'])]
    if p.get('USER'):
        a += ['-U', str(p['USER'])]
    if p.get('PASSWORD'):
        env['PGPASSWORD'] = str(p['PASSWORD'])
    return a, env


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def databases():
    """[(name, settings)] – the main database and each own database of ZEO Cloud (ready), each physical database once."""
    from django.db import connections
    seen, out = set(), []

    def add(alias, p):
        k = (p.get('HOST') or '', str(p.get('PORT') or ''), p.get('NAME'))
        if p.get('NAME') and k not in seen:
            seen.add(k)
            out.append((alias, p))
    try:
        from ZeoCloud import owndb
        from ZeoCloud.models import DatabaseServer
        add('default', owndb._params(''))
        for s in DatabaseServer.objects.filter(state='ready').order_by('key'):
            try:
                add(s.key, owndb._params(s.key))
            except Exception:
                pass
    except Exception:
        pass
    for alias, p in settings.DATABASES.items():
        try:
            if connections[alias].vendor == 'postgresql':
                add(alias, p)
        except Exception:
            continue
    return out


def check(folder, key=None):
    """[] when the backup is sound, else the problems. Encrypted files: the checksum always; the content (every piece's tag
    and the plain checksum) when the key is given."""
    problems = []
    mf = os.path.join(folder, 'manifest.json')
    if not os.path.exists(mf):
        return ['manifest.json is missing']
    m = json.load(open(mf))
    for f in m.get('files', []):
        p = os.path.join(folder, f['name'])
        if not os.path.exists(p):
            problems.append(f'{f["name"]} is missing')
            continue
        if sha256(p) != f['sha256']:
            problems.append(f'{f["name"]} is damaged (checksum differs)')
            continue
        if f['name'].endswith('.enc'):
            if key:
                from ZeoAdmin.backupcrypt import decrypt_stream
                try:
                    if decrypt_stream(p, key) != f.get('plain_sha256'):
                        problems.append(f'{f["name"]}: the decrypted content does not match the manifest')
                except Exception as e:
                    problems.append(f'{f["name"]} cannot be decrypted: {e}')
            continue
        if f['name'].endswith('.dump'):
            r = subprocess.run([_tool('pg_restore'), '--list', p], capture_output=True, text=True)
            if r.returncode != 0 or 'TABLE' not in r.stdout:
                problems.append(f'{f["name"]} cannot be read by pg_restore')
        elif f['name'].endswith('.tar.gz'):
            try:
                with tarfile.open(p, 'r:gz') as t:
                    t.getmembers()
            except Exception as e:
                problems.append(f'{f["name"]} cannot be read: {e}')
    return problems


def _company_files(folder, schema, db_key):
    """v1.67.1 (companies): files.tar.gz with the uploaded files of one company (or of every company of an own database):
    the files registered for it (ZeoCloud.MediaFile), its logo and a media/<schema>/ folder when there is one. None = no file."""
    from django.conf import settings as st
    media = str(getattr(st, 'MEDIA_ROOT', '') or '')
    if not media or not os.path.isdir(media):
        return None
    schemas = [schema] if schema else []
    if not schemas and db_key:
        from ZeoCloud.models import AccountCompany
        schemas = list(AccountCompany.objects.filter(db_alias=db_key).values_list('company__schema_name', flat=True))
    if not schemas:
        return None
    from ZeoCloud.models import MediaFile
    from UserManagement.models import company as Company
    paths = set(MediaFile.objects.filter(schema__in=schemas).values_list('path', flat=True))
    for c in Company.objects.filter(schema_name__in=schemas):
        if c.logo:
            paths.add(c.logo.name)
    root = os.path.realpath(media)
    found = []
    for pth in sorted(paths):
        full = os.path.realpath(os.path.join(root, pth))
        if full.startswith(root + os.sep) and os.path.isfile(full):
            found.append((full, os.path.relpath(full, root)))
    for sch in schemas:
        d = os.path.join(root, sch)
        if os.path.isdir(d):
            for base, _dirs, names in os.walk(d):
                for nm in names:
                    full = os.path.join(base, nm)
                    found.append((full, os.path.relpath(full, root)))
    if not found:
        return None
    seen = set()
    with tarfile.open(os.path.join(folder, 'files.tar.gz'), 'w:gz') as t:
        for full, rel in found:
            if rel not in seen:
                seen.add(rel)
                t.add(full, arcname=os.path.join('media', rel))
    return len(seen)


class Command(BaseCommand):
    help = 'Back up the ZEO database(s) and uploaded files (see the module text for restore).'

    def add_arguments(self, p):
        p.add_argument('--dir', default='')
        p.add_argument('--schema', default='', help='one company (schema) only')
        p.add_argument('--no-media', action='store_true')
        p.add_argument('--keep', type=int, default=14, help='days to keep backups made by this command (0 = keep all)')
        p.add_argument('--check', default='', help='check an existing backup folder instead of making one')
        p.add_argument('--no-encrypt', action='store_true', help='a plain backup even when ZEO_BACKUP_KEY is set')
        p.add_argument('--new-key', action='store_true', help='print a new encryption key for ZEO_BACKUP_KEY')
        p.add_argument('--decrypt', default='', help='decrypt the files of this backup folder (for a restore)')
        p.add_argument('--out', default='', help='with --decrypt: where the plain files go (default: the same folder)')
        p.add_argument('--copy-to', default='', help='copy the new backup to this folder (another disk / a network share)')
        # v1.67.1 (companies) the backup made before a company / an own database is deleted
        p.add_argument('--db', default='', help='one own database (key) only; with --schema the schema is read from it')
        p.add_argument('--company-files', action='store_true', help='with --schema / --db: the uploaded files of the company(ies)')
        p.add_argument('--tag', default='', help='added to the folder name')
        p.add_argument('--no-status', action='store_true', help='do not write last-backup.json')

    def handle(self, *a, **o):
        key = os.environ.get('ZEO_BACKUP_KEY') or getattr(settings, 'ZEO_BACKUP_KEY', '')
        if o['new_key']:
            from ZeoAdmin.backupcrypt import new_key
            self.stdout.write(new_key())
            self.stdout.write('Put it in .env as ZEO_BACKUP_KEY=<key> and keep a copy outside this server – without it the backups cannot be read.')
            return
        if o['decrypt']:
            return self._decrypt(o['decrypt'], o['out'] or o['decrypt'], key)
        if o['check']:
            probs = check(o['check'], key)
            if probs:
                raise CommandError('Backup NOT sound: ' + '; '.join(probs))
            enc = any(f['name'].endswith('.enc') for f in json.load(open(os.path.join(o['check'], 'manifest.json'))).get('files', []))
            self.stdout.write(self.style.SUCCESS(f'Backup sound: {o["check"]}' + (' (encrypted; content checked with the key)' if enc and key else
                                                                                   ' (encrypted; checksums only – no key here)' if enc else '')))
            return
        root = o['dir'] or os.environ.get('ZEO_BACKUP_DIR') or getattr(settings, 'ZEO_BACKUP_DIR', '') or os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(root, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        tag = ''.join(ch for ch in str(o.get('tag') or '') if ch.isalnum() or ch in '_-')[:60]   # v1.67.1 (companies)
        folder = os.path.join(root, PREFIX + stamp + (f'-{tag}' if tag else ''))
        os.makedirs(folder)
        t0 = time.time()
        files = []
        try:
            for alias, p in databases():
                name = f'db-{alias}.dump'
                args, env = _pg(p)
                if o.get('db'):   # v1.67.1 (companies) one own database (whole, or the company schema in it)
                    if alias != o['db']:
                        continue
                elif o['schema'] and alias != 'default':
                    continue
                cmd = [_tool('pg_dump'), *args, '-d', p['NAME'], '-Fc', '--no-owner', '--no-privileges', '-f', os.path.join(folder, name)]
                if o['schema']:
                    cmd[1:1] = ['-n', o['schema']]
                r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)
                if r.returncode != 0:
                    raise CommandError(f'pg_dump of {alias} failed: {(r.stderr or r.stdout).strip()[:500]}')
                files.append(name)
                self.stdout.write(f'database {alias}: {os.path.getsize(os.path.join(folder, name)):,} bytes')
            media = str(getattr(settings, 'MEDIA_ROOT', '') or '')
            if not o['no_media'] and not o['schema'] and media and os.path.isdir(media):
                with tarfile.open(os.path.join(folder, 'media.tar.gz'), 'w:gz') as t:
                    t.add(media, arcname='media')
                files.append('media.tar.gz')
                self.stdout.write(f'files: {os.path.getsize(os.path.join(folder, "media.tar.gz")):,} bytes')
            if o.get('company_files') and (o['schema'] or o.get('db')):   # v1.67.1 (companies)
                n = _company_files(folder, o['schema'], o.get('db'))
                if n is not None:
                    files.append('files.tar.gz')
                    self.stdout.write(f'company files: {n} file(s)')
            version = ''
            try:
                version = open(os.path.join(settings.BASE_DIR, 'VERSION')).read().strip()
            except Exception:
                pass
            manifest = {'zeo_version': version, 'made_at': datetime.now().isoformat(timespec='seconds'), 'schema': o['schema'] or 'all',
                        'seconds': round(time.time() - t0, 1),
                        'files': [{'name': f, 'bytes': os.path.getsize(os.path.join(folder, f)), 'sha256': sha256(os.path.join(folder, f))} for f in files]}
            json.dump(manifest, open(os.path.join(folder, 'manifest.json'), 'w'), indent=1)
            if not files:
                raise CommandError('No PostgreSQL database found to back up.')
            probs = check(folder)
            if probs:
                raise CommandError('The new backup failed its check: ' + '; '.join(probs))
            encrypted = bool(key and not o['no_encrypt'])
            if encrypted:   # v1.62.0 the checked plain files → .enc (then removed); the manifest lists the encrypted files
                from ZeoAdmin.backupcrypt import encrypt_file
                enc_files = []
                for f in manifest['files']:
                    src = os.path.join(folder, f['name'])
                    dst = src + '.enc'
                    plain = encrypt_file(src, dst, key)
                    os.remove(src)
                    enc_files.append({'name': f['name'] + '.enc', 'bytes': os.path.getsize(dst), 'sha256': sha256(dst), 'plain_sha256': plain,
                                      'plain_bytes': f['bytes']})
                manifest['files'], manifest['encrypted'] = enc_files, 'AES-256-GCM'
                json.dump(manifest, open(os.path.join(folder, 'manifest.json'), 'w'), indent=1)
                probs = check(folder, key)
                if probs:
                    raise CommandError('The encrypted backup failed its check: ' + '; '.join(probs))
                self.stdout.write('encrypted (AES-256-GCM)')
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        status = {'folder': folder, 'made_at': manifest['made_at'], 'zeo_version': version, 'bytes': sum(f['bytes'] for f in manifest['files']),
                  'checked': True, 'encrypted': bool(manifest.get('encrypted'))}
        status['copy'] = self._copy(folder, o['copy_to'] or os.environ.get('ZEO_BACKUP_COPY_TO', ''), key)
        if o.get('no_status'):   # v1.67.1 (companies)
            self.stdout.write(self.style.SUCCESS(f'Backup made and checked: {folder} ({status["bytes"]:,} bytes).'))
            return
        target = os.environ.get('ZEO_BACKUP_STATUS_FILE') or getattr(settings, 'ZEO_BACKUP_STATUS_FILE', '') or os.path.join(root, 'last-backup.json')
        json.dump(status, open(target, 'w'), indent=1)
        removed = 0
        if o['keep'] > 0:
            limit = datetime.now() - timedelta(days=o['keep'])
            for n in os.listdir(root):
                if not n.startswith(PREFIX) or n == PREFIX + stamp:
                    continue
                try:
                    made = datetime.strptime(n[len(PREFIX):], '%Y%m%d-%H%M%S')
                except ValueError:
                    continue
                if made < limit and os.path.exists(os.path.join(root, n, 'manifest.json')):
                    shutil.rmtree(os.path.join(root, n), ignore_errors=True)
                    removed += 1
        self.stdout.write(self.style.SUCCESS(f'Backup made and checked: {folder} ({status["bytes"]:,} bytes); {removed} old backup(s) removed.'))


    def _copy(self, folder, to, key):
        """v1.62.0 the backup off this disk: a folder (another disk, a mounted share) and / or ZEO_BACKUP_COPY_CMD."""
        out = {}
        name = os.path.basename(folder)
        if to:
            try:
                dst = os.path.join(to, name)
                shutil.copytree(folder, dst)
                probs = check(dst, key)
                out['folder'] = {'to': dst, 'ok': not probs, 'problems': probs}
                self.stdout.write(f'copied to {dst}' + ('' if not probs else f' – CHECK FAILED: {"; ".join(probs)}'))
            except Exception as e:
                out['folder'] = {'to': to, 'ok': False, 'problems': [str(e)[:300]]}
                self.stderr.write(f'copy to {to} failed: {e}')
        cmd = os.environ.get('ZEO_BACKUP_COPY_CMD') or getattr(settings, 'ZEO_BACKUP_COPY_CMD', '')
        if cmd:
            line = cmd.replace('{folder}', folder).replace('{name}', name)
            try:
                r = subprocess.run(line, shell=True, capture_output=True, text=True, timeout=6 * 3600)
                out['command'] = {'ok': r.returncode == 0, 'code': r.returncode, 'output': (r.stdout + r.stderr)[-400:]}
                self.stdout.write(f'copy command: {"done" if r.returncode == 0 else "FAILED (" + str(r.returncode) + ")"}')
            except Exception as e:
                out['command'] = {'ok': False, 'output': str(e)[:400]}
        return out

    def _decrypt(self, folder, out_dir, key):
        from ZeoAdmin.backupcrypt import decrypt_stream
        if not key:
            raise CommandError('ZEO_BACKUP_KEY is not set – the key the backup was made with is needed.')
        m = json.load(open(os.path.join(folder, 'manifest.json')))
        os.makedirs(out_dir, exist_ok=True)
        n = 0
        for f in m.get('files', []):
            if not f['name'].endswith('.enc'):
                continue
            target = os.path.join(out_dir, f['name'][:-4])
            with open(target + '.part', 'wb') as fo:
                h = decrypt_stream(os.path.join(folder, f['name']), key, fo)
            if h != f.get('plain_sha256'):
                os.remove(target + '.part')
                raise CommandError(f'{f["name"]}: the decrypted file does not match the manifest')
            os.replace(target + '.part', target)
            n += 1
            self.stdout.write(f'{target}: {os.path.getsize(target):,} bytes, checksum OK')
        self.stdout.write(self.style.SUCCESS(f'{n} file(s) decrypted into {out_dir}. Restore them as described at the top of this command.'))
