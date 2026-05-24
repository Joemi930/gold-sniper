# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — AGENT CARD
# ═══════════════════════════════════════════════════════════════════════════════
# Carte individuelle d'un agent avec :
#   - Anneau de score circulaire animé (arc Canvas)
#   - Nom + poids + icône
#   - Statut textuel (BULLISH, SLEEPING, BLACKOUT...)
#   - Détail clé sur 1 ligne
#   - Mini-sparkline des 20 derniers scores
#   - Timestamp "il y a Xs" avec pulsation si > 5s
#   - Bordure or clignotante si HARD FILTER (A1/A2)
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import customtkinter as ctk
import math
import time
from ui import theme


class AgentCard(ctk.CTkFrame):
    """Carte d'un agent unique avec anneau animé et sparkline."""

    SPARKLINE_LEN = 20  # Nombre de points dans l'historique

    def __init__(self, parent, agent_id: str, hard_filter_pass: bool = False, **kwargs):
        super().__init__(
            parent,
            fg_color=theme.BG_CARD,
            corner_radius=theme.CARD_RADIUS,
            border_width=1,
            border_color=theme.BG_BORDER,
            **kwargs
        )

        self.agent_id     = agent_id
        self.hard_filter_pass = hard_filter_pass
        self._color       = theme.AGENT_COLORS.get(agent_id, theme.ELECTRIC_BLUE)
        self._label       = theme.AGENT_LABELS.get(agent_id, agent_id.upper())
        self._icon        = theme.AGENT_ICONS.get(agent_id, "●")
        self._weight      = theme.AGENT_WEIGHTS.get(agent_id, 0)

        self._score           = 0.0
        self._score_history   = [0.0] * self.SPARKLINE_LEN
        self._last_update_ts  = 0.0
        self._ring_phase      = 0       # Pour animation pulsation
        self._blink_phase     = 0
        self._blink_on        = False

        self._build_ui()
        self._animate()

    # ─────────────────────────────────────────────────────────────────────────
    # CONSTRUCTION UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.pack_propagate(True)

        # ── Ligne 1 : Anneau + Info droite ────────────────────────────────
        top_frame = tk.Frame(self, bg=theme.BG_CARD)
        top_frame.pack(fill="x", padx=8, pady=(8, 4))

        # Anneau de score (Canvas 80×80)
        self._ring_size = 76
        self._ring_canvas = tk.Canvas(
            top_frame,
            width=self._ring_size, height=self._ring_size,
            bg=theme.BG_CARD, highlightthickness=0
        )
        self._ring_canvas.pack(side="left", padx=(0, 8))

        # Info à droite de l'anneau
        info_frame = tk.Frame(top_frame, bg=theme.BG_CARD)
        info_frame.pack(side="left", fill="both", expand=True)

        # Icône + nom
        name_frame = tk.Frame(info_frame, bg=theme.BG_CARD)
        name_frame.pack(fill="x")

        self._icon_lbl = tk.Label(
            name_frame, text=self._icon,
            font=("Consolas", 14),
            bg=theme.BG_CARD, fg=self._color
        )
        self._icon_lbl.pack(side="left")

        self._name_lbl = tk.Label(
            name_frame, text=self._label,
            font=("Consolas", 10, "bold"),
            bg=theme.BG_CARD, fg=theme.TEXT_PRIMARY, anchor="w"
        )
        self._name_lbl.pack(side="left", padx=(4, 0))

        # Poids (badge)
        if self._weight > 0:
            weight_lbl = tk.Label(
                name_frame, text=f"{self._weight}pts",
                font=("Consolas", 8),
                bg=theme.BG_CARD, fg=self._color
            )
            weight_lbl.pack(side="right")
        else:
            gate_lbl = tk.Label(
                name_frame, text="GATE",
                font=("Consolas", 8, "bold"),
                bg=theme.BG_CARD, fg=theme.ORANGE
            )
            gate_lbl.pack(side="right")

        # Statut principal
        self._status_lbl = tk.Label(
            info_frame, text="INITIALIZING...",
            font=("Consolas", 10, "bold"),
            bg=theme.BG_CARD, fg=theme.TEXT_MUTED, anchor="w"
        )
        self._status_lbl.pack(fill="x", pady=(4, 0))

        # Détail clé
        self._detail_lbl = tk.Label(
            info_frame, text="—",
            font=("Consolas", 8),
            bg=theme.BG_CARD, fg=theme.TEXT_SECONDARY, anchor="w",
            wraplength=130
        )
        self._detail_lbl.pack(fill="x")

        # Timestamp
        self._ts_lbl = tk.Label(
            info_frame, text="en attente...",
            font=("Consolas", 7),
            bg=theme.BG_CARD, fg=theme.TEXT_MUTED, anchor="w"
        )
        self._ts_lbl.pack(fill="x")

        # ── Sparkline ─────────────────────────────────────────────────────
        spark_h = 28
        self._spark_canvas = tk.Canvas(
            self, width=100, height=spark_h,
            bg=theme.BG_CARD, highlightthickness=0
        )
        self._spark_canvas.pack(fill="x", padx=8, pady=(0, 8))

        # Dessiner l'anneau initial
        self._draw_ring()
        self._draw_sparkline()

    # ─────────────────────────────────────────────────────────────────────────
    # DESSIN DE L'ANNEAU
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_ring(self):
        c = self._ring_canvas
        c.delete("all")
        s = self._ring_size
        pad = 6
        x0, y0 = pad, pad
        x1, y1 = s - pad, s - pad

        # Fond de l'anneau (gris très sombre)
        c.create_arc(
            x0, y0, x1, y1,
            start=90, extent=360,
            style=tk.ARC, width=6,
            outline="#1a2040"
        )

        # Arc du score
        score_extent = (self._score / 100.0) * 360
        ring_color = theme.score_to_hex_ring(self._score)

        # Pulsation si score élevé
        if self._score >= 90:
            pulse = abs(math.sin(self._ring_phase * 0.12)) * 0.4 + 0.8
            r = int(int(ring_color[1:3], 16) * pulse)
            g = int(int(ring_color[3:5], 16) * pulse)
            b = int(int(ring_color[5:7], 16) * pulse)
            r, g, b = min(255, r), min(255, g), min(255, b)
            ring_color = f"#{r:02x}{g:02x}{b:02x}"

        if score_extent > 0:
            c.create_arc(
                x0, y0, x1, y1,
                start=90, extent=-score_extent,
                style=tk.ARC, width=6,
                outline=ring_color
            )

        # Score au centre
        score_text = f"{int(self._score)}"
        c.create_text(
            s // 2, s // 2 - 5,
            text=score_text,
            font=("Consolas", 16, "bold"),
            fill=ring_color if self._score > 0 else theme.TEXT_MUTED
        )
        c.create_text(
            s // 2, s // 2 + 10,
            text="/100",
            font=("Consolas", 7),
            fill=theme.TEXT_MUTED
        )

    # ─────────────────────────────────────────────────────────────────────────
    # DESSIN DE LA SPARKLINE
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_sparkline(self):
        c = self._spark_canvas
        c.delete("all")
        w = c.winfo_width() or 200
        h = 28
        n = len(self._score_history)
        if n < 2:
            return

        # Ligne de fond
        c.create_line(0, h - 1, w, h - 1, fill=theme.BG_BORDER, width=1)

        points = []
        for i, v in enumerate(self._score_history):
            x = int(i * w / (n - 1))
            y = int((1.0 - v / 100.0) * (h - 4)) + 2
            points.extend([x, y])

        if len(points) >= 4:
            color = theme.score_color(self._score_history[-1])
            c.create_line(points, fill=color, width=1, smooth=True)
            # Dot final
            lx, ly = points[-2], points[-1]
            c.create_oval(lx - 2, ly - 2, lx + 2, ly + 2, fill=color, outline="")

    # ─────────────────────────────────────────────────────────────────────────
    # ANIMATION
    # ─────────────────────────────────────────────────────────────────────────

    def _animate(self):
        self._ring_phase += 1
        self._draw_ring()

        # Mise à jour du timestamp
        if self._last_update_ts > 0:
            elapsed = time.time() - self._last_update_ts
            if elapsed < 5:
                ts_text = f"il y a {elapsed:.0f}s"
                ts_color = theme.TEXT_MUTED
            else:
                ts_text = f"⚠ il y a {elapsed:.0f}s"
                ts_color = theme.ORANGE
            try:
                self._ts_lbl.configure(fg=ts_color)
                self._ts_lbl["text"] = ts_text
            except Exception:
                pass

        # Clignotement bordure HARD FILTER si score = 0
        if self.hard_filter_pass and self._score == 0:
            self._blink_phase += 1
            if self._blink_phase % 8 == 0:
                self._blink_on = not self._blink_on
                bc = theme.GOLD if self._blink_on else theme.BG_BORDER
                try:
                    self.configure(border_color=bc)
                except Exception:
                    pass
        else:
            bc = self._color if self.hard_filter_pass else theme.BG_BORDER
            try:
                self.configure(border_color=bc)
            except Exception:
                pass

        self.after(80, self._animate)

    # ─────────────────────────────────────────────────────────────────────────
    # API PUBLIQUE — mise à jour depuis le Blackboard
    # ─────────────────────────────────────────────────────────────────────────

    def set_score(self, score: float):
        self._score = max(0.0, min(100.0, score))
        self._score_history.append(self._score)
        if len(self._score_history) > self.SPARKLINE_LEN:
            self._score_history.pop(0)
        self._last_update_ts = time.time()
        self._draw_sparkline()

    def set_status(self, status_text: str, color: str = None):
        c = color or theme.score_color(self._score)
        try:
            self._status_lbl.configure(text=status_text, fg=c)
        except Exception:
            pass

    def set_detail(self, detail: str):
        try:
            self._detail_lbl.configure(text=detail or "—")
        except Exception:
            pass
