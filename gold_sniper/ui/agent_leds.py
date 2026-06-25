# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — UI AGENT LEDS
# ═══════════════════════════════════════════════════════════════════════════════

import customtkinter as ctk

class AgentLeds(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self.grid_columnconfigure(0, weight=1)
        
        self.title_label = ctk.CTkLabel(self, text="LEDs DES 7 AGENTS", font=("Arial", 14, "bold"))
        self.title_label.grid(row=0, column=0, padx=10, pady=(5, 5), sticky="w")
        
        self.leds = {}
        agents = [
            ("Agent 1 (Météo)", "agent_1_meteo"),
            ("Agent 2 (Carto)", "agent_2_cartographe"),
            ("Agent 3 (Liquidité)", "agent_3_liquidite"),
            ("Agent 4 (Fibonacci)", "agent_4_fibonacci"),
            ("Agent 5 (Micro 1m)", "agent_5_microscope"),
            ("Agent 6 (Sentinelle)", "agent_6_sentinelle"),
            ("Agent 7 (Sessions)", "agent_7_sessions"),
        ]
        
        for i, (name, agent_key) in enumerate(agents):
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.grid(row=i+1, column=0, padx=10, pady=2, sticky="ew")
            
            # LED Canvas (Circle)
            led_label = ctk.CTkLabel(frame, text="⚫", font=("Arial", 16))
            led_label.pack(side="left", padx=5)
            
            name_label = ctk.CTkLabel(frame, text=name, font=("Arial", 12, "bold"), width=150, anchor="w")
            name_label.pack(side="left", padx=5)
            
            status_label = ctk.CTkLabel(frame, text="SLEEPING", font=("Arial", 12), text_color="gray")
            status_label.pack(side="left", padx=5, fill="x", expand=True)
            
            self.leds[agent_key] = {"led": led_label, "status": status_label}

    def update_led(self, agent_key, state_color, status_text):
        if agent_key in self.leds:
            color_map = {
                "green": "🟢",
                "red": "🔴",
                "grey": "⚫",
                "yellow": "🟡"
            }
            self.leds[agent_key]["led"].configure(text=color_map.get(state_color, "⚫"))
            self.leds[agent_key]["status"].configure(text=status_text)
