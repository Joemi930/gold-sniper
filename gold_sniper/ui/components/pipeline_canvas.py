# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — PIPELINE CANVAS (Réseau Neuronal Animé)
# ═══════════════════════════════════════════════════════════════════════════════
# Pièce maîtresse de l'interface. Visualise le flux de décision en temps réel :
#
#   - 7 nœuds hexagonaux + 1 nœud Orchestrateur central
#   - Connexions entre agents selon l'architecture V2
#   - Particules lumineuses qui voyagent le long des connexions actives
#   - Nœud Orchestrateur pulsant quand score >= 90
#   - Grid de fond cyberpunk (lignes fines)
#   - Légende intégrée
#
# Connexions du pipeline :
#   A6 → ORCH (gate)     A7 → ORCH (gate)
#   A1 → A2 (séquentiel)  A2 → A3, A4, A5 (POI event)
#   A1, A2, A3, A4, A5 → ORCH
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import math
import time
import random
from ui import theme


# ─── Positions relatives des nœuds (0..1 dans chaque dimension) ───────────────
# Elles seront mises à l'échelle selon la taille réelle du canvas

NODE_LAYOUT = {
    # Ligne 0 (GATES — en haut)
    "agent_6": (0.20, 0.14),
    "agent_7": (0.80, 0.14),

    # Ligne 1 (HARD FILTERS — milieu haut)
    "agent_1": (0.28, 0.38),
    "agent_2": (0.72, 0.38),

    # Ligne 2 (CONFIRMATIONS — milieu bas)
    "agent_3": (0.18, 0.65),
    "agent_4": (0.50, 0.65),
    "agent_5": (0.82, 0.65),

    # Orchestrateur — centre bas
    "orchestrator": (0.50, 0.87),
}

# Connexions : (source, dest)
CONNECTIONS = [
    ("agent_6",  "orchestrator"),
    ("agent_7",  "orchestrator"),
    ("agent_1",  "agent_2"),
    ("agent_2",  "agent_3"),
    ("agent_2",  "agent_4"),
    ("agent_2",  "agent_5"),
    ("agent_1",  "orchestrator"),
    ("agent_2",  "orchestrator"),
    ("agent_3",  "orchestrator"),
    ("agent_4",  "orchestrator"),
    ("agent_5",  "orchestrator"),
]

NODE_R      = 32   # Rayon des nœuds standard (en px, recalculé à l'init)
ORCH_R      = 44   # Rayon nœud orchestrateur


class Particle:
    """Particule lumineuse qui voyage le long d'une connexion."""
    def __init__(self, x0, y0, x1, y1, color, speed=3.5):
        self.x0, self.y0 = x0, y0
        self.x1, self.y1 = x1, y1
        self.color = color
        self.speed = speed
        self.t = 0.0   # 0..1 position along the connection
        self.dt = speed / max(math.dist((x0, y0), (x1, y1)), 1)
        self.alive = True
        # Taille aléatoire pour variété
        self.r = random.uniform(2.5, 4.5)

    def step(self):
        self.t += self.dt
        if self.t >= 1.0:
            self.t = 1.0
            self.alive = False

    @property
    def pos(self):
        x = self.x0 + (self.x1 - self.x0) * self.t
        y = self.y0 + (self.y1 - self.y0) * self.t
        return x, y


class PipelineCanvas(tk.Canvas):
    """Canvas principal du réseau neuronal animé."""

    def __init__(self, parent, blackboard, **kwargs):
        super().__init__(
            parent,
            bg=theme.BG_DEEP,
            highlightthickness=0,
            **kwargs
        )
        self.blackboard = blackboard
        self._particles: list[Particle] = []
        self._scores: dict[str, float] = {k: 0.0 for k in NODE_LAYOUT}
        self._statuses: dict[str, str] = {k: "" for k in NODE_LAYOUT}
        self._orch_score = 0.0
        self._orch_phase = 0
        self._frame = 0
        self._last_particle_spawn = {c: 0.0 for c in CONNECTIONS}
        self._active_connections = set()  # Connexions avec flux actif

        self.bind("<Configure>", self._on_resize)
        self._cw = 600
        self._ch = 500
        self._scale = 1.0
        self._node_px = {}  # Positions en pixels
        self._started = False

        self._compute_positions()
        # Démarrer la boucle APRES que le widget soit placé et rendu
        self.after_idle(self._start_loop)

    # ─────────────────────────────────────────────────────────────────────────

    def _on_resize(self, event):
        self._cw = max(event.width, 100)
        self._ch = max(event.height, 100)
        self._compute_positions()

    def _compute_positions(self):
        """Recalcule les positions pixel des nœuds selon la taille du canvas."""
        self._node_px = {
            nid: (int(rx * self._cw), int(ry * self._ch))
            for nid, (rx, ry) in NODE_LAYOUT.items()
        }
        self._node_r  = max(22, int(min(self._cw, self._ch) * 0.065))
        self._orch_r  = max(30, int(min(self._cw, self._ch) * 0.088))

    # ─────────────────────────────────────────────────────────────────────────
    # BOUCLE PRINCIPALE
    # ─────────────────────────────────────────────────────────────────────────

    def _start_loop(self):
        """Démarre la boucle après initialisation complète."""
        if not self._started:
            self._started = True
            self._loop()

    def _schedule_loop(self):
        self._loop()

    def _loop(self):
        # Sécurité : ne pas dessiner si le widget est détruit
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        # Mettre à jour les dimensions réelles
        w = self.winfo_width()
        h = self.winfo_height()
        if w > 10 and h > 10:
            self._cw = w
            self._ch = h
            self._compute_positions()

        self._frame += 1
        self._orch_phase += 1

        self._update_from_blackboard()
        self._spawn_particles()
        self._step_particles()
        self._redraw()

        self.after(50, self._loop)   # 20 FPS

    def _update_from_blackboard(self):
        """Lit les scores du Blackboard."""
        try:
            results = self.blackboard._data.get("agent_results", {})
            for aid in ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]:
                r = results.get(aid)
                if r is not None:
                    self._scores[aid] = float(r.score)
                    self._statuses[aid] = str(r.reason or "")

            orch = self.blackboard.read_sync("orchestrator") or {}
            dec = orch.get("last_decision") or {}
            self._orch_score = float(dec.get("score", 0))

            # Connexions actives : celles entre agents dont les deux scores > 0
            self._active_connections = set()
            for (src, dst) in CONNECTIONS:
                s_score = self._scores.get(src, 0)
                d_score = self._scores.get(dst, 0) if dst != "orchestrator" else self._orch_score
                if s_score > 0:
                    self._active_connections.add((src, dst))
        except Exception:
            pass

    def _spawn_particles(self):
        """Crée des particules sur les connexions actives."""
        now = time.time()
        for conn in self._active_connections:
            src, dst = conn
            interval = 0.6  # secondes entre particules

            if now - self._last_particle_spawn.get(conn, 0) < interval:
                continue

            x0, y0 = self._node_px.get(src, (0, 0))
            x1, y1 = self._node_px.get(dst, (0, 0))
            color = theme.AGENT_COLORS.get(src, theme.ELECTRIC_BLUE)

            self._particles.append(Particle(x0, y0, x1, y1, color, speed=4.0))
            self._last_particle_spawn[conn] = now

    def _step_particles(self):
        for p in self._particles:
            p.step()
        self._particles = [p for p in self._particles if p.alive]

    # ─────────────────────────────────────────────────────────────────────────
    # RENDU
    # ─────────────────────────────────────────────────────────────────────────

    def _redraw(self):
        self.delete("all")
        self._draw_grid()
        self._draw_connections()
        self._draw_particles()
        self._draw_nodes()
        self._draw_legend()

    def _draw_grid(self):
        """Grille cyberpunk en fond."""
        step = max(30, int(min(self._cw, self._ch) / 16))
        for x in range(0, self._cw, step):
            self.create_line(x, 0, x, self._ch, fill="#0d1630", width=1)
        for y in range(0, self._ch, step):
            self.create_line(0, y, self._cw, y, fill="#0d1630", width=1)

    def _draw_connections(self):
        """Connexions entre nœuds."""
        for (src, dst) in CONNECTIONS:
            x0, y0 = self._node_px.get(src, (0, 0))
            x1, y1 = self._node_px.get(dst, (0, 0))
            is_active = (src, dst) in self._active_connections
            color  = theme.ELECTRIC_BLUE if is_active else theme.BG_BORDER
            width  = 2 if is_active else 1

            # Ligne principale
            self.create_line(x0, y0, x1, y1, fill=color, width=width, dash=(6, 4) if not is_active else None)

            # Flèche au milieu
            if is_active:
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                angle  = math.atan2(y1 - y0, x1 - x0)
                self._draw_arrow_tip(mx, my, angle, color, size=5)

    def _draw_arrow_tip(self, x, y, angle, color, size=5):
        a1 = angle + math.radians(150)
        a2 = angle - math.radians(150)
        pts = [
            x, y,
            x + size * math.cos(a1), y + size * math.sin(a1),
            x + size * math.cos(a2), y + size * math.sin(a2),
        ]
        self.create_polygon(pts, fill=color, outline="")

    def _draw_particles(self):
        for p in self._particles:
            x, y = p.pos
            r = p.r
            # Glow effect : cercle externe dim + cercle interne bright
            # Tkinter ne supporte pas les couleurs hex 8 chars (#rrggbbaa)
            # On calcule une couleur dimm??e pour le halo
            glow_r = r * 2.5
            glow_color = self._dim_color(p.color, 0.35)
            self.create_oval(
                x - glow_r, y - glow_r, x + glow_r, y + glow_r,
                fill="", outline=glow_color, width=1
            )
            self.create_oval(
                x - r, y - r, x + r, y + r,
                fill=p.color, outline=""
            )

    @staticmethod
    def _dim_color(hex_color: str, factor: float) -> str:
        """Assombrit une couleur hex (#rrggbb) par un facteur 0..1."""
        try:
            c = hex_color.lstrip("#")
            if len(c) != 6:
                return hex_color
            r = int(int(c[0:2], 16) * factor)
            g = int(int(c[2:4], 16) * factor)
            b = int(int(c[4:6], 16) * factor)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _draw_nodes(self):
        """Dessine tous les nœuds."""
        for nid, (cx, cy) in self._node_px.items():
            if nid == "orchestrator":
                self._draw_orchestrator_node(cx, cy)
            else:
                self._draw_agent_node(nid, cx, cy)

    def _draw_agent_node(self, nid, cx, cy):
        score  = self._scores.get(nid, 0)
        color  = theme.AGENT_COLORS.get(nid, theme.ELECTRIC_BLUE)
        icon   = theme.AGENT_ICONS.get(nid, "●")
        label  = theme.AGENT_LABELS.get(nid, nid)
        r      = self._node_r
        weight = theme.AGENT_WEIGHTS.get(nid, 0)

        # Halo externe si actif
        if score > 0:
            halo_r = r + 8
            self.create_oval(
                cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r,
                fill="", outline=self._dim_color(color, 0.25), width=2
            )

        # Fond hexagonal (approximé par cercle)
        bg_color = "#0d1838" if score == 0 else "#0a1a2e"
        self.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=bg_color, outline=color,
            width=2 if score > 0 else 1
        )

        # Arc de score
        if score > 0:
            extent = (score / 100.0) * 359
            ring_color = theme.score_to_hex_ring(score)
            self.create_arc(
                cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3,
                start=90, extent=-extent,
                style=tk.ARC, width=3, outline=ring_color
            )

        # Icône
        self.create_text(
            cx, cy - 6,
            text=icon, font=("Consolas", int(r * 0.45)),
            fill=color if score > 0 else theme.TEXT_MUTED
        )

        # Score
        score_str = f"{int(score)}"
        self.create_text(
            cx, cy + 8,
            text=score_str, font=("Consolas", int(r * 0.30), "bold"),
            fill=theme.score_color(score) if score > 0 else theme.TEXT_MUTED
        )

        # Label sous le nœud
        font_size = max(7, int(r * 0.25))
        self.create_text(
            cx, cy + r + 8,
            text=label, font=("Consolas", font_size),
            fill=color if score > 0 else theme.TEXT_MUTED
        )

        # Badge GATE si pas de poids
        if weight == 0:
            self.create_text(
                cx, cy - r - 10,
                text="GATE", font=("Consolas", 7, "bold"),
                fill=theme.ORANGE
            )

    def _draw_orchestrator_node(self, cx, cy):
        r     = self._orch_r
        score = self._orch_score

        # Pulsation si score >= 90
        if score >= 90:
            phase  = self._orch_phase
            glow   = abs(math.sin(phase * 0.15)) * 0.5 + 0.5
            halo_r = r + int(15 * glow)
            color_pulse = self._dim_color(theme.NEON_GREEN, max(0.15, glow * 0.7))
            self.create_oval(
                cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r,
                fill="", outline=color_pulse, width=3
            )
            ring_color = theme.NEON_GREEN
        else:
            ring_color = theme.score_to_hex_ring(score)

        # Fond
        self.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill="#0a0d20", outline=theme.GOLD, width=2
        )

        # Arc de score
        if score > 0:
            extent = (score / 100.0) * 359
            self.create_arc(
                cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4,
                start=90, extent=-extent,
                style=tk.ARC, width=4, outline=ring_color
            )

        # Icône
        self.create_text(
            cx, cy - 10,
            text="🧠", font=("Consolas", int(r * 0.5)),
            fill=theme.GOLD
        )

        # Score en grand
        self.create_text(
            cx, cy + 8,
            text=f"{int(score)}/100",
            font=("Consolas", int(r * 0.35), "bold"),
            fill=ring_color if score > 0 else theme.TEXT_MUTED
        )

        # Label
        self.create_text(
            cx, cy + r + 10,
            text="CERVEAU V2",
            font=("Consolas", 9, "bold"),
            fill=theme.GOLD
        )

        # Seuil indicator
        self.create_text(
            cx, cy + r + 22,
            text=f"SEUIL: 90",
            font=("Consolas", 7),
            fill=theme.TEXT_MUTED
        )

    def _draw_legend(self):
        """Légende compacte en bas à gauche."""
        items = [
            ("─── Actif", theme.ELECTRIC_BLUE),
            ("--- Inactif", theme.BG_BORDER),
            ("● Particule", theme.NEON_GREEN),
        ]
        y = self._ch - 14
        x = 10
        for text, color in reversed(items):
            self.create_text(x, y, text=text, anchor="w",
                             font=("Consolas", 7), fill=color)
            y -= 14

    # ─────────────────────────────────────────────────────────────────────────
    # API publique
    # ─────────────────────────────────────────────────────────────────────────

    def force_particle(self, src: str, dst: str):
        """Force l'émission d'une particule sur une connexion spécifique."""
        x0, y0 = self._node_px.get(src, (0, 0))
        x1, y1 = self._node_px.get(dst, (0, 0))
        color = theme.AGENT_COLORS.get(src, theme.ELECTRIC_BLUE)
        self._particles.append(Particle(x0, y0, x1, y1, color, speed=6.0))
