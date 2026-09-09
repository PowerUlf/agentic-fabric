# TODO — Phase 2: Modulsystem und `workspace`-Modul

Stand 2026-09-09. Phase 0 und 1 sind fertig, committet und gepusht.
Plan: [docs/plan.md](docs/plan.md)

## Wieder reinkommen (2 Minuten)

```bash
cd ~/omarchy/agentic-fabric
mise install                      # falls die Shell frisch ist
.venv/bin/afab status             # muss faf_dev + capfabricf4 zeigen, ohne Login
.venv/bin/pytest -q               # 16 grün
```

Zeigt `afab status` einen Device-Code, ist der gespeicherte Login abgelaufen —
einmal bestätigen, dann ist wieder Ruhe.

## Reihenfolge für morgen

Der Kern-Mechanismus zuerst, das Modul als sein erster Nutzer. Andersherum
entsteht ein Reconciler mit angeklebtem Modulsystem statt umgekehrt.

### 1. `kernel/modules.py` — Discovery
- [ ] Entry-Point-Gruppe `afabric.modules` lesen (`importlib.metadata`)
- [ ] `src/afabric/modules/` scannen, damit ein Verzeichnis ohne
      `pyproject.toml`-Eintrag ebenfalls gefunden wird
- [ ] Duplikate und kaputte Manifeste melden statt still zu schlucken
- [ ] `provides`/`requires` gegen den ToolBus auflösen, ungedeckte
      `requires` als klaren Fehler
- [ ] `afab modules` zeigt Name, Version, Config-Key, Capabilities

### 2. Konfig-Kaskade
- [ ] `fabric.yaml` laden, `fabric.d/*.yaml` in Dateinamen-Reihenfolge mergen
- [ ] Merge-Semantik festlegen: Listen anhängen oder ersetzen?
      **Vorschlag:** Workspaces per `name` zusammenführen, Skalare überschreiben
- [ ] Jedes Modul validiert nur seinen eigenen Top-Level-Key
- [ ] Unbekannter Top-Level-Key → Fehler mit Hinweis auf die geladenen Module

### 3. `modules/workspace/model.py` — Schema
- [ ] Workspace: `name`, `description`, `capacity`, `folders[]`, `roles[]`
- [ ] Capacity als Name statt UUID (Auflösung über `list_capacities`)
- [ ] Rollen zunächst mit Principal-IDs — siehe offene Punkte

### 4. `modules/workspace/process.py` — `plan()`
- [ ] Rein funktional: `plan(desired, observed) -> list[Change]`, keine I/O
- [ ] Verben: `workspace.create|update|delete`, `folder.create|delete`,
      `role.grant|update|revoke`
- [ ] Risiko korrekt setzen — `delete` und `role.revoke` sind `DESTRUCTIVE`
- [ ] Nicht deklarierte Workspaces **nicht** löschen; das braucht ein
      explizites `prune: true`, sonst ist der erste Lauf ein Massaker

### 5. Tests
- [ ] Fixtures aus dem echten Tenant aufzeichnen (`faf_dev`) und anonymisieren
- [ ] Drift in beide Richtungen, leerer Diff bei Gleichstand
- [ ] Idempotenz: `plan(a, apply(plan(a,b), b)) == []`
- [ ] **Modularitäts-Test:** Dummy-Modul im Testverzeichnis wird ohne jede
      Kern-Änderung entdeckt und gelistet. Das ist der Lackmustest für die
      ganze Architektur — wenn der wehtut, stimmt der Schnitt nicht.

## Offene Punkte, die eine Entscheidung brauchen

- [ ] **Principal-IDs vs. E-Mail.** Rollen brauchen UUIDs. Komfortable
      E-Mail-Auflösung geht nur über den Microsoft Graph MCP Server.
      Morgen: IDs im YAML. Später entscheiden, ob Graph dazukommt.
- [ ] **Service Principal.** Für den unbeaufsichtigten REST-Pfad fehlen noch
      App-Registrierung und das Tenant-Setting
      „Service principals can use Fabric APIs". Für Phase 2 nicht nötig,
      für Phase 5 (Dienst) schon.
- [ ] **`prune`-Semantik.** Soll der Reconciler je etwas löschen, das nicht im
      YAML steht? Vorschlag: nur mit explizitem Flag, und nie ohne Freigabe.

## Nicht vergessen

- Core MCP ist Preview. Ändern sich Tool-Namen, ist `kernel/tools.py` die
  einzige Stelle — dort nachziehen, nirgends sonst.
- Vor jedem Commit: `.venv/bin/ruff check . && .venv/bin/pytest -q`
