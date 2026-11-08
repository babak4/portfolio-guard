"""What the dashboard reads: plain data computed from the stores, never any writing.

Every page of the dashboard goes through these functions. They take a read-only connection and
return frozen dataclasses (money as ``Decimal``, weights as fractions) and know nothing about
Streamlit.
"""

from guard.readapi.advice import advice_view
from guard.readapi.connect import reader
from guard.readapi.history import history_view, run_detail
from guard.readapi.index import index_view
from guard.readapi.models import CONTRIBUTION_LABEL
from guard.readapi.portfolio import portfolio_view
from guard.readapi.responses import responses_view
from guard.readapi.status import status_view

__all__ = [
    "CONTRIBUTION_LABEL",
    "advice_view",
    "history_view",
    "index_view",
    "portfolio_view",
    "reader",
    "responses_view",
    "run_detail",
    "status_view",
]
