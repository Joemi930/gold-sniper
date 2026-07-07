# Gold Sniper — Rapport de validation DÉMO avant redémarrage

**Destinataire :** Opus, architecte suprême
**Date d’exécution :** 7 juillet 2026
**Branche :** `P1-Gold_sniper_trading_and_optimisation`
**Dépôt :** `Joemi930/gold-sniper`
**Périmètre :** validation locale PAPER/DEMO jusqu’au point précédant le véritable redémarrage Windows.

## 1. Conclusion exécutive

Le pipeline PAPER, MT5 démo, le Dashboard Cloudflare, Discord, les watchdogs et l’autostart manuel par tâche planifiée ont été testés avec succès.

La certification finale après redémarrage Windows n’est volontairement pas prononcée dans ce rapport : le propriétaire doit effectuer lui-même ce redémarrage et vérifier la relance automatique après environ trois minutes.

Une réserve externe subsiste : Google Drive n’est pas authentifié, car `gold_sniper/data/credentials.json` et `gold_sniper/data/drive_token.json` sont absents. Aucun succès Drive n’est inventé.

Les clés Finnhub et FMP sont présentes, mais les endpoints économiques répondent respectivement HTTP 403 et HTTP 402. La chaîne reste opérationnelle grâce au fallback ForexFactory/cache local, testé avec succès.

## 2. Doctrine de sécurité confirmée

- `RUN_MODE=PAPER`
- `LIVE_MODE=False`
- `ALLOW_BROKER_WRITES=True`, limité au compte démo autorisé
- `GS_UNIFIED_PIPELINE=1`
- `GS_MIN_RR=4`
- `GS_RISK_SCALE=6`
- `GS_BE_PLUS_RR=0.5`
- `GS_MAX_CONCURRENT_SAME_SIDE=1`
- rolling drawdown 10 %, pause 7 jours
- loss breaker 2, cooldown 60 minutes
- aucun `!calibrate confirm` exécuté
- aucune utilisation d’un compte MT5 réel

Le contrôle MT5 a confirmé : initialisation réussie, login conforme à la whitelist, `trade_mode=DEMO`, serveur conforme et symbole `XAUUSD.m` disponible.

## 3. Corrections et composants ajoutés

### 3.1 Runtime et imports

- Correction de `pc_manager.py` pour rendre `utils` importable sans `PYTHONPATH` manuel.
- Correction de `main.py` pour exposer à la fois la racine du dépôt et le package `gold_sniper` dans `sys.path`.
- Chargement déterministe de `.env` puis `.env.runtime` dans `config.py`.
- Ajout des dépendances runtime manquantes : `pyarrow`, `pytz` et dépendances Google Drive.
- Installation locale réussie de `pyarrow 24.0.0` et `pytz 2026.2`.

### 3.2 Watchdog externe

Le fichier critique `gold_sniper/watchdog.py`, attendu par l’architecture mais absent du dépôt, a été créé.

Fonctions implémentées :

- verrou d’instance unique ;
- lancement caché de `main.py` ;
- surveillance du processus et du heartbeat ;
- état persistant dans `data/watchdog_state.json` ;
- cinq redémarrages bornés avec backoff ;
- signal de recovery vers le PC Manager après épuisement ;
- respect de `kill_flag.txt` ;
- nettoyage du verrou à la sortie.

Le nettoyage lifecycle a aussi été durci pour attendre réellement la disparition de `watchdog.py` et `main.py` avant une relance.

### 3.3 Dashboard

TradingView et `lightweight-charts` ont été retirés.

Le Dashboard comprend désormais :

- carte animée des sept agents autour de l’orchestrateur ;
- avatars optimisés provenant des créations du dossier `docs` ;
- flux de décisions ;
- suivi de position `SL → ENTRY → TP1 → TP2` ;
- P&L en R et télémétrie ;
- navigation mobile ;
- état offline explicite, sans données synthétiques trompeuses.

Le serveur sert les assets locaux, compresse le WebSocket et n’envoie plus les 200 bougies ni les couches TradingView dans chaque payload.

Cloudflare reste volontairement le lien principal. Vercel est différé jusqu’à une future phase d’hébergement permanent.

### 3.4 Discord

- Correction des notifications de boot : fallback `alerts → commands → logs`.
- Correction de `!calibrate` lorsqu’une base SQLite ne contient pas encore la table `trades`.
- Aucun changement de poids n’a été appliqué.
## 4. Résultats des tests

### 4.1 Suite automatisée

```text
1695 passed
631 warnings
37 subtests passed
Durée : 61.01 s
```

Les avertissements restants concernent principalement des dépréciations `datetime.utcnow`, des tests unittest asynchrones historiques et une recommandation aiohttp `AppKey`. Aucun test n’a échoué après suppression des scripts temporaires de diagnostic.

### 4.2 Parité live/replay

```text
comparisons=88
divergences=0
PARITY PROVED
risk_scale=6
min_rr=4
```

L’adaptateur `unified_live_decision` reproduit les champs décisionnels du `ReplayDecisionPipeline` sur le segment contrôlé.

### 4.3 Dashboard local et Cloudflare

- page locale : HTTP 200 ;
- page publique Cloudflare : HTTP 200 ;
- assets agents : HTTP 200 ;
- WebSocket public : message `full_state` reçu ;
- payload final observé : environ 32 Ko ;
- temps de réception WS observé lors du contrôle final : 6,3 ms ;
- cadence serveur : environ une mise à jour par seconde.

La latence réelle sur le téléphone en 4G/5G reste une vérification utilisateur après remise du lien dynamique.
### 4.4 Commandes Discord

Tests réels via l’inbox opérationnelle et vérification des réponses sur Discord :

| Commande | Résultat |
|---|---|
| `!status` | OK |
| `!pause` | OK |
| `!resume` | OK |
| `!risk` | OK, lecture sans modification |
| `!trades` | OK |
| `!agents` | OK |
| `!regime` | OK |
| `!news` | OK |
| `!backtest` | OK, réponse finale reçue |
| `!calibrate` | OK, dry-run/refus sûr ; aucun confirm |
| `!report` | OK |
| `!logs` | OK |
| `!memory` | OK |
| `!health` | OK |
| `!chart` | OK |
| `!help` | OK |

Les commandes lifecycle ont également été testées directement sur le même code de production :

- `!pc_status` : statut cohérent ;
- `!kill` : arrêt de main/watchdog et création du kill flag ;
- `!start` : relance complète et nouvelle URL Cloudflare ;
- `!restart` : arrêt propre puis nouvelle instance main/watchdog et nouvelle URL.
### 4.5 Recovery et pannes

- Crash forcé de `main.py` : watchdog externe relancé automatiquement avec un nouveau PID et heartbeat frais.
- Fermeture forcée de MetaTrader 5 : un nouveau processus terminal a été rétabli et le compte démo est redevenu accessible.
- Perte réseau simulée au niveau du watchdog : veto `NETWORK_OFFLINE_*` activé, puis supprimé avec état `NETWORK_RESTORED`.
- Aucun ordre ni position n’était ouvert durant ces simulations.

### 4.6 Tâche planifiée et lancement caché

La tâche `GoldSniper_PCManager` est activée avec :

- déclenchement à l’ouverture de session ;
- délai de trois minutes ;
- exécution via `wscript.exe` et `pythonw.exe` ;
- politique `MultipleInstances IgnoreNew`.

Un lancement manuel de cette tâche a été réalisé après arrêt complet de la pile. Résultat :

- dernier résultat Windows : `0` ;
- PC Manager caché lancé ;
- watchdog caché lancé ;
- `main.py` caché lancé ;
- MT5 disponible ;
- Cloudflare lancé ;
- heartbeat frais ;
- `bot_ready.json` en phase `engine_ready` et mode `PAPER` ;
- Dashboard local/public et WebSocket validés après ce lancement caché.

Le seul test volontairement non exécuté est le vrai redémarrage Windows.
## 5. Intégrations externes

### Finnhub

- token présent ;
- endpoint calendrier économique : HTTP 403 ;
- cause probable : permission/plan API insuffisant pour cet endpoint.

### Financial Modeling Prep

- token présent ;
- endpoint calendrier économique : HTTP 402 ;
- cause probable : endpoint réservé à un plan payant.

### ForexFactory / cache local

- fallback testé ;
- événements chargés ;
- source finale : `FOREXFACTORY` ;
- `feed_alive=True`.

Le comportement fail-safe reste conforme : si les fournisseurs premium refusent l’accès, l’Agent 6 utilise son fallback au lieu de tuer le moteur.

### Google Drive

Test réel tenté, résultat :

```text
credentials.json introuvable
gold_sniper/data/credentials.json
```

Les dépendances Google sont installées et le module gère proprement l’échec, mais l’upload réel nécessite encore :

1. un OAuth Client Desktop Google dans `gold_sniper/data/credentials.json` ;
2. une validation utilisateur dans le navigateur au premier lancement ;
3. la création automatique de `drive_token.json` ;
4. un nouveau test d’upload et de lecture.
## 6. État actuel laissé sur le PC

La pile est actuellement lancée en arrière-plan par la tâche Windows :

```text
GoldSniper_PCManager
  → pc_manager.py
  → watchdog.py
  → main.py
  → MT5 Demo
  → Dashboard localhost:8765
  → Cloudflare Tunnel
  → Discord
```

Le lien Cloudflare est dynamique : il change à chaque relance complète. Le PC Manager transmet le nouveau lien sur Discord.

## 7. Vérification finale à effectuer après redémarrage

Le propriétaire doit maintenant redémarrer Windows, se reconnecter à sa session, puis attendre environ trois à cinq minutes.

Critères de réussite :

1. `GoldSniper_PCManager` s’exécute automatiquement ;
2. MT5 se reconnecte au compte démo autorisé ;
3. watchdog et `main.py` apparaissent en arrière-plan ;
4. un nouveau `bot_ready.json` indique `engine_ready` et `PAPER` ;
5. Discord reçoit la notification de boot et le nouveau lien Cloudflare ;
6. `!pc_status`, `!status` et `!health` répondent ;
7. le Dashboard s’ouvre sur téléphone et reçoit les états WebSocket ;
8. aucune erreur ou exception critique nouvelle n’apparaît dans les logs.

## 8. Statut de certification

**Statut avant reboot : PRÊT POUR LE TEST FINAL DE REDÉMARRAGE DÉMO.**

La phrase « Gold Sniper est maintenant opérationnel pour la démo » ne doit être prononcée qu’après validation de ce véritable redémarrage Windows et, pour une certification sans réserve, après configuration OAuth Google Drive.
