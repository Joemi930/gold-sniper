# Proposition de déploiement Gold Sniper — Deux architectures possibles

## Contexte commun aux deux architectures

Dans les deux cas, les rôles externes restent les mêmes :

```text
Vercel       = Dashboard web
Discord      = Télécommande + alertes + commandes
Supabase     = Base de données temps réel / état système / logs / commandes
Google Drive = Backup froid / archives / rapports / journaux
GitHub       = Dépôt officiel à remettre à jour avec la version locale actuelle
```

Le point à trancher concerne uniquement l’endroit où tournera le **moteur Gold Sniper** : soit sur le PC local, soit sur une machine cloud/VPS si une option gratuite ou très avantageuse est trouvée.

---

## Architecture 1 — Déploiement sur PC local

```text
PC local RDC
  ├─ Gold Sniper Engine
  ├─ MetaTrader 5 démo
  ├─ Watchdogs locaux
  ├─ Broker Gateway démo
  ├─ Sync Supabase
  ├─ Sync Google Drive
  └─ Connexion Discord

Supabase  ← logs / trades / décisions / heartbeats / commandes
Vercel    ← dashboard web
Discord   ← télécommande + alertes
Drive     ← backups / rapports
GitHub    ← code officiel à jour
```

### Principe

Gold Sniper tourne directement sur le PC local actuel. C’est l’option la plus simple et gratuite, car l’environnement Windows, MT5, Python, les watchdogs et les fichiers locaux existent déjà.

### Condition importante

Sur le PC local, **Gold Sniper, MetaTrader 5 et toutes les applications nécessaires doivent tourner en arrière-plan**, sans fenêtre visible ni interaction manuelle permanente.

L’utilisateur ne doit pas avoir besoin de regarder MT5 ou les fenêtres Python. Toute l’observation doit se faire via :

```text
Dashboard Vercel → suivi visuel complet
Discord          → contrôle, pause, kill, alertes, rapports
Supabase         → état système, données temps réel, historique
Google Drive     → sauvegardes et archives
```

### Avantages

```text
- Gratuit
- Simple à mettre en place
- Compatible naturellement avec MT5 Windows
- Réutilise l’architecture locale actuelle
- Plus rapide à tester en DEMO_EXECUTION
```

### Risques

```text
- Coupures de courant en RDC
- Coupures Internet locales
- PC éteint ou redémarré au mauvais moment
- Setups live potentiellement ratés pendant les interruptions
```

### Protections nécessaires

```text
- Auto-start Windows après reboot
- Watchdog qui relance Gold Sniper si crash
- MT5 watchdog avec reconnexion automatique
- Heartbeat régulier vers Supabase
- Alerte Discord si heartbeat absent
- Recovery au redémarrage
- Backfill candles après interruption
- Relecture des positions ouvertes MT5 après redémarrage
- Backup Drive automatique des journaux et rapports
```

### Usage recommandé

Cette architecture peut être utilisée comme première version de déploiement, surtout si aucune solution cloud gratuite fiable n’est disponible immédiatement.

---

## Architecture 2 — Déploiement sur VPS / machine cloud gratuite

```text
VPS ou VM cloud gratuite
  ├─ Gold Sniper Engine
  ├─ MetaTrader 5 démo
  ├─ Watchdogs cloud
  ├─ Broker Gateway démo
  ├─ Sync Supabase
  ├─ Sync Google Drive
  └─ Connexion Discord

Supabase  ← logs / trades / décisions / heartbeats / commandes
Vercel    ← dashboard web
Discord   ← télécommande + alertes
Drive     ← backups / rapports
GitHub    ← code officiel à jour
```

### Principe

Gold Sniper ne dépend plus du PC local ni du courant en RDC. Le moteur tourne sur une machine cloud disponible 24/7. Le PC local devient seulement un poste de consultation.

### Avantages

```text
- Moins exposé aux coupures de courant locales
- Fonctionnement 24/7 plus stable
- Meilleur pour une validation démo continue
- Dashboard et Discord restent accessibles même si le PC local est éteint
- Plus proche d’un vrai environnement de production
```

### Contraintes

```text
- Il faut trouver une offre gratuite ou très peu chère
- MT5 fonctionne mieux sur Windows
- Les offres cloud gratuites sont souvent Linux ou limitées
- Il faudra tester compatibilité MT5 + Python + watchdogs
- Il faudra sécuriser l’accès distant et les secrets
```

### Pistes à étudier

```text
- Oracle Cloud Always Free
- Autres free tiers cloud compatibles VM longue durée
- VM Windows gratuite ou crédit cloud temporaire
- Linux + Wine pour MT5 si Windows gratuit indisponible
```

### Protections nécessaires

```text
- Même garde-fous que le PC local
- Compte MT5 démo obligatoire
- Login MT5 démo whitelisté
- Interdiction technique des comptes réels
- Heartbeat Supabase
- Alertes Discord
- Watchdog process
- Watchdog MT5
- Auto-restart après reboot VM
- Backup Drive
- Secrets stockés proprement
```

### Usage recommandé

Cette architecture devient prioritaire si une solution cloud gratuite fiable est trouvée. Si Opus identifie un bon plan VPS ou VM gratuite compatible avec MT5 et Gold Sniper, il peut le proposer comme architecture finale.

---

## Décision attendue d’Opus

Opus peut lire le dépôt actuel, comparer avec l’architecture locale réelle, puis trancher la meilleure voie de déploiement.

Son analyse devra surtout vérifier comment connecter proprement :

```text
- le moteur Gold Sniper
- le dashboard Vercel
- la base Supabase
- Discord comme télécommande
- Google Drive comme backup
- les watchdogs
- les API externes
- MetaTrader 5 démo
- le Broker Gateway
- les garde-fous anti-compte réel
```

L’objectif final est de choisir entre :

```text
Architecture 1 : PC local gratuit, mais exposé aux coupures
Architecture 2 : VPS / cloud gratuit, plus stable si une bonne option existe
```
