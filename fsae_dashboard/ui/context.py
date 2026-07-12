"""Shared services handed to every panel."""
from __future__ import annotations

from dataclasses import dataclass

from fsae_dashboard.data.hub import DataHub
from fsae_dashboard.data.subscriptions import SubscriptionManager


@dataclass
class AppContext:
    hub: DataHub
    subs: SubscriptionManager
