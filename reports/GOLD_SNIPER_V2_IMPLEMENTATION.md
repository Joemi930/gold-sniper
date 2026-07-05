# GOLD SNIPER V2 — Sélecteur de régime dual-edge (implémenté par Claude, 02/07/2026)

## Ce qui a changé (4 fichiers, édits chirurgicaux)
1. **`config.py`** : flag `STRATEGY_V2` (`$env:GS_STRATEGY_V2="1"`, défaut OFF).
2. **`replay/decision_pipeline.py`** : fix cause-racine — `primary_regime` calculé par agent_1
   n'était JAMAIS écrit au blackboard (c'est pourquoi l'ancien GS_REGIME_FILTER ne mordait pas).
   Maintenant écrit, et il coule naturellement jusqu'à l'évidence Kasper.
3. **`strategy/kasper_contracts.py`** : `Agent1Context.primary_regime` (RANGE/WEAK_*/STRONG_*).
4. **`strategy/kasper_scenario_engine.py`** — le cœur V2 :
   - Dispatch par régime : **STRONG_UP/DOWN → continuation UNIQUEMENT** (le reversal y est
     bloqué — fade de tendance forte = le tueur de février, 0/5 straight-SL) ;
     **RANGE/WEAK → reversal UNIQUEMENT** (la mean-reversion dans son habitat).
   - Le modèle continuation (qui existait mais était verrouillé WAIT) est **débloqué** :
     poids dédiés (5 gates → 100), side = AVEC le biais HTF, décision par grade (A+/A → ENTER).

## Validation (sandbox)
- STRONG_UP + évidence complète → `bos_continuation`, side BUY, **A_PLUS, ENTER_ELIGIBLE** (5/5 gates) ✓
- STRONG_DOWN → continuation SELL ✓ ; RANGE → chemin reversal ✓
- V2 OFF → comportement legacy intact, **1710 tests passent** ✓
- Le trade manager n'exige que ENTER_ELIGIBLE (aucun filtre par scenario_type) → chemin ouvert ✓

## Commande replay (V2 complet, 5 mois continus)
```powershell
$env:GS_EXECUTION_TF="15m"; $env:GS_STOP_ATR_FLOOR_MULT="2.0"; $env:GS_STRATEGY_V2="1"; $env:GS_REGIME_FILTER=""
python -m gold_sniper.replay_app.Gold_Sniper_Replay --no-menu --graded-entry --warmup-start 2025-12-01 --start 2026-01-01 --end 2026-06-01 --run-id v2_valid --initial-equity 100
```
IMPORTANT : `GS_REGIME_FILTER` doit rester VIDE (V2 le remplace au niveau Kasper).

## Ce qu'on lit dans summary.json
- `kasper_scenario_type_distribution` : doit montrer un MIX (`liquidity_sweep_reversal` + `bos_continuation`).
- `monthly_breakdown` : février doit cesser d'être un massacre (soit continuation profitable, soit quasi-0 trade).
- Net `expectancy_R` global et par mois ; WR ; fréquence.

## ANTICIPATIONS (corrections pré-planifiées)
- **Cas A — février neutralisé/positif, net global positif** → V2 validée ; on discute affinage
  (poids continuation, floor) puis demo.
- **Cas B — 0 trade continuation (distribution 100% reversal encore)** → soit aucun régime
  STRONG détecté par agent_1 (vérifier `monthly_breakdown` de février : s'il y a encore des
  trades reversal en février, le régime février n'est PAS classé STRONG → le problème est la
  CLASSIFICATION d'agent_1, on la renforce — ADX/pente) ; soit continuation gates trop stricts
  (micro_confirmation rare en tendance) → on assouplit `continuation_bos` (accepter displacement seul).
- **Cas C — continuation trade mais perd** → les gates continuation sont trop laxistes ;
  on exige `htf_confluence` du POI ou freshness, ou on remonte le seuil de grade à A+ seul.
- **Cas D — février vide mais AUSSI les bons mois vidés** → la classification STRONG est trop
  large ; on resserre la définition STRONG dans agent_1 (elle absorbe des WEAK).
- **Cas E — crash/exception** → m'envoyer la stacktrace ; le dispatch est isolé dans
  `_evaluate_impl`, rollback = `$env:GS_STRATEGY_V2=""` (retour legacy instantané).
- **Cas F — ça marche sur 5 mois** → ne PAS figer tout de suite : on refait UNE validation
  déc-2025 en warmup court pour vérifier le mois hors-échantillon, puis on fige STRATEGY_V2=True.
