# CONTEXTE DU PROJET

## 1. Présentation générale
- **Nom** : Gold Sniper — moteur de trading autonome XAUUSD (or), stratégie SMC/Kasper, intraday.
- **Objectif principal** : système rentable, STABLE et discipliné, capable de se protéger, sans triche ni lookahead.
- **Résultat attendu** : edge robuste hors-échantillon + drawdown maîtrisé (< 15%), déployé en LIVE démo puis réel.
- **Utilisateur** : Joemi (broker JustMarkets, compte démo, levier **1:2000**). Langue de travail : français.
- **Rôle de l'assistant (Opus)** : architecte/auditeur senior. Rigueur absolue : causalité (aucune connaissance du futur hors warmup), un levier à la fois, validation OOS avant de figer, honnêteté sur les limites, jamais d'overfit.

## 2. État actuel
- **Phase** : FORWARD-TEST démo en cours (moteur live tourne en arrière-plan sur le PC).
- **Dernière étape terminée** : **Maintenance Dashboard Lots 1-4 (10/07/2026)** — voir §11. Moteur sain, parité 88/0 re-prouvée, 1710 tests verts.
- **Dernier résultat** : replay `confirm_v8_scale6` = +1074%, WR 92,6%, DD réel 11,64%, edge 0,884R/trade.
- **Reste côté utilisateur** : rien de bloquant. Optionnel = tunnel Cloudflare NOMMÉ (exige un domaine, pas dispo → non fait). Auto-publication Vercel opérationnelle à la place.

## 3. Décisions validées (NE PLUS REMETTRE EN QUESTION sans raison)
- **Doctrine intraday** (pas scalping) : TP structurels, gagnants 3-5R.
- **Exécution 15m** ; SL floor 2.0×ATR ; Strategy V2 dual-edge (regime filter vide).
- **Filtre rr≥4** (`GS_MIN_RR=4`) = LE filtre d'edge, validé par année ET par buckets (2024 +0,42R, 2025 +0,86R, 2026 +1,13R). rr<4 perd OOS.
- **5 gardes** : rr≥4, cap concurrent same-side=1, rolling DD 10%/pause 7j, loss breaker 2/jour, cooldown 60min same-side.
- **Profit sweep** : à 2× équité, retirer 50% (sécurisé, hors DD).
- **Risk scale 6** en démo (A+ 6%/A 4,5%/B 3%) — validé à 11,6% DD. (Opus recommandait 3 ; 6 accepté car DD < 15%.)
- **Grades Kasper** : A+≥95, A≥85, B≥70, C≥50 (WAIT), D<50. Entrée si ≥B ET rr≥4.
- **ENTER_FULL par grade** (jamais ENTER_REDUCED plafonné 0,5%).
- **Déploiement PC local** (pas VPS pour l'instant), MT5 natif Windows.
- **Dashboard** : migrer Cloudflare → **Vercel** (lien permanent). Supabase NON utilisé (v1 locale).
- **Pipeline unifié live** : `unified_live_decision.py` reproduit exactement le ReplayDecisionPipeline (parité prouvée 88/0).

## 4. Contraintes et règles obligatoires
- **JAMAIS de lookahead** : décisions sur bougies clôturées uniquement, même 1 seconde d'avance interdite.
- **JAMAIS `RUN_MODE=LIVE`/compte réel** tant que validation démo non terminée. Actuellement `RUN_MODE=PAPER`, `LIVE_MODE=0`.
- **DÉMO only** : whitelist login MT5 démo, `trade_mode==DEMO`, MAGIC 240115, `order_send` seulement via `broker_gateway.py`.
- **Ne pas** baisser les seuils/tuner pour embellir un replay ; **ne pas** supprimer artificiellement des trades.
- **Un seul levier à la fois**, validé OOS (par année + buckets, pas slices favorables) avant de figer.
- **Objectif "WR≥65%/mois" statistiquement impossible** à 1-2 trades/mois (variance) → le bon critère = edge OOS positif + DD maîtrisé, PAS le WR mensuel.
- **Réserve d'honnêteté** : 2026 = période in-sample (overfit développeur, pas triche moteur). Attente live réaliste = chiffres OOS 2024-2025 (75-93% WR), PAS 100%.
- **Utilisateur lance tous les replays** (économie de tokens). Assistant ne lance JAMAIS de replay.

## 5. Architecture et technologies
- **Langage** : Python 3.13, Windows. Repo racine : `C:\Users\tetej\Music\Bug bounty\Trading`.
- **Accès assistant** : Desktop Commander (terminal + fichiers). (Filesystem MCP désactivé au profit de DC.)
- **3 process live** : `pc_manager.py` (Discord Gateway + lifecycle) → `watchdog.py` → `main.py` (moteur, 22+ tâches asyncio).
- **7 agents** (Météo/Structure, Cartographe/OB, Liquidité, Fibonacci, Microscope, Sentinelle/News, Chronos/Sessions) → Orchestrateur → **délègue à Kasper/PDE** (pipeline unifié) → Trade Manager → Broker Gateway → MT5.
- **Kasper** (`strategy/kasper_scenario_engine.py`) = cerveau (scénarios, score, grade). **Orchestrateur** (`core/orchestrator.py`) = assemble agents + délègue à Kasper.
- **Services externes** : MT5 démo JustMarkets ; Discord (télécommande + alertes) ; Google Drive (backups) ; News (Finnhub/FMP en 403/402 → fallback ForexFactory OK) ; Cloudflare tunnel (→ à remplacer par Vercel) ; GitHub (dépôt `Joemi930/gold-sniper`).
- **Config** : `.env` + `.env.runtime` (override PAPER/démo, chargé après .env).

## 6. Fonctionnalités
### Terminées
- Moteur replay validé (edge OOS + 5 gardes + sweep + rolling DD).
- Pipeline unifié live (parité prouvée) + 5 gardes portés dans `safety/live_guards.py`.
- Config PAPER/démo + garde-fous anti-compte-réel.
- Autostart Windows (tâche planifiée ~3 min post-reboot), watchdogs, recovery (9 cas de panne), arrière-plan headless.
- Discord (commandes + `!lien`), Google Drive (OAuth OK, dossiers mensuels).
- **Dashboard Lots 1-4 (10/07)** : `!logs` = summary.json+report.md ; `!lien` ; Drive mensuel ; bloc "Trade en attente" + multi-positions (web+mobile) ; coquille Vercel (lien permanent) + auto-publication URL tunnel.
### Abandonnées
- Weekly Quality Governor (proposé par ChatGPT) — rejeté : réactif, traite le symptôme. Le filtre rr≥4 (préventif) le remplace.
- VPS pour l'instant. Seuil rr=3 (mini-overfit, remplacé par rr=4).
- **Tunnel Cloudflare NOMMÉ** (hostname fixe) : abandonné faute de domaine sur le compte CF. Remplacé par lien Vercel permanent + auto-republication de `config.js` (window.GS_BACKEND) à chaque boot via `VERCEL_TOKEN`.

## 7. Problèmes connus
- **Re-lien Cloudflare horaire** : RÉSOLU (10/07). Cause racine = watchdog trop agressif (heartbeat critique 30s < durée du recalcul MTF à la clôture 15m) → ~60 faux restarts/jour → ~60 URLs trycloudflare. Fix : `watchdog.py` seuil 30s→120s + reset `restart_count` après uptime stable. Le quick-tunnel reste éphémère mais l'URL est auto-republiée sur Vercel (lien permanent inchangé).
- **Instabilité DNS/réseau PC** : boucle de reconnexion Discord Gateway observée (`pc_manager.log`). Non bloquant (le bot re-connecte). À surveiller.
- **Trend Continuation ne trade jamais** : câblée (agent_2 + V2 régime fort) mais 100% des trades exécutés = Liquidity Sweep Reversal. La distribution "continuation" du summary compte des évaluations, pas des trades. Statut : à surveiller (comportement connu, non bloquant).
- **Finnhub 403 / FMP 402** : plans API insuffisants. Fallback ForexFactory opérationnel (`feed_alive=True`). Statut : acceptable.
- **SSL Windows** : Drive + Vercel exigeaient truststore avant les libs réseau (inspection HTTPS locale → CERTIFICATE_VERIFY_FAILED). Corrigé dans `drive_sync.py` et `vercel_publisher.py`.

## 8. Tests et validations
- **Suite de tests** : **1710 passed** (au 10/07). Commande : `python -m pytest gold_sniper/tests -q -p no:cacheprovider` avec `PYTHONPATH` = repo + repo\gold_sniper. NB : `test_agent6_calendar_resilience` peut échouer FAUSSEMENT si le moteur live tourne pendant les tests (il partage `data/agent6_feed_down_alert.json`) — supprimer ce fichier avant test ou tester moteur à l'arrêt.
- **Parité live/replay** : `python gold_sniper/tests/parity_proof.py` → `comparisons=88 divergences=0 PARITY PROVED`.
- **Critère de validation d'une étape** : tests verts + parité + gardes actifs + causalité respectée + OOS positif.

## 9. Fichiers importants
- `gold_sniper/core/orchestrator.py` — orchestrateur, délègue à Kasper si `GS_UNIFIED_PIPELINE=1`.
- `gold_sniper/core/unified_live_decision.py` — adaptateur pipeline unifié live (parité).
- `gold_sniper/safety/live_guards.py` — 5 gardes (source unique) ; façade `execution/live_guards.py`.
- `gold_sniper/execution/trade_manager.py` — exécution live ; appelle `run_all_live_guards` (bloque l'ordre).
- `gold_sniper/replay/simulated_trade_manager.py` — manager replay (gardes, sweep, DD, rr filter).
- `gold_sniper/strategy/kasper_scenario_engine.py` — scénarios/score/grade (seuils §3).
- `gold_sniper/agents/agent_5_microscope.py` — TP structurels + rr_estimate (causal, swing 20 bougies clôturées).
- `gold_sniper/config.py` — toutes les constantes `GS_*` (env-overridable).
- `gold_sniper/.env.runtime` — override PAPER/démo (RUN_MODE, GS_*, whitelist login).
- `gold_sniper/pc_manager.py` / `watchdog.py` — lifecycle + supervision.
- `web/dashboard_server.py` / `web/dashboard.html` — dashboard (à migrer Vercel).
- `DEPLOIEMENT_LIVE_LOCAL_SPEC.md` — spec de déploiement (référence).
- Rapports quotidiens : `gold_sniper/logs/reports/daily_report_*.txt`.

## 10. Variables, commandes et configurations importantes
- **Env-vars officielles (démo, dans `.env.runtime`)** : `RUN_MODE=PAPER`, `LIVE_MODE=0`, `GS_UNIFIED_PIPELINE=1`, `GS_EXECUTION_TF=15m`, `GS_STOP_ATR_FLOOR_MULT=2.0`, `GS_STRATEGY_V2=1`, `GS_REGIME_FILTER=` (vide), `GS_RISK_SCALE=6`, `GS_MIN_RR=4`, `GS_MAX_CONCURRENT_SAME_SIDE=1`, `GS_ROLLING_DD_PCT=10`, `GS_ROLLING_DD_PAUSE_DAYS=7`, `GS_LOSS_BREAKER=2`, `GS_LOSS_COOLDOWN_MIN=60`, `GS_STRUCT_MIN_ATR=0.8`, `GS_SWING_K=3`, `GS_PROFIT_SWEEP=1`, `GS_SWEEP_TRIGGER_MULT=2.0`, `GS_SWEEP_PCT=50`, `GS_BE_PLUS_RR=0.5`.
- **Levier compte démo** : 1:2000 (aligne le cap de marge du modèle).
- **Tests** : voir §8. **NE JAMAIS reproduire tokens/secrets en clair** (présents dans `.env.runtime`, ignorés par Git).
- **Sessions à surveiller (heure Kinshasa UTC+1)** : Londres ~09h-12h ; Overlap Londres-NY ~14h-18h.

## 11. Historique compact des étapes (chronologique)
1. Diagnostic scalping M1 non viable → migration doctrine intraday. ✅
2. Bug agent_1 gelé (mémoization) corrigé → WR 46%→74%. ✅
3. TP structurels + BE-à-1R + ENTER_FULL. ✅
4. Optimisation vitesse (fuites MTF, lru_cache, get_all) → ~30 min/2,4 ans. ✅
5. Doctrine risque + levier 1:2000 + métrique DD réelle (vs pic). ✅
6. Cap concurrent same-side (autopsie déc-2024 : empilement). ✅
7. Rolling DD guard (hémorragie multi-mois). ✅
8. Profit sweep. ✅
9. Filtre rr≥4 (validé par année + buckets). ✅
10. Replay final `confirm_v8_scale6` : +1074%, DD 11,6%. ✅
11. Unification pipeline live + parité prouvée + certification démo. ✅
12. Forward-test démo lancé (sain, 0 trade). ✅
13. **Maintenance Dashboard Lots 1-4 (10/07/2026)** : ✅
    - Phase 0 (stabilité) : `watchdog.py` heartbeat 30s→120s + reset restart_count (fix ~60 faux restarts/jour) ; regex URL exclut `api.trycloudflare.com`.
    - Lot 1 : Drive réparé (nouveau `credentials.json` + fix SSL truststore) + dossiers mensuels `GoldSniper_Backups/YYYY-MM/` (testé live) ; `!logs`→summary.json+report.md (module `utils/daily_summary.py`) ; `!lien`.
    - Lot 3 : `utils/pending_setup.py` (lecture seule) → bloc "Trade en attente" + multi-positions dans `web/dashboard.html` (web+mobile).
    - Lot 2 : coquille `web/vercel_shell/` + `utils/vercel_publisher.py` (auto-publie l'URL tunnel + token dashboard sur Vercel à chaque boot). Lien permanent Vercel.
    - Lot 4 : audit (parité 88/0, headless OK, secrets rédigés, PAPER/démo intact), 1710 tests, `.env` cloudflared corrigé.

## 12. Prochaine action précise
- **Maintenance Lots 1-4 terminée.** Le forward-test démo reprend son cours normal.
- **À surveiller** : le bloc "Trade en attente" et le multi-positions ne s'affichent qu'une fois un vrai setup/position présent (0 trade actuellement = normal). Vérifier visuellement au premier setup surveillé.
- **Vercel** : lien permanent `gold-sniper-dashboard-sable.vercel.app` (protection désactivée). Auto-republication active via `VERCEL_TOKEN` dans `.env.runtime`. Si le dashboard affiche "hors ligne" longtemps après un reboot → vérifier que `vercel_publisher` a bien tourné (log `VERCEL_PUBLI`).
- **Optionnel non fait** : tunnel Cloudflare nommé (hostname fixe) — nécessite un domaine (aucun sur le compte CF). L'auto-publication Vercel couvre le besoin.
- **Nouveaux fichiers** : `utils/daily_summary.py`, `utils/pending_setup.py`, `utils/vercel_publisher.py`, `web/vercel_shell/`.

## 13. Instructions pour les prochaines conversations
1. Lire ENTIÈREMENT ce fichier avant toute modification.
2. Ne pas recommencer une tâche déjà terminée (§6, §11).
3. Respecter toutes les décisions et contraintes validées (§3, §4).
4. Vérifier l'état RÉEL des fichiers (Desktop Commander) avant de proposer une modification.
5. Ne JAMAIS inventer un résultat de test — lancer réellement via terminal.
6. Mettre à jour ce fichier après toute avancée importante.
7. Garder le document compact ; supprimer l'obsolète.
8. En cas de contradiction, signaler le conflit AVANT d'agir.
9. Ne jamais lancer de replay soi-même (l'utilisateur les lance). Ne jamais activer le compte réel.
