# Gold Sniper — Spécification de déploiement LIVE local (DÉMO)

> **Auteur** : Opus (architecte) — pour exécution par **Claude Code** (accès terminal).
> **Périmètre Opus** : Étape 0 (conception) + Étape 1 (cette spec). Opus n'a que Filesystem — il ne lance rien.
> **Périmètre Claude Code** : Étapes 2 & 3 (implémentation + tests dans le terminal local).
> **Règle d'or** : DÉMO uniquement. Aucun ordre réel. `RUN_MODE=PAPER`, compte JustMarkets-Demo3.

---

## 0. Contexte — ce qui est DÉJÀ validé (ne pas y toucher)

Le **replay** (`gold_sniper/replay/`) a validé un edge robuste hors-échantillon sur 27 mois :
- WR global 92,6 %, expectancy **+0,884R/trade**, DD réel **11,6 %** (< 15 %), +1074 % @ scale 6.
- OOS 2024 (jamais tuné) : **+6,15R** ; OOS 2025 : **+22,6R**. L'edge tient hors de la période in-sample 2026.

**Doctrine validée (les paramètres officiels, tous en env-vars `GS_*`) :**
```bash
GS_EXECUTION_TF="15m"
GS_STOP_ATR_FLOOR_MULT="2.0"
GS_STRATEGY_V2="1"
GS_REGIME_FILTER=""            # vide = laisse agent_1 choisir l'edge
GS_RISK_SCALE="6"             # A+ 6% / A 4.5% / B 3% (démo). Repasser à 3 si prudence.
GS_MIN_RR="4"                # LE filtre clé — validé par année + buckets
GS_MAX_CONCURRENT_SAME_SIDE="1"
GS_ROLLING_DD_PCT="10"
GS_ROLLING_DD_PAUSE_DAYS="7"
GS_LOSS_BREAKER="2"
GS_LOSS_COOLDOWN_MIN="60"
GS_STRUCT_MIN_ATR="0.8"
GS_SWING_K="3"
GS_PROFIT_SWEEP="1"
GS_SWEEP_TRIGGER_MULT="2.0"
GS_SWEEP_PCT="50"
```

**Le problème central (confirmé par audit de `core/orchestrator.py`)** : le moteur LIVE n'exécute PAS ce pipeline. Il utilise un **vote pondéré legacy** (`BASE_WEIGHTS` : agent_1×30, agent_2×25… seuil EXECUTE≥85) qui ignore Kasper/PDE, le filtre rr≥4, les 5 gardes, ENTER_FULL par grade et le profit sweep. **Déployer le live tel quel = trader une stratégie différente et non validée.**

---

## 1. Architecture locale cible (conceptuelle — Étape 0)

```text
PC LOCAL (Windows, arrière-plan total)
  ├─ MT5 (JustMarkets-Demo3)         ← natif Windows, headless
  ├─ Gold Sniper Engine (main.py)    ← moteur unifié (voir §2)
  ├─ Watchdogs (MT5 / réseau / spread / process)
  ├─ Broker Gateway (DÉMO only, ExecutionGuard fail-closed)
  ├─ Dashboard server (localhost:8765) ─── exposé via ─▶ VERCEL
  ├─ Discord (télécommande + alertes)
  └─ Google Drive sync (backups / rapports)

SUPABASE  = NON utilisé (v1 locale)
CLOUDFLARE = REMPLACÉ par Vercel pour le dashboard
GITHUB    = dépôt officiel, à mettre à jour avec la version locale
```

Rôles : PC local = calcul + exécution démo ; Vercel = vue ; Discord = contrôle ; Drive = archives ; GitHub = code.

---

## 2. ÉTAPE 2-A (PRÉREQUIS ABSOLU) — Unifier le pipeline live sur la stratégie validée

> C'est le vrai chantier. **Rien d'autre ne compte tant que ce n'est pas fait.** À exécuter avec tests à chaque sous-étape (jamais à l'aveugle).

### Principe
Le live doit produire ses décisions via **exactement** les mêmes modules que le replay a validés, pas via le vote legacy. Deux voies possibles — **choisir la voie B (encapsulation), plus sûre** :

- **Voie A (remplacement)** : réécrire `core/orchestrator.py` pour appeler le pipeline Kasper/PDE. Risqué (beaucoup de dépendances live : dashboard, decision_logger, diamond_detector).
- **Voie B (encapsulation) ✅ RECOMMANDÉE** : garder l'ossature live (boucle asyncio, blackboard, notifications) mais **remplacer le cœur décisionnel** par un appel au pipeline validé. Concrètement :

### Plan d'implémentation (Voie B)
1. **Créer `core/unified_live_decision.py`** — un adaptateur qui :
   - lit l'état des agents depuis le `BlackBoard` live (`agent_results`),
   - construit un `EvidenceBundle` via `strategy/evidence_builder`/`replay.evidence_builder` (réutiliser la MÊME logique que le replay),
   - appelle `KasperScenarioEngine` + `ProfessionalDecisionEngine` (les mêmes classes que `replay/decision_pipeline.py` utilise),
   - applique la promotion **ENTER_FULL par grade** (logique de `replay/decision_pipeline.py`),
   - renvoie une décision `{action, grade, rr_estimate, entry, sl, tp1, tp2, risk_pct, scenario_type}`.
2. **Dans `core/orchestrator.py`** : derrière un flag `GS_UNIFIED_PIPELINE=1`, court-circuiter le calcul `weighted_score` legacy et appeler `unified_live_decision()`. Garder le legacy accessible (flag off) pour rollback.
3. **Porter les 5 gardes** dans le chemin d'exécution live (voir §2-B) — ils vivent aujourd'hui dans `replay/simulated_trade_manager.py`. Les extraire dans un module partagé `execution/live_guards.py` réutilisable par le live ET le replay (source unique).
4. **Le `rr_estimate`** doit être calculé exactement comme dans agent_5 replay (cible = swing des 20 dernières bougies CLÔTURÉES / risque). Vérifier qu'agent_5 live produit le même champ. Si non, l'ajouter.

### Tests obligatoires de cette sous-étape
- Rejouer un segment connu (ex. 2026-06) en mode PAPER avec `GS_UNIFIED_PIPELINE=1` et vérifier que les décisions/trades **correspondent** à celles du replay `confirm_v8_scale6` (mêmes entrées, mêmes grades, même rr). Tolérance : différences mineures de timing tick, mais mêmes setups.
- Si divergence : le live n'exécute pas encore la stratégie validée → corriger avant d'avancer.

---

## 2-B. Porter les 5 gardes validés dans le live

Les gardes suivants existent dans `replay/simulated_trade_manager.py` et **doivent** gouverner l'exécution live (choke point = juste avant l'envoi d'ordre dans `execution/trade_manager.py`) :

| Garde | env-var | Effet |
|---|---|---|
| Filtre RR | `GS_MIN_RR=4` | rejette tout setup rr_estimate < 4 (LE filtre d'edge) |
| Cap concurrent | `GS_MAX_CONCURRENT_SAME_SIDE=1` | 1 position max par direction (anti-empilement) |
| Rolling DD | `GS_ROLLING_DD_PCT=10` / `PAUSE_DAYS=7` | pause des entrées si équité recule de 10 % du pic |
| Loss breaker | `GS_LOSS_BREAKER=2` | stop du jour après 2 SL pleins |
| Cooldown | `GS_LOSS_COOLDOWN_MIN=60` | pas de ré-entrée même direction < 60 min après un SL |

**Profit sweep** (`GS_PROFIT_SWEEP=1`) : en live démo, comptabiliser le retrait virtuel (withdrawn_total) sans mouvement broker réel — c'est de la protection de sizing (le sizing continue sur l'équité restante).

**Action** : refactorer ces gardes en `execution/live_guards.py` (fonctions pures : `min_rr_block`, `concurrency_block`, `rolling_dd_block`, `loss_guard_block`) importées à la fois par le live et le replay. **Source unique = pas de divergence.**

---

## 2-C. Harmoniser les divergences (Étape 1 de la roadmap)

| Divergence (doc §20.3) | Live actuel | Replay validé | Action |
|---|---|---|---|
| Protected SL après TP1 | `BE_PLUS_RR=0.10` | `0.5R` | Aligner le live sur **0.5R** (config.py `BE_PLUS_RR` ou env `GS_BE_PLUS_RR`) |
| Risk par grade | legacy RISK_PERCENT 1% | ENTER_FULL × GS_RISK_SCALE | Câbler `GRADE_RISK_PCT × GS_RISK_SCALE` dans le sizing live (`execution/risk_calculator.py`) |
| Toutes les `GS_*` | non lues par le runtime live | lues par le replay | Vérifier que `config.py` expose ces constantes ET que le live les consomme réellement |

**Vérification** : après harmonisation, un trade A+ en live doit risquer le même % qu'en replay, et le SL protégé doit se placer à +0.5R (pas +0.10R).

---

## 3. ÉTAPE 2-D — Config PAPER/DÉMO + garde-fous anti-compte-réel

`.env` (déjà présent, contient MT5 démo / Discord / Drive / news) — vérifier/forcer :
```bash
RUN_MODE=PAPER              # jamais LIVE tant que non validé 6 mois
ALLOW_BROKER_WRITES=1       # autorise l'exécution SUR DÉMO uniquement
GS_UNIFIED_PIPELINE=1       # active le pipeline validé (§2-A)
# + tout le bloc GS_* de la section 0
```

**Garde-fous anti-compte-réel (à vérifier/durcir dans `execution/execution_guard.py`) :**
1. **Whitelist du login MT5 démo** : refuser l'exécution si le compte connecté n'est pas le login démo attendu (comparer `mt5.account_info().login` à `MT5_DEMO_LOGIN` du `.env`).
2. **Vérifier `account_info().trade_mode == DEMO`** avant tout ordre — bloquer si REAL.
3. **`MAGIC_NUMBER=240115`** sur tous les ordres (isolation).
4. ExecutionGuard reste fail-closed : RUN_MODE, ALLOW_BROKER_WRITES, kill switch, pause, vetos.

---

## 4. ÉTAPE 3-A — Tout en arrière-plan (headless)

- **MT5** : lancer via `utils/mt5_bootstrap.py` en mode terminal sans UI (ou minimisé system tray). Pas de fenêtre requise.
- **Gold Sniper** : `pc_manager.py` → `watchdog.py` → `main.py` (les 3 process décrits dans `architecture.md §2`), lancés sans console visible (pythonw.exe ou tâche planifiée `-WindowStyle Hidden`).
- **Contrôle** : uniquement via Discord (16 commandes) + Dashboard. Aucune interaction fenêtre.

## 4-B — Démarrage automatique après reboot (~3 min)
- **Tâche planifiée Windows** (`schtasks`) déclenchée `ONLOGON` (ou `ONSTART` + délai 3 min) lançant `pc_manager.py` en hidden.
- `pc_manager.py` a déjà une **boot policy** (auto-start si pas de `kill_flag.txt`) — la brancher sur la tâche planifiée.
- Séquence de boot : MT5 d'abord (attendre connexion) → main.py → watchdogs → dashboard → notif Discord "système redémarré".

## 4-C — Gestion des pannes (recovery)
Vérifier/restaurer chaque cas via les modules existants :
| Panne | Module responsable | Comportement attendu |
|---|---|---|
| Perte Wi-Fi / reconnexion | `utils/network_watchdog.py` | veto entrées offline, alerte Discord, reprise auto |
| MT5 fermé/crashé | `utils/mt5_watchdog.py` | reconnexion 3 essais, alerte 15s/30s |
| Gold Sniper crashé | `watchdog.py` + `core/engine.py supervised_task` | restart (5 tentatives, backoff) |
| PC redémarré | tâche planifiée + boot policy | relance ~3 min + recovery positions orphelines |
| Positions ouvertes au reboot | `core/recovery_manager.py` | relecture MT5, gap-breach close si SL dépassé |
| Discord indispo | `utils/discord_notifier.py` | retry, fallback logs channel |
| API news indispo | `agents/agent_6_sentinelle.py` | mode ASSUME_HOSTILE après 5 échecs |
| Drive indispo | `utils/drive_sync.py` | retry lendemain, alerte |
| Backfill candles après coupure | `core/candle_builder.py` / import | recharger les bougies manquées avant de re-trader |

---

## 5. ÉTAPE 3-B — Migrer le dashboard Cloudflare → Vercel

Le dashboard actuel = `web/dashboard_server.py` (aiohttp + WebSocket, localhost:8765) exposé par Cloudflare Tunnel. **Cible : Vercel.**

**Problème** : Vercel héberge du statique/serverless, pas un serveur WebSocket long-running local. Solution recommandée :
1. **Frontend sur Vercel** : déployer `web/dashboard.html` (SPA) comme site statique Vercel.
2. **Backend reste local** (`dashboard_server.py` sur le PC) — exposé via un tunnel léger. Deux options :
   - **a)** Garder un tunnel (Cloudflare tunnel nommé, ou `ngrok`) pour l'API/WebSocket, et le frontend Vercel pointe dessus (variable d'env `NEXT_PUBLIC_WS_URL`).
   - **b)** Si latence critique mobile : héberger une fine API relay sur Vercel qui lit l'état depuis un store partagé. **Mais** comme Supabase est exclu en v1, l'option (a) est la plus simple et fonctionnelle.
3. **Anti-latence mobile** (exigence utilisateur) : WebSocket push (déjà en place), pas de polling ; compression payload ; heartbeat 1s. Tester depuis un téléphone sur réseau mobile.
4. **Sécurité** : token Bearer `DASHBOARD_TOKEN` obligatoire en public, redaction des secrets (déjà implémenté dans `dashboard_server.py`).

> Note : documenter clairement l'URL Vercel finale + la variable pointant vers le backend local.

---

## 6. ÉTAPE 3-C — Vérifications des intégrations (avec le `.env` existant)

| Intégration | Fichier | Test à faire |
|---|---|---|
| MT5 démo | `core/mt5_bridge.py` | connexion, `account_info` = DEMO, symbole XAUUSD dispo |
| Discord | `pc_manager.py` + `utils/discord_notifier.py` | `!status`, `!pause`, `!resume`, boutons Pause/Kill, 4 channels |
| News API | `agents/agent_6_sentinelle.py` | Finnhub/FMP/ForexFactory répondent, blackout NFP/FOMC |
| Google Drive | `utils/drive_sync.py` | OAuth2 OK, upload test à 23:00 |
| Watchdogs | `utils/*_watchdog.py` | simuler coupure → veto + alerte + reprise |
| GitHub | — | committer la version locale conforme, push |
| Dashboard | Vercel + backend local | reflète l'état réel, latence mobile acceptable |

---

## 7. Validation obligatoire AVANT rapport final (Claude Code)

1. `python -m pytest gold_sniper/tests -q -p no:cacheprovider` → **1715 passed** (1 échec pré-existant hors-scope `test_p3_payoff_r_accounting` toléré).
2. Test de parité pipeline (§2-A) : décisions live PAPER == replay `confirm_v8_scale6` sur un segment connu.
3. Vérifier les 5 gardes actifs en live (log `loss_guard_diag` équivalent).
4. Connexion MT5 démo OK, `trade_mode=DEMO`, whitelist login.
5. Discord contrôle OK. Vercel dashboard OK (desktop + mobile). Drive OK. Watchdogs OK.
6. Un cycle reboot PC complet → relance auto ~3 min → recovery OK.
7. Backtest/replay court de sanité (1 mois) avec le runtime unifié → métriques cohérentes.

## 8. Contenu du rapport final (Claude Code)
- Ce qui a été modifié (pipeline unifié, gardes portés, divergences harmonisées, dashboard Vercel, auto-start).
- Ce qui a été testé + résultats (parité pipeline, tests, intégrations).
- Ce qui fonctionne / ce qui reste à surveiller.
- Comment démarrer/arrêter Gold Sniper (commandes + Discord).
- Comment vérifier que tout tourne en arrière-plan (dashboard, `!status`, `!health`, `!pc_status`).

---

## 9. INTERDITS ABSOLUS (rappel)
- ❌ Jamais `RUN_MODE=LIVE` (compte réel) tant que la validation démo 6 mois n'est pas faite.
- ❌ Jamais d'`order_send` hors `execution/broker_gateway.py`.
- ❌ Ne pas déployer avant que la **parité pipeline (§2-A)** soit prouvée — sinon on trade une stratégie non validée.
- ❌ Ne pas baisser les seuils / tuner pour embellir. La doctrine est figée (section 0).
- ✅ Toute divergence live/replay doit être corrigée, pas contournée.

---

*Spec produite par Opus — Étapes 0-1. Exécution Étapes 2-3 par Claude Code avec tests terminal.*
