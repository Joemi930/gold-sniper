# Gold Sniper — Rapport de validation DÉMO après redémarrage et correctifs Dashboard

**Destinataire :** Opus, architecte suprême
**Date d’exécution :** 7 juillet 2026
**Branche :** `P1-Gold_sniper_trading_and_optimisation`
**Dépôt :** `Joemi930/gold-sniper`
**Périmètre :** validation locale PAPER/DEMO, redémarrage Windows réel et correctifs Dashboard post-redémarrage.

## 1. Conclusion exécutive

Le pipeline PAPER, MT5 démo, le Dashboard Cloudflare, Discord, les watchdogs et l’autostart par tâche planifiée ont été validés avant puis après un véritable redémarrage Windows. La tâche s’est exécutée automatiquement avec un résultat Windows `0`, puis `pc_manager.py`, `watchdog.py`, `main.py`, MT5 et Cloudflare ont été relancés en arrière-plan.

Après le redémarrage, trois défauts visibles ont été signalés puis corrigés : chargement des avatars/logos, mesure de latence sur téléphone et affichage du solde MT5. Google Drive reste volontairement différé à une phase ultérieure.

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
- assets agents : HTTP 200 sans token, type `image/webp` ;
- WebSocket public : message `full_state` reçu ;
- payload final observé : environ 32 Ko ;
- cadence de secours ramenée de 1 seconde à 500 ms ;
- latence désormais mesurée par un aller-retour WebSocket `ping/pong`, indépendant du décalage d’horloge entre PC et téléphone ;
- médiane glissante sur les sept dernières mesures, rafraîchie toutes les trois secondes.

La valeur précédente pouvant monter vers 1 900 ms provenait d’une soustraction entre l’horloge du téléphone et celle du PC, donc ce n’était pas une mesure réseau fiable.
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

Le véritable redémarrage Windows a ensuite été exécuté. La tâche planifiée a démarré à l’ouverture de session, avec dernier résultat `0`, puis toute la pile a retrouvé l’état `engine_ready` en mode `PAPER`.
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

## 7. Validation réelle après redémarrage

Le redémarrage Windows a été effectué par le propriétaire. Les huit critères ont été observés : exécution automatique de `GoldSniper_PCManager`, reconnexion MT5 démo, relance cachée du watchdog et du moteur, phase `engine_ready`/`PAPER`, notification Discord, nouveau tunnel Cloudflare, Dashboard accessible et heartbeat frais.

La tâche Windows affiche une dernière exécution le 7 juillet 2026 à 19:21:25 avec le résultat `0`.

## 8. Statut de certification

**Statut après reboot : PILE PAPER/DEMO ET AUTOSTART VALIDÉS, CORRECTIFS DASHBOARD DÉPLOYÉS.**

La réserve restante est Google Drive, explicitement reportée par décision du propriétaire. La certification sans réserve de cette intégration attendra la configuration OAuth et un test réel d’upload/lecture.

## 9. Correctifs Dashboard post-redémarrage

### 9.1 Avatars et logos

Le middleware public exigeait le token pour les fichiers `/assets/*`. Or le token est retiré de l’URL visible après le premier chargement, ce qui provoquait des réponses 401 sur les balises `<img>`. Les assets graphiques sont désormais publics, non sensibles, servis avec un cache d’un jour et versionnés dans le HTML. Le contrôle public a retourné HTTP 200, `image/webp`, pour `agent_1.webp`.

### 9.2 Latence et heure de Kinshasa

La mesure `Date.now() - ts_ms` a été supprimée, car elle comparait deux horloges différentes. Un protocole applicatif WebSocket `ping/pong` mesure maintenant le vrai RTT sans dépendre de l’heure du PC ou du téléphone. Le Dashboard affiche l’heure `Africa/Kinshasa` avec le suffixe `KIN`. Windows est configuré sur `W. Central Africa Standard Time` (`UTC+01:00`). Une comparaison avec l’en-tête HTTP `Date` de Cloudflare a mesuré un écart de seulement `-0.31 s`, confirmant que l’horloge du PC est correctement alignée.

### 9.3 Solde MT5

Le serveur extrait uniquement les champs non sensibles `balance`, `equity`, `margin`, `free_margin` et `daily_pnl` dans un objet `portfolio`. L’identité du compte, le login et le serveur restent masqués. Le haut droit du Dashboard affiche désormais `SOLDE`, à la place du P&L. Le compte démo contrôlé indiquait un solde et une equity de `100.00$` au moment du test.

### 9.4 Tests après correctifs

```text
1698 passed
634 warnings
37 subtests passed
Durée : 50.59 s
```

Les nouveaux tests couvrent les assets publics, l’exposition sûre du solde sans identité de compte et le `ping/pong` WebSocket.
