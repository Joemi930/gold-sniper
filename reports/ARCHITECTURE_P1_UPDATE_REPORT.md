# Architecture.md — P1 Update Report

**Date:** 2026-06-26
**Branch:** `P1-Gold_sniper_trading_and_optimisation`
**Commit:** Pending (architecture P1 update)

---

## Fichiers lus pour cette mise a jour

| Fichier | Contenu |
|---------|---------|
| `architecture.md` | Document d'architecture complet (976 lignes avant mise a jour) |
| `CLAUDE.md` | Constitution operationnelle du repo |
| `README.md` | README du depot |
| `reports/P1_GOLD_SNIPER_REPLAY_APP_REPORT.md` | Rapport final P1 |
| `reports/DATA_PROVENANCE_AUDIT_REPORT.md` | Audit de provenance des donnees |
| `gold_sniper/replay_app/Gold_Sniper_Replay.py` | Point d'entree Replay Control Center |
| `gold_sniper/replay_app/live_runner.py` | Runner asyncio en thread background |
| `gold_sniper/replay_app/display.py` | TUI display helpers (Rich) |
| `gold_sniper/replay_app/report_writer.py` | Extraction trades, generation rapports |
| `gold_sniper/replay_app/data_prep.py` | Detection data, generation synthetique |
| `tools/data_import/import_mt5_history.py` | Import MT5 historique read-only |
| `tools/data_import/import_external_m1.py` | Importeur multi-source (histdata + Dukascopy) |
| `gold_sniper/data_pipeline/timeframe_aggregation.py` | Agregation deterministe timeframes |
| `gold_sniper/replay/run_replay.py` | Runner replay CLI legacy |
| `gold_sniper/replay/replay_engine.py` | Moteur de replay (~3200 lignes) |
| `gold_sniper/replay/simulated_trade_manager.py` | Gestion trades simules |
| `gold_sniper/replay/shadow_live_policy.py` | Policy shadow replay (grades, limits, sizing) |

**Total:** 17 fichiers lus et analyses.

---

## Sections ajoutees dans architecture.md

### Nouvelle section majeure : Phase 1 — Gold Sniper Replay Control Center V3.2

La section complete comprend 10 sous-sections :

| Sous-section | Titre | Lignes estimees |
|-------------|-------|----------------|
| 6B.1 | Architecture de l'application terminal | ~60 |
| 6B.2 | Flux complet du replay | ~75 |
| 6B.3 | Donnees P1 | ~70 |
| 6B.4 | Timeframes reconstruits | ~25 |
| 6B.5 | News pipeline | ~20 |
| 6B.6 | Rapports generes | ~25 |
| 6B.7 | Commandes d'utilisation | ~60 |
| 6B.8 | Garde-fous P1 | ~20 |
| 6B.9 | Etat final P1 | ~15 |
| 6B.10 | Handoff for Claude Opus 4.8 | ~40 |

**Total ajoute :** ~410 lignes de documentation P1.

### Contenu couvert

1. **Vue d'ensemble** — objectif P1, statut final, documents de reference, commits cles
2. **Architecture app terminal** — Gold_Sniper_Replay.py, menu interactif, mode CLI, affichage live, modules
3. **Flux complet replay** — diagramme ASCII du pipeline complet (data -> agents -> Kasper/PDE -> trades -> reports)
4. **Donnees P1** — M1 source de verite, provenance par periode, fermeture gap, spread realism, regles strictes
5. **Timeframes reconstruits** — tableau 7 TFs avec counts pre/post gap
6. **News pipeline** — CSV -> JSONL, 4,427 events, indexation, integration Agent6
7. **Rapports generes** — description de chaque fichier de sortie
8. **Commandes d'utilisation** — toutes les commandes exactes, ordre baseline
9. **Garde-fous P1** — 12 garde-fous documentes avec statut et verification
10. **Handoff Opus 4.8** — ordre de lecture, code a auditer, interdits, role apres baseline

---

## Incoherences corrigees

### Corrections dans les sections existantes

| Section | Ancien contenu | Nouveau contenu |
|---------|---------------|-----------------|
| **3. Flux data historique** | "M30 et D1 restent manquants" | Tous les TFs disponibles (cf. Section 6B.4) |
| **6. Chargement historique** | Donnees avril-juin 2026 seulement (64,375 M1) | Donnees completes Dec 2025 -> Jun 2026 (201,513 M1, 7 TFs) |
| **7. Data — Objectif P3** | M30 "manquant", D1 "manquant" | M30=6,644, D1=166, tous derives de M1 |
| **10. Partiellement valide** | M30/D1 manquants, news wiring a confirmer | M30/D1 resolus, news confirme |
| **10. Bloque** | 6 blocages originaux | 3 resolus (M30/D1, MT5 import, replay long), 1 ajoute (shadow diag perf), 3 restants |
| **10. Prochaine etape** | 8 etapes (centrees P3) | 9 etapes actualisees (centrees baseline P1) |
| **10. Valide/prouve** | 12 points | 7 points P1 ajoutes |
| **11. Historique** | Pas de mention P1 | Entree P1 ajoutee |
| **12. Zones a confirmer** | 8 zones, sans statut | 10 zones avec statut P1 (2 resolues, 2 ajoutees) |

### Incoherences specifiques corrigees

1. **Gap M1** : Toutes les references au "gap de 17 jours" ou "donnees incompletes" ont ete mises a jour vers "gap ferme"
2. **Total candles** : 185,692 -> 201,513 partout
3. **M30/D1** : "manquants" -> "reconstruits (6,644 et 166)"
4. **Spread histdata** : Non mentionne auparavant -> documente avec la correction 32 pts
5. **Replay long** : "bloque par P3" -> "tous presets disponibles"
6. **Couverture temporelle** : avril-juin -> Dec 2025 - Jun 2026

---

## Resume de l'etat P1

```
Verdict:         READY_FOR_FULL_BASELINE_REPLAYS
M1 candles:      201,513 (Dec 2025 -> Jun 2026, continu)
Timeframes:      7 (1m/5m/15m/30m/1H/4H/1D)
Gap M1:          FERME (14,447 candles gap-filling)
Spread histdata: 32 pts fixe (conservateur)
News:            4,427 events, 736 USD HIGH/MEDIUM
Replay smoke:    7 trades, 71.4% WR, +1.26% sur $100
Replay crossover: Gap traverse, 0 erreurs
P1 app:          V3.2, menu interactif + CLI, Rich TUI
Garde-fous:      12/12 verifices
Presets:         6/6 green (1w/1m/2m/3m/6m)
```

---

## Prochaines etapes

1. Lancer les 5 baselines (1w/1m/2m/3m/6m) sans optimisation
2. Collecter les metriques de chaque baseline
3. Analyser : WR, expectancy, drawdown, grade/session breakdowns, rejection patterns
4. Produire un rapport d'analyse comparative
5. **Seulement ensuite** : recommander des optimisations controlees
6. Apres 6 mois de validation positive : planifier l'unification live-safe

---

## Commit final

Fichiers modifies :
- `architecture.md` — Ajout Section 6B (Phase 1, ~410 lignes) + corrections incoherences
- `reports/ARCHITECTURE_P1_UPDATE_REPORT.md` — Ce fichier

Prochain commit : `docs(p1): add comprehensive Phase 1 section to architecture.md`
