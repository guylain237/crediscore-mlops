"""Garde-fou d'environnement : le code applicatif doit tourner dans le venv du dépôt.

Empêche l'exécution silencieuse sur le Python global ou sur Anaconda, qui
casserait la reproductibilité (décision D-103).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_interpreteur_est_celui_du_venv_du_depot() -> None:
    prefix = Path(sys.prefix).resolve()
    assert prefix != Path(sys.base_prefix).resolve(), (
        "Python s'exécute hors environnement virtuel. "
        "Activez le venv du dépôt : .venv\\Scripts\\activate"
    )
    assert prefix == (REPO_ROOT / ".venv").resolve(), (
        f"Le venv actif ({prefix}) n'est pas celui du dépôt "
        f"({REPO_ROOT / '.venv'}). Anaconda ou un autre venv est actif."
    )


def test_dependances_cles_installees() -> None:
    import importlib.util

    requis = ["fastapi", "uvicorn", "pydantic", "lightgbm", "shap", "psycopg", "evidently"]
    manquants = [m for m in requis if importlib.util.find_spec(m) is None]
    assert not manquants, (
        f"Modules manquants : {', '.join(manquants)}. "
        "Lancez : python -m pip install -r requirements.txt"
    )
