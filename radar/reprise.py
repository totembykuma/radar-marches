"""Nouvelles tentatives automatiques quand un service distant (BOAMP, Resend, Claude) a un raté passager."""
import logging
import time

import requests

log = logging.getLogger(__name__)
PAUSES = (10, 30)   # secondes d'attente avant la 2e puis la 3e tentative (mis à 0 dans les tests)


def passager(exc):
    """Vrai si l'erreur vaut une nouvelle tentative : réseau, délai dépassé, 429 ou erreur serveur (5xx)."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    return code == 429 or (code or 0) >= 500


def avec_reessais(fn, quoi, pauses=None):
    """Appelle fn() ; en cas d'erreur passagère, attend puis réessaie (3 tentatives en tout par défaut)."""
    pauses = PAUSES if pauses is None else pauses
    for i in range(len(pauses) + 1):
        try:
            return fn()
        except Exception as exc:
            if i == len(pauses) or not passager(exc):
                raise
            log.warning("%s : tentative %d échouée (%s), nouvel essai dans %ss", quoi, i + 1, str(exc)[:200], pauses[i])
            time.sleep(pauses[i])
