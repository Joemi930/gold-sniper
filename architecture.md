# Architecture Gold Sniper - documentation centrale

Audit local realise le 2026-06-25 dans `C:\Users\tetej\Music\Bug bounty\Trading`.

Ce document decrit l'architecture reelle du depot local Gold Sniper. Il consolide les contrats de `CLAUDE.md`, `GOLD_SNIPER_ULTIMATE_GOAL_LOOP_PROTOCOL.md`, `.claude/loop.md`, les rapports `docs/FINAL_OPUS_REPORT.md`, `docs/P3_TRADE_LIFECYCLE_DATA_REPLAY_VALIDATION_GOAL.md`, les rapports P2/P3, et le code source audite.

Principe de lecture important :

- Le chemin **replay/shadow Kasper/PDE** est le chemin le plus documente et valide par les rapports P2/P3.
- Le chemin **live** existe dans le code (`core/*`, `execution/*`, agents live, blackboard), mais il reste en partie legacy : le pipeline live actuel ne prouve pas encore l'integration directe `EvidenceBuilder -> KasperScenarioEngine -> PDE -> RiskAllocator -> BrokerGateway`.
- Toute partie non prouvee par le code ou les rapports est marquee **À confirmer**.

---

## 1. Vision globale

### Objectif ultime

Gold Sniper est une application de trading XAUUSD basee sur une lecture SMC/ICT/Kasper. L'objectif cible, repete dans les protocoles du projet, est :

```text
Analyser -> filtrer -> decider -> executer en simulation -> gerer -> cloturer -> journaliser -> prouver
```

Le systeme doit produire des decisions defendables, traçables et rejouables. Une entree n'est valide que si elle est soutenue par une chaine complete d'evidence : contexte HTF, liquidite, sweep, reintegration, displacement, structure, POI tradable, confirmation micro, session/news/risk acceptables.

### Philosophie Kasper / SMC

La philosophie Kasper formalisee dans le depot est une sequence stricte :

```text
HTF bias
  -> liquidity pool
  -> sweep
  -> reintegration
  -> displacement
  -> BOS / CHoCH
  -> OB / FVG / imbalance
  -> retest
  -> micro confirmation
  -> risk realism
  -> ENTER / WAIT / REJECT
```

Le systeme ne doit pas "chercher un trade". Il doit attendre que les preuves se construisent. L'absence de POI, de micro trigger, de session valide, de risk realism ou de news clearance doit produire `WAIT`, `WATCH_ONLY` ou `REJECT`, pas un contournement.

### Comportement cible en live / shadow / live-safe

En conditions live ou shadow, Gold Sniper doit :

1. Recevoir les ticks/candles XAUUSD.
2. Construire et mettre a jour les timeframes utiles.
3. Evaluer les agents de contexte, POI, liquidite, structure, micro, news, session et risk.
4. Construire une evidence complete.
5. Produire une decision `ENTER`, `WAIT` ou `REJECT`.
6. Calculer le risque autorise.
7. Simuler ou executer uniquement si tous les garde-fous passent.
8. Gerer le cycle de vie du trade.
9. Cloturer proprement.
10. Journaliser les decisions, evenements, trades, metriques et raisons de blocage.
11. Produire une preuve replayable.

### Interdits absolus

Le depot et les protocoles interdisent explicitement :

| Interdit | Raison |
|---|---|
| Forced `ENTER` | Detruit la preuve strategique et invalide la validation. |
| Baisse opportuniste des seuils | Assimile a du tuning non prouve. |
| `POI_REACTION` tradable | Setup d'observation, pas d'execution. |
| Bypass risk/news/session | Contourne les hard veto. |
| `LIVE_MODE` non valide | Le live n'est pas autorise sans validation longue et gates explicites. |
| `order_send` hors broker gateway | Toute ecriture broker doit passer par `execution/broker_gateway.py` et `execution/execution_guard.py`. |
| MT5 write dans agents/strategie/replay/data import | MT5 doit rester confine a l'execution ou au read-only historique. |
| Replay long avant prerequisites P3 | La phase P3 bloque les replays longs tant que data/import/news/lifecycle ne sont pas stabilises. |

---

## 2. Architecture generale

### Carte des dossiers

| Dossier / fichier | Role |
|---|---|
| `CLAUDE.md` | Constitution operationnelle du repo : doctrine, interdits, commandes, workflow. |
| `GOLD_SNIPER_ULTIMATE_GOAL_LOOP_PROTOCOL.md` | Vision cible, protocole de boucle, agents, doctrine Kasper, interdits. |
| `.claude/loop.md` | Route P3 actuelle : lifecycle, payoff, MT5 import, calendar, acceleration, long validation. |
| `docs/` | Rapports de phase, objectifs, validations, limites connues. Source de verite historique. |
| `gold_sniper/agents/` | Agents live historiques : HTF, POI, liquidite, structure/OTE, micro, news, sessions, risk manager. |
| `gold_sniper/strategy/` | Contrats strategiques, Kasper scenario engine, PDE, risk allocator, readiness, scorecard, taxonomy, veto registry. |
| `gold_sniper/replay/` | Replay offline/shadow : chargement data, decision pipeline, evidence builder, simulated trade manager, journaux, rapports. |
| `gold_sniper/core/` | Runtime live : blackboard, MT5 bridge, tick ingestion, candle builder, engine, orchestrator. |
| `gold_sniper/execution/` | Gateway broker, execution guard, trade manager live, runtime MT5, position/order helpers. |
| `gold_sniper/data/` | Donnees historiques locales, manifests, runs replay, news normalisees. |
| `gold_sniper/data_pipeline/` | Manifest candles, aggregation timeframes, JSONL news, scripts pipeline data. |
| `gold_sniper/validation/` | Validateurs, rapports de performance P1/P2/P3, breakdowns. |
| `gold_sniper/tests/` | Tests unitaires, integration, regressions, safety, replay, P2/P3. |
| `tools/data_import/` | Import MT5 historique read-only et normalisation calendrier. |
| `gold_sniper/safety/` | Garde-fous de branche/research et interdiction broker writes. |
| `gold_sniper/backtesting/`, `scrapers/`, `ui/`, `web/`, `watchdog/`, `utils/` | Support historique, UI, observabilite, outils et composants secondaires. |

### Flux de dependances internes

#### Flux live actuel dans le code

```text
MetaTrader5
  -> gold_sniper.core.mt5_bridge.MT5Bridge
  -> tick_ingestion.ingest_ticks
  -> candle_builder.CandleBuilder
  -> core.blackboard.BLACKBOARD
  -> agents live Agent1..Agent7 / RiskManager
  -> core.orchestrator.orchestrator_loop
  -> BLACKBOARD["trade_signals"]
  -> execution.trade_manager.TradeManager
  -> execution.broker_gateway.BrokerGateway
  -> execution.execution_guard.ExecutionGuard
  -> MT5BrokerAdapter.order_send
```

Etat reel : ce chemin live est present, mais l'orchestrateur live actuel est legacy. Il agrege des resultats d'agents et produit des `trade_signals`; il ne prouve pas encore l'appel direct au pipeline Kasper/PDE moderne.

#### Flux replay/shadow Kasper actuel

```text
CSV candles + JSONL/CSV news
  -> replay.run_replay
  -> replay.replay_engine.ReplayEngine
  -> replay.decision_pipeline.ReplayDecisionPipeline
  -> ReplayAgent1..ReplayAgent7
  -> replay.evidence_builder.EvidenceBuilder
  -> strategy.professional_decision_engine.evaluate_professional_decision
  -> strategy.kasper_scenario_engine.KasperScenarioEngine
  -> replay alignment bridge
  -> replay.simulated_trade_manager.SimulatedTradeManager
  -> events / decisions / trade_journal / summary
```

Etat reel : ce chemin porte les validations P2/P3, la logique deux legs, le risk realism replay, les journaux et les rapports.

#### Flux data historique

```text
MT5 read-only / CSV local
  -> tools/data_import/import_mt5_history.py
  -> gold_sniper/data/historical/XAUUSD/<timeframe>/*.csv
  -> data_pipeline/candle_manifest.py
  -> manifest / gaps report
  -> replay.run_replay
```

Le protocole P3 demande M1 comme source de verite, puis derivation/verification des timeframes M5/M15/M30/H1/H4/D1. L'etat local audite contient un chemin replay actif sur la periode complete Dec 2025 -> Jun 2026 avec M1 (201,513 candles), M5, M15, M30, H1, H4 et D1. Tous les timeframes requis sont disponibles (cf. Section 6B.3 et 6B.4 pour l'etat final P1).

### Fichiers critiques

| Fichier | Criticite |
|---|---|
| `gold_sniper/config.py` | Run mode, live flags, risk constants, sessions, cooldown, broker-write defaults. |
| `gold_sniper/core/blackboard.py` | Etat partage live : market data, agents, trade signals, positions, risk, events. |
| `gold_sniper/core/orchestrator.py` | Orchestrateur live legacy et emission de `trade_signals`. |
| `gold_sniper/execution/execution_guard.py` | Gate fail-closed avant broker write. |
| `gold_sniper/execution/broker_gateway.py` | Unique chemin attendu pour `order_send`. |
| `gold_sniper/execution/trade_manager.py` | Gestion live des ordres, cooldown, spread, volume, TP1/SL protege/trailing. |
| `gold_sniper/replay/decision_pipeline.py` | Orchestration des agents replay et assemblage evidence/decision. |
| `gold_sniper/replay/evidence_builder.py` | Construction de l'evidence exploitee par PDE/Kasper. |
| `gold_sniper/strategy/kasper_scenario_engine.py` | Moteur sequence Kasper, scoring, grade, scenario identity. |
| `gold_sniper/strategy/professional_decision_engine.py` | Decision `ENTER/WAIT/REJECT` a partir evidence, veto, readiness, scorecard, risk. |
| `gold_sniper/strategy/risk_allocator.py` | Mapping grade -> risk et garde-fous d'allocation. |
| `gold_sniper/replay/simulated_trade_manager.py` | Cycle de vie replay deux legs P3. |
| `gold_sniper/replay/trade_journal.py` | Journal trades/evenements/synthese. |
| `tools/data_import/import_mt5_history.py` | Import MT5 historique read-only. |
| `tools/data_import/normalize_calendar_csv.py` | Normalisation calendrier news. |

---

## 3. Strategie Gold Sniper / Kasper

### Sequence logique complete

Gold Sniper cherche des reversals ou continuations institutionnelles, mais la validation P2/P3 documentee se concentre sur la chaine Kasper de type sweep reversal.

```text
1. HTF context
   - biais dominant
   - structure H1/H4/D1 si disponible
   - premium / discount

2. Liquidite
   - pools buyside / sellside
   - equal highs / equal lows
   - niveau sweepable

3. Sweep
   - sweep BSL/SSL ou buyside/sellside
   - rejet ou reintegration dans la zone

4. Displacement
   - impulsion apres sweep
   - bougie ou sequence directionnelle

5. Structure
   - BOS / CHoCH / close breaking structure
   - alignement avec le scenario

6. POI
   - OB / FVG / imbalance / zone institutionnelle
   - POI tradable et non profondement mitige

7. Retest
   - retour dans la zone
   - prix meilleur ou zone d'entree

8. Micro confirmation
   - CHoCH/BOS micro
   - trigger/retest
   - niveaux entry/SL/TP exploitables

9. Risk realism
   - RR minimum
   - spread/slippage/cost model
   - daily limiter/cooldown/session/news

10. Decision
   - ENTER_FULL / ENTER_REDUCED
   - WAIT_FOR_TRIGGER / WAIT_FOR_BETTER_PRICE / WATCH_ONLY
   - REJECT
```

### Grades

| Grade | Role actuel |
|---|---|
| `A_PLUS` | Setup complet ou quasi complet. En replay P3, les trades executes sont reportes comme `A_PLUS`. Risk cible 1.00%. |
| `A` | Setup fort mais potentiellement en attente de meilleur prix/trigger selon readiness. Risk cible 0.75%. |
| `B` | Setup acceptable reduit. Risk cible 0.50%, souvent `ENTER_REDUCED` si eligible. |
| `C_CONFIRMED` | Grade introduit dans la doctrine P3 pour autoriser un C strictement confirme a 0.25%. À confirmer dans le contrat core `SetupGrade`, car le mapping replay/shadow P3 et le mapping strategy ne sont pas totalement identiques. |
| `C` | Observation/watch ou attente. Dans `risk_allocator.py`, un C peut avoir base 0.25, mais les entrees doivent rester bloquees si non eligible. |
| `D` | Rejet. Risk 0. |

### Conditions d'entree

Une entree doit avoir au minimum :

- hard veto absents ;
- session/news/risk acceptables ;
- setup type executable, principalement `SWEEP_REVERSAL` ;
- POI tradable ;
- sweep valide et coherent avec le side ;
- micro confirmation ;
- niveaux entry/SL/TP exploitables ;
- RR precheck valide ;
- duplicate gate et daily limiter valides ;
- decision `ENTER_FULL` ou `ENTER_REDUCED` ;
- risk plan autorise.

### Conditions de rejet ou attente

| Cas | Decision attendue |
|---|---|
| News high impact / blackout / hostile feed | `REJECT` ou hard veto. |
| Session interdite, Asia block, Friday halt | `REJECT` ou hard veto. |
| Spread ou risk unsafe | `REJECT`. |
| POI manquant | `WAITING_POI`, `WAIT`, `WATCH_ONLY` ou `REJECT`. |
| Micro trigger manquant | `WAIT_FOR_TRIGGER`. |
| Prix pas assez bon | `WAIT_FOR_BETTER_PRICE`. |
| Setup non executable comme `POI_REACTION` | `WATCH_ONLY` ou `REJECT`, jamais trade. |
| Duplicate scenario ou daily limit atteint | Blocage trade. |
| Side mismatch | Blocage / rejet. |

### Sessions, news, volatilite, cooldown, daily limiter

| Mecanisme | Source principale | Role |
|---|---|---|
| Sessions / killzones | `gold_sniper/agents/agent_7_chronos.py`, `gold_sniper/config.py`, replay Agent7 | Autoriser Londres/NY, filtrer Asia, vendredi, hors session. |
| News | `agent_6_sentinelle.py`, `replay/news_index.py`, calendrier JSONL/CSV | Appliquer blackout, post-news, hostile feed. |
| Volatilite / spread | Agent6, risk manager, execution model, MT5 symbol info | Eviter execution dans conditions anormales. |
| Cooldown | `config.py`, `execution/trade_manager.py`, replay policy | Eviter sur-trading et entrees trop proches. |
| Daily limiter | `simulated_trade_manager.py`, shadow live policy, risk manager | Limiter le nombre de trades standard et exceptionnels. |

### Logique deux legs P3

La phase P3 remplace le cycle single-position par :

```text
1 signal = 1 parent setup = 2 child legs
```

Regles P3 documentees :

| Element | Regle |
|---|---|
| Risk total parent | `A_PLUS` 1.00%, `A` 0.75%, `B` 0.50%, `C_CONFIRMED` 0.25%. |
| Split | Risk total partage egalement entre leg TP1 et leg TP2. |
| TP1 | Premier objectif. Quand TP1 est touche, le runner est protege. |
| TP2 | Deuxieme objectif. |
| Full SL direct | `-1R` parent. |
| TP1 + protected SL | `+0.75R` parent. |
| TP1 + TP2 | `+1.5R` parent. |
| Protected SL | Runner remonte a `+0.5R` apres TP1 dans le replay P3. |

Attention : `gold_sniper/config.py` contient encore `BE_PLUS_RR = 0.10` pour le live legacy, alors que la doctrine P3 replay utilise `0.5R`. C'est une divergence connue a traiter avant toute live-safe activation.

---

## 4. Agents et composants

### Vue synthetique

| Composant | Objectif | Inputs | Outputs | Fichiers principaux |
|---|---|---|---|---|
| Agent1 HTF / contexte / bias | Lire le regime HTF et le biais. | Candles HTF, swings, structure. | Biais, tendance, structure, score. | `agents/agent_1_meteo.py`, replay Agent1. |
| Agent2 POI / OB / FVG / imbalance | Identifier les zones institutionnelles. | Candles, swings, displacement, HTF context. | POI, OB, FVG, imbalance, proximite. | `agents/agent_2_cartographe.py`, `strategy/poi_contracts.py`. |
| Agent3 liquidite / sweep | Detecter pools et sweeps. | Highs/lows, equal levels, candles. | Liquidity events, sweep side/type. | `agents/agent_3_liquidite.py`, `strategy/liquidity_contracts.py`. |
| Agent4 structure / scenario | Lire structure, OTE, premium/discount, scenario. | HTF/MTF candles, POI anchors. | Structure, OTE, scenario hints. | `agents/agent_4_fibonacci.py`, replay Agent4. |
| Agent5 micro trigger / execution | Detecter trigger micro et niveaux. | M1/M5 candles, POI, liquidity. | Micro confirmation, entry, SL, TP, RR. | `agents/agent_5_microscope.py`, `strategy/micro_contracts.py`. |
| Agent6 news/session/context | Bloquer conditions externes dangereuses. | Calendar, spread, volatility, feed state. | Veto/news status/context safety. | `agents/agent_6_sentinelle.py`, `replay/news_index.py`. |
| Agent7 risk/session timing | Gerer sessions/killzones et timing. | Time UTC, session config. | Session status, Asia/Friday flags. | `agents/agent_7_chronos.py`. |
| EvidenceBuilder | Construire evidence unifiee. | Results agents replay, candles, context. | `EvidenceBundle`. | `replay/evidence_builder.py`, `strategy/contracts.py`. |
| KasperScenarioEngine | Verifier sequence Kasper et grader. | Evidence/KasperEvidenceBundle. | Scenario result, score, missing confluence, grade. | `strategy/kasper_scenario_engine.py`. |
| PDE | Transformer evidence en decision professionnelle. | Evidence, hard veto, readiness, scorecard, risk. | `DecisionResult`. | `strategy/professional_decision_engine.py`. |
| RiskAllocator | Calculer risk autorise. | Grade, decision, setup, guards. | `RiskPlan`. | `strategy/risk_allocator.py`. |
| SimulatedTradeManager | Simuler cycle trade replay. | Decisions/replay candles. | Trades, events, summary, safety counters. | `replay/simulated_trade_manager.py`. |
| ReplayEngine | Orchestrer replay complet. | Data historiques, pipeline, manager. | Reports, journals, summaries. | `replay/replay_engine.py`, `replay/run_replay.py`. |
| Broker/live gateway | Encapsuler broker writes. | Trade intent, guard context. | Order result ou blocage. | `execution/broker_gateway.py`, `execution/execution_guard.py`. |

### Agent1 - HTF / context / bias

- Objectif : produire le contexte HTF, le biais et la structure de marche.
- Inputs : candles HTF/MTF, swings, highs/lows, historique blackboard en live ou replay dataframe.
- Outputs : `AgentResult`, observation Agent1, biais bullish/bearish/neutral, structure, trend.
- Fichiers/classes/fonctions : `AgentMeteo`, `detect_swings`, `classify_market_structure`, `calculate_agent_1_result`, `build_agent_1_observation`.
- Lecture/ecriture : en live, lit `BLACKBOARD["market_data"]` et ecrit dans les resultats agents ; en replay, le pipeline construit une observation normalisee.
- Utilisation downstream : Agent2/Agent4 utilisent le contexte ; EvidenceBuilder/PDE/Kasper l'utilisent pour la gate HTF.
- Garde-fous : neutralite possible ; pas d'ENTER seul.
- Limites connues : disponibilite D1/M30 incomplete localement ; le replay actif se concentre surtout sur M1/M15/H4.

### Agent2 - POI / OB / FVG / imbalance

- Objectif : identifier les POI institutionnels : order blocks, fair value gaps, imbalances, zones de retest.
- Inputs : candles, contexte HTF, displacement, anchors structure/liquidite.
- Outputs : POI selectionne, type, bounds, side, tradable/non-tradable, mitigation.
- Fichiers/classes/fonctions : `AgentCartographe`, fonctions FVG/OB/POI, `build_p2a_poi_connectivity_payload`, `build_agent_2_observation`, `strategy/poi_contracts.py`.
- Lecture/ecriture : alimente le blackboard live et l'evidence replay.
- Utilisation downstream : KasperScenarioEngine gate "tradable POI", Agent5 retest, PDE scorecard/readiness.
- Garde-fous : `POI_REACTION` ne doit pas etre executable ; POI mitige/profond ou manquant doit bloquer l'entree.
- Limites connues : les rapports P2/P3 ont documente des phases `POI_MISSING` puis des corrections de connectivite. Toute entree basee sur POI doit rester auditee.

### Agent3 - liquidite / sweep

- Objectif : detecter pools de liquidite et sweeps.
- Inputs : highs/lows, equal highs/lows, candles recentes.
- Outputs : `LiquidityEvent`, side, sweep type, swept level, reintegration hints.
- Fichiers/classes/fonctions : `AgentLiquidite`, equal levels, liquidity event, sweep detection, handoff P2-A, `strategy/liquidity_contracts.py`.
- Lecture/ecriture : ecrit evenement de liquidite dans agent results/evidence.
- Utilisation downstream : Kasper sequence, micro confirmation, scenario identity, duplicate gate.
- Garde-fous : sweep doit etre coherent avec le side ; P2.3 a corrige le mismatch `SWEEP_BSL/SSL` vs `buyside/sellside`.
- Limites connues : un sweep seul ne donne jamais une entree ; il declenche l'attente de reintegration/displacement/structure/micro.

### Agent4 - structure / BOS / CHoCH / scenario

- Objectif : lire structure, OTE, premium/discount et aider la construction du scenario.
- Inputs : HTF/MTF candles, POI anchors, swings.
- Outputs : structure shift, zone OTE, premium/discount, scenario hints.
- Fichiers/classes/fonctions : `AgentFibonacci`, fonctions OTE et handoff avec Agent2 ; replay Agent4 dans `replay/decision_pipeline.py`.
- Lecture/ecriture : ecrit resultats agents et observation evidence.
- Utilisation downstream : Kasper gate "structure shift", readiness, scorecard.
- Garde-fous : structure manquante ou side mismatch maintient `WAIT`/`REJECT`.
- Limites connues : le nom historique "Fibonacci" couvre plus que Fibonacci ; documenter toute extension future dans strategy contracts.

### Agent5 - micro trigger / execution

- Objectif : confirmer le trigger micro et produire des niveaux exploitables.
- Inputs : M1/M5 candles, POI, sweep, structure, session/risk context.
- Outputs : micro confirmation, CHoCH/BOS micro, entry, SL, TP, RR candidate, trigger status.
- Fichiers/classes/fonctions : `AgentMicroscope`, `AMDPhase`, `AMDState`, `build_agent_5_observation`.
- Lecture/ecriture : ecrit micro result et niveaux ; en live, ces niveaux peuvent etre repris par `core/orchestrator.py` pour un `trade_signal`.
- Utilisation downstream : Kasper gate "micro confirmation", PDE readiness, trade manager/simulated manager.
- Garde-fous : absence de micro trigger = `WAIT_FOR_TRIGGER` ; niveaux incomplets = pas d'execution.
- Limites connues : P2.3 a identifie Agent5 comme bottleneck strict apres correction POI/sweep. Ne pas le contourner.

### Agent6 - news / contexte externe

- Objectif : bloquer les conditions exogenes dangereuses.
- Inputs : calendrier news CSV/JSONL, UTC time, spread/volatilite/feed state.
- Outputs : blackout, post-news, high impact, hostile feed, veto ou clear.
- Fichiers/classes/fonctions : `AgentSentinelle`, `replay/news_index.py`, `tools/data_import/normalize_calendar_csv.py`.
- Lecture/ecriture : met a jour blackboard/risk context ; alimente hard veto dans replay.
- Utilisation downstream : PDE hard veto, Kasper hard block, execution guard.
- Garde-fous : news veto doit bloquer l'entree, pas reduire seulement le score.
- Limites connues : le calendrier normalise Jan-Jun existe, mais le chemin par defaut exact du replay vers le fichier le plus recent doit etre confirme avant replay long.

### Agent7 - sessions / timing

- Objectif : qualifier session, killzone, vendredi et timing.
- Inputs : timestamp UTC, config sessions.
- Outputs : session label, killzone, Asia block, Friday halt.
- Fichiers/classes/fonctions : `AgentSessions`, `build_agent_7_observation`.
- Lecture/ecriture : alimente blackboard et evidence.
- Utilisation downstream : hard veto, readiness, risk modulators.
- Garde-fous : Asia block, vendredi ou hors session peuvent bloquer.
- Limites connues : les horaires exacts doivent rester UTC et coherents entre live/replay.

### EvidenceBuilder

- Objectif : convertir les resultats agents replay en `EvidenceBundle` structure.
- Inputs : observations replay Agents1..7, candles, contexte, niveaux.
- Outputs : `EvidenceBundle` dans `strategy/contracts.py`.
- Fichiers/classes/fonctions : `gold_sniper/replay/evidence_builder.py`.
- Lecture/ecriture : lit les observations replay ; n'envoie pas d'ordre ; transmet au PDE/Kasper.
- Utilisation downstream : source d'evidence pour `evaluate_professional_decision` et `KasperScenarioEngine`.
- Garde-fous : nettoie les champs d'execution interdits ; conserve seulement les champs analytiques utiles.
- Limites connues : actuellement documente et utilise dans replay, pas prouve comme source live directe.

### KasperScenarioEngine

- Objectif : verifier la sequence Kasper et produire une identite de scenario.
- Inputs : `EvidenceBundle` ou `KasperEvidenceBundle`.
- Outputs : `KasperScenarioResult`, `scenario_id`, `scenario_key`, score, grade, missing confluence, hard veto reason.
- Fichiers/classes/fonctions : `strategy/kasper_scenario_engine.py`, `strategy/kasper_contracts.py`, `strategy/scenario_identity.py`.
- Lecture/ecriture : pur calcul strategique ; pas d'I/O broker.
- Utilisation downstream : PDE/alignment bridge/replay manager, diagnostics et journaux.
- Garde-fous : hard veto avant scoring ; gates sequence ; RR precheck ; scenario identity stable pour duplicate gate.
- Limites connues : integration live directe non prouvee dans `core/orchestrator.py`.

### PDE - ProfessionalDecisionEngine

- Objectif : transformer une evidence en decision professionnelle.
- Inputs : evidence, hard veto, scorecard, readiness, enter eligibility, risk allocator.
- Outputs : `DecisionResult` avec decision, grade, action, reasons, risk.
- Fichiers/classes/fonctions : `strategy/professional_decision_engine.py`, `strategy/readiness.py`, `strategy/scorecard.py`, `strategy/enter_eligibility.py`, `strategy/hard_veto_registry.py`.
- Lecture/ecriture : pur calcul ; pas d'ordre ; appele par replay pipeline.
- Utilisation downstream : SimulatedTradeManager et rapports replay.
- Garde-fous : hard veto => `REJECT`; non eligible => pas d'ENTER ; mode attendu `SHADOW_ONLY`.
- Limites connues : coexistence avec orchestrateur live legacy ; unification live/PDE reste à confirmer.

### RiskAllocator

- Objectif : convertir grade/action/setup en risk plan.
- Inputs : grade, action, enter eligibility, setup type, risk guards, multipliers.
- Outputs : `RiskPlan` avec `risk_pct`, `risk_amount`, allowed/disallowed reason.
- Fichiers/classes/fonctions : `strategy/risk_allocator.py`.
- Lecture/ecriture : pur calcul.
- Utilisation downstream : PDE, replay policy, sizing.
- Garde-fous : risk 0 si non eligible, action non executable, risk guard hit ou setup non autorise.
- Limites connues : mapping `C_CONFIRMED` P3 a harmoniser avec enum/contrats core si necessaire.

### SimulatedTradeManager

- Objectif : simuler le cycle complet des trades en replay.
- Inputs : decisions, candles M1, execution model, daily policy.
- Outputs : parent trades, legs TP1/TP2, events, journal, summary, safety counters.
- Fichiers/classes/fonctions : `replay/simulated_trade_manager.py`, `replay/trade_journal.py`, `replay/shadow_live_policy.py`, `replay/execution_model.py`, `replay/fill_model.py`.
- Lecture/ecriture : ecrit journaux de replay, trade events, summaries ; ne contacte pas MT5.
- Utilisation downstream : validation P3, metriques, rapports.
- Garde-fous : duplicate gate, daily limiter, setup tradability, side consistency, no broker write.
- Limites connues : replay conservateur ; divergence possible entre pure_R et net_R selon couts.

### ReplayEngine

- Objectif : orchestrer un replay complet.
- Inputs : CSV/parquet candles, manifest, calendrier news, config CLI.
- Outputs : decisions, events, `trade_journal`, `summary.json`, rapports de validation.
- Fichiers/classes/fonctions : `replay/replay_engine.py`, `replay/run_replay.py`, `replay/decision_pipeline.py`.
- Lecture/ecriture : charge historique local et ecrit dans `gold_sniper/data/replay_runs/*`.
- Utilisation downstream : rapports P2/P3 et criteres avant live.
- Garde-fous : live orchestrator interdit en replay P1-clean ; no broker writes.
- Limites connues : replay long bloque tant que P3-C/P3-D/P3-E ne sont pas stabilises.

### Broker gateway / live gateway

- Objectif : etre le seul point de passage des ecritures broker.
- Inputs : `BrokerRequest` ou intention ordre, contexte guard.
- Outputs : `BrokerResult` execute ou bloque.
- Fichiers/classes/fonctions : `execution/broker_gateway.py`, `execution/execution_guard.py`, `execution/trade_manager.py`, `execution/mt5_runtime.py`.
- Lecture/ecriture : `MT5BrokerAdapter.send_order` appelle `mt5.order_send` seulement apres `ExecutionGuard`.
- Utilisation downstream : trade manager live.
- Garde-fous : run mode live, `ALLOW_BROKER_WRITES`, research branch guard, kill switch, pause, Agent6 veto, risk manager veto, paper forced.
- Limites connues : live non valide ; `TradeManager` legacy gere TP1/protected SL avec constantes live anciennes.

---

## 5. Flux live reel

### Origine des donnees marche

Le live code attend MetaTrader5 via :

- `gold_sniper/core/mt5_bridge.py` pour connexion, ticks, symbol info, historique, account info ;
- `gold_sniper/execution/mt5_runtime.py` comme runtime d'import MT5 ;
- `gold_sniper/core/tick_ingestion.py` pour lire les ticks ;
- `gold_sniper/core/candle_builder.py` pour construire les candles.

### Entree des ticks/candles

```text
MT5 tick
  -> MT5Bridge.get_tick()
  -> tick_ingestion.ingest_ticks()
  -> BLACKBOARD["market_data"]["current_tick"]
  -> CandleBuilder.update_tick()
  -> deques M1/M15/H4
  -> BLACKBOARD candle close events
```

Le `BLACKBOARD` contient aussi market analysis, risk management, agent results, trade signals, positions, performance et controle runtime.

### Lecture par les agents

Les agents live lisent le blackboard et ecrivent des `AgentResult` :

```text
BLACKBOARD market_data / context
  -> Agent1..Agent7 loops
  -> BLACKBOARD["agent_results"]
  -> orchestrator_loop
```

### Construction d'une decision live actuelle

Dans l'etat actuel du code :

1. `core/engine.py` lance les boucles tick, candle, agents, orchestrator, trade manager.
2. Les agents publient leurs scores/resultats dans le blackboard.
3. `core/orchestrator.py` attend candle close ou evenement critique.
4. Il applique hard filters, pondérations agents, session/risk/news checks.
5. Il peut produire `EXECUTE`, `WAIT`, `NO_TRADE`, `EXCEPTIONAL_OVERRIDE`.
6. Si les niveaux Agent5 existent, il ecrit un signal dans `BLACKBOARD["trade_signals"]`.
7. `execution/trade_manager.py` recupere ce signal, applique cooldown/spread/sizing/guard.
8. L'ordre live eventuel passe par `BrokerGateway`.

Ce chemin est fonctionnellement present mais legacy. Le document de reference live-safe doit considerer que la decision Kasper/PDE moderne est encore **À confirmer** dans le chemin live.

### Risk live et autorisation execution

Le live applique plusieurs couches :

| Couche | Fichier | Role |
|---|---|---|
| Config run mode | `config.py` | `RUN_MODE`, `LIVE_MODE`, `ALLOW_BROKER_WRITES`, risk constants. |
| Research branch guard | `safety/research_branch_guard.py` | Bloque broker writes sur branche de recherche. |
| Execution guard | `execution/execution_guard.py` | Fail-closed avant action broker. |
| Trade manager | `execution/trade_manager.py` | Cooldown, spread, tick, SL/TP, volume, position lifecycle. |
| Broker gateway | `execution/broker_gateway.py` | Encapsule `order_send`. |

### Ou un ordre serait envoye

Le seul chemin broker write attendu est :

```text
TradeManager.place_order()
  -> BrokerGateway.execute(BrokerRequest(action=OPEN_ORDER))
  -> ExecutionGuard.evaluate(...)
  -> MT5BrokerAdapter.send_order()
  -> mt5.order_send(...)
```

Tout autre `order_send` dans le code metier serait une violation architecturale.

### Garde-fous contre ordre dangereux

- `RUN_MODE` par defaut replay.
- `ALLOW_BROKER_WRITES` faux par defaut.
- research branch guard.
- kill switch global.
- pause trading.
- Agent6 veto.
- risk manager veto.
- paper mode forced.
- session/news/spread/cooldown.
- SL/TP obligatoires.
- volume calcule selon risk et limites symbol.
- duplicate/daily gates cote replay/shadow.

---

## 6. Replay et validation

### Role du replay

Le replay sert a prouver la strategie sans broker write :

- reproduire les decisions sur donnees historiques ;
- tester la chaine agents/evidence/PDE/Kasper ;
- simuler le lifecycle deux legs ;
- mesurer win rate, expectancy, payoff, drawdown, safety counters ;
- produire des journaux auditables.

### Difference replay vs live

| Dimension | Replay | Live |
|---|---|---|
| Donnees | CSV/parquet historiques, calendrier JSONL/CSV. | Ticks/candles MT5 live. |
| Execution | `SimulatedTradeManager`, fill model conservateur. | `TradeManager` + `BrokerGateway`, non valide live. |
| Decision moderne | EvidenceBuilder/PDE/Kasper actifs. | À confirmer dans orchestrateur live. |
| Journaux | `data/replay_runs/*`. | Blackboard, logs, trade manager, notifications. |
| Broker write | Interdit. | Bloque par defaut et non autorise sans validation. |

### Chargement historique

`replay/run_replay.py` charge les timeframes depuis `gold_sniper/data/historical/XAUUSD`. Le manifest audite pour la periode P1 complete contient :

- M1 : `2025-12-01T00:00Z` a `2026-06-26T03:46Z`, **201,513 candles**.
- M5 : `2025-12-01T00:00Z` a `2026-06-26T03:45Z`, 40,253 candles.
- M15 : `2025-12-01T00:00Z` a `2026-06-26T03:45Z`, 13,366 candles.
- M30 : `2025-12-01T00:00Z` a `2026-06-26T03:30Z`, 6,644 candles.
- H1 : `2025-12-01T00:00Z` a `2026-06-26T03:00Z`, 3,284 candles.
- H4 : `2025-12-01T00:00Z` a `2026-06-26T00:00Z`, 738 candles.
- D1 : `2025-12-01T00:00Z` a `2026-06-26T00:00Z`, 166 candles.
- Duplicates OHLC : 0 selon manifest.
- Sources : histdata.com (Dec 2025 - Mar 2026) + MT5 JustMarkets-Demo3 (Mar - Jun 2026).
- Spread : 32 pts fixe sur segment histdata.com, reel sur segment MT5.

### Reproduction des decisions

`ReplayDecisionPipeline` orchestre :

```text
Replay agents
  -> EvidenceBuilder
  -> ProfessionalDecisionEngine
  -> KasperScenarioEngine / alignment bridge
  -> DecisionResult
```

Les decisions sont ensuite donnees au `SimulatedTradeManager`.

### Simulation lifecycle

Le cycle P3 simule :

- parent setup ;
- 2 legs ;
- TP1 ;
- protection runner a +0.5R ;
- TP2 ;
- protected SL ;
- full SL ;
- partial closes ;
- pure_R/net_R selon rapports et couts.

### Rapports produits

Les runs ecrivent notamment :

- `summary.json` ;
- events ;
- trade journal ;
- decisions ;
- metriques performance ;
- safety counters ;
- breakdowns par grade/setup/session/news/agent.

### Limites du replay actuel

- Le rapport final Opus annonce une validation 2 mois, mais la doctrine P3 la considere insuffisante pour live.
- Les replays longs 6/12 mois sont bloques tant que MT5 import, M30/D1, news et acceleration ne sont pas stabilises.
- L'execution model est conservateur ; l'ecart pure_R/net_R doit etre interprete.
- Un diagnostic de `summary.json` P3 montre encore des compteurs `risk_positive_by_setup_type` incluant `POI_REACTION`, alors que la doctrine interdit `POI_REACTION` tradable. Les rapports indiquent 0 entree `POI_REACTION`; ce point doit etre confirme comme artefact de reporting ou mapping intermediaire.
- Fast-precomputed est mentionne comme stub/partiel dans les rapports.

---

## Phase 1 — Gold Sniper Replay Control Center V3.2

### Vue d'ensemble

La Phase 1 (P1) est la base operationnelle de la derniere ligne droite Gold Sniper. Elle fournit une application terminal complete pour lancer des replays offline rapides, propres, sans logs inutiles, avec des rapports compacts lisibles par un LLM.

**Objectif P1 :** lancer des replays de baseline (1 semaine, 1 mois, 2 mois, 3 mois, 6 mois) sur des donnees reelles completes, sans fuite du futur, sans live trading, sans modification strategique automatique, avec un capital initial force de **$100.00**. Le but est d'etablir une baseline de performance brute, puis d'optimiser de facon controlee vers 1-2 trades/jour avec WR >= 70%.

**Statut final P1 :** `READY_FOR_FULL_BASELINE_REPLAYS`

**Documents de reference :**
- `reports/P1_GOLD_SNIPER_REPLAY_APP_REPORT.md` — rapport final P1 complet
- `reports/DATA_PROVENANCE_AUDIT_REPORT.md` — audit de provenance des donnees
- `CLAUDE.md` — constitution operationnelle (phase P1-READY)

**Commits cles :**
- `89f19c3` — base P1-clean
- `6ac648c` — Replay Control Center V3.2
- `c4eb4b3` — pipeline data externe histdata.com
- `fa0b183` — audit complet de provenance
- `7277d73` — fermeture du gap M1 + spread realism fix

---

### 6B.1 Architecture de l'application terminal

#### Fichier principal

`gold_sniper/replay_app/Gold_Sniper_Replay.py` (~800 lignes)

L'application propose deux modes :

| Mode | Commande | Usage |
|------|----------|-------|
| **Interactif** | `python -m gold_sniper.replay_app.Gold_Sniper_Replay` | Menu fleche avec Rich TUI, navigation clavier, lancement interactif |
| **CLI** | `--no-menu --start ... --end ... --warmup-start ... --run-id ... --initial-equity 100` | Automatisation, scripts, CI |

#### Menu interactif (fleches haut/bas)

Presets integres :

| Touche | Preset | Periode | Warmup |
|--------|--------|---------|--------|
| 1 | 1-week smoke | 2026-01-01 -> 2026-01-08 | Dec 2025 |
| 2 | 1-month | 2026-01-01 -> 2026-02-01 | Dec 2025 |
| 3 | 2-month | 2026-01-01 -> 2026-03-01 | Dec 2025 |
| 4 | 3-month | 2026-01-01 -> 2026-04-01 | Dec 2025 |
| 5 | 6-month | 2026-01-01 -> 2026-06-01 | Dec 2025 |
| 0 | Synthetic test data | — | — |
| C | Custom replay | Saisie interactive | Saisie interactive |

Options avancees : choix des agents, capital initial, timeframes, news ON/OFF, profiler ON/OFF.

#### Affichage live pendant le replay

Pendant l'execution, le terminal affiche en temps reel via Rich :

- **Agent workspace** : statut et score des 7 agents (Agent1 bias, Agent2 POI, Agent3 liquidite, Agent4 structure, Agent5 micro, Agent6 news, Agent7 sessions)
- **Progress bar** : progression des candles (warmup + eval)
- **Metriques live** : equity, P&L, winrate, trades, decision courante
- **Decision panel** : derniere decision PDE (ENTER_FULL/ENTER_REDUCED/WAIT/REJECT), grade, score, setup type
- **Stop** : touche Echap pour arreter proprement le replay

Implementation technique :
- Thread asyncio dedie pour le replay (via `live_runner.py`)
- Queue thread-safe (`queue.Queue`) pour communication replay -> TUI
- `LiveState` dataclass — etat thread-safe pour l'affichage
- Fallback texte simple si Rich n'est pas installe

#### Modules de l'app

| Module | Role |
|--------|------|
| `replay_app/Gold_Sniper_Replay.py` | Point d'entree, menu, orchestration CLI/interactive |
| `replay_app/live_runner.py` | Runner asyncio en thread background, hook display, `LiveState` |
| `replay_app/display.py` | Layout Rich (agents, metriques, progress bar, decision panel) |
| `replay_app/report_writer.py` | Extraction de trades, generation rapports compacts, nettoyage logs |
| `replay_app/data_prep.py` | Detection disponibilite data, generation synthetique, import MT5 lazy |

---

### 6B.2 Flux complet du replay

```
CSV candles M1 completes (201,513 candles, Dec 2025 -> Jun 2026)
  │
  ├─> MultiTimeframeBuilder
  │     └─> Derivation deterministe M5/M15/M30/H1/H4 depuis M1
  │         (UTC-anchored, no lookahead, bar-closure-gated)
  │
  ├─> ReplayClock
  │     └─> Iteration progressive candle par candle
  │         (index, ts_utc, ts_ny, session_label, bar_closed)
  │
  ├─> BlackBoard (market_data, agent_results, meta.replay)
  │     └─> Injection progressive: _m1_window (240 candles max)
  │         _inject_external_timeframe: seules les candles <= current_time
  │
  ├─> Agents 1-7 (replay variants)
  │     Agent1 (Meteo)      -> HTF bias, structure, tendance
  │     Agent2 (Cartographe) -> POI, OB, FVG, imbalance, zones
  │     Agent3 (Liquidite)   -> Pools, sweep detection, reintegration
  │     Agent4 (Fibonacci)   -> BOS/CHoCH, OTE, premium/discount
  │     Agent5 (Microscope)  -> Micro confirmation, entry/SL/TP, trigger
  │     Agent6 (Sentinelle)  -> News blackout, spread, hostile feed
  │     Agent7 (Chronos)     -> Session label, Asia block, Friday halt
  │
  ├─> EvidenceBuilder
  │     └─> EvidenceBundle unifie depuis observations agents
  │
  ├─> KasperScenarioEngine
  │     └─> Sequence gate: HTF -> liquidite -> sweep -> displacement
  │         -> BOS -> POI -> retest -> micro -> risk realism
  │         -> Scoring, grade A+..D, scenario identity
  │
  ├─> ProfessionalDecisionEngine (PDE)
  │     └─> Hard veto -> Readiness -> Scorecard -> Eligibility
  │         -> ENTER_FULL / ENTER_REDUCED / WAIT / REJECT
  │
  ├─> RiskAllocator
  │     └─> Grade -> risk_pct (A_PLUS=1.00%, A=0.75%, B=0.50%, C=0.25%, D=0)
  │
  ├─> ShadowLivePolicy + SimulatedTradeManager
  │     └─> Daily limiter, duplicate gate, grade executability
  │     └─> 2-leg lifecycle: TP1 (1R) + runner protege (+0.5R) -> TP2 (2R)
  │     └─> Fill model conservateur, execution model
  │
  └─> Reports
        ├─> trade_journal.jsonl    (events ouverts/fermetures/legs)
        ├─> events.jsonl           (decisions, snapshots, erreurs)
        ├─> summary.json           (metriques globales, 50+ blocs diagnostic)
        ├─> decisions.jsonl        (historique decisions PDE/Kasper)
        ├─> REPORT.md              (rapport compact lisible LLM)
        ├─> metrics.json           (metriques cles machine)
        ├─> important_trades.jsonl (meilleurs trades avec P&L)
        └─> optimization_findings.json (suggestions automatisees)
```

**Principes critiques :**

1. **Injection progressive** : `ReplayEngine._inject_candle()` n'injecte que la candle courante. Les timeframes externes (M5, M15, H1, H4) avancent via un pointeur `_external_indices` qui ne depasse jamais `candle["time"] <= current_time`.

2. **Warmup decembre autorise comme contexte** : La periode de warmup (decembre 2025) est injectee avec `eval_active=False`. Les agents construisent leur contexte (structure HTF, swing points, session tracking) mais aucun trade n'est comptabilise dans les metriques d'evaluation. La methode `_phase_for_candle()` determine `eval_active = timestamp >= eval_start`.

3. **Aucune bougie future dans la decision** : Chaque agent recoit `(candle, blackboard)` — la candle courante + l'etat accumule du passe. Aucun agent ne lit le dataset complet. L'acces direct a `clock._candles` est confirme UNIQUEMENT dans `_build_summary()` pour les diagnostics post-hoc.

4. **ReplayEngine simule le temps reel accelere** : `ReplayClock` itere sequentiellement sur les candles M1. `ReplayTick` contient `ts_utc`, `ts_ny`, `session_label` derives de la candle. La progression est deterministe et reproductible.

---

### 6B.3 Donnees P1

#### M1 source de verite

| Propriete | Valeur |
|-----------|--------|
| **Fichier** | `XAUUSD_1m_COMPLETE_2025-12-01_2026-06-26.csv` |
| **Candles** | **201,513** |
| **Periode** | 2025-12-01 00:00 UTC -> 2026-06-26 03:46 UTC |
| **Continuite** | Continue (gap Feb-Mar ferme) |
| **Doublons** | 0 |
| **Ordre** | Chronologique strict |
| **Timezone** | UTC (tous les timestamps terminent par Z) |
| **Colonnes** | time, open, high, low, close, tick_volume, volume, spread, real_volume |
| **Taille** | 12.7 MB |

#### Provenance par periode

| Periode | Source | Candles | tick_volume | spread |
|---------|--------|---------|-------------|--------|
| 2025-12-01 -> 2026-02-27 | **histdata.com** | 85,657 | 0 (non fourni) | **32 pts fixe** |
| 2026-03-01 -> 2026-03-31 | **histdata.com** (gap fill) | 30,595 | 0 (non fourni) | **32 pts fixe** |
| 2026-03-16 -> 2026-06-26 | **MT5 JustMarkets-Demo3** | 85,261 | Reel (18-300+) | Reel (28-36 pts) |

#### Fermeture du gap M1 (2026-06-26)

L'ancien gap de 17 jours entre les sources histdata.com et MT5 :

```
2026-02-27 16:58 UTC (derniere candle histdata.com Dec-Feb)
       |
       |  14,447 candles gap-filling (histdata.com Mars 2026)
       |
2026-03-16 04:51 UTC (premiere candle MT5)
```

**Statut : GAP FERME.** 14,447 candles comblent la periode manquante. Les seuls gaps restants sont des weekends standard (48-55h, 25 occurrences), un weekend de Paques 2026 (73h, 2-6 avril, legitime), et 11 micro-gaps sub-30min (artefacts de flux de donnees).

#### Spread realism

**Probleme :** histdata.com ne fournit que OHLCV — `tick_volume=0`, `spread=0`. 57.7% du dataset (116,252 candles) auraient un cout de trading nul sans correction.

**Solution :** spread fixe conservateur de **32 points** applique a toutes les candles histdata.com (`tick_volume=0`). Base sur la mediane de la plage observee MT5 XAUUSD.m (28-36 pts). Les candles MT5 conservent leur spread reel.

**Impact :** couts de trading realistes. Pas d'embellissement des resultats. La valeur de 32 pts est legerement en-dessous de la mediane donc conservative (sous-estime legerement les couts -> plus pessimiste).

#### Regles strictes

- Pas de donnees synthetiques dans les baselines
- UTC partout
- 0 doublon (verifie par `Counter(timestamps)`)
- Ordre chronologique strict (verifie par `sorted(ts) == ts`)
- Colonnes compatibles replay (9 colonnes standard)
- Backup cree avant chaque merge (`.csv.bak`)

---

### 6B.4 Timeframes reconstruits

Depuis le M1 gap-closed, tous les timeframes superieurs ont ete reconstruits via `MultiTimeframeBuilder` (deterministe, UTC-anchored, no lookahead) :

| TF | Candles | Pre-gap | Delta | Methode |
|----|---------|---------|-------|---------|
| **1m** | 201,513 | 185,692 | +15,821 | Merge histdata.com + MT5 |
| **5m** | 40,253 | 37,184 | +3,069 | MultiTimeframeBuilder (barres de 5 min) |
| **15m** | 13,366 | 12,398 | +968 | MultiTimeframeBuilder (barres de 15 min) |
| **30m** | 6,644 | 6,200 | +444 | MultiTimeframeBuilder (barres de 30 min) |
| **1H** | 3,284 | 3,102 | +182 | MultiTimeframeBuilder (barres de 60 min) |
| **4H** | 738 | 825 | -87* | MultiTimeframeBuilder (barres de 240 min) |
| **1D** | **166** | **N/A (nouveau)** | — | Agregation manuelle depuis M1 |

\* La reduction en 4H est due a un builder plus strict (bar-closure-gated vs l'ancien aggregateur).

**Regle :** M1 reste la source de verite. Les TF superieurs servent au contexte HTF (Agent1, Agent4, Kasper) et doivent rester synchronises avec M1. Toute reconstruction future doit passer par `MultiTimeframeBuilder` pour garantir la coherence.

---

### 6B.5 News pipeline

| Propriete | Valeur |
|-----------|--------|
| **Fichier source** | `calendar-event-list.csv` (ForexFactory) |
| **Fichier normalise** | `XAUUSD_news_2025-12-31_2026-06-19.jsonl` |
| **Evenements** | 4,427 |
| **USD HIGH/MEDIUM** | 736 |
| **Couverture** | 2025-12-31 -> 2026-06-19 |
| **Timezone** | UTC (converti pendant normalisation) |
| **Indexation** | `NewsIndex` — bisect-based O(log n) lookup |
| **Integration replay** | Agent6 (Sentinelle) lit depuis le JSONL indexe |

**Role dans le replay :**
- Agent6 applique blackout, post-news, hostile feed
- Hard veto si news HIGH impact dans la fenetre de blackout
- Le `NewsIndex` est construit au lancement du replay (`run_replay.py` ligne 134-149)

**Script de normalisation :** `tools/data_import/normalize_calendar_csv.py`

---

### 6B.6 Rapports generes

Chaque replay produit un repertoire `reports/replay/<run_id>/` :

| Fichier | Contenu | Usage |
|---------|---------|-------|
| `REPORT.md` | Resume compact lisible par LLM : metriques, trades, grade perf, rejections, suggestions | Lecture humaine/Opus rapide |
| `metrics.json` | 15 metriques cles (equity, P&L, WR, expectancy, drawdown, TP1/TP2/SL counts, trades/day, payoff) | Analyse automatisee |
| `important_trades.jsonl` | Trades individuels avec P&L en R, resultat, TP1/TP2/SL flags | Audit par trade |
| `optimization_findings.json` | Suggestions automatisees (frequence, WR, expectancy, grade perf) | Guide optimisation |
| `summary.json` | Summary complet du replay (50+ blocs diagnostic, 200+ KB) | Analyse approfondie |

Rapports globaux (racine `reports/`) :

| Fichier | Contenu |
|---------|---------|
| `P1_GOLD_SNIPER_REPLAY_APP_REPORT.md` | Rapport final P1 : statut, metriques, bugs, donnees, verdict |
| `DATA_PROVENANCE_AUDIT_REPORT.md` | Audit complet de provenance : sources, formats, gap, spread, timeframes, integrite, fuite du futur, commandes |

---

### 6B.7 Commandes d'utilisation

#### Lancer l'application interactive

```powershell
python -m gold_sniper.replay_app.Gold_Sniper_Replay
```

#### Replays baseline (ordre recommande)

```powershell
# 1. Smoke test 1 semaine
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-01-08 \
  --warmup-start 2025-12-01 \
  --run-id baseline_1w_jan --initial-equity 100

# 2. Baseline 1 mois
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-02-01 \
  --warmup-start 2025-12-01 \
  --run-id baseline_1m_jan --initial-equity 100

# 3. Baseline 2 mois
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-03-01 \
  --warmup-start 2025-12-01 \
  --run-id baseline_2m_jan_feb --initial-equity 100

# 4. Baseline 3 mois
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-04-01 \
  --warmup-start 2025-12-01 \
  --run-id baseline_3m_jan_mar --initial-equity 100

# 5. Baseline 6 mois
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu \
  --start 2026-01-01 --end 2026-06-01 \
  --warmup-start 2025-12-01 \
  --run-id baseline_6m_jan_jun --initial-equity 100
```

#### Autres commandes utiles

```powershell
# Generer des donnees synthetiques (test)
python -m gold_sniper.replay_app.Gold_Sniper_Replay --generate-synthetic

# Verifier disponibilite des donnees
python -m gold_sniper.replay_app.Gold_Sniper_Replay --check-data

# Replay avec diagnostic agent specifique
python -m gold_sniper.replay.run_replay \
  --run-id diag_agent2 --start 2026-01-01 --end 2026-01-08 \
  --diagnose-agent2-zonelifecycle --initial-equity 100

# Replay avec profiler active
python -m gold_sniper.replay.run_replay \
  --run-id profiled_run --start 2026-01-01 --end 2026-01-08 \
  --profile-replay --initial-equity 100

# Importer donnees MT5 (read-only)
python tools/data_import/import_mt5_history.py \
  --symbol XAUUSD --mt5-symbol XAUUSD.m \
  --start 2025-12-01 --end 2026-06-01

# Telecharger donnees externes (histdata.com)
python tools/data_import/import_external_m1.py \
  --source histdata --start 2026-03-01 --end 2026-04-01

# Fermer le gap M1 (script reproductible)
python tools/data_import/close_m1_gap.py

# Normaliser calendrier news
python tools/data_import/normalize_calendar_csv.py \
  --input <source.csv> --output <output.jsonl>

# Lancer tous les tests
python -m unittest discover gold_sniper/tests -q
```

---

### 6B.8 Garde-fous P1

Tous les garde-fous P1 sont documentes et verifices :

| Garde-fou | Statut | Verification |
|-----------|--------|-------------|
| **Offline only** | ✅ | Aucun appel MT5 write dans le chemin replay |
| **No broker writes** | ✅ | `SimulatedTradeManager` uniquement, pas de `BrokerGateway` |
| **No live trading** | ✅ | `LIVE_MODE=0`, `ALLOW_BROKER_WRITES=0` |
| **No order_send** | ✅ | Confine a `execution/broker_gateway.py`, jamais appele en replay |
| **No forced ENTER** | ✅ | Verifie par safety counters dans summary.json |
| **No thresholds lowering** | ✅ | Constantes strategiques inchangees |
| **No strategy optimization during baseline** | ✅ | Aucun parametre modifie pendant les replays |
| **No synthetic data in baseline** | ✅ | Toutes les baselines utilisent des donnees reelles (histdata.com + MT5) |
| **No future leakage** | ✅ | 6 checks : injection progressive, warmup isolation, agent scoping, ReplayClock, _external_indices, _build_summary only |
| **Raw logs temp only** | ✅ | `.tmp/replay_runs/` nettoye apres chaque run |
| **Capital initial = $100** | ✅ | Force dans le menu et les presets |
| **POI_REACTION non tradable** | ✅ | Interdit par doctrine, verify par safety counters |

---

### 6B.9 Etat final P1

| Dimension | Statut |
|-----------|--------|
| **Verdict** | `READY_FOR_FULL_BASELINE_REPLAYS` |
| **Gap M1** | Ferme (14,447 candles gap-filling) |
| **Spread realism** | Corrige (32 pts conservateur sur histdata.com) |
| **Tous presets** | Green (1w/1m/2m/3m/6m) |
| **M1 continu** | 201,513 candles, Dec 2025 -> Jun 2026 |
| **Timeframes** | 7 TFs reconstruits (1m/5m/15m/30m/1H/4H/1D) |
| **News** | 4,427 events indexes, USD HIGH/MEDIUM: 736 |
| **Replay smoke** | 7 trades reels, 71.4% WR, +1.26% sur $100 |
| **Replay crossover** | Gap traverse sans erreur (26K+ events, 0 erreurs) |
| **Audit data** | Provenance complete, chaque candle tracable a sa source |

**Prochaine phase :** lancer les baselines brutes 1w/1m/2m/3m/6m **sans aucune optimisation strategique**, collecter les metriques, puis analyser les resultats avant toute modification.

---

### 6B.10 Handoff for Claude Opus 4.8

Cette section est destinee au prochain architecte (Claude Opus 4.8 ou modele superieur) qui reprendra le projet. Elle resume le contrat de passation.

#### Ordre de lecture obligatoire

1. **`architecture.md`** (ce document) — commencer par la Section 6B (Phase 1) puis la Section 2 (architecture generale)
2. **`CLAUDE.md`** — constitution operationnelle, interdits absolus, commandes
3. **`reports/P1_GOLD_SNIPER_REPLAY_APP_REPORT.md`** — rapport final P1 avec metriques et verdict
4. **`reports/DATA_PROVENANCE_AUDIT_REPORT.md`** — audit complet de provenance des donnees

#### Code source a auditer (optionnel)

Si Opus souhaite auditer le code :

- `gold_sniper/replay_app/Gold_Sniper_Replay.py` — point d'entree app terminal
- `gold_sniper/replay/run_replay.py` — runner replay CLI legacy
- `gold_sniper/replay/replay_engine.py` — moteur de replay (le plus gros fichier, ~3200 lignes)
- `gold_sniper/replay/simulated_trade_manager.py` — gestion trades simules
- `gold_sniper/replay/decision_pipeline.py` — pipeline agents replay
- `gold_sniper/strategy/kasper_scenario_engine.py` — moteur sequence Kasper
- `gold_sniper/strategy/professional_decision_engine.py` — PDE
- `tools/data_import/close_m1_gap.py` — script de fermeture du gap reproductible
- `tools/data_import/import_external_m1.py` — importeur multi-source (histdata.com + Dukascopy)

#### Ce qu'Opus NE doit PAS faire avant baseline

- **Ne pas modifier la strategie** (Kasper, PDE, RiskAllocator, agents, contrats)
- **Ne pas baisser les seuils** tactiquement
- **Ne pas forcer ENTER**
- **Ne pas rendre `POI_REACTION` tradable**
- **Ne pas activer `LIVE_MODE=1` ou `ALLOW_BROKER_WRITES=1`**
- **Ne pas lancer de replays avec capital != $100**
- **Ne pas melanger donnees synthetiques et reelles**

#### Ce qu'Opus DOIT attendre avant d'agir

- Les 5 rapports de baseline (1w, 1m, 2m, 3m, 6m) doivent etre produits
- Les metriques doivent etre collectees et analysees
- Le verdict de performance brute doit etre etabli

#### Role d'Opus apres baseline

1. **Audit strategique** : analyser les decisions, grades, rejections, profils de trades
2. **Analyse des resultats** : WR, expectancy, drawdown, distribution par session/grade/setup
3. **Recommandations d'optimisation controlee** : identifier les axes d'amelioration sans casser la doctrine Kasper
4. **Validation** : confirmer ou infirmer la viabilite de la strategie sur 6 mois

#### Regle d'or

```text
Ne pas optimiser avant d'avoir vu les baselines.
Ne pas tuner avant d'avoir compris les patterns de rejet.
Ne pas activer le live avant preuve statistique sur 6 mois.
```

---## 7. Data

### Donnees bougies

Structure locale principale :

```text
gold_sniper/data/historical/XAUUSD/
  1m/
  5m/
  15m/
  1H/
  4H/
  manifest.json
```

Objectif P3 (statut P1 final) :

| Timeframe | Statut |
|-----------|--------|
| M1 | Source de verite. **201,513 candles, Dec 2025 -> Jun 2026, continu.** |
| M5 | Derive de M1. **40,253 candles.** |
| M15 | Derive de M1. **13,366 candles.** |
| M30 | Derive de M1. **6,644 candles.** |
| H1 | Derive de M1. **3,284 candles.** |
| H4 | Derive de M1. **738 candles.** |
| D1 | Derive de M1. **166 candles (ajoute en P1).** |

### Import MT5 read-only

`tools/data_import/import_mt5_history.py` est le chemin prevu pour recuperer l'historique MT5. La doctrine P3 autorise uniquement des APIs read-only comme :

- initialize ;
- shutdown ;
- last_error ;
- copy_rates_range.

APIs interdites dans l'import data :

- `order_send` ;
- `order_check` ;
- `positions_get` ;
- `orders_get`.

### Formats

| Format | Usage |
|---|---|
| CSV | Source historique locale et output import. |
| Parquet | Mentionne par les pipelines/manifests pour stockage/derivation. |
| Manifest JSON | Couverture, fichiers, gaps, duplicates, metadata. |
| Gaps report | Validation couverture et trous. |
| JSONL news | Calendrier normalise pour replay/news index. |

### Calendrier news

Le manifest news local indique :

- source : `calendar-event-list.csv` ;
- lignes source : 4 444 ;
- evenements normalises : 4 427 ;
- doublons par cle : 17 ;
- couverture : `2025-12-31T23:00Z` a `2026-06-19T14:30Z` ;
- devise USD : 1 360 evenements ;
- USD high/medium : 736 ;
- timezone : UTC.

Limite : confirmer que le replay par defaut utilise bien le calendrier normalise le plus recent, et pas seulement l'ancien calendrier avril-juin.

---

## 8. Risk, lifecycle et journaux

### Risk mapping

Mapping cible documente :

| Grade | Risk parent cible |
|---|---|
| `A_PLUS` | 1.00% |
| `A` | 0.75% |
| `B` | 0.50% |
| `C_CONFIRMED` | 0.25% |
| `C` | 0 ou watch-only sauf cas confirme P3. |
| `D` | 0 |

`strategy/risk_allocator.py` contient le mapping core `A_PLUS=1.00`, `A=0.75`, `B=0.50`, `C=0.25`, `D=0`. Le caractere executable depend ensuite de l'eligibilite, de l'action et des guards.

### Sizing

En replay P3 :

```text
risk parent -> split 50/50 -> leg TP1 + leg TP2
```

En live legacy :

`execution/trade_manager.py` calcule le volume a partir du risk amount, de la distance SL, des infos symbol et des contraintes lot/min/max/step.

### Daily limiter et cooldown

- Replay : `shadow_live_policy.py` et `SimulatedTradeManager` appliquent limite quotidienne, duplicates et safety counters.
- Live : `config.py` et `TradeManager` contiennent cooldown, max trades/day et limites drawdown/loss.

### Lifecycle deux legs

```text
Parent setup ouvert
  -> leg TP1 ouvert
  -> leg TP2 ouvert

Si SL direct:
  -> parent -1R

Si TP1 touche:
  -> leg TP1 cloture
  -> runner protege a +0.5R

Puis:
  -> TP2 touche => parent +1.5R
  -> protected SL touche => parent +0.75R
```

### Pure R / Net R

- `pure_R` mesure la logique payoff theorique.
- `net_R` inclut les couts/fill model/spread/slippage selon le replay.
- Les rapports P3 signalent que l'ecart pure/net peut etre important et doit etre diagnostique avant conclusion live.

### Journaux

| Journal / rapport | Role |
|---|---|
| `trade_journal` | Liste des trades parent/legs et resultats. |
| `events` | Evenements lifecycle et safety. |
| `decisions` | Decisions PDE/Kasper/replay. |
| `summary.json` | Metriques globales. |
| `performance_summary` | Distributions grades/setups/actions/agents. |
| Safety counters | Forced ENTER, legacy ENTER, POI_REACTION, side mismatch, risk realism. |

### Safety counters importants

Les rapports P3 surveillent notamment :

- forced ENTER ;
- legacy ENTER ;
- POI_REACTION ENTER ;
- side mismatch ;
- risk realism status ;
- daily limit rejections ;
- open trades fin de replay ;
- missed entries ;
- full SL / protected SL / TP1 / TP2.

---

## 9. Tests et qualite

### Familles de tests

| Famille | Protege |
|---|---|
| Tests agents | Detection HTF, POI, liquidite, micro, news, session. |
| Tests strategy contracts | Evidence, setup taxonomy, readiness, scorecard, hard veto, risk allocation. |
| Tests replay | Pipeline decisions, no live orchestrator, deterministic replay, journals. |
| Tests P2 | Scenario identity, side consistency, duplicate gate, risk mapping, session veto. |
| Tests P3 | Lifecycle deux legs, payoff, protected SL, summaries, data import/news prerequis. |
| Tests safety | No broker write, no forbidden MT5 APIs, no forced ENTER, no POI_REACTION tradable. |

### Tests critiques cites par les rapports

- Scenario identity stable et `decision_id` par candle.
- Side consistency entre POI/liquidite/micro/trade.
- Duplicate gate par scenario/opportunity.
- Risk mapping grade -> risk.
- `POI_REACTION` non tradable.
- Replay orchestrator live interdit.
- P3 two-leg lifecycle : TP1, TP2, protected SL, full SL.
- News/calendar normalisation.
- MT5 import read-only.

### Commandes utiles

Les commandes exactes peuvent evoluer, mais les familles utiles sont :

```powershell
pytest gold_sniper/tests
pytest gold_sniper/tests/test_p3_trade_lifecycle_two_legs.py
python -m gold_sniper.replay.run_replay --help
python tools/data_import/import_mt5_history.py --help
python tools/data_import/normalize_calendar_csv.py --help
```

Avant un replay long ou une activation live, verifier au minimum :

1. working tree compris et propre pour les fichiers de validation ;
2. tests P2/P3 critiques verts ;
3. MT5 import read-only valide ;
4. M1/M5/M15/M30/H1/H4/D1 disponibles ou derivables ;
5. calendrier news normalise et branche dans le replay ;
6. aucun safety counter interdit ;
7. aucune entree `POI_REACTION` ;
8. aucun forced/legacy ENTER ;
9. no broker write en mode replay/shadow ;
10. divergence live/replay des constantes de protection resolue.

---

## 10. Etat actuel du projet

### Valide / prouve localement par docs et code

- Doctrine Gold Sniper/Kasper documentee.
- Interdits principaux explicites.
- Pipeline replay Kasper/PDE present.
- EvidenceBuilder present et utilise en replay.
- KasperScenarioEngine present avec gates sequence et hard veto.
- PDE present avec hard veto, readiness, scorecard, enter eligibility, risk.
- RiskAllocator present.
- SimulatedTradeManager P3 deux legs present.
- BrokerGateway et ExecutionGuard presents.
- Replay P3 1M/2M produit des summaries et safety counters.
- **P1 — Replay Control Center V3.2** : app terminal interactive, 6 presets, rapports compacts.
- **Data M1 complete** : 201,513 candles, Dec 2025 -> Jun 2026, continu, gap ferme.
- **7 timeframes reconstruits** : M1/M5/M15/M30/H1/H4/D1 depuis M1 gap-closed.
- **Spread realism** : 32 pts fixe sur segment histdata.com (116,252 candles).
- **Provenance auditee** : chaque candle tracable a sa source (histdata.com ou MT5).
- **Garde-fous P1 verifices** : 0 future leakage, 0 forced ENTER, 0 broker writes.
- Calendrier news normalise Jan-Jun present (4,427 events, 736 USD HIGH/MEDIUM).
- Sanity replays : smoke 1 semaine (7 trades, 71.4% WR), crossover gap (26K events, 0 erreurs).

### Partiellement valide

- Live runtime : present, mais legacy et non valide live-safe.
- Integration Kasper/PDE dans le live : non prouvee dans `core/orchestrator.py`.
- Risk/live lifecycle : `TradeManager` gere TP1/protection mais avec constantes legacy.
- ~~Data Jan-Jun multi-timeframe~~ : ✅ RESOLU P1 — M1/M5/M15/M30/H1/H4/D1 tous disponibles.
- ~~M30/D1 manquants~~ : ✅ RESOLU P1 — M30 (6,644) et D1 (166) reconstruits.
- News replay : normalisation presente, wiring confirme.
- Fast replay/precomputed : mentionne comme partiel/stub dans les rapports.
- Shadow diagnostics performance : `_build_summary()` lent sur >10K candles (50+ blocs, 2 GB RAM).

### Bloque (mis a jour P1)

| Blocage | Niveau | Impact | Statut P1 |
|---|---|---|---|
| ~~M30 et D1 manquants~~ | ~~P0/P1 data~~ | Resolu P1 — M30 (6,644) et D1 (166) reconstruits depuis M1 | ✅ RESOLU |
| ~~MT5 import non valide~~ | ~~P0/P1 data~~ | Resolu P1 — MT5 import fonctionnel + complement histdata.com | ✅ RESOLU |
| Live Kasper/PDE non integre/prouve | P0 live | Interdit live-safe. | ❌ BLOCANT LIVE |
| Divergence protected SL live `0.10R` vs replay P3 `0.5R` | P1 risk | Risque de comportement different live/replay. | ⚠️ A HARMONISER |
| Diagnostics `POI_REACTION` dans compteurs intermediaires | P1 reporting/safety | Doit etre confirme non tradable de bout en bout. | ⚠️ A CONFIRMER |
| ~~Replay long bloque par P3~~ | ~~P1 validation~~ | Resolu P1 — tous les presets 1w/1m/2m/3m/6m disponibles | ✅ RESOLU |
| Shadow diagnostics lent (2 GB RAM, 10-30 min pour 26K+ events) | P1 performance | `_build_summary()` avec 50+ blocs ralentit les longs replays | ⚠️ CONNU |

### Risques P0 / P1 / P2

| Niveau | Risque |
|---|---|
| P0 | Live activation avant integration Kasper/PDE + validation longue. |
| P0 | Broker write hors gateway ou sans guard. |
| P0 | Forced ENTER / baisse seuils / bypass news/risk/session. |
| P1 | Data incomplete M30/D1 ou calendrier news mal branche. |
| P1 | Divergence replay/live sur protected SL, daily limiter, risk. |
| P1 | `POI_REACTION` apparaissant dans risk diagnostics sans explication claire. |
| P2 | Performance faible net_R vs pure_R a diagnostiquer. |
| P2 | Documentation historique contradictoire entre "final opus" et constitution P3. |

### Prochaine etape recommandee (P1-ready)

La route la plus coherente avec l'etat P1 actuel est :

1. ~~Stabiliser et committer l'etat documentaire/validation actuel.~~ ✅ FAIT (P1)
2. ~~Confirmer P3-A/P3-B sans modifier la strategie.~~ ✅ FAIT
3. ~~Valider l'import MT5 read-only et completer M30/D1.~~ ✅ FAIT — M30/D1 reconstruits, gap M1 ferme
4. ~~Confirmer que le calendrier news normalise est utilise par le replay.~~ ✅ FAIT
5. **Lancer les baselines brutes 1w/1m/2m/3m/6m** sans aucune optimisation strategique
6. Collecter et analyser les metriques de baseline (WR, expectancy, drawdown, grade/session breakdowns)
7. **Seulement apres analyse des baselines :** recommander des optimisations controlees
8. Apres 6 mois de validation positive : planifier l'unification live-safe
9. Reporter toute activation live jusqu'a integration live Kasper/PDE + guards + validation explicite

---

## 11. Historique utile conserve

| Phase | Signal historique |
|---|---|
| P1-clean | Separation replay/live, interdiction orchestrateur live en replay, no broker write. |
| P2-A | Connectivite POI et handoffs agents. |
| P2-B/P2-C | Simulation fidele, validation replay, no forced ENTER. |
| P2-D/P2-E | Diagnostics POI/micro, refus de tuning premature. |
| P2.2 | Scenario identity, side consistency, duplicate gate, session veto. |
| P2.3 | Correction warmup/sweep type ; bottleneck micro identifie. |
| Final Opus | Rapport 2 mois positif mais insuffisant pour live selon P3. |
| P1 | **Gold Sniper Replay Control Center V3.2** : app terminal, menu interactif, 6 presets baseline, rapports compacts. Data M1 complete (201,513 candles), gap ferme, spread corrige, 7 TFs reconstruits, provenance auditee. |
| P3-A | Lifecycle deux legs implemente et teste. |
| P3-B | Payoff replay 1M/2M diagnostique, pure_R/net_R a surveiller. |
| P3-C/D/E/F | MT5 import, calendar, acceleration, long validation encore gates. |

---

## 12. Zones à confirmer

| Zone | Fichier(s) a verifier | Question |
|---|---|---|
| Integration live Kasper/PDE | `core/orchestrator.py`, `replay/decision_pipeline.py`, `strategy/*` | Le live doit-il remplacer l'orchestrateur legacy par le pipeline PDE/Kasper ou l'encapsuler ? |
| `C_CONFIRMED` | `strategy/contracts.py`, `strategy/risk_allocator.py`, `replay/shadow_live_policy.py` | Le grade P3 est-il un grade core ou une policy replay/shadow ? |
| Protected SL live | `config.py`, `execution/trade_manager.py`, `replay/simulated_trade_manager.py` | Harmoniser `0.10R` live legacy et `0.5R` replay P3. |
| News default replay | `replay/run_replay.py`, `replay/news_index.py`, `data/historical/news/*` | Le replay lit-il le calendrier normalise le plus recent par defaut ? | ✅ CONFIRME P1 |
| ~~M30/D1~~ | `data/historical/XAUUSD/30m/`, `data/historical/XAUUSD/1D/` | Reconstruits depuis M1 gap-closed en P1. M30=6,644, D1=166. | ✅ RESOLU P1 |
| `POI_REACTION` counters | `replay/summary.json`, `strategy/setup_taxonomy.py`, `simulated_trade_manager.py` | Confirmer que les compteurs risk-positive ne correspondent jamais a des trades executes. | ⚠️ A CONFIRMER |
| Fast replay | `replay/replay_profiler.py`, `run_replay.py` | Les flags precomputed sont-ils operationnels ou seulement stubs ? | ⚠️ A EXPLORER |
| Live safety parity | `execution/*`, `strategy/*`, `replay/*` | Les guards live reproduisent-ils les guards replay/shadow ? | ⚠️ A CONFIRMER |
| ~~Gap M1 Feb-Mar~~ | `tools/data_import/close_m1_gap.py` | Le gap de 17 jours entre histdata et MT5 est-il ferme ? | ✅ RESOLU P1 |
| ~~Spread histdata=0~~ | `tools/data_import/close_m1_gap.py` | Le spread a 0 fausse-t-il les couts de trading ? | ✅ RESOLU P1 (32 pts) |

---

## 13. Regle d'or pour le prochain architecte

Ne pas optimiser, ne pas tuner, ne pas forcer une entree, ne pas lancer de replay long et ne pas activer le live tant que les preuves suivantes ne sont pas explicites :

```text
Data complete -> News branchee -> Lifecycle coherent -> Replay court propre
  -> Safety counters propres -> Replay long valide -> Live-safe pipeline unifie
  -> Broker gateway guarded -> Validation humaine explicite
```
