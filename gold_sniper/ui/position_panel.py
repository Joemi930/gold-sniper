# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — UI POSITION PANEL
# ═══════════════════════════════════════════════════════════════════════════════

import customtkinter as ctk

class PositionPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        
        self.title_label = ctk.CTkLabel(self, text="POSITION OUVERTE", font=("Arial", 14, "bold"))
        self.title_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        self.info_label = ctk.CTkLabel(self, text="Aucune position en cours.", font=("Arial", 12))
        self.info_label.grid(row=1, column=0, padx=10, pady=5, sticky="w")
        
        self.details_label = ctk.CTkLabel(self, text="", font=("Arial", 12))
        self.details_label.grid(row=2, column=0, padx=10, pady=5, sticky="w")

    def update_position(self, active_trades):
        if not active_trades:
            self.info_label.configure(text="Aucune position en cours.", text_color="gray")
            self.details_label.configure(text="")
            return
            
        # Prendre le premier trade pour l'affichage
        ticket = list(active_trades.keys())[0]
        trade = active_trades[ticket]
        
        action = trade["type"]
        entry = trade["entry_price"]
        sl = trade["current_sl"]
        tp = trade["tp"]
        be = " (BE)" if trade["breakeven_activated"] else ""
        
        color = "green" if action == "BUY" else "red"
        
        self.info_label.configure(
            text=f"#{ticket}  {action} @ {entry:.2f}", 
            text_color=color
        )
        
        self.details_label.configure(
            text=f"SL: {sl:.2f}{be}  |  TP: {tp:.2f}",
            text_color="white"
        )
