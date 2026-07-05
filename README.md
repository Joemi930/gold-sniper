# Gold Sniper

Gold Sniper est une application Python de recherche, diagnostic et replay de setups XAUUSD. Le projet vise a transformer une pile d'agents ICT/SMC en moteur de decision auditable, testable et reproductible avant toute remise en service live.

Depot officiel : [Joemi930/gold-sniper](https://github.com/Joemi930/gold-sniper)

## Statut actuel

Le projet est actuellement en mode **offline / replay-only / shadow-only**.

Cela signifie :

- aucun ordre broker ne doit etre envoye ;
- `gold_sniper/main.py` ne doit pas etre lance pour trader ;
- `LIVE_MODE=1` ne doit pas etre active ;
- le chemin replay ne doit pas etre connecte a l'orchestrateur live ;
- les validations passent par des replays historiques, des tests et des rapports.

La branche de travail active est `P1-Gold_sniper_trading_and_optimisation`.

## Objectif de l'application

Gold Sniper sert a analyser des donnees historiques XAUUSD et a produire une decision structuree autour de plusieurs couches :

- contexte HTF et regime de marche ;
- zones POI / OB / FVG ;
- liquidite, sweep et reaction autour du POI ;
- OTE et timing de session ;
- micro-confirmation M1 ;
- decision professionnelle, readiness, risque et explication du rejet.

Le projet ne cherche pas a forcer des entrees. Un resultat `NO_TRADES` est un etat valide si les conditions strategiques ne sont pas reunies.

## Architecture actuelle

### Replay

Le coeur actuel est le replay historique :

- `gold_sniper/replay/replay_engine.py` : moteur legacy full scan ;
- `gold_sniper/replay/replay_engine_v2.py` : orchestration candidate-driven P4.2 ;
- `gold_sniper/replay/feature_store.py` : features timestamped avec protection no-lookahead ;
- `gold_sniper/replay/candidate_discovery.py` : detection de fenetres candidates via gates peu couteux ;
- `gold_sniper/replay/candidate_window.py` : appel de la pile lourde uniquement dans une fenetre candidate ;
- `gold_sniper/replay/metrics_aggregator.py` : agregation des decisions, trades, blockers et etat `NO_TRADES` ;
- `gold_sniper/replay/profiler_v2.py` : profiling par section avec `unaccounted_ms`.

### Decision

Le chemin decisionnel canonique est `gold_sniper/strategy/`.

Il contient notamment :

- contrats et types de decision ;
- EvidenceBundle ;
- Kasper scenario engine ;
- ProfessionalDecisionEngine ;
- RiskAllocator ;
- contrats POI, micro, readiness et risk gate ;
- taxonomie des setups.

Le dossier `gold_sniper/strategies/` reste present pour des selecteurs legacy et diagnostics/reporting. Il n'est pas le coeur canonique de decision.

### Agents

Les agents publient des observations specialisees dans le blackboard :

- Agent 1 : meteo / contexte HTF ;
- Agent 2 : cartographie POI ;
- Agent 3 : liquidite ;
- Agent 4 : Fibonacci / OTE ;
- Agent 5 : microscope M1 ;
- Agent 6 : news / sentinelle ;
- Agent 7 : session / timing.

La logique des agents, de Kasper, du PDE et du RiskAllocator est protegee : les travaux P4.2 changent quand la pile est appelee, pas ce qu'elle decide.

## Etat P4.2

La phase P4.2 introduit une architecture replay V2 candidate-driven :

```text
candle M1
  -> FeatureStore.update()
  -> TradeLifecycleSimulator.on_candle()
  -> CandidateDiscoveryEngine.scan()
  -> CandidateWindowEvaluator.evaluate() uniquement si fenetre candidate
  -> MetricsAggregator.finalize()
```

Etat de validation local connu :

- suite pytest complete verte : `1676 passed` ;
- test parity contract present : `gold_sniper/tests/test_parity_one_day.py` ;
- rapport de validation : `reports/P4_2_ARCHITECTURE_VALIDATION_REPORT.md` ;
- parity 1 jour partiellement validee sur trade count : V2 `0`, legacy `0`, `trade_count_match=true` ;
- validation semaine fast P4.2 non acceptee : le run `week_v2` a depasse 3 minutes sans produire `summary_v2.json` lors du dernier passage.

P4.2 ne doit donc pas etre presentee comme totalement acceptee tant que :

- la parity full/fast 1 jour ne prouve pas le hash decisions + `ENTER` + trades ;
- le replay fast 1 semaine ne termine pas en moins de 3 minutes ;
- le mois fast n'a pas ete lance apres validation de la semaine.

## Installation

Depuis la racine du depot :

```powershell
python -m pip install -r gold_sniper/requirements.txt
python -m pip install pytest
```

Certaines fonctionnalites historiques utilisent MetaTrader5, Discord, aiohttp, pandas ou pyarrow. Le mode replay offline ne doit pas envoyer d'ordre broker.

## Commandes utiles

### Tests

```powershell
python -m pytest gold_sniper/tests -q
python -m pytest gold_sniper/tests/test_parity_one_day.py -q
```

### Replay P4.2

Parity 1 jour :

```powershell
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --engine v2 --parity `
  --start 2025-12-08 --end 2025-12-09 `
  --warmup-start 2025-12-01 `
  --run-id parity_1d `
  --initial-equity 100
```

Replay fast 1 semaine :

```powershell
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --engine v2 --fast `
  --start 2025-12-08 --end 2025-12-15 `
  --warmup-start 2025-12-01 `
  --run-id week_v2 `
  --initial-equity 100
```

Ne pas lancer le replay fast 1 mois tant que la parity et la semaine ne sont pas validees.

## Structure du depot

```text
gold_sniper/
  agents/          Agents 1 a 7 et contrats de handoff
  core/            Blackboard, engine, orchestrateur, MT5 bridge legacy
  execution/       Trade manager, broker gateway, risk calculator
  replay/          Moteurs replay, FeatureStore, CandidateDiscovery, rapports
  replay_app/      CLI de replay et affichage
  strategy/        Pile decisionnelle canonique
  strategies/      Selecteurs legacy et diagnostics/reporting
  tests/           Suite pytest/unittest
  tools/           Imports et diagnostics offline
reports/           Rapports de validation
docs/              Documentation de gouvernance
```

## Securite et gouvernance

Regles actives :

- ne pas forcer `ENTER` ;
- ne pas baisser les seuils ou affaiblir les veto/session/news/risk ;
- ne pas modifier les chemins live broker/order send pendant les travaux replay ;
- ne pas supprimer un module strategique sans audit d'import non-test ;
- ne pas pousser automatiquement vers GitHub.

Voir aussi :

- `AGENTS.md` pour les guardrails P4.2 ;
- `docs/research_branch_governance.md` pour le mode research shadow-only ;
- `reports/P4_2_ARCHITECTURE_VALIDATION_REPORT.md` pour l'etat de validation le plus recent.

## Donnees sensibles

Ne pas commiter :

- `.env` ;
- tokens Discord, GitHub ou API ;
- identifiants MT5 ;
- bases SQLite locales ;
- logs, caches, exports historiques volumineux ;
- fichiers de session ou credentials locaux.

## Avertissement

Gold Sniper est un outil de recherche et de simulation. Il ne constitue pas un conseil financier. Toute activation live doit etre precedee d'une validation statistique explicite, reproductible et documentee.
