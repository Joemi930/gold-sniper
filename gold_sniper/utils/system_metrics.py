"""Metriques RAM/CPU via psutil (partage pc_manager + discord_commander)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_ram_cpu_percent(interval: float = 0.2) -> tuple[float, float] | None:
    """Retourne (ram_pct, cpu_pct) ou None si psutil indisponible."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=interval)
        return float(mem.percent), float(cpu)
    except ImportError:
        logger.warning("psutil non installe — RAM/CPU indisponibles")
        return None
    except Exception as exc:
        logger.warning("Lecture RAM/CPU: %s", exc)
        return None


def format_ram_cpu(interval: float = 0.2) -> str:
    """Chaine 'RAM: X% | CPU: Y%' pour Discord."""
    values = get_ram_cpu_percent(interval)
    if values is None:
        return "RAM: n/a | CPU: n/a"
    ram, cpu = values
    return f"RAM: {ram:.0f}% | CPU: {cpu:.0f}%"


def format_ram_cpu_health(interval: float = 0.2) -> str:
    """Chaine 'ram% / cpu%' pour embed health."""
    values = get_ram_cpu_percent(interval)
    if values is None:
        return "n/a / n/a"
    ram, cpu = values
    return f"{ram:.0f}% / {cpu:.0f}%"
