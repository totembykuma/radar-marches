import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(name):
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def premier_client_payant(state_path=None):
    """Date du premier client payant (hors essai), notée par l'envoi quotidien dans data/state.json."""
    try:
        with open(state_path or ROOT / "data" / "state.json", encoding="utf-8") as f:
            return json.load(f).get("premier_client_payant")
    except (OSError, ValueError):
        return None


def ia_autorisee(state_path=None):
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) and bool(premier_client_payant(state_path))


def business():
    cfg = load_yaml("business.yaml")
    # Les secrets / valeurs d'environnement ont priorité sur le fichier.
    # Résumés IA : seulement avec la clé Claude ET après le premier client payant (aucune dépense avant).
    cfg["ia_active"] = ia_autorisee()
    cfg["email_proprietaire"] = os.getenv("OWNER_EMAIL") or cfg.get("email_proprietaire") or ""
    for key in ("essentiel", "pro"):
        env_link = os.getenv(f"STRIPE_LINK_{key.upper()}")
        if env_link:
            cfg["offres"][key]["lien_paiement"] = env_link
    cfg["dossier_actif"] = dossier_actif(cfg)
    cfg["cle_actif"] = cle_actif(cfg)
    # Comptes avec mot de passe (Supabase) : en service dès que comptes-setup a écrit config/comptes.json.
    cfg["comptes_actifs"] = (ROOT / "config" / "comptes.json").exists()
    from .annuel import enrichir     # économie annuelle calculée sur la grille mensuelle (affichage seulement)
    return enrichir(cfg)


def dossier_actif(cfg):
    """Option « Dossier prêt à signer » : seulement avec l'IA active et les liens de paiement créés."""
    d = cfg.get("dossier") or {}
    return bool(cfg.get("ia_active") and d.get("lien_paiement") and d.get("adresse"))


def cle_actif(cfg):
    """Offre « Clé en main » : comme l'option dossier, plus ses liens d'abonnement créés."""
    return bool(dossier_actif(cfg) and ((cfg.get("offres") or {}).get("cle") or {}).get("lien_paiement"))


def metiers():
    return load_yaml("metiers.yaml")
