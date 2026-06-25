# P1 — Gold Sniper Trading & Optimisation Replay Control Center

## 0. Branche

Créer une branche propre depuis `main` :

```bash
git checkout main
git pull
git checkout -b P1-Gold_sniper_trading_and_optimisation
```

Nom de phase : **P1 — Gold Sniper Trading & Optimisation**.

Cette phase est la base de Gold Sniper V3.2 : construire l’application terminal de replay, préparer les données, accélérer les tests, rendre les rapports lisibles, puis permettre l’optimisation contrôlée.

---

## 1. Objectif global

Gold Sniper possède déjà l’essentiel :

- stratégie Kasper/SMC unique ;
- logique Agent1 → Agent7 ;
- décision Kasper/PDE ;
- règles ENTER/WAIT/REJECT ;
- trade lifecycle 2 legs ;
- risque par grade ;
- journaux et métriques.

L’objectif de P1 n’est pas de réinventer la stratégie. L’objectif est de créer un **Replay Control Center terminal** permettant de lancer rapidement des replays propres, lisibles, comparables, sans produire des gigas de logs inutiles.

But final :

> Calibrer et optimiser Gold Sniper pour viser un comportement proche d’un trader humain discipliné capable de produire environ **1 à 2 trades propres par jour actif**, idéalement autour de **1.5 à 2 trades/jour**, avec **70%+ de winrate**, expectancy_R positive, drawdown contrôlé, et aucune triche doctrinale.

Baseline connue :

- environ **1.6 trade/jour** ;
- winrate supérieur à **65%** ;
- donc le plus dur est déjà fait ;
- P1 sert à améliorer la sélection, la vitesse de replay, la lecture des décisions et l’optimisation safe.

---

## 2. Interdits absolus

P1 est une phase d’optimisation sérieuse, pas une phase de triche.

Interdit :

- forced ENTER ;
- baisse artificielle des seuils pour générer plus de trades ;
- rendre POI_REACTION tradable ;
- rendre C incomplet tradable ;
- bypass hard veto/news/session/risk/daily limiter/cooldown ;
- utiliser les bougies futures pour décider ;
- optimisation basée sur fuite de données ;
- supprimer des tests pour passer ;
- masquer des pertes ;
- modifier les métriques pour embellir les résultats ;
- lancer LIVE_MODE, paper/live ou vrai broker ;
- appeler `order_send` hors broker gateway isolé.

Le replay doit simuler le réel : Gold Sniper peut connaître le passé, jamais le futur.

---

## 3. Capital initial obligatoire

Chaque replay doit démarrer avec :

```text
initial_equity = 100.00 USD
```

Cela vaut pour :

- replay 1 semaine ;
- replay 1 mois ;
- replay 2 mois ;
- replay 3 mois ;
- replay 6 mois ;
- replay custom.

Le rapport doit toujours afficher :

- capital initial ;
- capital final ;
- net_pnl ;
- net_pnl_pct ;
- pure_R ;
- net_R ;
- drawdown ;
- winrate ;
- expectancy_R.

---

## 4. Données à importer et préparer

Créer ou fiabiliser le pipeline de préparation des données.

### 4.1 Période à importer

Importer les bougies de :

```text
2025-12-01 → 2026-06-01
```

Raison :

- décembre sert de **warmup/context** ;
- janvier à juin servent de période de replay/évaluation ;
- Gold Sniper doit lire décembre pour comprendre le contexte, mais ses résultats officiels commencent sur la période choisie.

### 4.2 Timeframes obligatoires

Importer ou générer :

- M1 ;
- M5 ;
- M15 ;
- M30 ;
- H1 ;
- H4.

M1 est la source de vérité.

Si certains timeframes ne peuvent pas être importés directement, ils peuvent être dérivés à partir de M1, mais le rapport doit le dire clairement.

### 4.3 Format rapide

Préparer les données pour replay rapide :

- CSV brut si nécessaire ;
- Parquet recommandé pour lecture rapide ;
- manifest de couverture ;
- gaps_report ;
- timestamps UTC ;
- doublons détectés ;
- trous détectés ;
- monotonicité validée.

### 4.4 News

Le fichier CSV news fourni par Joemi doit être converti une seule fois en JSONL exploitable.

Exigences :

- conserver toutes les devises ;
- prioriser USD HIGH/MEDIUM ;
- timestamps UTC ;
- manifest news ;
- coverage_start ;
- coverage_end ;
- nombre total events ;
- nombre USD HIGH/MEDIUM ;
- index rapide O(log n) ou équivalent.

---

## 5. Principe fondamental : pas de fuite du futur

Même si les données sont préchargées pour accélérer, le moteur ne doit jamais recevoir tout le futur en une fois.

### 5.1 Warmup

Pour un replay qui commence le 1er janvier :

- charger décembre comme warmup ;
- injecter les bougies progressivement ;
- décembre sert à construire le contexte HTF, les zones, la structure, les repères ;
- aucun trade officiel ne doit être compté pendant warmup, sauf si explicitement demandé en mode diagnostic.

### 5.2 Replay réel accéléré

À partir du 1er janvier :

- injecter les bougies progressivement, candle par candle ;
- Gold Sniper ne voit que le passé + la bougie actuelle ;
- il ne voit jamais les bougies futures ;
- le replay accélère seulement la perception du temps.

Objectif performance :

```text
1 mois de replay ≤ 5 minutes si possible
```

Si ce n’est pas possible, profiler et optimiser sans casser la doctrine.

---

## 6. Application terminal à créer

Créer une application terminal interactive :

```text
Gold_Sniper_Replay.py
```

Elle doit fonctionner comme un petit logiciel terminal, inspiré de Claude Code : clair, dynamique, navigable au clavier.

Emplacement recommandé :

```text
tools/replay/Gold_Sniper_Replay.py
```

ou, si plus propre :

```text
gold_sniper/replay_app/
```

Le choix doit être documenté.

---

## 7. Maquette terminal souhaitée

### 7.1 Écran d’accueil

Interface avec navigation par flèches :

```text
╔══════════════════════════════════════════════════════════════╗
║                  GOLD SNIPER REPLAY CENTER                  ║
║                 V3.2 — Trading & Optimisation               ║
╠══════════════════════════════════════════════════════════════╣
║ Capital initial : 100.00 USD                                ║
║ Data range      : 2025-12-01 → 2026-06-01                   ║
║ Symbol          : XAUUSD                                    ║
║ Mode            : Offline replay / no broker / no future     ║
╠══════════════════════════════════════════════════════════════╣
║  > Replay 1 semaine smoke                                   ║
║    Replay 1 mois                                            ║
║    Replay 2 mois                                            ║
║    Replay 3 mois                                            ║
║    Replay 6 mois                                            ║
║    Replay custom                                            ║
║    Voir rapports                                            ║
║    Nettoyer logs temporaires                                ║
║    Quitter                                                  ║
╚══════════════════════════════════════════════════════════════╝

↑/↓ sélectionner | Enter lancer | Esc annuler/quitter
```

### 7.2 Écran replay live

Pendant le replay, afficher une interface dynamique :

```text
╔════════════════ GOLD SNIPER LIVE REPLAY ════════════════╗
║ Run        : 2026-01-01 → 2026-02-01                    ║
║ Warmup     : 2025-12-01 → 2025-12-31                    ║
║ Equity     : 100.00 → 103.42 USD                        ║
║ Progress   : ███████████████░░░░░░░░░░ 58%              ║
╠════════════════ MARKET CLOCK ═══════════════════════════╣
║ Current candle : 2026-01-17 14:35 UTC                   ║
║ Candles        : 48,210 / 83,000                        ║
║ Speed          : 12,400 candles/sec                     ║
╠════════════════ AGENT WORKSPACE ════════════════════════╣
║ Agent1 HTF       : BULLISH context / score 78           ║
║ Agent2 POI       : OB valid / FVG aligned / score 72    ║
║ Agent3 Liquidity : sweep detected / score 81            ║
║ Agent4 Structure : BOS confirmed / score 75             ║
║ Agent5 Trigger   : WAIT micro confirmation              ║
║ Agent6 News      : no hard veto                         ║
║ Agent7 Risk      : allowed / size OK                    ║
║ Orchestrator/PDE : WAIT                                 ║
╠════════════════ TRADES & METRICS ═══════════════════════╣
║ Decisions      : ENTER 4 | WAIT 1204 | REJECT 832       ║
║ Open trades    : 1                                      ║
║ TP1/TP2/SL     : 3 / 2 / 1                              ║
║ Winrate        : 71.4%                                  ║
║ Expectancy_R   : +0.31R                                 ║
║ Drawdown       : -0.72%                                 ║
╚══════════════════════════════════════════════════════════╝

Esc = stop replay, cleanup temp logs, write partial report
```

L’interface peut utiliser `rich`, `textual` ou un fallback simple si les dépendances ne sont pas disponibles.

---

## 8. Gestion des logs

Les logs lourds ne doivent plus polluer le repo.

### 8.1 Pendant le replay

Écrire les logs complets dans un dossier temporaire :

```text
.tmp/replay_runs/<run_id>/
```

ou via `tempfile`.

Ces fichiers peuvent contenir :

- events complets ;
- décisions détaillées ;
- agent traces ;
- trade journal complet ;
- profiling brut.

### 8.2 Après le replay

À la fin, l’application doit extraire uniquement les parties importantes :

- trades pris ;
- raisons des trades ;
- validations/rejets par agent ;
- veto news/session/risk ;
- TP1/TP2/protected SL/full SL ;
- erreurs/anomalies ;
- métriques finales ;
- exemples de bons trades ;
- exemples de trades perdants ;
- raisons principales des rejets ;
- recommandations d’optimisation.

### 8.3 Conservation finale

Garder seulement dans le dossier de rapports :

```text
reports/replay/<run_id>/
```

Fichiers recommandés :

- `REPORT.md` ;
- `summary.json` ;
- `important_trades.jsonl` ;
- `metrics.json` ;
- `optimization_findings.json` ;
- `profile_report.json` si profiling activé.

Supprimer ou compresser les logs temporaires après extraction.

Le repo ne doit pas versionner :

- logs lourds ;
- runs complets ;
- CSV/Parquet générés ;
- caches ;
- fichiers temporaires.

Mettre à jour `.gitignore` si nécessaire.

---

## 9. Fonction Esc / arrêt propre

Si Joemi appuie sur `Esc` pendant un replay :

1. arrêter le replay ;
2. fermer proprement les writers ;
3. extraire un rapport partiel si possible ;
4. supprimer les logs temporaires ;
5. afficher un message clair :

```text
Replay stopped by user. Temporary logs cleaned. Partial report written.
```

Aucun fichier lourd ne doit rester.

---

## 10. Rapports pour Opus/GPT

Le but de l’application est de produire des rapports lisibles qui pourront être joints à Opus/GPT pour audit.

Chaque rapport doit permettre de répondre :

- pourquoi Gold Sniper a pris tel trade ;
- pourquoi il a rejeté tel setup ;
- quels agents ont validé/rejeté ;
- quels filtres bloquent trop ;
- quels setups gagnent/perdent ;
- quels seuils semblent trop stricts ou trop faibles ;
- quelles sessions sont les meilleures ;
- quels moments news sont dangereux ;
- quels grades sont rentables ;
- où optimiser pour atteindre 70%+ WR sans tricher.

Le rapport doit être détaillé mais compact.

---

## 11. Modes de replay à intégrer

L’app doit proposer :

### 11.1 Smoke 1 semaine

Pour tester que tout marche.

Période suggérée :

```text
2026-01-01 → 2026-01-08
```

### 11.2 Replay 1 mois

```text
2026-01-01 → 2026-02-01
```

### 11.3 Replay 2 mois

```text
2026-01-01 → 2026-03-01
```

### 11.4 Replay 3 mois

```text
2026-01-01 → 2026-04-01
```

### 11.5 Replay 6 mois

```text
2026-01-01 → 2026-06-01
```

### 11.6 Replay custom

Permettre à Joemi d’entrer :

- date début ;
- date fin ;
- warmup_start ;
- agents à activer ;
- profiling oui/non ;
- mode compact logs oui/non.

---

## 12. Connexion aux agents

L’app ne doit pas réécrire les agents.

Elle doit appeler l’existant :

- ReplayEngine ;
- ReplayDecisionPipeline ;
- BlackBoard ;
- Agent1 à Agent7 ;
- KasperScenarioEngine ;
- PDE ;
- SimulatedTradeManager ;
- ShadowLivePolicy ;
- Economic calendar / NewsIndex.

Elle doit seulement améliorer :

- orchestration terminal ;
- préchargement ;
- menu ;
- affichage dynamique ;
- rapport final ;
- nettoyage des logs ;
- facilité d’usage.

---

## 13. Tests obligatoires avant rapport de réussite

Avant de dire que l’app est créée et fonctionnelle, le Maçon doit tester lui-même.

Minimum :

1. tests unitaires existants ;
2. compilation/import des nouveaux fichiers ;
3. test du menu en mode non interactif si possible ;
4. test préparation news CSV → JSONL ;
5. test détection data manquante ;
6. test cleanup logs temporaires ;
7. test Esc/interrupt si automatisable ;
8. lancement d’un vrai replay smoke 1 semaine via l’app ;
9. vérification que le rapport final est créé ;
10. vérification qu’aucun gros log temporaire ne reste dans le repo ;
11. `git status --short` propre ou fichiers modifiés documentés.

La confirmation finale doit inclure :

- commandes lancées ;
- durée replay smoke ;
- nombre de candles traitées ;
- trades ;
- winrate ;
- expectancy_R ;
- rapport généré ;
- fichiers temporaires nettoyés ;
- limites restantes.

---

## 14. Critères de réussite P1

P1 est réussie si :

- l’app terminal se lance ;
- les menus fonctionnent ;
- les données décembre → juin sont préparées ou blocage documenté ;
- news CSV converties en JSONL ;
- replay smoke 1 semaine fonctionne depuis l’app ;
- capital initial = 100 USD ;
- pas de fuite du futur ;
- logs lourds temporaires nettoyés ;
- rapport compact généré ;
- metrics R présentes ;
- agents visibles dynamiquement ;
- trade lifecycle 2 legs visible ;
- aucun live/broker réel ;
- aucun gros artefact versionné.

---

## 15. Rapport final de P1

Créer :

```text
reports/P1_GOLD_SNIPER_REPLAY_APP_REPORT.md
```

Contenu :

- verdict ;
- branche ;
- commit ;
- fichiers créés/modifiés ;
- architecture de l’app ;
- commandes ;
- data coverage ;
- news coverage ;
- replay smoke 1 semaine ;
- métriques ;
- exemples de trades ;
- logs supprimés/conservés ;
- risques ;
- prochaine étape recommandée pour optimisation 1m/2m/3m/6m.

Ne pas faire semblant : si MT5, données, news, UI ou replay bloquent, créer un rapport BLOCKED clair avec commandes exactes à lancer.

---

## 16. Résumé de mission

Construire la base du kernel de développement Gold Sniper :

```text
Data prepared once
News indexed once
Replay controlled from terminal
Candles injected progressively
Agents visible dynamically
Trades managed with 100 USD initial equity
Logs temporary
Reports compact
Optimization ready
No future leakage
No live trading
```

Cette app est la fondation de Gold Sniper V3.2.
