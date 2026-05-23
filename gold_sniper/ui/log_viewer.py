# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — UI LOG VIEWER
# ═══════════════════════════════════════════════════════════════════════════════

import customtkinter as ctk
import queue
import re

def strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

class LogViewer(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        self.title_label = ctk.CTkLabel(self, text="LOG EN TEMPS RÉEL", font=("Arial", 14, "bold"))
        self.title_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        # Zone de texte défilante
        self.textbox = ctk.CTkTextbox(self, font=("Consolas", 12), state="disabled", wrap="word")
        self.textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

    def append_log(self, msg: str):
        clean_msg = strip_ansi(msg)
        self.textbox.configure(state="normal")
        self.textbox.insert("end", clean_msg + "\n")
        self.textbox.see("end")  # Auto-scroll
        self.textbox.configure(state="disabled")

