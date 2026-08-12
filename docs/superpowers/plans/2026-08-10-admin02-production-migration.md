# Admin02 Production Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize the complete current local oTree project to `/home/admin02/econlab/my_platform` and switch the production service to run entirely from the `admin02` deployment directory without losing production configuration or PostgreSQL data.

**Architecture:** Build a clean source archive from the current local `my_platform` tree while excluding machine-specific and secret files. Stage a fresh Python 3.10 virtual environment under `/home/admin02/econlab`, preserve the production `.env`, then atomically switch the systemd unit from `/opt/econlab` to the new directory. Keep the old `/opt/econlab` deployment intact for rollback.

**Tech Stack:** Ubuntu 20.04, Python 3.10, oTree 5, PostgreSQL, systemd, tar, SSH/SCP

---

### Task 1: Validate the local release

**Files:**
- Read: `/Users/hybsmac/Python项目/economics-experiment/my_platform/requirements.txt`
- Test: `/Users/hybsmac/Python项目/economics-experiment/my_platform`

- [ ] **Step 1: Compile all Python files**

Run:

```bash
find my_platform -name '*.py' -not -path '*/.git/*' -not -path '*/__pycache__/*' -print0 | xargs -0 python3 -m py_compile
```

Expected: exit code `0` with no syntax errors.

- [ ] **Step 2: Run the project test suite**

Run:

```bash
cd my_platform
python3 -m unittest discover -p '*tests.py'
```

Expected: tests complete without failures. If local dependency availability prevents a full run, record the exact limitation and run the server-side checks before switching systemd.

### Task 2: Package the complete local project

**Files:**
- Create: `/private/tmp/econlab_my_platform_admin02_20260810.tar.gz`
- Preserve on server: `/opt/econlab/my_platform/.env`
- Exclude: `my_platform/.git`, `my_platform/.env`, `my_platform/db.sqlite3`, Python caches, macOS metadata, and temporary bot output

- [ ] **Step 1: Create a deterministic source archive**

Run:

```bash
COPYFILE_DISABLE=1 tar --no-xattrs \
  --exclude='my_platform/.git' \
  --exclude='my_platform/.env' \
  --exclude='my_platform/db.sqlite3' \
  --exclude='my_platform/__pycache__' \
  --exclude='my_platform/*/__pycache__' \
  --exclude='my_platform/*/*/__pycache__' \
  --exclude='my_platform/*/*/*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='*/.DS_Store' \
  --exclude='my_platform/__temp_bots_*' \
  -czf /private/tmp/econlab_my_platform_admin02_20260810.tar.gz my_platform
```

Expected: archive contains the complete current source tree but no `.env`, `.git`, SQLite database, cache, or temporary bot files.

### Task 3: Stage the admin02 deployment

**Files:**
- Create: `/home/admin02/econlab/my_platform`
- Create: `/home/admin02/econlab/py310`
- Create: `/home/admin02/econlab/backups`
- Read: `/opt/econlab/my_platform/.env`

- [ ] **Step 1: Upload the source archive and migration script**

Run SCP as `admin02` to `/tmp`.

Expected: both files are owned by `admin02` and readable.

- [ ] **Step 2: Back up existing service configuration and target directory**

Run:

```bash
cp /etc/systemd/system/econlab-otree.service /home/admin02/econlab/backups/econlab-otree.service.before-admin02-migration
tar -czf /home/admin02/econlab/backups/my_platform.before-admin02-migration.tar.gz -C /home/admin02/econlab my_platform
```

The second command runs only when a prior target directory exists.

Expected: rollback copies exist before replacing any target content.

- [ ] **Step 3: Extract source and preserve production secrets**

Extract the uploaded archive to a staging directory, copy `/opt/econlab/my_platform/.env` into the staged project, set `.env` mode to `600`, then atomically replace `/home/admin02/econlab/my_platform`.

Expected: the staged project matches local source and uses the existing production environment configuration.

- [ ] **Step 4: Create the new virtual environment**

Run:

```bash
/opt/econlab/py310/bin/python -m venv /home/admin02/econlab/py310
/home/admin02/econlab/py310/bin/pip install -r /home/admin02/econlab/my_platform/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Expected: `/home/admin02/econlab/py310/bin/otree` exists and reports oTree `5.x`.

### Task 4: Switch and verify production

**Files:**
- Modify: `/etc/systemd/system/econlab-otree.service`
- Preserve: `/opt/econlab/my_platform`
- Preserve: `/opt/econlab/py310`

- [ ] **Step 1: Update systemd paths**

Set:

```ini
User=admin02
Group=admin02
WorkingDirectory=/home/admin02/econlab/my_platform
EnvironmentFile=/home/admin02/econlab/my_platform/.env
Environment=PATH=/home/admin02/econlab/py310/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/admin02/econlab/py310/bin/otree prodserver 18000
```

Expected: PostgreSQL dependency, restart policy, signals, and port remain unchanged.

- [ ] **Step 2: Reload and restart**

Run:

```bash
systemctl daemon-reload
systemctl restart econlab-otree.service
```

Expected: `systemctl is-active econlab-otree.service` prints `active`.

- [ ] **Step 3: Verify application health**

Run:

```bash
curl -fsSI http://127.0.0.1:18000/login
curl -fsSI http://127.0.0.1:18000/room/prod_room
ss -ltn
```

Expected: login and room return HTTP `200`, and port `18000` is listening.

- [ ] **Step 4: Verify database connectivity and deployed version**

Run:

```bash
systemctl status econlab-otree.service --no-pager
/home/admin02/econlab/py310/bin/pip show otree
```

Expected: service command paths point to `/home/admin02/econlab`, no database connection errors appear, and oTree is installed from the pinned `requirements.txt` range.

- [ ] **Step 5: Roll back automatically on failure**

If any post-switch health check fails, restore the backed-up systemd unit, run `systemctl daemon-reload`, restart the service, and verify `/opt/econlab` is active again.

Expected: a failed migration does not leave production unavailable.
