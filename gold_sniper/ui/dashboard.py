# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — DASHBOARD PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════
#
# Layout :
#   ┌──────────────────── HEADER (full width) ───────────────────────┐
#   ├── AGENTS LEFT (240px) ──┬── PIPELINE (center) ─┬── ACCOUNT ───┤
#   │   7 cartes agent cards  │   Réseau neuronal     │   (280px)    │
#   │   scrollables           │   animé               │              │
#   ├─────────────────────────┴───────────────────────┴──────────────┤
#   │                 LOG FEED FULL WIDTH (200px)                    │
#   └────────────────────────────────────────────────────────────────┘
#
# ═══════════════════════════════════════════════════════════════════════════════

import tkinter as tk
import customtkinter as ctk
from queue import Queue

from ui import theme
from ui.components.header_bar    import HeaderBar
from ui.components.agent_card    import AgentCard
from ui.components.pipeline_canvas import PipelineCanvas
from ui.components.account_panel import AccountPanel
from ui.components.log_feed      import LogFeed
from utils.logger import setup_ui_queue


# Agents avec leur identifiant Blackboard
AGENTS = [
    ("agent_1", True),   # (id, is_hard_filter)
    ("agent_2", True),
    ("agent_3", False),
    ("agent_4", False),
    ("agent_5", False),
    ("agent_6", False),
    ("agent_7", False),
]


class Dashboard(ctk.CTk):
    def __init__(self, blackboard, stop_event):
        super().__init__()
        self.blackboard  = blackboard
        self.stop_event  = stop_event
        self.log_queue   = Queue()

        # Brancher le logger système sur notre queue UI
        setup_ui_queue(self.log_queue)

        # ── CONFIG FENÊTRE ────────────────────────────────────────────────
        self.title("◆ GOLD SNIPER V2.1 — Institutional Intelligence")
        self.geometry("1400x820")
        self.minsize(1100, 680)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(bg=theme.BG_DEEP)

        # Icône (silencieux si absente)
        try:
            self.iconbitmap("assets/icon.ico")
        except Exception:
            pass

        # ── GRID PRINCIPALE ───────────────────────────────────────────────
        # row 0 : header
        # row 1 : (agents | pipeline | account) → expand
        # row 2 : log feed → fixed height
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0, minsize=theme.LOG_HEIGHT)
        self.grid_columnconfigure(0, weight=0, minsize=theme.AGENT_COL_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=theme.ACCOUNT_COL_W)

        # ── HEADER ─────────────────────────────────────────────────────────
        self._header = HeaderBar(self, blackboard=blackboard, stop_event=stop_event)
        self._header.grid(row=0, column=0, columnspan=3, sticky="ew")

        # ── COLONNE GAUCHE : AGENT CARDS ───────────────────────────────────
        agents_outer = ctk.CTkFrame(self, fg_color=theme.BG_PANEL, corner_radius=0)
        agents_outer.grid(row=1, column=0, sticky="nsew")
        agents_outer.grid_rowconfigure(0, weight=1)
        agents_outer.grid_columnconfigure(0, weight=1)

        # Scrollable si trop d'agents
        self._agents_scroll = ctk.CTkScrollableFrame(
            agents_outer,
            fg_color=theme.BG_PANEL,
            corner_radius=0,
            scrollbar_button_color=theme.BG_BORDER,
            scrollbar_button_hover_color=theme.ELECTRIC_BLUE,
        )
        self._agents_scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        # Titre
        ctk.CTkLabel(
            self._agents_scroll,
            text="🤖  AGENTS  ×7",
            font=("Consolas", 10, "bold"),
            text_color=theme.TEXT_SECONDARY
        ).pack(anchor="w", padx=10, pady=(10, 6))

        # Séparateur
        tk.Frame(self._agents_scroll, bg=theme.BG_BORDER, height=1).pack(fill="x", padx=10, pady=2)

        # Créer les 7 cartes agent
        self._agent_cards: dict[str, AgentCard] = {}
        for agent_id, is_hf in AGENTS:
            card = AgentCard(
                self._agents_scroll,
                agent_id=agent_id,
                is_hard_filter=is_hf
            )
            card.pack(fill="x", padx=8, pady=4)
            self._agent_cards[agent_id] = card

        # ── CENTRE : PIPELINE CANVAS ──────────────────────────────────────
        center_frame = ctk.CTkFrame(self, fg_color=theme.BG_DEEP, corner_radius=0)
        center_frame.grid(row=1, column=1, sticky="nsew")
        center_frame.grid_rowconfigure(0, weight=0)
        center_frame.grid_rowconfigure(1, weight=1)
        center_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            center_frame,
            text="⚡  PIPELINE V2  —  FLUX EN TEMPS RÉEL",
            font=("Consolas", 10, "bold"),
            text_color=theme.TEXT_SECONDARY
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(8, 4))

        self._pipeline = PipelineCanvas(center_frame, blackboard=blackboard)
        self._pipeline.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        # ── COLONNE DROITE : ACCOUNT PANEL ────────────────────────────────
        acc_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.BG_PANEL,
            corner_radius=0,
            scrollbar_button_color=theme.BG_BORDER,
            scrollbar_button_hover_color=theme.ELECTRIC_BLUE,
        )
        acc_scroll.grid(row=1, column=2, sticky="nsew")

        self._account_panel = AccountPanel(acc_scroll, blackboard=blackboard)
        self._account_panel.pack(fill="both", expand=True)

        # ── BAS : LOG FEED ─────────────────────────────────────────────────
        self._log_feed = LogFeed(self, log_queue=self.log_queue)
        self._log_feed.grid(row=2, column=0, columnspan=3, sticky="nsew")

        # Séparateur haut du log feed
        sep = tk.Frame(self, bg=theme.BG_BORDER, height=1)
        sep.grid(row=2, column=0, columnspan=3, sticky="new")

        # ── BOUCLE DE RAFRAÎCHISSEMENT UI ──────────────────────────────────
        self._update_loop()

    # ─────────────────────────────────────────────────────────────────────────
    # BOUCLE DE MISE À JOUR (Tkinter after loop)
    # ─────────────────────────────────────────────────────────────────────────

    def _update_loop(self):
        """Rafraîchit tous les composants depuis le Blackboard."""
        try:
            self._update_agent_cards()
            self._header.update()
            self._account_panel.update()
        except Exception as e:
            pass  # Ne jamais crasher l'UI

        # Planifier la prochaine mise à jour
        self.after(theme.REFRESH_MED_MS, self._update_loop)

    def _update_agent_cards(self):
        """Met à jour les 7 cartes agents depuis agent_results."""
        results = self.blackboard._data.get("agent_results", {})
        agents_data = self.blackboard._data.get("agents", {})

        for agent_id, card in self._agent_cards.items():
            # Score depuis agent_results (V2)
            result = results.get(agent_id)
            if result is not None:
                card.set_score(float(result.score))
                card.set_status(result.reason or "—", theme.score_color(result.score))

                # Détail selon l'agent
                detail = self._get_agent_detail(agent_id, result, agents_data)
                card.set_detail(detail)
            else:
                # Agents non encore calculés
                card.set_status("EN ATTENTE...", theme.TEXT_MUTED)

    def _get_agent_detail(self, agent_id: str, result, agents_data: dict) -> str:
        """Génère une ligne de détail pour chaque agent."""
        try:
            if agent_id == "agent_1":
                a = agents_data.get("agent_1_meteo", {})
                bias  = a.get("bias", "?")
                phase = a.get("market_phase", "?")
                return f"{bias} · {phase}"

            elif agent_id == "agent_2":
                a = agents_data.get("agent_2_cartographe", {})
                n_ob  = len(a.get("order_blocks", []))
                n_fvg = len(a.get("fvg_zones", []))
                poi   = a.get("active_poi")
                if poi:
                    return f"POI ACTIF: {poi.get('type', '?')} @ {poi.get('price', 0):.1f}"
                return f"{n_ob} OB · {n_fvg} FVG"

            elif agent_id == "agent_3":
                a = agents_data.get("agent_3_liquidite", {})
                asian = a.get("asian_session", {})
                swept = asian.get("swept", "NONE")
                return f"Sweep: {swept}"

            elif agent_id == "agent_4":
                a = agents_data.get("agent_4_fibonacci", {})
                in_ote = a.get("price_in_ote", False)
                levels = a.get("ote_zone", {})
                if in_ote:
                    return f"DANS OTE ✓ ({levels.get('low', 0):.1f}–{levels.get('high', 0):.1f})"
                return "En attente OTE..."

            elif agent_id == "agent_5":
                a = agents_data.get("agent_5_microscope", {})
                state = a.get("state", "SLEEPING")
                choch = a.get("choch_detected", {})
                if choch.get("type"):
                    return f"CHoCH {choch['type']} @ {choch.get('level', 0):.1f}"
                return f"État: {state}"

            elif agent_id == "agent_6":
                a = agents_data.get("agent_6_sentinelle", {})
                if a.get("blackout_active"):
                    evt = a.get("next_red_event", {})
                    return f"BLACKOUT: {evt.get('name', '?')}"
                evt = a.get("next_red_event", {})
                if evt and evt.get("name"):
                    mins = evt.get("minutes_until", "?")
                    return f"Next: {evt['name'][:20]} (~{mins}min)"
                return "Calendrier vide ✓"

            elif agent_id == "agent_7":
                a = agents_data.get("agent_7_sessions", {})
                sess  = a.get("current_session", "?")
                kz    = a.get("killzone_name", "")
                rm    = getattr(result, "risk_modifier", 1.0) or 1.0
                kz_str = f" [{kz}]" if kz else ""
                return f"{sess}{kz_str} · RM={rm:.1f}×"

        except Exception:
            pass
        return "—"
