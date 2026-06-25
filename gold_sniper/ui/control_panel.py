# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — UI CONTROL PANEL
# ═══════════════════════════════════════════════════════════════════════════════

import customtkinter as ctk

class ControlPanel(ctk.CTkFrame):
    def __init__(self, master, kill_callback):
        super().__init__(master)
        
        self.grid_columnconfigure(0, weight=1)
        self.kill_callback = kill_callback
        
        self.title_label = ctk.CTkLabel(self, text="CONTRÔLES", font=("Arial", 14, "bold"))
        self.title_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        # Risk Slider
        self.risk_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.risk_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        
        self.risk_label = ctk.CTkLabel(self.risk_frame, text="Risque : 1.0%")
        self.risk_label.pack(side="left", padx=5)
        
        self.risk_slider = ctk.CTkSlider(self.risk_frame, from_=0.1, to=5.0, number_of_steps=49, command=self.slider_event)
        self.risk_slider.set(1.0)
        self.risk_slider.pack(side="left", fill="x", expand=True, padx=10)
        
        # Kill Switch Button
        self.kill_btn = ctk.CTkButton(
            self, 
            text="🔴 KILL SWITCH 🔴\n(Ferme tout + Arrête le robot)", 
            fg_color="#8b0000", 
            hover_color="#cc0000",
            font=("Arial", 14, "bold"),
            height=60,
            command=self.kill_action
        )
        self.kill_btn.grid(row=2, column=0, padx=20, pady=15, sticky="ew")

    def slider_event(self, value):
        self.risk_label.configure(text=f"Risque : {value:.1f}%")

    def kill_action(self):
        self.kill_btn.configure(text="💀 ARRÊT EN COURS...", state="disabled", fg_color="black")
        self.kill_callback()
