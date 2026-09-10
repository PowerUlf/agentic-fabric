# TODO — Phase 3: Plan und Apply mit Guardrails

Stand 2026-09-10. Phase 0, 1 und 2 sind fertig, committet und gepusht.
Plan: `docs/plan.md` (lokal, nicht im Repo)

## Wieder reinkommen

```bash
cd ~/omarchy/agentic-fabric
.venv/bin/afab status             # muss faf_dev zeigen, ohne Login
.venv/bin/afab modules            # workspace, keine Probleme
.venv/bin/pytest -q               # 57 grün
```

## Vor dem ersten Schreibzugriff

- [ ] **Capacity `capfabricf4` fortsetzen.** Stand 2026-09-10 ist sie `Inactive`
      (pausiert). Lesen geht, Anlegen und Zuweisen auf dieser Capacity nicht.
- [ ] Einen **Wegwerf-Workspace** für die E2E-Verifikation festlegen, nicht `faf_dev`.

## Reihenfolge

### 1. `kernel/policy.py`
- [ ] `DESTRUCTIVE` braucht immer Freigabe, `require_approval` zusätzlich pro Verb
- [ ] `max_blast_radius`: Lauf mit mehr Changes ablehnen, nicht kürzen
- [ ] `deny`-Muster auswerten — Form im Beispiel ist `{workspace: "Production*"}`,
      der Kernel kennt aber keine Workspaces. Muster gegen `Change.target` statt
      gegen modul-spezifische Felder?
- [ ] **Eigene Identität nie entziehen.** Live belegt: mit `prune: true` plant das
      Workspace-Modul `role.revoke` für die Admin-Rolle des angemeldeten Nutzers —
      die einzige Rolle auf `faf_dev`. Harte Sperre im Kernel, unabhängig vom YAML.
      Braucht die Object-ID des Aufrufers (aus dem Token-Claim `oid`).
- [ ] Doppelte Einstellung auflösen: `max_blast_radius` gibt es in `Settings` (env)
      und in `policy` (YAML). Eine Quelle.

### 2. `kernel/journal.py`
- [ ] Append-only JSONL, ein Eintrag pro geplantem und pro ausgeführtem Change

### 3. `workspace/process.py` — `apply()`
- [ ] Reihenfolge: Workspace anlegen vor Ordnern und Rollen darin; die `planned:`-IDs
      aus `project()` durch echte ersetzen
- [ ] Neue Tools im Katalog: `create_workspace`, `update_workspace`,
      `assign_to_capacity`, `create_folder`, `delete_folder`, `add/update/delete_workspace_role`,
      `delete_workspace` — Argumentnamen am Live-Server prüfen (`list_tools`), MCP
      nimmt hier `Details`-Objekte
- [ ] LRO-Polling für die Operationen, die eines zurückgeben

### 4. CLI
- [ ] `afab plan [--file fabric.yaml]` — Discovery, Laden, `observe`, `plan`, farbige Tabelle
- [ ] `afab apply` — gleicher Plan, Policy, Freigabe, `apply`, Journal
- [ ] Vor `plan`: `registry.ok` prüfen, bei Modulproblemen abbrechen

### 5. Verifikation (aus dem Plan)
- [ ] Wegwerf-Workspace anlegen → `plan` leer → YAML ändern → `plan` zeigt genau die
      Änderung → `apply` → `plan` wieder leer
- [ ] Löschen ohne Freigabe muss blockiert werden

## Ungeprüft gegen den echten Tenant

- **Ordner-Antwortform.** `faf_dev` hat keine Ordner, `parentFolderId` in `observe()`
  ist aus der Doku übernommen. Beim ersten `folder.create` in Phase 3 prüfen.

## Offen, ohne Eile

- [ ] Principal-IDs vs. E-Mail — Graph MCP Server, später
- [ ] Service Principal für den unbeaufsichtigten REST-Pfad — Phase 5
