import customtkinter as ctk

class AccountPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="#151515", corner_radius=10, border_width=1, border_color="#333333")
        
        # Account Name & Broker
        self.name_label = ctk.CTkLabel(self, text="...", font=("Arial", 16, "bold"), text_color="#ffd700")
        self.name_label.pack(anchor="w", padx=15, pady=(15, 5))
        
        self.server_label = ctk.CTkLabel(self, text="Connexion en cours...", font=("Arial", 12), text_color="#888888")
        self.server_label.pack(anchor="w", padx=15, pady=(0, 15))
        
        # Grid for stats
        self.stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        self.lbl_equity = self._add_stat(self.stats_frame, "Equity")
        self.lbl_balance = self._add_stat(self.stats_frame, "Balance")
        self.lbl_margin = self._add_stat(self.stats_frame, "Free Margin")
        
    def _add_stat(self, parent, label_text):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        lbl = ctk.CTkLabel(row, text=f"{label_text}:", font=("Arial", 12), text_color="#aaaaaa", width=90, anchor="w")
        lbl.pack(side="left")
        val = ctk.CTkLabel(row, text="---", font=("Consolas", 14, "bold"), text_color="#ffffff")
        val.pack(side="right")
        return val
        
    def update_account(self, info):
        if not info:
            return
        
        self.name_label.configure(text=f"👤 {info.get('name', 'Unknown')}")
        self.server_label.configure(text=f"🏦 {info.get('server', 'Unknown')}")
        
        curr = info.get("currency", "$")
        equity = info.get("equity", 0.0)
        balance = info.get("balance", 0.0)
        margin = info.get("margin_free", 0.0)
        
        self.lbl_equity.configure(text=f"{equity:,.2f} {curr}")
        self.lbl_balance.configure(text=f"{balance:,.2f} {curr}")
        self.lbl_margin.configure(text=f"{margin:,.2f} {curr}")
