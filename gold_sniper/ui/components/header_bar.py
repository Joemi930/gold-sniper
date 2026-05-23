# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — HEADER BAR
# ═══════════════════════════════════════════════════════════════════════════════
# Barre supérieure pleine largeur avec :
#   - Logo + titre animé (gradient text via canvas)
#   - Infos compte : equity, balance, PnL journalier
#   - Session en cours (LONDON / NY / OFF)
#   - Heure UTC live
#   - Mode (LIVE / PAPER)
#   - Kill Switch
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import customtkinter as ctk
from datetime import datetime, timezone
from ui import theme


class HeaderBar(ctk.CTkFrame):
    def __init__(self, parent, blackboard, stop_event, **kwargs):
        super().__init__(
            parent,
            fg_color=theme.BG_HEADER,
            corner_radius=0,
            border_width=0,
            height=theme.HEADER_HEIGHT,
            **kwargs
        )
        self.blackboard = blackboard
        self.stop_event = stop_event
        self._kill_confirmed = False

        self.pack_propagate(False)
        self.grid_propagate(False)

        # ── Layout interne : 3 zones (left | center | right) ─────────────────
        self.columnconfigure(0, weight=0)   # Logo
        self.columnconfigure(1, weight=1)   # Centre (vide ou métriques)
        self.columnconfigure(2, weight=0)   # Métriques compte
        self.columnconfigure(3, weight=0)   # Session + heure
        self.columnconfigure(4, weight=0)   # Kill Switch

        # ── LOGO ─────────────────────────────────────────────────────────────
        logo_frame = ctk.CTkFrame(self, fg_color="transparent")
        logo_frame.grid(row=0, column=0, padx=(16, 30), pady=0, sticky="ns")

        self._gold_canvas = tk.Canvas(
            logo_frame, width=220, height=theme.HEADER_HEIGHT,
            bg=theme.BG_HEADER, highlightthickness=0
        )
        self._gold_canvas.pack()
        self._logo_phase = 0
        self._draw_logo()

        # ── MODE BADGE ───────────────────────────────────────────────────────
        self._mode_label = ctk.CTkLabel(
            self, text="● PAPER",
            font=("Consolas", 11, "bold"),
            text_color="#3cff8f",
            fg_color="#0a2e1c", corner_radius=4
        )
        self._mode_label.grid(row=0, column=1, padx=4, sticky="w")

        # ── MÉTRIQUES COMPTE ─────────────────────────────────────────────────
        metrics_frame = ctk.CTkFrame(self, fg_color="transparent")
        metrics_frame.grid(row=0, column=2, padx=20, pady=6, sticky="ns")

        self._equity_lbl   = self._make_metric(metrics_frame, "EQUITY", "$0.00", 0)
        self._balance_lbl  = self._make_metric(metrics_frame, "BALANCE", "$0.00", 1)
        self._pnl_lbl      = self._make_metric(metrics_frame, "PNL JOUR", "+$0.00", 2, pnl=True)

        # ── SESSION + HEURE ───────────────────────────────────────────────────
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=3, padx=12, pady=6, sticky="ns")

        self._session_badge = ctk.CTkLabel(
            right_frame, text="OFF HOURS",
            font=("Consolas", 11, "bold"),
            text_color=theme.TEXT_MUTED,
            fg_color=theme.BG_CARD, corner_radius=4,
            width=120, padx=8
        )
        self._session_badge.pack(pady=(4, 2))

        self._clock_lbl = ctk.CTkLabel(
            right_frame, text="00:00:00 UTC",
            font=("Consolas", 11),
            text_color=theme.TEXT_SECONDARY
        )
        self._clock_lbl.pack()

        # ── KILL SWITCH ───────────────────────────────────────────────────────
        self._kill_btn = ctk.CTkButton(
            self,
            text="⏹ KILL",
            font=("Consolas", 12, "bold"),
            fg_color="#2a0a0f",
            hover_color="#5c0018",
            text_color=theme.RED,
            border_color=theme.RED,
            border_width=1,
            corner_radius=6,
            width=80, height=34,
            command=self._on_kill
        )
        self._kill_btn.grid(row=0, column=4, padx=(8, 16))

        # Séparateur bas
        sep = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep.place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

        # Démarrer animations
        self._animate_logo()
        self._update_clock()

    # ─────────────────────────────────────────────────────────────────────────

    def _make_metric(self, parent, label, value, col, pnl=False):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, padx=14)

        ctk.CTkLabel(
            f, text=label, font=("Consolas", 8),
            text_color=theme.TEXT_MUTED
        ).pack()

        v = ctk.CTkLabel(
            f, text=value,
            font=("Consolas", 13, "bold"),
            text_color=theme.NEON_GREEN if pnl else theme.TEXT_PRIMARY
        )
        v.pack()
        return v

    def _draw_logo(self):
        c = self._gold_canvas
        c.delete("all")
        h = theme.HEADER_HEIGHT

        # Gradient text effect via multiple overlaid text items
        pulse = abs((self._logo_phase % 40) - 20) / 20  # 0..1..0
        brightness = int(200 + 55 * pulse)
        b2 = min(255, int(180 + 75 * pulse))
        gold_current = f"#{brightness:02x}{int(brightness * 0.78):02x}{0:02x}"

        c.create_text(
            110, h // 2 - 2,
            text="◆ GOLD SNIPER V2",
            font=("Consolas", 16, "bold"),
            fill=gold_current, anchor="center"
        )
        c.create_text(
            110, h // 2 + 14,
            text="INSTITUTIONAL INTELLIGENCE",
            font=("Consolas", 7),
            fill=theme.TEXT_MUTED, anchor="center"
        )

    def _animate_logo(self):
        self._logo_phase += 1
        self._draw_logo()
        self.after(50, self._animate_logo)

    def _update_clock(self):
        now = datetime.now(timezone.utc)
        self._clock_lbl.configure(text=now.strftime("%H:%M:%S UTC"))
        self.after(1000, self._update_clock)

    def _on_kill(self):
        if not self._kill_confirmed:
            self._kill_btn.configure(text="CONFIRM?", fg_color="#5c0018")
            self._kill_confirmed = True
            self.after(3000, self._reset_kill_btn)
        else:
            self._kill_btn.configure(text="🛑 KILLED", state="disabled")
            self.stop_event.set()

    def _reset_kill_btn(self):
        self._kill_confirmed = False
        self._kill_btn.configure(text="⏹ KILL", fg_color="#2a0a0f")

    # ─────────────────────────────────────────────────────────────────────────
    # MISE À JOUR DEPUIS LE BLACKBOARD
    # ─────────────────────────────────────────────────────────────────────────

    def update(self):
        """Appelé par la boucle principale toutes les 250ms."""
        try:
            # Infos compte
            acc = self.blackboard.read_sync("meta.account_info") or {}
            equity  = acc.get("equity", 0.0)
            balance = acc.get("balance", 0.0)
            self._equity_lbl.configure(text=f"${equity:,.2f}")
            self._balance_lbl.configure(text=f"${balance:,.2f}")

            # PnL journalier
            daily = self.blackboard._data.get("daily_stats", {})
            realized  = daily.get("realized_pnl", 0.0)
            floating  = daily.get("floating_pnl", 0.0)
            pnl_total = realized + floating
            pnl_str   = f"{pnl_total:+,.2f}"
            pnl_color = theme.NEON_GREEN if pnl_total >= 0 else theme.RED
            self._pnl_lbl.configure(text=f"${pnl_str}", text_color=pnl_color)

            # Mode
            live = self.blackboard.read_sync("meta.live_mode")
            if live:
                self._mode_label.configure(
                    text="● LIVE", text_color=theme.RED,
                    fg_color="#2e0a0a"
                )
            else:
                self._mode_label.configure(
                    text="● PAPER", text_color="#3cff8f",
                    fg_color="#0a2e1c"
                )

            # Session
            agent7 = self.blackboard.read_sync("agents.agent_7_sessions") or {}
            sess = agent7.get("current_session", "OFF_HOURS")
            in_kz = agent7.get("in_killzone", False)

            if in_kz:
                kz_name = agent7.get("killzone_name", sess)
                self._session_badge.configure(
                    text=f"⚡ {kz_name}",
                    text_color=theme.NEON_GREEN,
                    fg_color="#0a2e1c"
                )
            else:
                self._session_badge.configure(
                    text=sess,
                    text_color=theme.TEXT_MUTED,
                    fg_color=theme.BG_CARD
                )
        except Exception:
            pass
