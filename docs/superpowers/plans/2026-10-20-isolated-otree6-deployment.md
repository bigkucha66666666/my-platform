# Independent oTree 6 Deployment Plan

**Goal:** Publish the current local project to admin02@10.8.128.8 using Python 3.10 / oTree 6.0.13 without changing the old database, application, or service.

**Architecture:** Keep the old application on port 18000. Install an independent application, virtualenv, PostgreSQL database/user, production environment file, SQLite synchronization journal, and systemd service on port 18001. Both dependency files and all new project modules are included in a content-hashed release; no local secrets, databases, papers, or generated output are uploaded as application source.

**Tech Stack:** Existing Linux Python 3.10, pinned oTree 6.0.13, PostgreSQL, SQLite online backups, systemd, existing campus network.

- [x] Confirm target with user and inspect the existing service and data read-only.
- [x] Record old database/table hashes, back up PostgreSQL and SQLite, and verify backup readability (including a real restore and all 29 table hashes).
- [x] Package the working-tree source, including untracked runtime modules, without reverting or committing user changes.
- [x] Install the pinned dependencies in a separate Linux virtualenv and compare versions and oTree source with the local environment.
- [x] Run isolated server regressions with a disabled paid provider and record failures. Core 413 and Agent/label 158 pass; extended full flow completes 32 rounds, then fails at round 33 with decision_page_http_failure. Full-flow acceptance remains NOT achieved.
- [x] Create a separate database/user and journal; initialize only the empty new database without resetdb.
- [x] Start and enable only the new systemd service; preserve the old service and port.
- [x] Verify login, Session creation, metadata, timing, data export and new-service persistence; recheck old-data hashes.
- [x] Write addresses, paths, results, update instructions and the unresolved full-flow failure to docs/oTree6独立新库部署与旧数据保留_2026-10-20.md.
