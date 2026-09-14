# TODO — Phase 5: weitere Module

Stand 2026-09-14. Phase 0 bis 4 sind fertig, committet und gepusht.
Vier Module: `workspace`, `job-health`, `governance`, `deploy` — drei davon schreiben,
`governance` meldet nur. 204 Tests.
Plan: `docs/plan.md` (lokal, nicht im Repo)

## Hier weitermachen

Nichts ist halb fertig, alles ist committet und gepusht. Zur Auswahl, grob nach Gewicht:

1. **`data-engineering`** — das letzte geplante Modul, Fernziel aus `docs/plan.md`.
   Notebooks, Pipelines, Lakehouse-Schemata. Vorher entscheiden, was daran überhaupt
   deklarierbar ist: `deploy` deckt das Ausrollen schon ab, `job-health` das Laufen.
2. **`governance.apply`** — Item-Beschreibungen setzen. Klein, aber es heißt, Item-Metadaten
   zu verwalten, was bisher kein Modul tut. Schließt den letzten offenen Befund
   (`lh_probe` in `faf_dev` hat keine Beschreibung).
3. **`deploy` vertiefen** — mehrere Stufen (dev → test → prod) und Parametrisierung über
   Ids hinaus, etwa Verbindungszeichenfolgen.
4. **E-Mail statt Principal-Id** — siehe „Offen, ohne Eile". Braucht eine
   Graph-Zustimmung, also nichts für nebenbei.

Der Tenant ist aufgeräumt: `afab_e2e` ist leer, keine Test-Workspaces, keine
Test-Zeitpläne. `faf_dev` hat seit heute eine Beschreibung.

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
~/.venvs/agentic-fabric/bin/afab modules    # vier Module, keine Probleme
~/.venvs/agentic-fabric/bin/pytest -q       # 204 grün

# Lesende Läufe gegen den Tenant, beide lokal und gitignored. Nicht
# examples/fabric.yaml nehmen — die nennt eine Capacity, die es hier nicht gibt.
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/e2e/fabric.yaml
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/jobs/fabric.yaml     # job-health
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/gov/fabric.yaml      # governance
~/.venvs/agentic-fabric/bin/afab plan -f .afabric/deploy/fabric.yaml   # deploy
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
      stimmt für Workspace- und Ordner-Operationen nicht.
      **Nachtrag 2026-09-14:** Auch ein Notebook-Lauf geht. `nb_seed_probe` lief über
      `job.rerun` in 30 Sekunden durch, `status: Completed`, bei weiterhin `Inactive`
      gemeldeter Capacity. Der gemeldete Zustand sagt also wenig über die Nutzbarkeit —
      möglicherweise weckt Fabric die Capacity bei Bedarf selbst.
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
    rerun_failed: true      # plant job.rerun, wenn der neueste Lauf fehlschlug
    stale_after_hours: 24   # plant job.rerun, wenn der letzte Erfolg zu alt ist
    schedule:               # deklariert = anlegen, wo keiner existiert
      interval_minutes: 1440
      timezone: W. Europe Standard Time
      start: 2030-01-01T03:00:00   # ohne Angabe: eine Stunde nach dem Lauf
      end: 2030-12-31T03:00:00     # ohne Angabe: ein Jahr nach start
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

**Schreibpfad seit 2026-09-14, live verifiziert.** `apply` legt Zeitpläne an und startet
Läufe, in Plan-Reihenfolge, Abbruch beim ersten Fehler.

- Die Konfiguration entsteht **beim Planen**, nicht beim Anwenden: `apply` sendet genau
  den Body, den die Plan-Tabelle gezeigt hat. Sonst verschöbe sich ein Default-Start
  zwischen Plan und Apply, weil die Uhr weiterläuft.
- Verifiziert an `nb_seed_probe`: Zeitplan mit Start 2030 angelegt (danach wieder
  gelöscht), Lauf gestartet, Antwort **202 ohne** `x-ms-operation-id` — nur `Location`.
  Der ToolBus gibt dafür `None` zurück, `apply` kommt damit klar. Mehr als „gestartet"
  sagt die API nicht; das Ergebnis steht beim nächsten `plan` in den Job-Instanzen.
- `start` in der Vergangenheit löst laut API sofort einen Lauf aus. Deshalb ist der
  Standard eine Stunde in der Zukunft und nicht „jetzt".

Offen an diesem Modul:
- [x] **Zeitplan-Inhalt wird verglichen (2026-09-14).** Intervall, Zeitzone und `enabled`;
      bei Abweichung `job.schedule_update` (PATCH, reversibel). Das **Fenster bleibt außen
      vor**: ohne deklariertes `start` wandert der Default mit der Uhr, ein Vergleich
      meldete also bei jedem Plan Drift und käme nie zur Ruhe. Beim Anwenden wird das
      vorhandene Fenster übernommen — ein geändertes Intervall darf den Start nicht
      stillschweigend verschieben.
      Live geprüft: 1440 → 60 Minuten geändert, Fenster (2030) unangetastet, `plan` danach
      leer, Zeitplan wieder gelöscht.
- [x] **Auto-Disable wird erkannt (2026-09-14)** — als Sonderfall des Inhaltsvergleichs:
      ein `enabled: false` gegen eine Deklaration mit `enabled: true`. Der Grund nennt
      Fabrics Abschaltung nach etwa zehn Fehlläufen ausdrücklich, damit niemand den
      Zeitplan blind wieder einschaltet, ohne die Ursache anzusehen.
- [x] **Mehrere Zeitpläne je Item (2026-09-14).** `schedule:` nimmt auch eine Liste.
      Fabric-Zeitpläne haben keinen Namen, nur Id und Erstellzeit — die Zuordnung läuft
      deshalb **über das Alter**: der erste deklarierte gehört zum ältesten vorhandenen.
      Überzählige löscht nur `policy.prune`, und destruktiv wie überall.
      Live geprüft: zwei angelegt, `plan` danach leer, dann auf einen reduziert — der
      neuere (720 Minuten) kam korrekt als `job.schedule_delete` heraus und brauchte
      ausdrückliche Freigabe.

## Phase 5 — Modul 3: `governance` (2026-09-14)

Der eigentliche Gradmesser-Test, und er ist bestanden: **kein Kernel-Eingriff**. Geändert
wurden nur `src/afabric/modules/governance/`, ein Eintrag in `pyproject.toml` und eine
Testzusicherung über die Zahl der eingebauten Module. Keine neuen Endpunkte nötig, alle
Regeln lesen, was der Katalog ohnehin serviert.

```yaml
governance:
  scope: "*"                    # Glob über Workspace-Namen, Personal nie im Scope
  workspace_description: true
  capacity_required: true
  min_admins: 1
  item_description: [Notebook, DataPipeline, Lakehouse]
  item_naming: "^[a-z][a-z0-9_]*$"
```

Ein Befund ist ein ganz normaler `Change`: `before` ist der Ist-Zustand, `after` die
Forderung der Regel. Dadurch laufen Policy, Freigabe und Journal unverändert darüber.

**Live verifiziert:** 2 Befunde über beide Transporte identisch — `faf_dev` ohne
Beschreibung, Lakehouse `lh_probe` ohne Beschreibung. `apply` verweigert und verweist auf
`workspaces:`, wo der Wert deklariert wird.

Gelernt:

- `list_items` liefert **kein** `folderId`. Eine Regel „jedes Item liegt in einem Ordner"
  wäre also nicht ohne Weiteres möglich.
- Abgeleitete Items (`SQLEndpoint`, `SemanticModel`) haben keine eigene Beschreibung und
  lassen sich nicht einzeln umbenennen — sie fliegen raus, sonst meldet das Audit Rauschen,
  an dem niemand etwas ändern kann.

Offen an diesem Modul:

- [ ] Nur Beschreibung, Capacity, Admin-Anzahl und Item-Namen. Keine Labels, keine
      Domains, keine Endorsements.
- [ ] `apply` bewusst nicht implementiert: Jeder Befund braucht eine Entscheidung, die
      das Modul nicht treffen kann — welche Beschreibung, welche Capacity, welcher Name.

## Phase 5 — Modul 4: `deploy` (2026-09-14)

Vergleicht Item-Definitionen zwischen Quell- und Ziel-Workspace und plant die Promotion.
Bewusst **nicht** über `fabric-cicd`: die Bibliothek veröffentlicht, sie zeigt nichts
vorher an. Ein ehrlicher `plan` wäre darauf nicht baubar gewesen.

```yaml
deploy:
  - source: faf_dev
    target: afab_e2e
    items: "*"
    types: [Notebook, DataPipeline]
```

- Gleichheit = gleicher SHA-256 über die Definitionsteile, **ohne** `.platform`. Die Datei
  enthält Anzeigename, logische Id und Heimat-Workspace und unterscheidet sich zwischen
  zwei Workspaces zwangsläufig — mitgehasht wäre jedes Item für immer „geändert".
- Identität über Name **und** Typ. Ids stimmen zwischen Workspaces nie überein.
- `policy.prune` plant Löschungen im Ziel, destruktiv wie überall.

**Live verifiziert:** 5 `item.create` für `faf_dev` → `afab_e2e`, über beide Transporte
identisch.

**Nebenbei erledigt: der LRO-Pfad ist live gelaufen.** `getDefinition` antwortet mit
**202**, `RestClient.await_operation` holt das Ergebnis. Der Code, der laut diesem TODO
nie ausgeführt worden war, trägt also.

**Schreibpfad seit 2026-09-14, live verifiziert.** `apply` legt Items an und aktualisiert
sie, mit umgeschriebenen Ids.

- **Vergleich über normalisierte Fingerabdrücke.** Beim Promoten ändern sich die Ids —
  gegen die rohe Quelle verglichen wäre jede Kopie für immer „anders". Deshalb ersetzt
  `observe` auf **beiden** Seiten jede bekannte Id durch das, was sie *bedeutet*
  (`@workspace`, `@nb_bronze (Notebook)`), und hasht erst dann. Ids, die keine Seite
  benennen kann, bleiben stehen: unterscheiden die sich, ist das ein echter Unterschied.
- **Unauflösbare Referenz blockiert das Item** (`item.blocked`), und die Blockade pflanzt
  sich fort: Wer ein blockiertes Item ruft, wandert selbst nicht. Auflösen über `map:`
  (Quell-Item-Name → Ziel-Item-Name) oder indem man das Referenzierte mitnimmt.
- **Reihenfolge nach Abhängigkeit.** Ids neu angelegter Items stehen erst beim Anwenden
  fest, deshalb sortiert `plan` Abhängiges hinter seine Abhängigkeit, und `apply` füttert
  die frisch entstandene Id in die nächste Definition.
- Live: `faf_dev` → `afab_e2e`, sechs Items in der Reihenfolge Lakehouse, Notebooks,
  Pipeline. Danach zeigten alle Kopien auf **Ziel**-Lakehouse, -Notebooks und -Workspace,
  `plan` war leer. Dann eine Kopie von Hand verändert → `item.update` → repariert →
  wieder leer. Anschließend alles aus `afab_e2e` entfernt.

Offen an diesem Modul:

- [ ] Nur `Notebook`, `DataPipeline` und `Lakehouse` sind erprobt. Andere Typen sind
      deklarierbar, aber ungetestet — und ein Lakehouse wandert **leer**, nur als Hülle.
- [ ] Kein Deployment über mehrere Stufen (dev → test → prod) und keine Parametrisierung
      jenseits von Ids, etwa Verbindungszeichenfolgen.

## Offen, ohne Eile

- [x] **`_create` überspringt die eigene Identität (2026-09-14).** `observe` legt
      `bus.identity` in den beobachteten Zustand, `plan` lässt den Grant für genau diesen
      Principal beim *Anlegen* weg — nur dort, denn auf einem bestehenden Workspace ist
      eine fehlende eigene Rolle echte Drift. Live geprüft mit `faf_role_probe`: Plan
      enthielt nur `workspace.create`, Anwenden lief durch, `plan` danach leer.
- [x] **LRO über REST live geprüft (2026-09-14).** `getDefinition` im `deploy`-Modul
      antwortet mit 202, `await_operation` liefert das Ergebnis. Offen bleibt nur der
      MCP-eigene Pfad: `McpBackend` wartet nicht, und der MCP-Server bietet dafür
      `get_operation_state`/`get_operation_result`. Bisher kam über MCP nichts asynchron
      zurück — Tools ohne MCP-Gegenstück laufen ohnehin über den REST-Fallback.
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
- [x] **GET-Argumente außerhalb des Pfads gehen nicht mehr verloren (2026-09-14).**
      `RestBackend` reicht sie als Query-Parameter durch, `get_all` trägt sie über alle
      Seiten mit — sonst wäre ab Seite zwei eine andere Frage beantwortet worden. Der
      Agent bietet solche Argumente wieder an, `list_items(type=...)` inklusive.
      Live: `faf_dev` hat 7 Items, mit `type=Notebook` genau 4 — über beide Transporte.
- [ ] **Principal-IDs statt E-Mail.** `roles:` verlangt Entra-Objekt-Ids, weil die
      Fabric-API keine Auflösung von Namen kennt. Der Weg wäre ein **zweites Token** für
      `https://graph.microsoft.com/.default` aus derselben App-Registrierung und ein
      Lookup `GET /users/{upn}`. Das braucht eine eigene Zustimmung
      (`User.ReadBasic.All`), also nichts, was nebenbei passiert — und einen Cache, sonst
      kostet jeder Plan zusätzliche Aufrufe. Alternative bliebe der Graph-MCP-Server.
- [ ] Service Principal für den unbeaufsichtigten REST-Pfad — Phase 5
