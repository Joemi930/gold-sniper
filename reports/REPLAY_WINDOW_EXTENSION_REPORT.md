# Rapport - Extension de la fenetre replay Gold Sniper

Date: 2026-07-03  
Statut: OK valide localement  
Fenetre cible: 2024-01-01 -> 2026-06-30  
Symbole: XAUUSD / MT5 XAUUSD.m

## Objectif

Elargir `Gold_Sniper_Replay` pour que le replay dispose des bougies et news
necessaires sur toute la periode demandee: du 1 janvier 2024 au 30 juin 2026.

## Procedure appliquee

1. Verification de l'architecture replay existante:
   - application principale: `gold_sniper/replay_app/Gold_Sniper_Replay.py`
   - runner live: `gold_sniper/replay_app/live_runner.py`
   - runner offline: `gold_sniper/replay/run_replay.py`
   - racine donnees: `gold_sniper/data/historical/XAUUSD`

2. Import des bougies depuis MetaTrader 5 local:
   - terminal detecte: MetaTrader 5
   - symbole MT5 utilise: `XAUUSD.m`
   - broker: `JustMarkets-Demo3`
   - import read-only via `MetaTrader5.copy_rates_range`
   - `MaxBars` MT5 augmente de `100000` a `2000000` pour permettre l'historique long
   - backup MT5 conserve:
     `C:\Users\tetej\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\config\common.ini.codex-backup-20260703031033`

3. Recuperation des news FXStreet:
   - source: `https://www.fxstreet.com/economic-calendar`
   - recuperation par fenetres de 4 mois maximum
   - fenetres couvertes de `2024-01-01` a `2026-06-30`
   - normalisation en JSONL compatible avec le loader replay

4. Raccordement de l'application:
   - ancien calendrier 2025/2026 remplace par:
     `gold_sniper/data/historical/news/calendar_events_20240101_20260630.jsonl`
   - menu interactif mis a jour:
     - smoke: `2024-01-02 -> 2024-01-09`
     - 1 mois: `2024-01-02 -> 2024-02-01`
     - 4 mois: `2024-01-02 -> 2024-05-01`
     - 1 an: `2024-01-02 -> 2025-01-02`
     - full window: `2024-01-02 -> 2026-06-30`
   - defaults custom replay mis a jour:
     - start: `2024-01-02`
     - end: `2026-06-30`
     - warmup_start: `2024-01-01`

## Donnees disponibles

Manifest principal:
`gold_sniper/data/historical/XAUUSD/data_manifest.json`

Bougies importees:

| Timeframe | Bougies | Debut effectif | Fin effective |
| --- | ---: | --- | --- |
| 1m | 882,707 | 2024-01-02T01:00:00Z | 2026-06-30T23:57:00Z |
| 5m | 176,975 | 2024-01-02T01:00:00Z | 2026-06-30T23:55:00Z |
| 15m | 59,001 | 2024-01-02T01:00:00Z | 2026-06-30T23:45:00Z |
| 30m | 29,507 | 2024-01-02T01:00:00Z | 2026-06-30T23:30:00Z |
| 1H | 14,762 | 2024-01-02T01:00:00Z | 2026-06-30T23:00:00Z |
| 4H | 3,862 | 2024-01-02T00:00:00Z | 2026-06-30T20:00:00Z |
| 1D | 644 | 2024-01-02T00:00:00Z | 2026-06-30T00:00:00Z |

Total bougies: `1,167,458`

Note: la demande commence au 2024-01-01, mais la premiere bougie XAUUSD
disponible dans l'historique MT5 local commence le 2024-01-02. Le 2024-01-01
n'a pas de bougies de marche dans ce terminal.

News:

| Fichier | Evenements | Debut effectif | Fin effective |
| --- | ---: | --- | --- |
| `calendar_events_20240101_20260630.jsonl` | 22,328 | 2024-01-01T05:00:00Z | 2026-06-29T23:50:00Z |

## Validations effectuees

Commandes / controles passes:

- `python -m gold_sniper.replay_app.Gold_Sniper_Replay --check-data`
  - resultat: `overall_status = COVERAGE_OK`
  - timeframes manquants: aucun
  - couverture disponible: `2024-01-02T00:00:00Z -> 2026-06-30T23:57:00Z`

- controle manifest + fichiers CSV + calendrier:
  - `total_bars = 1,167,458`
  - timeframes presents: `1m, 5m, 15m, 30m, 1H, 4H, 1D`
  - tous les CSV existent et sont non vides
  - news chargees: `22,328`
  - erreurs news: aucune

- smoke replay final:
  - commande: `python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --engine v2 --fast --no-tui --minimal-events --run-id codex_final_validation_20240102 --start 2024-01-02 --end 2024-01-02 --warmup-start 2024-01-01`
  - resultat: execution terminee proprement
  - bougies M1 chargees: `1,377`
  - candidates: `601`
  - trades: `0`
  - summary: `gold_sniper/data/replay_runs/codex_final_validation_20240102/summary_v2.json`

## Fichiers modifies

- `gold_sniper/replay_app/Gold_Sniper_Replay.py`
- `gold_sniper/replay_app/live_runner.py`
- `gold_sniper/replay/run_replay.py`

## Points d'attention

- Les dossiers de donnees semblent ignores par Git: les CSV et JSONL sont bien
  presents localement, mais ne remontent pas dans `git status`.
- Le replay complet `2024-01-02 -> 2026-06-30` n'a pas ete lance entierement
  car il est lourd; la validation a confirme le chargement data/news et un
  replay smoke V2 final.
- Les gaps reportes dans le manifest MT5 incluent les fermetures normales de
  marche/week-ends et les discontinuites eventuelles de l'historique broker.

Conclusion: l'elargissement est implemente et valide localement. `Gold_Sniper_Replay`
utilise maintenant la fenetre longue et le nouveau calendrier FXStreet par defaut.
