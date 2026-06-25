# CLEANUP_BASELINE_REPORT

Date: 2026-06-25

Statut: local clean baseline prepared; GitHub remote creation/push blocked until manual owner action.

## Archive obligatoire

Archive complete creee avant suppression:

```text
C:\Users\tetej\Music\Bug bounty\Gold_Sniper_ARCHIVE_before_clean_20260625_083252.zip
```

Verification effectuee:

- archive creee hors du repo nettoye;
- taille: environ 565 MB;
- contient l'ancien `.git/HEAD`;
- contient `architecture.md`, `CLAUDE.md`, `gold_sniper/replay/replay_engine.py`;
- contient les anciens docs P3, dont `docs/P3_TRADE_LIFECYCLE_DATA_REPLAY_VALIDATION_GOAL.md`.

## Etat initial audite

```text
git status --short
 D AGENTS.md
 M architecture.md
 M docs/P3_A_STABILISATION_PREREQUIS_REPORT.md
 D docs/calendar-event-list.csv
 M gold_sniper/replay/replay_engine.py
 M gold_sniper/replay/run_replay.py
 M gold_sniper/replay/simulated_trade_manager.py
 M gold_sniper/replay/trade_journal.py
 M gold_sniper/tests/test_p3_trade_lifecycle_two_legs.py
?? .mcp.json
?? .serena/
?? GOLD_SNIPER_ULTIMATE_GOAL_LOOP_PROTOCOL.md
?? Phase2_2_Scenario_Identity_Side_Consistency_prompt_macon.md
?? calendar-event-list.csv
?? docs/P3_B_PAYOFF_REPLAY_1M_REPORT.md
?? docs/P3_TRADE_LIFECYCLE_DATA_REPLAY_VALIDATION_GOAL.md
?? gold_sniper/data/historical/news/calendar_events_manifest.json
?? gold_sniper/validation/session_breakdown.py

git branch --show-current
P1-kasper-brain-core

git log -1 --oneline
09852486 feat(p3): two-leg lifecycle, pure R, profiler, session breakdown
```

L'ancien depot contenait de nombreuses branches locales P1/P2/P3, archivees dans le zip via l'ancien `.git`.

## Supprime du repo propre

Categories supprimees:

- anciens rapports et docs de phase: `docs/`;
- anciens logs et rapports runtime: `logs/`, `gold_sniper/logs/`;
- anciens dossiers de preflight/reports: `P1_PREFLIGHT_RECOVERY/`, `reports/`;
- prompts/protocoles de phase obsoletes a la racine;
- caches Python: `__pycache__`, `.pytest_cache`, `*.pyc`;
- donnees historiques et replay generees: `gold_sniper/data/historical/`, `gold_sniper/data/replay_runs/`, `gold_sniper/data/validation_reports/`;
- donnees news generees: `gold_sniper/data/news/`, `calendar-event-list.csv`;
- fichiers runtime/sensibles locaux: credentials, tokens, locks, pid, inbox, watchdog state;
- lanceurs/build artifacts Windows obsoletes dans `gold_sniper/`;
- metadata locale `.serena/` et `.mcp.json`;
- ancien `.git` apres archive complete.

## Conserve

Fichiers racine conserves:

- `README.md`;
- `.gitignore`;
- `architecture.md`;
- `CLAUDE.md`;
- `CLEANUP_BASELINE_REPORT.md`.

Code source conserve:

- `gold_sniper/`;
- `tools/`;
- tests sources dans `gold_sniper/tests/`;
- requirements existant: `gold_sniper/requirements.txt`.

Donnees conservees dans `gold_sniper/data/`:

- `__init__.py`;
- `historical_loader.py`;
- `memory_db.py`.

Toutes les donnees generees/runtime ont ete retirees du snapshot propre.

## Structure finale attendue

```text
.
├── .gitignore
├── README.md
├── CLAUDE.md
├── architecture.md
├── CLEANUP_BASELINE_REPORT.md
├── gold_sniper/
└── tools/
```

Principaux sous-dossiers source conserves dans `gold_sniper/`:

```text
agents/
backtesting/
context/
core/
data/
data_pipeline/
execution/
orchestrator/
replay/
safety/
scrapers/
scripts/
strategies/
strategy/
tests/
tools/
ui/
utils/
validation/
watchdog/
web/
web_app/
```

## Commandes Git locales utilisees

```powershell
git status --short
git branch --show-current
git log -1 --oneline
tar -a -cf C:\Users\tetej\Music\Bug bounty\Gold_Sniper_ARCHIVE_before_clean_20260625_083252.zip .
tar -tf C:\Users\tetej\Music\Bug bounty\Gold_Sniper_ARCHIVE_before_clean_20260625_083252.zip
git init
git branch -M main
git add .
git commit -m "chore: clean Gold Sniper source baseline"
```

## GitHub manuel recommande

Ne pas detruire l'ancien repo GitHub sans archive/renommage explicite.

Action recommandee cote GitHub, a executer par le proprietaire Joemi:

1. Renommer ou archiver l'ancien repo en:

```text
gold-sniper-legacy-archive
```

2. Creer un nouveau repo vide:

```text
gold-sniper
```

3. Connecter le depot local propre:

```powershell
git remote add origin https://github.com/Joemi/gold-sniper.git
git push -u origin main
```

Si l'organisation ou l'URL exacte differe, remplacer `https://github.com/Joemi/gold-sniper.git` par l'URL du nouveau repo cree manuellement.

## Statut final

Statut local: clean baseline prepared.

Statut GitHub: blocked/manual owner action required.

Prochaine etape:

1. Creer/renommer les repos GitHub manuellement.
2. Ajouter le remote `origin`.
3. Pousser `main`.
4. Verifier sur GitHub que seuls les fichiers source propres sont presents.
