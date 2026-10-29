"""Strategy library and the ``signal_exit`` engine (gate v6, paper only, BTC only).

``catalog`` declares the named strategies and their citations, ``daily``
resamples the 5m tape into completed UTC days, ``engine`` is the one
long-or-flat state machine shared by the backtest and the live cycle,
``evaluate`` runs every gate check, and ``live`` trades an admitted library
champion on the paper book.
"""
from hedge_fund.library.catalog import NAME_PREFIX, is_library_name

__all__ = ["NAME_PREFIX", "is_library_name"]
