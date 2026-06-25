# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — LOG FEED (Pleine largeur, bas de l'écran)
# ═══════════════════════════════════════════════════════════════════════════════
# Feed de logs temps réel avec :
#   - Couleurs par agent (chaque agent = couleur dédiée)
#   - Icônes par type (debug, analyse, signal, trade, erreur)
#   - Filtres cliquables (Tous / Trades / Signaux / Erreurs)
#   - Auto-scroll (pause au survol)
#   - Limite de 500 lignes (perf)
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import customtkinter as ctk
from queue import Queue, Empty
from ui import theme

MAX_LINES = 500

# Mapping agent_name → couleur
AGENT_LOG_COLORS = {
    "AGENT_1": theme.AGENT_COLORS["agent_1"],
    "AGENT_2": theme.AGENT_COLORS["agent_2"],
    "AGENT_3": theme.AGENT_COLORS["agent_3"],
    "AGENT_4": theme.AGENT_COLORS["agent_4"],
    "AGENT_5": theme.AGENT_COLORS["agent_5"],
    "AGENT_6": theme.AGENT_COLORS["agent_6"],
    "AGENT_7": theme.AGENT_COLORS["agent_7"],
    "ORCHESTR": theme.GOLD,
    "TRADE_MA": theme.NEON_GREEN,
    "RECOVERY": theme.CYAN,
    "TELEGRAM": theme.PURPLE,
    "MT5_BRID": theme.ELECTRIC_BLUE,
    "BLACKBOA": theme.TEXT_SECONDARY,
    "ENGINE":   theme.TEXT_SECONDARY,
    "MAIN":     theme.TEXT_SECONDARY,
    "LOGGER":   theme.TEXT_MUTED,
}

# Filtres actifs : clé → lambda sur le message
FILTER_LABELS = ["ALL", "TRADES", "SIGNALS", "ERRORS", "AGENTS"]
FILTER_FUNCS  = {
    "ALL":     lambda m: True,
    "TRADES":  lambda m: any(k in m for k in ["TRADE", "ORDRE", "TICKET", "CLÔTUR", "PARTIAL", "BREAK"]),
    "SIGNALS": lambda m: any(k in m for k in ["SIGNAL", "SCORE", "REJETÉ", "VALIDÉ", "EXECUTE"]),
    "ERRORS":  lambda m: any(k in m for k in ["ERROR", "ERREUR", "FAIL", "CRITICAL", "KILLED"]),
    "AGENTS":  lambda m: any(k in m for k in ["AGENT_", "MÉTÉO", "CARTO", "LIQUID", "FIBON", "MICRO", "SENTI", "CHRON"]),
}


class LogFeed(ctk.CTkFrame):
    def __init__(self, parent, log_queue: Queue, **kwargs):
        super().__init__(
            parent,
            fg_color=theme.BG_PANEL,
            corner_radius=0,
            **kwargs
        )
        self._queue         = log_queue
        self._paused        = False
        self._active_filter = "ALL"
        self._all_messages  = []  # Buffer complet pour re-filtrage
        self._line_count    = 0

        self._build()
        self._drain_queue()

    def _build(self):
        # ── Barre de titre + filtres ─────────────────────────────────────
        top = tk.Frame(self, bg=theme.BG_PANEL)
        top.pack(fill="x", side="top")

        tk.Label(
            top, text="📟 LIVE LOG FEED",
            font=("Consolas", 9, "bold"),
            bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY
        ).pack(side="left", padx=10, pady=4)

        # Filtres
        self._filter_btns = {}
        filter_frame = tk.Frame(top, bg=theme.BG_PANEL)
        filter_frame.pack(side="left", padx=8)

        for label in FILTER_LABELS:
            btn = tk.Label(
                filter_frame,
                text=label,
                font=("Consolas", 8),
                bg=theme.BG_BORDER,
                fg=theme.TEXT_MUTED,
                padx=8, pady=2,
                cursor="hand2"
            )
            btn.pack(side="left", padx=2)
            btn.bind("<Button-1>", lambda e, l=label: self._set_filter(l))
            self._filter_btns[label] = btn

        # NOTE: _set_filter called AFTER _txt is built below

        # Indicateur pause
        self._pause_lbl = tk.Label(
            top, text="",
            font=("Consolas", 8),
            bg=theme.BG_PANEL, fg=theme.ORANGE
        )
        self._pause_lbl.pack(side="right", padx=10)

        # Separateur
        tk.Frame(self, bg=theme.BG_BORDER, height=1).pack(fill="x")

        # ── Zone de texte ─────────────────────────────────────────────────
        txt_frame = tk.Frame(self, bg=theme.BG_DEEP)
        txt_frame.pack(fill="both", expand=True)

        self._txt = tk.Text(
            txt_frame,
            bg=theme.BG_DEEP,
            fg=theme.TEXT_PRIMARY,
            font=("Consolas", 9),
            wrap=tk.NONE,
            state=tk.DISABLED,
            bd=0,
            highlightthickness=0,
            selectbackground="#1a2545",
            insertbackground=theme.GOLD,
            cursor="arrow"
        )
        self._txt.pack(side="left", fill="both", expand=True)

        # Scrollbar Y
        sb_y = ctk.CTkScrollbar(txt_frame, command=self._txt.yview,
                                 fg_color=theme.BG_PANEL, button_color=theme.BG_BORDER)
        sb_y.pack(side="right", fill="y")
        self._txt.configure(yscrollcommand=sb_y.set)

        # Tags couleur
        self._define_tags()

        # Pause au survol
        self._txt.bind("<Enter>", lambda e: self._set_pause(True))
        self._txt.bind("<Leave>", lambda e: self._set_pause(False))

        # Activer le filtre par défaut APRES que _txt est construit
        self._set_filter("ALL")

    def _define_tags(self):
        """Définit les tags Tkinter pour les couleurs."""
        for name, color in AGENT_LOG_COLORS.items():
            self._txt.tag_config(f"agent_{name}", foreground=color)

        self._txt.tag_config("ts",      foreground=theme.TEXT_MUTED)
        self._txt.tag_config("info",    foreground=theme.TEXT_SECONDARY)
        self._txt.tag_config("trade",   foreground=theme.NEON_GREEN, font=("Consolas", 9, "bold"))
        self._txt.tag_config("error",   foreground=theme.RED, font=("Consolas", 9, "bold"))
        self._txt.tag_config("warning", foreground=theme.ORANGE)
        self._txt.tag_config("signal",  foreground=theme.GOLD)
        self._txt.tag_config("debug",   foreground=theme.TEXT_MUTED)

    def _set_filter(self, label: str):
        self._active_filter = label
        for lbl, btn in self._filter_btns.items():
            if lbl == label:
                btn.configure(bg=theme.ELECTRIC_BLUE, fg=theme.TEXT_PRIMARY)
            else:
                btn.configure(bg=theme.BG_BORDER, fg=theme.TEXT_MUTED)

        # Re-afficher les messages filtrés
        self._rerender()

    def _rerender(self):
        """Re-affiche les messages selon le filtre actif."""
        fn = FILTER_FUNCS.get(self._active_filter, lambda m: True)
        filtered = [m for m in self._all_messages if fn(m)]

        self._txt.configure(state=tk.NORMAL)
        self._txt.delete("1.0", tk.END)
        for msg in filtered[-MAX_LINES:]:
            self._insert_line(msg)
        self._txt.configure(state=tk.DISABLED)
        if not self._paused:
            self._txt.see(tk.END)

    def _insert_line(self, msg: str):
        """Insère une ligne colorée dans le widget Text."""
        # Détecter le tag agent
        agent_tag = "info"
        color_tag = "info"

        upper = msg.upper()
        for key in AGENT_LOG_COLORS:
            if key in upper:
                color_tag = f"agent_{key}"
                break

        # Tag de type
        if any(k in upper for k in ["ERROR", "ERREUR", "FAIL", "CRITICAL"]):
            type_tag = "error"
        elif any(k in upper for k in ["TRADE", "ORDRE", "TICKET", "PARTIAL", "BREAKEVEN"]):
            type_tag = "trade"
        elif any(k in upper for k in ["SIGNAL", "SCORE >=", "VALIDÉ", "EXECUTE"]):
            type_tag = "signal"
        elif any(k in upper for k in ["WARNING", "ATTENTION", "⚠"]):
            type_tag = "warning"
        elif "DEBUG" in upper or "🔍" in msg:
            type_tag = "debug"
        else:
            type_tag = "info"

        # Séparation timestamp | corps
        parts = msg.split("]", 1)
        if len(parts) == 2:
            ts   = parts[0] + "]"
            body = parts[1]
            self._txt.insert(tk.END, ts, "ts")
            self._txt.insert(tk.END, body + "\n", (type_tag, color_tag))
        else:
            self._txt.insert(tk.END, msg + "\n", (type_tag, color_tag))

    def _drain_queue(self):
        """Draine la queue de logs toutes les 100ms."""
        if not self._paused:
            fn = FILTER_FUNCS.get(self._active_filter, lambda m: True)
            new_lines = []

            try:
                for _ in range(50):  # Max 50 messages par cycle
                    msg = self._queue.get_nowait()
                    self._all_messages.append(msg)
                    if fn(msg):
                        new_lines.append(msg)
            except Empty:
                pass

            if new_lines:
                self._txt.configure(state=tk.NORMAL)
                # Suppression des lignes excédentaires
                total_lines = int(self._txt.index(tk.END).split(".")[0])
                if total_lines > MAX_LINES:
                    self._txt.delete("1.0", f"{total_lines - MAX_LINES}.0")

                for msg in new_lines:
                    self._insert_line(msg)

                self._txt.configure(state=tk.DISABLED)
                self._txt.see(tk.END)

            # Limiter le buffer en mémoire
            if len(self._all_messages) > MAX_LINES * 2:
                self._all_messages = self._all_messages[-MAX_LINES:]

        self.after(100, self._drain_queue)

    def _set_pause(self, paused: bool):
        self._paused = paused
        self._pause_lbl.configure(text="⏸ PAUSE" if paused else "")
