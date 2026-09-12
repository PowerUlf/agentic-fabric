# TODO — Phase 4: Agent-Loop

Stand 2026-09-12. Phase 0 bis 3 sind fertig, committet und gepusht.
Plan: `docs/plan.md` (lokal, nicht im Repo)

## Wieder reinkommen

```bash
cd ~/omarchy/agentic-fabric
.venv/bin/afab status             # muss faf_dev zeigen, ohne Login
.venv/bin/afab modules            # workspace, keine Probleme
.venv/bin/pytest -q               # 94 grün

# Lesender Lauf gegen den Tenant. Nicht examples/fabric.yaml nehmen — die nennt die
# Capacity `my-fabric-capacity`, die es hier nicht gibt, und bricht mit PlanError ab.
.venv/bin/afab plan -f .afabric/e2e/fabric.yaml    # lokal, gitignored
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

- [ ] `afab status` prüfen: pausiert `capfabricf4`, scheitern Anlegen und Zuweisen
- [ ] Für Schreibtests wieder einen Wegwerf-Workspace nehmen, nie `faf_dev`.
      Die YAMLs von Phase 3 liegen unter `.afabric/e2e/` (gitignored, lokal).

## Phase 4 — Agent-Loop

`agent.py` mit der Anthropic Messages API und dem ToolBus als Tools, zwei Fähigkeiten:

- [ ] `afab explain` — beobachtete Drift in Prosa erklären, samt Ursachenvermutung
- [ ] Intent in natürlicher Sprache („neue Dev-Umgebung für Team Vertrieb") wird zu
      einem `fabric.yaml`-Vorschlag, den der Nutzer prüft
- [ ] Der Agent schlägt nur vor. Angewendet wird weiter über `runner.plan`/`runner.apply`
      aus Phase 3, mit Policy, Freigabe und Journal.

*Verifikation:* Drift von Hand im Fabric-Portal erzeugen, `afab explain` muss sie
korrekt benennen.

## Offen, ohne Eile

- [ ] `_create` plant `role.grant` auch für die eigene Identität. Fabric macht den
      Ersteller automatisch zum Admin, ein solcher Grant würde beim Anlegen scheitern.
      In Phase 3 umgangen, indem die E2E-YAML keine eigene Rolle deklariert.
- [ ] LRO über MCP ungetestet: `RestBackend` wartet bei 202 auf die Operation,
      `McpBackend` nicht. Keine der Workspace- und Ordner-Operationen kam bisher
      asynchron zurück. Der MCP-Server bietet `get_operation_state`/`get_operation_result`,
      falls es nötig wird.
- [ ] **`assign_to_capacity` ist live ungetestet** — und damit der REST-Fallback über den
      MCP-Transport, das einzige architektonisch neue Stück im ToolBus. In der
      Verifikation kam es nie dran, weil der Workspace seine Capacity schon beim Anlegen
      bekam und `workspace.assign_capacity` deshalb nie geplant wurde. Nur Unit-Tests.
- [ ] **`OperationHasNoResult` ist geraten.** `await_operation` liest diesen Fehlercode
      als „Operation ohne Ergebnis". Kein Aufruf in Phase 3 kam asynchron zurück, der
      Code ist also nie gelaufen. Fällt im Zweifel sicher aus: ein unerwarteter Fehler
      fliegt, statt als Ergebnis durchzugehen.
- [ ] Principal-IDs vs. E-Mail — Graph MCP Server, später
- [ ] Service Principal für den unbeaufsichtigten REST-Pfad — Phase 5
