# 🎯 GOLD SNIPER V2.1 — Institutional Intelligence

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-black.svg)
![Status](https://img.shields.io/badge/Status-Private_Repository-red.svg)

**Gold Sniper V2.1** est un algorithme de trading institutionnel 100% automatisé, conçu exclusivement pour la paire **XAUUSD** (Or). Il s'appuie sur la méthodologie SMC (Smart Money Concepts) / ICT et un système asynchrone ultra-performant.

---

## 🧠 Architecture à 7 Agents

Le cœur du système repose sur une **unanimité pondérée (Score sur 100)** pilotée par 7 agents autonomes :

1. **Météo (Agent 1) 🌦️** : Analyse la structure de marché macro (4H) et intermédiaire (15M) (BOS, ChoCh, Phase).
2. **Cartographe (Agent 2) 🗺️** : Identifie les Order Blocks (OB) et Fair Value Gaps (FVG).
3. **Liquidité (Agent 3) 💧** : Traque les zones de liquidité (Equal Highs/Lows, Session Asiatique).
4. **Fibonacci (Agent 4) 📐** : Validation des retracements (Zone OTE).
5. **Microscope (Agent 5) 🔬** : Validation de l'entrée au tick près sur UT 1 minute.
6. **Sentinelle (Agent 6) 📰** : Aspiration du Calendrier Économique (ForexFactory) et protection contre les news.
7. **Chronos (Agent 7) ⏱️** : Gestion des sessions de liquidité (Killzones) et protection week-end.

---

## 🛡️ Sécurité & OPSEC (Prop-Firm Safe)

Conçu pour passer et conserver les comptes Prop-Firm, l'algorithme intègre des sécurités impénétrables :
- **Daily Drawdown Limit** : Blocage total de la journée si perte de -5%.
- **Maximum Trades/Jour** : Limite stricte à 2 trades par jour (anti-overtrade).
- **Gestion Dynamique du Risque** : Calcul précis du lot pour ne risquer que 1% du capital réel par trade.
- **Trade Management Avancé** : Clôture partielle automatique (50%) à 1:1 R:R, suivie d'un *Trailing Stop* basé sur l'ATR.
- **Recovery Manager** : Reprise sur crash. Scan et récupération des positions orphelines sur MT5.

---

## 🚀 Lancement Rapide

1. Assurez-vous que **MetaTrader 5** est installé, connecté à votre compte, avec l'Algo Trading activé.
2. Vérifiez que la paire `XAUUSD` est visible dans votre observation du marché.
3. Lancez le fichier de démarrage Windows :
   ```cmd
   GoldSniper.bat
   ```
*(Le script vérifiera Python, installera les dépendances manquantes, et lancera le Dashboard UI).*

---

## 📊 Dashboard UI

La V2.1 intègre un tableau de bord `CustomTkinter` avec :
- Affichage de l'Equity, Marge et PnL en temps réel.
- Réseau Neuronal animé reflétant l'état du Blackboard.
- Log feed détaillé.
- **Bouton KILL SWITCH** rouge pour tout fermer d'urgence.

---
*Développé pour la Cellule Bug Bounty / Analyse Offensive — Filtre Anti-Bullshit Activé 🟢*
