import customtkinter as ctk

class AgentBureau(ctk.CTkFrame):
    def __init__(self, master, title, icon="🟢", **kwargs):
        super().__init__(master, **kwargs)
        
        self.configure(fg_color="#1e1e24", corner_radius=10, border_width=1, border_color="#333333")
        
        # Header (Icon + Title)
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        self.icon_label = ctk.CTkLabel(self.header_frame, text=icon, font=("Arial", 18))
        self.icon_label.pack(side="left")
        
        self.title_label = ctk.CTkLabel(self.header_frame, text=title.upper(), font=("Arial", 12, "bold"), text_color="#aaaaaa")
        self.title_label.pack(side="left", padx=5)
        
        # Content area (Key-Value pairs or raw text)
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        self.labels = {}
        
    def set_status(self, color, main_text):
        """Update main icon and status"""
        color_map = {
            "green": "#00ff00", 
            "red": "#ff0000", 
            "grey": "#555555", 
            "yellow": "#ffff00"
        }
        hex_color = color_map.get(color, "#555555")
        
        # Use a solid circle and apply the text_color so it's guaranteed to be colored
        self.icon_label.configure(text="●", text_color=hex_color)
        self.set_data("Status", main_text)
        
    def set_data(self, key, value):
        """Add or update a key-value data row"""
        if key not in self.labels:
            row_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
            row_frame.pack(fill="x", pady=2)
            
            key_label = ctk.CTkLabel(row_frame, text=f"{key}:", font=("Arial", 11), text_color="#888888", width=80, anchor="w")
            key_label.pack(side="left")
            
            val_label = ctk.CTkLabel(row_frame, text=str(value), font=("Consolas", 11, "bold"), text_color="#ffffff", anchor="w", justify="left")
            val_label.pack(side="left", fill="x", expand=True)
            
            self.labels[key] = val_label
        else:
            self.labels[key].configure(text=str(value))
