"""Markdown report generation for Phase 7 replay pack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_phase_7_replay_summary(path: Path, metrics: dict[str, Any], *, process_status: str) -> None:
    trade = metrics.get("trade_simulation", {})
    lines = [
        "# Phase 7 Replay Summary",
        "",
        "## 1. Periode et donnees detectees",
        f"- Symbole: {metrics.get('symbol')}",
        f"- Debut: {metrics.get('date_start')}",
        f"- Fin: {metrics.get('date_end')}",
        f"- Timeframes: {', '.join(metrics.get('timeframes_used', []))}",
        f"- Evenements: {metrics.get('total_bars_or_events')}",
        "",
        "## 2. Methode de replay",
        "- Replay offline batch, sans MT5, sans broker, sans reseau.",
        "- Chaque evenement appelle `evaluate_unified_xauusd_strategy(event)`.",
        "- Les donnees manquantes ne sont pas inventees; elles produisent WAIT via le pipeline.",
        "",
        "## 3. Resultats globaux",
        f"- Total decisions: {metrics.get('total_decisions')}",
        f"- ENTER: {metrics.get('ENTER_count')}",
        f"- WAIT: {metrics.get('WAIT_count')}",
        f"- REJECT: {metrics.get('REJECT_count')}",
        f"- ENTER rate: {metrics.get('ENTER_rate')}",
        f"- Score moyen: {metrics.get('average_score')}",
        f"- Confidence moyenne: {metrics.get('average_confidence')}",
        f"- News source: {metrics.get('news_source', 'NON_DISPONIBLE')}",
        f"- News chargees: {metrics.get('news_loaded_count', 0)}",
        f"- News context missing: {metrics.get('events_with_news_context_missing', 0)}",
        f"- News veto: {metrics.get('events_with_news_veto', 0)}",
        "",
        "## 4. Decisions ENTER / WAIT / REJECT",
        _json_block(
            {
                "ENTER": metrics.get("ENTER_count"),
                "WAIT": metrics.get("WAIT_count"),
                "REJECT": metrics.get("REJECT_count"),
            }
        ),
        "",
        "## 5. Raisons principales de blocage",
        _json_block(metrics.get("main_missing_condition_counts", {})),
        "",
        "## 6. Resultats par session",
        _json_block(metrics.get("decision_by_session", {})),
        "",
        "## 7. Resultats par setup_type",
        _json_block(metrics.get("decision_by_setup_type", {})),
        "",
        "## 8. Resultats par mois",
        _json_block(metrics.get("decision_by_month", {})),
        "",
        "## 9. Trades simules si disponibles",
        _json_block(trade),
        "",
        "## 9 bis. Evidence coverage Agent1-Agent5",
        _json_block(
            {
                "coverage": metrics.get("evidence_coverage", {}),
                "quality": metrics.get("evidence_quality_distribution", {}),
                "setup_type_distribution": metrics.get("setup_type_distribution", {}),
                "top_warnings": metrics.get("top_warning_conditions", {}),
            }
        ),
        "",
        "## 9 ter. Opus funnel diagnostics",
        _json_block(
            {
                "session_context_unknown_count": metrics.get("session_context_unknown_count"),
                "off_session_count": metrics.get("off_session_count"),
                "setup_type_unknown_rate": metrics.get("setup_type_unknown_rate"),
                "funnel_exit_stage_counts": metrics.get("funnel_exit_stage_counts", {}),
                "poi_reject_own_count": metrics.get("poi_reject_own_count"),
                "poi_reject_inherited_count": metrics.get("poi_reject_inherited_count"),
                "micro_reject_own_count": metrics.get("micro_reject_own_count"),
                "micro_reject_inherited_count": metrics.get("micro_reject_inherited_count"),
                "near_miss_count": metrics.get("near_miss_count"),
                "best_scenario_distribution": metrics.get("best_scenario_distribution", {}),
                "phase_8_ready": metrics.get("phase_8_ready"),
                "phase_8_blocking_reasons": metrics.get("phase_8_blocking_reasons", []),
            }
        ),
        "",
        "## 10. Limites des donnees",
        "- Le calendrier news local doit etre charge depuis un cache valide; sans cache, le pipeline garde NEWS_CONTEXT_MISSING.",
        "- Les agents live ne sont pas reconstruits; le replay construit seulement les preuves calculables offline.",
        "- SL/TP theorique non calcule si aucun ENTER ou si modele structurel indisponible.",
        "",
        "## 11. Points de risque",
        "- Les resultats mesurent la stricte disponibilite des preuves shadow, pas une performance live.",
        "- Aucune optimisation de seuil n'a ete effectuee.",
        "- Les donnees locales se terminent avant la fin de juin.",
        "",
        "## 12. Conclusion provisoire",
        "- La strategie n'est pas declaree rentable.",
        "- Le systeme n'est pas autorise pour le live.",
        "- Les donnees montrent uniquement le comportement shadow actuel et les preuves manquantes.",
        f"- replay_process_status: {process_status}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_evidence_reconstruction_summary(path: Path, metrics: dict[str, Any], *, process_status: str) -> None:
    lines = [
        "# Phase 7 Evidence Reconstruction Summary",
        "",
        "## Methode",
        "- Reconstruction offline Agent1-Agent5 depuis 4H, 15m et 1m.",
        "- Anti-lookahead: chaque decision utilise uniquement les bougies cloturees avant ou a T.",
        "- Les preuves absentes restent absentes; aucun signal positif n'est invente.",
        "",
        "## Coverage",
        _json_block(metrics.get("evidence_coverage", {})),
        "",
        "## Counts",
        _json_block(
            {
                "htf_context_available_count": metrics.get("htf_context_available_count"),
                "dol_available_count": metrics.get("dol_available_count"),
                "liquidity_story_available_count": metrics.get("liquidity_story_available_count"),
                "poi_available_count": metrics.get("poi_available_count"),
                "premium_discount_available_count": metrics.get("premium_discount_available_count"),
                "ote_available_count": metrics.get("ote_available_count"),
                "micro_available_count": metrics.get("micro_available_count"),
                "micro_trigger_count": metrics.get("micro_trigger_count"),
            }
        ),
        "",
        "## Evidence quality distribution",
        _json_block(metrics.get("evidence_quality_distribution", {})),
        "",
        "## Setup types",
        _json_block(metrics.get("setup_type_distribution", {})),
        "",
        "## Blocages restants",
        _json_block(metrics.get("main_missing_condition_counts", {})),
        "",
        "## Funnel Opus",
        _json_block(
            {
                "funnel_exit_stage_counts": metrics.get("funnel_exit_stage_counts", {}),
                "funnel_exit_reason_counts": metrics.get("funnel_exit_reason_counts", {}),
                "near_miss_by_stage": metrics.get("near_miss_by_stage", {}),
                "poi": {
                    "accept": metrics.get("poi_accept_count"),
                    "watch_near_miss": metrics.get("poi_watch_near_miss_count"),
                    "reject_own": metrics.get("poi_reject_own_count"),
                    "reject_inherited": metrics.get("poi_reject_inherited_count"),
                },
                "micro": {
                    "confirmed": metrics.get("micro_confirmed_count"),
                    "watch_near_miss": metrics.get("micro_watch_near_miss_count"),
                    "reject_own": metrics.get("micro_reject_own_count"),
                    "reject_inherited": metrics.get("micro_reject_inherited_count"),
                },
            }
        ),
        "",
        "## Statut",
        f"- replay_process_status: {process_status}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_final_opus_dossier(path: Path, metrics: dict[str, Any], *, process_status: str) -> None:
    lines = [
        "# Phase 7 Final Opus Dossier",
        "",
        "## 1. Objectif de Gold Sniper",
        "- Assistant/bot de trading XAUUSD shadow visant une lecture institutionnelle auditable.",
        "",
        "## 2. Doctrine Kasper/ICT utilisee",
        "- News/session/risk -> HTF/DOL -> liquidite -> POI -> premium/OTE -> micro confirmation -> decision shadow.",
        "- CHoCH seul ne suffit jamais. La liquidite n'est pas un signal d'entree isole.",
        "",
        "## 3. Phases 0 a 7 realisees",
        "- Phase 0: gel legacy.",
        "- Phase 1: strategie XAUUSD unifiee shadow.",
        "- Phase 2: POI quality gate.",
        "- Phase 3: micro confirmation engine.",
        "- Phase 4: liquidity state machine.",
        "- Phase 5: session/premium/OTE gate.",
        "- Phase 6: decision explainer.",
        "- Phase 7: replay historique + reconstruction evidence offline.",
        "",
        "## 4. Ce qui a ete gele",
        "- Anciennes strategies autonomes sous `gold_sniper/strategies/`.",
        "",
        "## 5. Ce qui a ete ajoute",
        "- Builders offline Agent1-Agent5, simulation conservative, rapports replay/evidence.",
        "",
        "## 6. Ce qui n'a PAS ete modifie",
        "- Aucune regle strategy/*, aucun live, aucun broker, aucun MT5.",
        "",
        "## 7. Donnees utilisees",
        _json_block(metrics.get("data_profile", {})),
        "",
        "## 8. News cache utilise",
        _json_block(
            {
                "news_source": metrics.get("news_source"),
                "news_loaded_count": metrics.get("news_loaded_count"),
                "events_with_news_context_missing": metrics.get("events_with_news_context_missing"),
            }
        ),
        "",
        "## 9. Methode de reconstruction offline",
        "- H4/15m: biais HTF, DOL, POI, liquidite, premium/discount, OTE.",
        "- M1: micro sweep/displacement/reclaim/retest dans le POI.",
        "",
        "## 10. Regle anti-lookahead",
        "- Decision building: passe uniquement. Trade outcome simulation: futur autorise uniquement apres ENTER.",
        "",
        "## 11. Resultats replay",
        _json_block(
            {
                "total_decisions": metrics.get("total_decisions"),
                "ENTER": metrics.get("ENTER_count"),
                "WAIT": metrics.get("WAIT_count"),
                "REJECT": metrics.get("REJECT_count"),
            }
        ),
        "",
        "## 12. Resultats par session",
        _json_block(metrics.get("decision_by_session", {})),
        "",
        "## 13. Resultats par setup type",
        _json_block(metrics.get("decision_by_setup_type", {})),
        "",
        "## 14. Resultats par mois",
        _json_block(metrics.get("decision_by_month", {})),
        "",
        "## 15. Evidence coverage Agent1-Agent5",
        _json_block({"coverage": metrics.get("evidence_coverage", {}), "quality": metrics.get("evidence_quality_distribution", {})}),
        "",
        "## 16. Blocages principaux",
        _json_block(metrics.get("main_blocking_stage_counts", {})),
        "",
        "## 16 bis. Diagnostics Opus",
        _json_block(
            {
                "session_context_unknown_count": metrics.get("session_context_unknown_count"),
                "off_session_count": metrics.get("off_session_count"),
                "setup_type_unknown_count": metrics.get("setup_type_unknown_count"),
                "setup_type_unknown_rate": metrics.get("setup_type_unknown_rate"),
                "funnel_decoupled": metrics.get("funnel_decoupled"),
                "near_miss_count": metrics.get("near_miss_count"),
                "phase_8_ready": metrics.get("phase_8_ready"),
                "phase_8_blocking_reasons": metrics.get("phase_8_blocking_reasons", []),
            }
        ),
        "",
        "## 17. Decisions ENTER/WAIT/REJECT",
        "- Voir `phase_7_replay_decisions.csv`.",
        "",
        "## 18. Trade simulation si disponible",
        _json_block(metrics.get("trade_simulation", {})),
        "",
        "## 19. Limites restantes",
        "- Reconstruction offline minimale; elle ne remplace pas les agents live complets.",
        "- Les seuils ne sont pas optimises.",
        "- Les resultats ne prouvent pas une rentabilite.",
        "",
        "## 20. Risques",
        "- Faux negatif possible si la reconstruction offline manque des nuances Agent1-Agent5.",
        "- Aucun usage live autorise.",
        "",
        "## 21. Questions precises pour Opus",
        "1. La reconstruction offline respecte-t-elle suffisamment la doctrine Kasper/ICT ?",
        "2. Les regles sont-elles trop strictes ou encore trop permissives ?",
        "3. Le pipeline peut-il passer a Phase 8 si des ENTER existent ?",
        "4. Si aucun ENTER, est-ce un probleme de strategie ou de preuve offline ?",
        "5. Quels seuils doivent etre geles avant Phase 8 ?",
        "6. Quelles parties doivent rester shadow-only avant paper trading ?",
        "",
        f"replay_process_status: {process_status}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_modifications_summary_for_opus(path: Path) -> None:
    content = """# Gold Sniper — Modifications Summary for Opus Review

## Phase 0 — Legacy Strategy Freeze
- Les anciennes strategies autonomes ont ete gelees comme briques futures: veto, score, POI state, trigger ou indicateur.

## Phase 1 — Unified XAUUSD Strategy Shadow
- Creation du pipeline central shadow `unified_xauusd_strategy.py`.

## Phase 2 — POI Quality Gate
- Creation du gate POI Kasper/ICT pour OB, FVG et contexte de qualite.

## Phase 3 — Micro Confirmation Engine
- Creation du moteur micro: CHoCH seul ne suffit jamais; displacement/reclaim/retest requis.

## Phase 4 — Liquidity State Machine
- Creation de la state machine liquidite: DOL, sweep, purge, revert, run, breakout acceptance, cleanup.

## Phase 5 — Session / Premium / OTE Gate
- Creation du gate session/news/premium-discount/OTE.
- Tokyo/Asia bloque par defaut; premium/discount strict en reversal/sniper pullback.

## Phase 6 — Decision Explainer
- Creation de l'explainer shadow: stage responsable, primary reason, readiness, alignment, digest.

## Phase 7 — Replay Pack
- Creation du pack replay historique offline pour tester le pipeline central sans assouplir les regles.
- Correctif final: reconstruction offline Agent1-Agent5 depuis 4H/15m/1m, anti-lookahead, evidence coverage et dossier Opus final.

## Known Limitations
- Les donnees news completes ne sont pas presentes localement.
- Les agents live ne sont pas reconstruits avec toute leur richesse dans ce pack initial.
- Le replay ne prouve pas la rentabilite et ne valide pas le live.

## Questions for Opus
1. La hierarchie Kasper/ICT est-elle respectee ?
2. Les regles shadow sont-elles trop strictes ou encore trop permissives ?
3. Les resultats replay justifient-ils une Phase 8 validation statistique approfondie ?
4. Quels seuils doivent rester geles ?
5. Quels modules doivent etre revus avant paper trading ?
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"
