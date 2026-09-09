# agentic-fabric — Agentisches Operating System für Microsoft Fabric

> **Stand 2026-09-09:** Phase 0 und 1 sind abgeschlossen und verifiziert.
> Als Nächstes Phase 2 — siehe [TODO.md](../TODO.md) für den Einstiegspunkt.

## Context

Ziel ist ein „agentisches OS" für Microsoft Fabric: Agenten, die die Fabric-Plattform
selbst betreiben, statt nur Fragen über Daten zu beantworten.

Als dieser Plan entstand, war das Repo `PowerUlf/agentic-fabric` leer — ein
Greenfield-Start.

**Zentrale Erkenntnis aus der Recherche:** Microsoft liefert die Tool-Ebene bereits.
Fabric Core MCP Server (remote, preview, `api.fabric.microsoft.com/v1/mcp/core`) deckt
Workspaces, Items, Rollen, Ordner, Capacities und Katalogsuche als MCP-Tools ab;
dazu kommen der lokale Fabric MCP Server, der RTI MCP Server und Data Agents, die
selbst MCP-Server sind. Terraform-Provider und `fabric-cicd` decken IaC und
Item-Deployment ab.

Ein weiterer MCP-Server, der REST-Calls durchreicht, wäre also Doppelarbeit. Was fehlt
— und was „OS" überhaupt erst rechtfertigt — ist die Schicht darüber: **Desired State,
Policy/Guardrails, Audit, Scheduling und Agenten-Orchestrierung.** Genau das wird
gebaut; die MS-Server sind die „Gerätetreiber".

**Angestrebtes Ergebnis dieses Plans:** ein lauffähiger vertikaler Schnitt — der
Workspace-Reconciler — auf einer Kernel-Architektur, die weitere „Systemprozesse"
(Job-Health, Deployment, Governance) ohne Umbau aufnimmt.

## Entscheidungen (mit dem Nutzer abgestimmt)

| Frage | Entscheidung |
|---|---|
| Kern | Ops zuerst, Architektur aber von Beginn an als Kernel |
| Runtime | MCP-Client zuerst, eigenständiger Dienst später |
| Stack | Python-Kern, TypeScript-UI später |
| Tenant | Vorhanden, mit Capacity → echte End-to-End-Verifikation möglich |
| Positionierung | Kernel auf den MS-MCP-Servern |
| Erster Slice | Workspace-Reconciler |

## Leitprinzip: modular, Linux-artig

Das System muss über Jahre wachsen — Data Engineering (Pipelines, Notebooks,
Lakehouse-Schemata) ist ausdrücklich ein späteres Ziel, nicht ein anderes Produkt.
Deshalb gilt von Phase 0 an:

**Der Kernel weiß nichts über Anwendungsfälle.** Er kennt nur Tools, Policy, Journal
und den Agent-Loop. „Workspace-Reconciler" ist kein Sonderfall im Kern, sondern das
erste *Modul* — genau wie später „job-health" oder „data-engineering".

**Ein Modul ist ein Verzeichnis, kein Sonderfall im Code.** Jedes Modul unter
`src/afabric/modules/<name>/` liefert:

| Datei | Rolle |
|---|---|
| `manifest.py` | Name, Version, benötigte Capabilities, CLI-Verben |
| `tools.py` | eigene Tools, die es beim ToolBus registriert (optional) |
| `model.py` | sein Stück des `fabric.yaml`-Schemas (optional) |
| `process.py` | die eigentliche Logik: `plan()` → `list[Change]`, `apply()` |

Entdeckt werden Module über Python-Entry-Points plus Verzeichnis-Scan. Ein neues Modul
hinzuzufügen heißt: Verzeichnis anlegen. Kein Registry-File editieren, kein Kern
anfassen — das ist der Test, an dem sich die Modularität messen lässt.

**Konfiguration kaskadiert wie `conf.d`.** `fabric.yaml` bleibt eine Datei, aber
`fabric.d/*.yaml` wird eingelesen und gemerged; jedes Modul besitzt seinen eigenen
Top-Level-Key und validiert nur diesen. Module ohne Konfiguration kosten nichts.

**Ein Schnittstellen-Typ für alle.** Jedes Modul spricht denselben `Change`-Typ, geht
durch dieselbe Policy und schreibt in dasselbe Journal. Ob eine Änderung eine
Rollenzuweisung oder ein Lakehouse-Schema ist, ändert nichts an Freigabe, Audit und
Dry-Run. Genau das macht später Data Engineering zu einem Modul statt zu einem Umbau.

**Kein Modul ruft ein anderes direkt auf.** Abhängigkeiten laufen über deklarierte
Capabilities am ToolBus. Der Reconciler stellt z. B. `workspace.ensure` bereit; ein
Data-Engineering-Modul nutzt das später, ohne den Reconciler zu importieren.

## Architektur

```
        Intent (NL / fabric.yaml)
                  │
    ┌─────────────▼──────────────────────────────┐
    │  Kernel                                    │
    │  agent-loop · policy · journal · state     │
    └─────────────┬──────────────────────────────┘
                  │  ToolBus (einheitliche Tool-Oberfläche)
        ┌─────────┴─────────┐
        ▼                   ▼
  Fabric Core MCP     native Tools
  (OAuth, RBAC,       (REST/SDK für Lücken:
   Audit)              Job-Health, Capacity-
                       Metriken, fabric-cicd)
```

**Warum ToolBus zwischen Kernel und MCP:** Core MCP authentifiziert per
OAuth-Browser-Flow. Für unbeaufsichtigte Agenten (Phase „Dienst") braucht es
Service-Principal-Tokens gegen die REST-API. Der ToolBus abstrahiert deshalb über den
*Transport*, nicht über die Semantik — dieselbe Tool-Signatur, Backend MCP oder REST je
nach Kontext. Das ist die einzige Stelle, an der diese Doppelung existiert.

**Abgrenzung zu Terraform:** Der Terraform-Provider bleibt die bessere Wahl für
Capacity- und Tenant-Infrastruktur. Unser Reconciler arbeitet eine Ebene darüber —
Intent statt HCL, erklärte Drift statt `plan`-Diff, Item- und Rollenebene, mit
Freigabe-Workflow. Kein Reimplementieren von Terraform.

## Repo-Struktur

```
agentic-fabric/
├── .mise.toml                    # python = "3.12"  (siehe unten)
├── pyproject.toml                # uv, requires-python >=3.10,<3.13
├── .env.example                  # FABRIC_TENANT_ID, CLIENT_ID, CLIENT_SECRET, ANTHROPIC_API_KEY
├── README.md                     # Vision, Architektur-Diagramm, Quickstart
├── docs/
│   ├── architecture.md
│   └── adr/0001-kernel-auf-ms-mcp.md   # die Entscheidung oben, festgehalten
├── src/afabric/
│   ├── kernel/
│   │   ├── config.py             # pydantic-settings, fabric.yaml + fabric.d/ merge
│   │   ├── modules.py            # Modul-Discovery via Entry-Points + Scan
│   │   ├── auth.py               # azure-identity: SP + interaktiv
│   │   ├── mcp_client.py         # MCP-Client → Core MCP (streamable HTTP)
│   │   ├── rest_client.py        # httpx + Paging, LRO-Polling, Retry bei 429
│   │   ├── toolbus.py            # Tool-Registry, Backend-Auswahl
│   │   ├── policy.py             # Guardrails: deny-Regeln, Blast-Radius, dry-run
│   │   ├── journal.py            # append-only JSONL-Audit
│   │   └── agent.py              # Agent-Loop, Anthropic Messages API
│   ├── model/
│   │   ├── change.py             # der gemeinsame Change-Typ aller Module
│   │   ├── desired.py            # Basis-Schema, Module hängen ihre Keys ein
│   │   └── observed.py           # Snapshot des Ist-Zustands
│   ├── modules/
│   │   └── workspace/            # Slice 1 — erstes Modul, kein Sonderfall
│   │       ├── manifest.py
│   │       ├── model.py
│   │       └── process.py        # plan() / apply(), diff rein funktional
│   └── cli.py                    # typer: afab status|plan|apply|explain|modules
├── examples/
│   ├── fabric.yaml
│   └── fabric.d/
└── tests/
    ├── fixtures/                 # aufgezeichnete API-Antworten
    └── test_diff.py
```

**Python-Version:** Das System hat 3.14. `ms-fabric-cli` verlangt 3.10–3.12,
`fabric-cicd` 3.9–3.13. Deshalb `.mise.toml` mit `python = "3.12"` — sonst scheitern die
Fabric-Tools bei der Installation.

**Kern-Dependencies:** `mcp`, `anthropic`, `azure-identity`, `httpx`, `pydantic`,
`pydantic-settings`, `pyyaml`, `typer`, `rich`; dev: `pytest`, `ruff`.

## Umsetzung in Phasen

Jede Phase endet mit einem Commit; die Phasen 1–3 sind einzeln verifizierbar.

### Phase 0 — Skeleton
Repo-Gerüst wie oben, `.gitignore` (`.env`, `.venv`, `__pycache__`, `*.jsonl`),
README mit Vision und ADR 0001. Initial Commit + Push nach `origin/main`.

### Phase 1 — Konnektivität
`auth.py` (`DefaultAzureCredential` bzw. `ClientSecretCredential`, Scope
`https://api.fabric.microsoft.com/.default`), `mcp_client.py`, `rest_client.py`,
minimaler `toolbus.py`. CLI-Befehl `afab status` listet Workspaces und Capacities.

*Verifikation:* `afab status` gegen den echten Tenant zeigt die realen Workspaces —
über beide Transporte (`--transport mcp` und `--transport rest`) dasselbe Ergebnis.

### Phase 2 — Modulsystem und erstes Modul
Zuerst der Kern-Mechanismus: `modules.py` (Discovery, Manifest, Capability-Registrierung),
`change.py` (gemeinsamer Typ, mit Risiko-Einstufung), Konfig-Kaskade `fabric.yaml` +
`fabric.d/*.yaml`. `afab modules` listet, was geladen wurde.

Dann das Modul `workspace` als erster Nutzer davon: sein Schema-Anteil (Workspaces mit
Name, Beschreibung, Capacity, Ordner, Rollenzuweisungen) und ein rein funktionales
`plan()`, das Desired gegen Observed vergleicht und `Change`-Objekte liefert
(create/update/delete/role-grant/role-revoke). Keine I/O in der Diff-Logik.

*Verifikation:* `pytest` gegen Fixtures — Drift in beide Richtungen, leerer Diff bei
Gleichstand, Idempotenz (`plan(a, apply(plan(a,b), b)) == []`). Dazu ein
Modularitäts-Test: ein Dummy-Modul im Testverzeichnis wird ohne jede Änderung am Kern
entdeckt, registriert und in `afab modules` gelistet.

### Phase 3 — Plan und Apply mit Guardrails
`policy.py`: destruktive Änderungen (Workspace-Löschung, Admin-Rollen-Entzug) erfordern
explizite Freigabe; konfigurierbare deny-Regeln; Blast-Radius-Limit (max. N Änderungen
pro Lauf). `journal.py` schreibt jede geplante und ausgeführte Änderung als JSONL.
`afab plan` zeigt den Diff farbig, `afab apply` führt nach Bestätigung aus — mit
LRO-Polling über `get_operation_state`/`get_operation_result`.

*Verifikation:* End-to-End gegen einen Wegwerf-Workspace im echten Tenant:
anlegen → `plan` zeigt leer → YAML ändern → `plan` zeigt genau die Änderung →
`apply` → `plan` wieder leer. Danach Workspace löschen und prüfen, dass die Policy
die Löschung ohne Freigabe blockiert.

### Phase 4 — Agent-Loop
`agent.py` mit der Anthropic Messages API und dem ToolBus als Tools. Zwei Fähigkeiten:
`afab explain` erklärt beobachtete Drift in Prosa samt Ursachenvermutung, und Intent in
natürlicher Sprache („neue Dev-Umgebung für Team Vertrieb") wird zu einem
`fabric.yaml`-Vorschlag, den der Nutzer prüft. Der Agent schlägt vor — anwenden tut
weiterhin der geprüfte Pfad aus Phase 3.

*Verifikation:* Drift manuell im Fabric-Portal erzeugen, `afab explain` muss sie
korrekt benennen.

### Phase 5 — später, nicht Teil dieses Plans
Weitere Module, jeweils als eigenes Verzeichnis ohne Kern-Änderung: `job-health`
(Job-Scheduler-API, Diagnose, Neustart), `deploy` (`fabric-cicd`), `governance`
(Regel-Audit), `data-engineering` (Notebooks, Pipelines, Lakehouse-Schemata — das
erklärte Fernziel). Dazu TypeScript-UI über dem Journal und Betrieb als Dienst mit
Service Principal.

Der Aufwand, das zweite Modul hinzuzufügen, ist der Gradmesser: fällt er deutlich
höher aus als „Verzeichnis anlegen", war die Kern-Abstraktion aus Phase 2 falsch
geschnitten und gehört korrigiert, bevor Modul drei entsteht.

## Voraussetzungen im Tenant

Vor Phase 1 zu klären — blockiert sonst die Verifikation:

- Tenant-Setting **„Service principals can use Fabric APIs"** aktiviert (nur für den
  headless/REST-Pfad; der interaktive MCP-Pfad läuft ohne)
- App-Registrierung mit Client Secret, oder interaktiver Login als Fallback
- Der Identität die Rolle **Admin** oder **Member** auf dem Test-Workspace geben

## Offene Punkte

- Core MCP ist **Preview** — Tool-Namen können sich ändern. Der ToolBus kapselt das;
  Tool-Namen gehören an genau eine Stelle.
- Rollenzuweisungen brauchen Principal-IDs. Auflösung von E-Mail-Adressen erfordert den
  Microsoft Graph MCP Server — in Phase 2/3 zunächst IDs im YAML, Graph optional später.
