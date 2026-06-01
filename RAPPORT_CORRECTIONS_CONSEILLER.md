# Rapport de correction - Gold Sniper V3.1

Date de validation : 2 juin 2026
Depot : Joemi930/gold-sniper
Mode de validation : local Windows, MT5, watchdog externe, logs applicatifs et tests Python.

## Resume executif

Les corrections urgentes ont ete implementees et validees ensemble avant relance. Le bot redemarre proprement via le watchdog, Agent 1 ne reste plus bloque en `STRUCTURE_NEUTRAL_MTF`, la rotation quotidienne du decision log est active, et la cadence decisionnelle est revenue autour de 1 decision par minute apres redemarrage.

Le nettoyage GitHub precedent est egalement confirme cote historique local : aucun commit, auteur ou co-auteur externe indesirable ne ressort dans `git log --all`. Le dernier commit local est attribue a `Joemi930 <joemitete12@gmail.com>`.

## Correction 1 - Debogage Agent 1

Probleme observe :

Agent 1 retournait `STRUCTURE_NEUTRAL_MTF` pendant plus de 57 heures, y compris pendant London Open, NY Open et un mouvement significatif d'environ 70 pips. Cela bloquait ensuite la chaine d'agents et forçait des decisions `REJECT`.

Actions realisees :

- Ajout des logs `AGENT1_DEBUG` dans la boucle principale d'Agent 1.
- Ajout d'un calcul asynchrone via `run_in_executor` pour isoler le calcul lourd de structure.
- Verification explicite que l'executor retourne bien des structures valides.
- Correction de la classification de structure afin de scanner les swings dans l'ordre chronologique.
- Ajout d'un fallback de structure sur cassure directionnelle lorsque les swings sont insuffisants.
- Ajout du test d'isolation `test_agent1.py` avec injection de bougies 4H/15M baissieres.

Validation :

Logs observes :

```text
AGENT1_DEBUG: 4H=767 | 15M=1000 | market.4h=0 | market.15m=0
AGENT1_DEBUG: executor_ok=True | 4H_state=BEARISH event=BOS | 15M_state=BEARISH event=BOS | result=MTF_ALIGNED_SHORT_BOS_FRESH=0
```

Test d'isolation :

```text
AGENT1_ISOLATION: direction=SHORT score=95 reason=MTF_ALIGNED_SHORT_BOS_FRESH=0
```

Conclusion :

Agent 1 detecte maintenant correctement un BOS SHORT sur scenario baissier et ne reste plus bloque en `STRUCTURE_NEUTRAL_MTF` sur ce cas.

## Correction 2 - Watchdog autonome a 3 niveaux

Probleme observe :

Le bot pouvait rester inactif pendant plusieurs heures apres perte de heartbeat. L'ancien watchdog capitulait apres 3 relances et ne garantissait pas une recuperation complete.

Actions realisees :

- Niveau 1 : soft restart de `main.py`, tentatives 1 a 3, avec attente et confirmation via `bot_ready.json`.
- Niveau 2 : recuperation par `pc_manager.py`, tentatives 4 a 6, via `data/watchdog_recovery.json`.
- Niveau 3 : nuclear reset en tentative 7 ou si `pc_manager` est absent.
- Respect absolu de `kill_flag.txt` : si le kill intentionnel existe, le watchdog ne relance rien.
- Ajout de `data/pc_manager.pid` pour confirmer la presence du manager.
- Ajout d'une boucle dans `pc_manager.py` qui lit `data/watchdog_recovery.json` toutes les 10 secondes.
- Renforcement du nettoyage des doublons manager/watchdog, en protegeant le parent WindowsApps du vrai processus Python.

Validation Niveau 3 :

Simulation executee localement :

```text
kill_flag_present= False
launcher_command= ['wscript.exe', '...\\gold_sniper\\lancer_manager.vbs']
nuclear_result= True
bot_ready_exists= True
```

Etat apres reset :

```text
pc_manager.py actif
watchdog.py actif
main.py actif
terminal64.exe actif
cloudflared.exe actif
```

`bot_ready.json` confirme :

```json
{
  "phase": "engine_ready",
  "pid": 1668,
  "mode": "PAPER"
}
```

Point d'attention :

Le code tente bien les notifications Discord REST, mais les logs montrent des erreurs externes `HTTP Error 403: Forbidden` et parfois des erreurs SSL/DNS. La mecanique watchdog est fonctionnelle, mais l'envoi REST Discord depend encore des droits/configuration du token et du canal.

## Correction 3 - Rotation quotidienne du decision log

Probleme observe :

`decision_log.jsonl` accumulait plusieurs jours et atteignait environ 6 MB ou plus.

Actions realisees :

- Passage a un fichier quotidien UTC : `decision_log_YYYY-MM-DD.jsonl`.
- Conservation des 7 derniers fichiers quotidiens.
- Lecture retrocompatible de l'ancien `decision_log.jsonl`.
- Mise a jour des fonctions d'analyse de performance et de mise a jour de resultat de trade pour parcourir les logs quotidiens.

Validation :

Test de rotation avec fichiers simules :

```text
count 7
logs [
  decision_log_2026-05-25.jsonl,
  decision_log_2026-05-26.jsonl,
  decision_log_2026-05-27.jsonl,
  decision_log_2026-05-28.jsonl,
  decision_log_2026-05-29.jsonl,
  decision_log_2026-05-30.jsonl,
  decision_log_2026-06-01.jsonl
]
```

Conclusion :

La rotation garde exactement 7 fichiers, en incluant le fichier du jour UTC.

## Verification run_in_executor Agents 2 et 3

Agent 2 :

`detect_order_blocks()` est appele via `loop.run_in_executor`.

Agent 3 :

`detect_swings()` est appele via `loop.run_in_executor`.

Conclusion :

Les optimisations executor des Agents 2 et 3 sont toujours presentes.

## Cadence orchestrateur et decision log

Observation apres redemarrage :

Les dernieres entrees du nouveau decision log montrent une cadence autour de 1 decision par minute. Une minute a affiche 2 entrees pendant la sequence de relance, ce qui reste dans la tolerance demandee de 1 a 2 decisions/minute.

Extrait :

```text
2026-06-01T23:24 - 2 decisions pendant relance
2026-06-01T23:26 - 1 decision
2026-06-01T23:27 - 1 decision
```

Conclusion :

La cadence observee est conforme a l'objectif operationnel apres stabilisation.

## Validation technique executee

Commandes de validation effectuees :

```text
python -m compileall agents watchdog.py pc_manager.py utils\\decision_logger.py utils\\single_instance.py test_agent1.py
python test_agent1.py
Simulation locale du watchdog Niveau 3
Validation rotation decision_log
Verification git log --all contre l'ancien contributeur indesirable
```

Resultats :

- Compilation Python OK.
- Test Agent 1 OK.
- Watchdog Niveau 3 OK.
- Rotation decision log OK.
- L'ancien contributeur indesirable est absent de l'historique Git local.

## Fichiers documentaires supprimes du depot

Les fichiers suivants ont ete retires selon instruction :

- `CHANGELOG.md`
- `gold_sniper/CHANGELOG.md`
- `gold_sniper/MIGRATION_DISCORD_MACON.md`
- `gold_sniper/README.md`
- `gold_sniper/architecture.md`
- `gold_sniper/walkthrough.md`

## Points restants a surveiller

- Corriger la configuration Discord REST si les notifications watchdog doivent absolument arriver dans le canal : les erreurs actuelles indiquent un probleme de droits, de canal, de token ou de validation SSL.
- Surveiller le decision log pendant une session live complete afin de confirmer que la cadence reste alignee sur la cloture de bougie hors sequence de redemarrage.
- Revoquer toute cle GitHub ou token qui a ete partagee dans la conversation, puis recreer une cle propre si necessaire.
