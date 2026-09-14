# TODO — Phase 5: zweites Modul

Stand 2026-09-13. Phase 0 bis 4 sind fertig, committet und gepusht.
Plan: `docs/plan.md` (lokal, nicht im Repo)

## Erledigt am 2026-09-14: Modul-eigene Tools

Die `tools`-Komponente ist jetzt verdrahtet. `registry.tool_specs()` sammelt die `SPECS`
der Module, `session.connect(..., registry=...)` reicht sie an den ToolBus, und
`ToolBus.call` schlägt erst dort nach, dann im Katalog. Die Discovery meldet als Problem,
wenn ein Modul einen Katalog-Namen oder den eines anderen Moduls überschreiben will —
ein Modul, das still `delete_workspace` umdefiniert, wäre der teuerste denkbare Fehler.

Die zwei Job-Endpunkte liegen jetzt in `modules/jobhealth/tools.py`; `kernel/tools.py`
kennt sie nicht mehr. Live geprüft: `afab plan -f .afabric/jobs/fabric.yaml` liefert über
beide Transporte weiterhin 10 Changes.

**Damit ist die Gradmesser-Frage beantwortet:** Modul 3 braucht ein Verzeichnis, sonst
nichts — auch nicht für eigene Endpunkte.

## Wieder reinkommen

```bash
cd ~/omarchy/agentic-fabric

# Mac: venv liegt außerhalb des Repos, die Repo-.venv gehört der VM.
~/.venvs/agentic-fabric/bin/afab status     # faf_dev, faf_dev ohne Login
~/.venvs/agentic-fabric/bin/afab modules    # workspace + job-health, keine Probleme
~/.venvs/agentic-fabric/bin/pytest -q       # 132 grün

# Lesende Läufe gegen den Tenant, beide lokal und gitignored. Nicht
# examples/fabric.yaml nehmen — die nennt eine Capacity, die es hier nicht gibt.
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/e2e/fabric.yaml
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/jobs/fabric.yaml   # job-health
```

## Was Phase 3 am 2026-09-12 live bewiesen hat

Verifiziert gegen den echten Tenant, beide Transporte, mit einem Wegwerf-Workspace
`faf_e2e_scratch`, der am Ende wieder gelöscht wurde:

- `plan` über `mcp` und `rest` liefert identische Pläne
- `apply` legt Workspace und Ordner an; `plan` danach leer (Idempotenz live)
- Drift (Beschreibung geändert, Ordner ergänzt) wird genau als solche geplant und
  angewendet, danach `plan` wieder leer
- `--yes` allein gibt einen destruktiven Change **nicht** frei: Exit 1,
  „Nothing approved, nothing applied", Workspace stand danach noch
- `deny`-Regeln greifen live: `faf_dev` und `afab_e2e` kamen als `denied` heraus,
  auch mit `--approve-destructive`
- **Die Selbstschutz-Sperre greift:** `prune: true` gegen `faf_dev` plant
  `role.revoke` für die eigene Admin-Rolle, Policy antwortet
  „would change your own access; refused regardless of policy". Die `oid` kommt also
  aus dem Token an.
- Journal: pro Lauf eine eigene Run-ID, je Change `planned` → `approved` → `applied`,
  neue Workspace- und Ordner-IDs stehen in `output`

Erledigte Altlasten aus Phase 3:

- **Ordner-Antwortform geklärt.** `list_folders` liefert für Ordner auf oberster Ebene
  `id`, `displayName`, `workspaceId` — **kein** `parentFolderId`. Der Filter in
  `observe()` ist damit richtig, greift aber über die *Abwesenheit* des Felds.
- **Katalog gegen `list_tools` geprüft** (2026-09-10): alle Write-Tools existieren,
  `create_workspace` nimmt die Felder flach, alle anderen unter `Details`.
- **Capacity `capfabricf4`** war während der Verifikation `Active`.

## Vor dem nächsten Schreiblauf

- [x] **Pausierte Capacity blockiert nichts.** Am 2026-09-13 stand `capfabricf4` auf
      `Inactive` (beide Transporte melden das gleich). Trotzdem liefen `workspace.create`
      *mit* Capacity und `workspace.assign_capacity` durch, beide mit
      `capacityAssignmentProgress: Completed`. Die frühere Annahme, das scheitere,
      stimmt für Workspace- und Ordner-Operationen nicht. Ob das Anlegen von *Items*
      eine laufende Capacity braucht, ist damit nicht beantwortet.
- [ ] Für Schreibtests wieder einen Wegwerf-Workspace nehmen, nie `faf_dev`.
      Die YAMLs von Phase 3 liegen unter `.afabric/e2e/` (gitignored, lokal).

## Phase 4 — Agent-Loop

`agent.py` mit der Anthropic Messages API und dem ToolBus als Tools, zwei Fähigkeiten:

- [x] `afab explain` — beobachtete Drift in Prosa erklären, samt Ursachenvermutung.
      Code + Unit-Tests stehen (`kernel/agent.py`, `tests/test_agent.py`): Drift kommt
      aus `runner.plan`, der Agent liest nur (GET-Tools aus dem Katalog + `read_journal`),
      Ergebnis landet als `explained` im Journal.
      **Live verifiziert 2026-09-12** auf dem Mac, beide Transporte, Wegwerf-Workspace
      `faf_e2e_scratch` (danach per Prune gelöscht): ohne Drift kein Modellaufruf;
      Beschreibung geändert + Ordner `Silver` gelöscht → beide Abweichungen genau
      benannt, Ursache aus Journal-Zeitstempeln hergeleitet. 4–5 Turns, ca. $0.10 je Lauf
      (Schätzwert, aus dem Max-Abo). Nachgeschärft am 2026-09-13, live geprüft:
      - Beleg und Vermutung getrennt: Systemprompt sagt, das Journal zeigt nur, was afab
        tat, nicht wer sonst. Agent schreibt jetzt „Vermutung, nicht belegt".
      - Sprache über `AFABRIC_LANGUAGE`, Standard `German`.
      - Eigene Run-ID im Prompt: vorher las er die `planned`-Einträge des laufenden
        Vergleichs als früheren Lauf und datierte die Drift falsch.
      - Rest: Alte Prune-Läufe erwähnt er noch als ausdrücklich markierten Nachsatz
        „außerhalb der Änderungen". Hinnehmbar.
      - Auf dem Mac liegt die venv unter `~/.venvs/agentic-fabric`
        (`UV_PROJECT_ENVIRONMENT`), die Repo-`.venv` gehört der VM.
      Läuft auf dem **Claude Agent SDK** mit dem Max-Abo, nicht auf einem API-Key:
      in der VM einmal `claude` starten und mit dem Abo anmelden (oder
      `CLAUDE_CODE_OAUTH_TOKEN` aus `claude setup-token`). `ANTHROPIC_API_KEY` darf
      **nicht** gesetzt sein, sonst rechnet das SDK über den Key ab. Modell: `AFABRIC_MODEL`.
- [x] Intent in natürlicher Sprache („neue Dev-Umgebung für Team Vertrieb") wird zu
      einem `fabric.yaml`-Vorschlag, den der Nutzer prüft — `afab propose "<intent>"`.
      Der Vorschlag ist ein eigenes `fabric.d/`-Fragment, nie eine Änderung an
      bestehenden Dateien. Der Agent bekommt zusätzlich `declared_schema` (JSON-Schema
      je Modul, aus `model.Config`), `validate_fragment` (prüft über `load_desired`,
      also denselben Pfad wie `plan`) und `submit_proposal` (nimmt nur Gültiges an).
      Danach plant die CLI das Fragment lesend in einer Wegwerf-Kopie der Kaskade und
      zeigt die Plan-Tabelle. Geschrieben wird nur mit `-o`.
      **Live verifiziert 2026-09-13:** Intent „Dev-Umgebung Team Vertrieb, Capacity
      capfabricf4, Ordner Bronze/Silver/Gold" → gültiges Fragment, 4 Changes in der
      Vorschau, 6 Turns, ca. $0.18. Der Agent prüfte selbst, dass der Name frei ist,
      ließ `roles` weg (keine Principal-IDs bekannt) und wies darauf hin, dass
      `capfabricf4` auf `Inactive` steht — das stimmte, über beide Transporte.
- [ ] Der Agent schlägt nur vor. Angewendet wird weiter über `runner.plan`/`runner.apply`
      aus Phase 3, mit Policy, Freigabe und Journal.

*Verifikation:* Drift von Hand im Fabric-Portal erzeugen, `afab explain` muss sie
korrekt benennen.

## Phase 5 — Modul 2: `job-health` (2026-09-13)

Nur lesen und planen. `observe` holt Items, Läufe und Zeitpläne; `plan` vergleicht mit
der Erwartung; `apply` verweigert ausdrücklich, statt halb zu handeln.

```yaml
jobs:
  - workspace: faf_dev
    items: "*"              # Glob über Anzeigenamen
    schedule: required      # plant job.schedule, wo keiner existiert
    rerun_failed: true      # plant job.rerun, wenn der neueste Lauf fehlschlug
    stale_after_hours: 24   # plant job.rerun, wenn der letzte Erfolg zu alt ist
```

**Live verifiziert:** 10 Changes über 5 Items in `faf_dev`, beide Transporte identisch.
`apply` bricht beim ersten Change ab, 9 nicht versucht, Tenant unberührt.

Gelernt, aus dem Tenant, nicht aus der Doku:

- Der Job-Typ hängt am Item-Typ: `RunNotebook` für Notebooks, `Pipeline` für Data
  Pipelines. Ein falsches Paar beantwortet der Schedules-Endpunkt mit **400**, nicht mit
  einer leeren Liste. `model.JOB_TYPES` hält die Zuordnung; unbekannte Item-Typen werden
  übersprungen, nie geraten.
- Läufe kommen **unsortiert** zurück. „Der neueste Lauf" heißt Maximum über
  `startTimeUtc`, nicht `[0]`.
- Zeitstempel tragen keine Zeitzone und sind laut Doku UTC.
- Notebooks, die aus einer Pipeline laufen, melden `PipelineRunNotebook` statt
  `RunNotebook` — der Lauf-Typ ist also nicht der Zeitplan-Typ.

**Gradmesser (die Frage aus `docs/plan.md`):** Manifest, Modell und Prozess brauchten nur
ein Verzeichnis. Für die zwei neuen API-Endpunkte musste zunächst `kernel/tools.py` ran —
das ist am 2026-09-14 behoben, die Specs liegen jetzt im Modul (siehe oben).

Offen an diesem Modul:

- [ ] `apply`: Zeitplan anlegen (`POST .../jobs/{jobType}/schedules`) und Lauf starten
      (`POST .../jobs/instances?jobType=...`). Schreibpfad, bewusst vertagt.
- [ ] Zeitplan-Inhalt wird nicht verglichen — nur „existiert" oder „fehlt". Intervall,
      Zeitzone und `enabled` bleiben unbeachtet.
- [ ] Auto-Disable des Schedulers (nach ~10 Fehlläufen) wird nicht erkannt.

## Offen, ohne Eile

- [ ] `_create` plant `role.grant` auch für die eigene Identität. Fabric macht den
      Ersteller automatisch zum Admin, ein solcher Grant würde beim Anlegen scheitern.
      In Phase 3 umgangen, indem die E2E-YAML keine eigene Rolle deklariert.
- [ ] LRO über MCP ungetestet: `RestBackend` wartet bei 202 auf die Operation,
      `McpBackend` nicht. Keine der Workspace- und Ordner-Operationen kam bisher
      asynchron zurück. Der MCP-Server bietet `get_operation_state`/`get_operation_result`,
      falls es nötig wird.
- [x] **`assign_to_capacity` live geprüft (2026-09-13)** — und damit der REST-Fallback
      über den MCP-Transport, das einzige architektonisch neue Stück im ToolBus.
      Vorgehen, falls nochmal nötig: Workspace *ohne* `capacity` deklarieren und anlegen,
      dann dieselbe Datei mit `capacity:` anwenden — erst dann plant das Modul
      `workspace.assign_capacity`. Lief über `-t mcp` durch, `capacityId` danach gesetzt,
      `plan` über beide Transporte anschließend leer. Kein 202/LRO dabei, der
      MCP-seitige LRO-Pfad bleibt also weiter ungetestet.
- [ ] **`OperationHasNoResult` ist geraten.** `await_operation` liest diesen Fehlercode
      als „Operation ohne Ergebnis". Kein Aufruf in Phase 3 kam asynchron zurück, der
      Code ist also nie gelaufen. Fällt im Zweifel sicher aus: ein unerwarteter Fehler
      fliegt, statt als Ergebnis durchzugehen.
- [ ] **GET-Argumente außerhalb des Pfads gehen über REST verloren.** `RestBackend.call`
      wirft `leftover` bei GET weg, `list_items(type=...)` liefert über `-t rest` also
      ungefiltert, über MCP gefiltert. Der Agent bietet `type` deshalb nicht an. Fix:
      `leftover` als Query-Parameter senden (Namen je Tool prüfen).
- [ ] Principal-IDs vs. E-Mail — Graph MCP Server, später
- [ ] Service Principal für den unbeaufsichtigten REST-Pfad — Phase 5
