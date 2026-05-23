# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — ACCOUNT PANEL (Compte + Trade Actif)
# ═══════════════════════════════════════════════════════════════════════════════
# Panneau droit avec :
#   - Infos du compte (nom, serveur, solde, equity)
#   - Barre de drawdown journalier
#   - Compteur trades du jour / max
#   - Étoiles de qualité de l'Orchestrateur
#   - Carte trade actif (PnL, SL/TP progress bar, status, durée)
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import customtkinter as ctk
from datetime import datetime, timezone
from ui import theme
from config import MAX_TRADES_PER_DAY, MAX_DAILY_DRAWDOWN_PERCENT


class AccountPanel(ctk.CTkFrame):
    def __init__(self, parent, blackboard, **kwargs):
        super().__init__(
            parent,
            fg_color=theme.BG_PANEL,
            corner_radius=0,
            **kwargs
        )
        self.blackboard = blackboard
        self._build()

    def _build(self):
        # ── TITRE ─────────────────────────────────────────────────────────
        self._section("⚡ COMPTE", pady_top=12)

        # Compte name + server
        self._acc_name = self._kv_row("Compte", "—")
        self._acc_server = self._kv_row("Serveur", "—")

        sep = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep.pack(fill="x", padx=10, pady=6)

        # Equity en grand
        eq_frame = ctk.CTkFrame(self, fg_color=theme.BG_CARD, corner_radius=6)
        eq_frame.pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(eq_frame, text="EQUITY",
                     font=("Consolas", 8), text_color=theme.TEXT_MUTED).pack(pady=(6, 0))
        self._equity_big = ctk.CTkLabel(
            eq_frame, text="$0.00",
            font=("Consolas", 22, "bold"),
            text_color=theme.NEON_GREEN
        )
        self._equity_big.pack()

        # Balance
        self._balance_lbl = self._kv_row("Balance", "$0.00")

        # Marge disponible
        self._margin_lbl = self._kv_row("Marge libre", "$0.00")

        sep2 = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep2.pack(fill="x", padx=10, pady=6)

        # ── DRAWDOWN JOURNALIER ─────────────────────────────────────────
        self._section("📉 RISQUE JOURNALIER")

        dd_frame = ctk.CTkFrame(self, fg_color=theme.BG_CARD, corner_radius=6)
        dd_frame.pack(fill="x", padx=10, pady=(0, 4))

        dd_top = tk.Frame(dd_frame, bg=theme.BG_CARD)
        dd_top.pack(fill="x", padx=8, pady=(6, 2))

        ctk.CTkLabel(dd_top, text="Drawdown", font=("Consolas", 8),
                     text_color=theme.TEXT_MUTED, fg_color=theme.BG_CARD).pack(side="left")
        self._dd_pct_lbl = ctk.CTkLabel(dd_top, text="0.00%",
                                         font=("Consolas", 8, "bold"),
                                         text_color=theme.NEON_GREEN,
                                         fg_color=theme.BG_CARD)
        self._dd_pct_lbl.pack(side="right")

        self._dd_bar = ctk.CTkProgressBar(dd_frame, height=8, corner_radius=4,
                                           progress_color=theme.NEON_GREEN,
                                           fg_color=theme.BG_BORDER)
        self._dd_bar.pack(fill="x", padx=8, pady=(2, 6))
        self._dd_bar.set(0)

        # Trades du jour
        self._trades_lbl = self._kv_row("Trades / Jour", f"0 / {MAX_TRADES_PER_DAY}")

        # Cooldown
        self._cooldown_lbl = self._kv_row("Cooldown", "— libre")

        sep3 = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep3.pack(fill="x", padx=10, pady=6)

        # ── SCORE ORCHESTRATEUR ───────────────────────────────────────────
        self._section("🧠 SIGNAL QUALITÉ")

        star_frame = ctk.CTkFrame(self, fg_color=theme.BG_CARD, corner_radius=6)
        star_frame.pack(fill="x", padx=10, pady=(0, 4))

        self._stars_lbl = ctk.CTkLabel(
            star_frame, text="☆ ☆ ☆ ☆ ☆",
            font=("Consolas", 18),
            text_color=theme.TEXT_MUTED, fg_color=theme.BG_CARD
        )
        self._stars_lbl.pack(pady=(6, 2))

        self._score_lbl = ctk.CTkLabel(
            star_frame, text="Score: 0 / 100",
            font=("Consolas", 10),
            text_color=theme.TEXT_SECONDARY, fg_color=theme.BG_CARD
        )
        self._score_lbl.pack(pady=(0, 6))

        sep4 = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep4.pack(fill="x", padx=10, pady=6)

        # ── TRADE ACTIF ───────────────────────────────────────────────────
        self._section("💰 TRADE ACTIF")
        self._trade_frame = ctk.CTkFrame(self, fg_color=theme.BG_CARD, corner_radius=6)
        self._trade_frame.pack(fill="x", padx=10, pady=(0, 10))
        self._build_trade_card()

    def _build_trade_card(self):
        """Construit le bloc trade actif (ou 'Aucun trade')."""
        for w in self._trade_frame.winfo_children():
            w.destroy()

        self._no_trade_lbl = ctk.CTkLabel(
            self._trade_frame,
            text="Aucun trade ouvert",
            font=("Consolas", 10),
            text_color=theme.TEXT_MUTED,
            fg_color=theme.BG_CARD
        )
        self._no_trade_lbl.pack(pady=16)

        # Widgets du trade actif (créés une fois, visibilité contrôlée)
        self._tc_dir = ctk.CTkLabel(
            self._trade_frame, text="▲ BUY XAUUSD",
            font=("Consolas", 13, "bold"),
            text_color=theme.NEON_GREEN, fg_color=theme.BG_CARD
        )
        self._tc_pnl = ctk.CTkLabel(
            self._trade_frame, text="+$0.00",
            font=("Consolas", 20, "bold"),
            text_color=theme.NEON_GREEN, fg_color=theme.BG_CARD
        )
        self._tc_status = ctk.CTkLabel(
            self._trade_frame, text="OPEN",
            font=("Consolas", 9, "bold"),
            text_color=theme.ELECTRIC_BLUE, fg_color=theme.BG_CARD
        )
        # Barre SL → prix → TP
        self._tc_bar_label = ctk.CTkLabel(
            self._trade_frame, text="SL ◄─────── TP",
            font=("Consolas", 7),
            text_color=theme.TEXT_MUTED, fg_color=theme.BG_CARD
        )
        self._tc_progress = ctk.CTkProgressBar(
            self._trade_frame, height=6, corner_radius=3,
            progress_color=theme.NEON_GREEN,
            fg_color="#300000"
        )
        self._tc_meta = ctk.CTkLabel(
            self._trade_frame, text="Ticket: — | 0 lots | 0s",
            font=("Consolas", 8),
            text_color=theme.TEXT_MUTED, fg_color=theme.BG_CARD
        )

    def _section(self, title, pady_top=8):
        ctk.CTkLabel(
            self, text=title,
            font=("Consolas", 10, "bold"),
            text_color=theme.TEXT_SECONDARY
        ).pack(anchor="w", padx=14, pady=(pady_top, 2))

    def _kv_row(self, key, value):
        """Ligne clé / valeur inline."""
        f = tk.Frame(self, bg=theme.BG_PANEL)
        f.pack(fill="x", padx=14, pady=1)
        tk.Label(f, text=key, font=("Consolas", 8),
                 bg=theme.BG_PANEL, fg=theme.TEXT_MUTED, anchor="w").pack(side="left")
        val_lbl = tk.Label(f, text=value, font=("Consolas", 8, "bold"),
                           bg=theme.BG_PANEL, fg=theme.TEXT_PRIMARY, anchor="e")
        val_lbl.pack(side="right")
        return val_lbl

    # ─────────────────────────────────────────────────────────────────────────
    # MISE À JOUR
    # ─────────────────────────────────────────────────────────────────────────

    def update(self):
        try:
            acc = self.blackboard.read_sync("meta.account_info") or {}
            equity  = acc.get("equity", 0.0)
            balance = acc.get("balance", 0.0)
            margin  = acc.get("margin_free", 0.0)
            name    = acc.get("name", acc.get("login", "—"))
            server  = acc.get("server", "—")

            self._equity_big.configure(text=f"${equity:,.2f}")
            self._balance_lbl.configure(text=f"${balance:,.2f}")
            self._margin_lbl.configure(text=f"${margin:,.2f}")
            self._acc_name.configure(text=str(name)[:16])
            self._acc_server.configure(text=str(server)[:16])

            # Drawdown
            daily  = self.blackboard._data.get("daily_stats", {})
            pnl    = daily.get("realized_pnl", 0.0) + daily.get("floating_pnl", 0.0)
            dd_pct = abs(min(0.0, pnl)) / max(equity, 1) * 100
            dd_rat = min(1.0, dd_pct / MAX_DAILY_DRAWDOWN_PERCENT)
            dd_color = theme.NEON_GREEN if dd_pct < MAX_DAILY_DRAWDOWN_PERCENT * 0.5 else \
                       theme.ORANGE if dd_pct < MAX_DAILY_DRAWDOWN_PERCENT * 0.8 else theme.RED
            self._dd_bar.configure(progress_color=dd_color)
            self._dd_bar.set(dd_rat)
            self._dd_pct_lbl.configure(text=f"{dd_pct:.2f}%", text_color=dd_color)

            # Trades du jour
            cnt = self.blackboard._data.get("meta", {}).get("daily_trade_count", 0)
            halt = daily.get("drawdown_halt", False)
            self._trades_lbl.configure(
                text=f"{cnt} / {MAX_TRADES_PER_DAY}",
                fg=theme.RED if halt or cnt >= MAX_TRADES_PER_DAY else theme.TEXT_PRIMARY
            )

            # Score orchestrateur
            orch = self.blackboard.read_sync("orchestrator") or {}
            dec  = orch.get("last_decision") or {}
            score = float(dec.get("score", 0))
            stars_n = orch.get("star_rating", 0)
            stars_str = "★ " * stars_n + "☆ " * (5 - stars_n)
            star_color = theme.NEON_GREEN if stars_n >= 5 else \
                         theme.GOLD if stars_n >= 4 else \
                         theme.ORANGE if stars_n >= 3 else theme.TEXT_MUTED
            self._stars_lbl.configure(text=stars_str.strip(), text_color=star_color)
            self._score_lbl.configure(text=f"Score: {score:.0f} / 100")

            # Trade actif
            active = self.blackboard.read_sync("active_trades") or {}
            if active:
                self._update_trade_card(list(active.values())[0])
            else:
                self._clear_trade_card()

        except Exception:
            pass

    def _update_trade_card(self, trade: dict):
        """Affiche les infos du trade actif."""
        self._no_trade_lbl.pack_forget()

        direction = trade.get("type", "BUY")
        entry = trade.get("entry_price", 0.0)
        sl    = trade.get("original_sl", 0.0)
        tp    = trade.get("tp", 0.0)
        be    = trade.get("breakeven_activated", False)
        ticket = trade.get("ticket", "—")
        vol   = trade.get("volume_original", 0.0)

        # PnL flottant depuis le blackboard
        pnl = self.blackboard._data.get("daily_stats", {}).get("floating_pnl", 0.0)
        pnl_color = theme.NEON_GREEN if pnl >= 0 else theme.RED

        arrow = "▲" if direction == "BUY" else "▼"
        dir_color = theme.NEON_GREEN if direction == "BUY" else theme.RED

        self._tc_dir.configure(text=f"{arrow} {direction} XAUUSD", text_color=dir_color)
        self._tc_dir.pack(pady=(8, 0))

        self._tc_pnl.configure(text=f"{pnl:+.2f} USD", text_color=pnl_color)
        self._tc_pnl.pack()

        status = "✅ BE ACTIF" if be else "OPEN"
        st_color = theme.NEON_GREEN if be else theme.ELECTRIC_BLUE
        self._tc_status.configure(text=status, text_color=st_color)
        self._tc_status.pack()

        # Barre de progression SL → TP
        if sl > 0 and tp > 0:
            tick = self.blackboard.read_sync("market_data.current_tick") or {}
            curr = tick.get("bid", entry) if direction == "BUY" else tick.get("ask", entry)
            total_range = abs(tp - sl)
            progress = abs(curr - sl) / total_range if total_range > 0 else 0
            progress = max(0.0, min(1.0, progress))
            self._tc_bar_label.configure(
                text=f"SL:{sl:.1f}  →{curr:.1f}→  TP:{tp:.1f}"
            )
            self._tc_bar_label.pack(pady=(4, 0))
            self._tc_progress.set(progress)
            bar_color = theme.NEON_GREEN if progress > 0.5 else theme.ORANGE
            self._tc_progress.configure(progress_color=bar_color)
            self._tc_progress.pack(fill="x", padx=8, pady=(2, 4))

        # Méta
        opened = trade.get("opened_at", "")
        dur = ""
        if opened:
            try:
                t0 = datetime.fromisoformat(opened)
                elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
                m, s = divmod(int(elapsed), 60)
                h, m = divmod(m, 60)
                dur = f"{h:02d}:{m:02d}:{s:02d}"
            except Exception:
                pass
        self._tc_meta.configure(text=f"#{ticket} | {vol:.2f}L | {dur}")
        self._tc_meta.pack(pady=(0, 8))

    def _clear_trade_card(self):
        for w in [self._tc_dir, self._tc_pnl, self._tc_status,
                  self._tc_bar_label, self._tc_progress, self._tc_meta]:
            try:
                w.pack_forget()
            except Exception:
                pass
        self._no_trade_lbl.pack(pady=16)
