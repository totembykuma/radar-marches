import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(name):
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def business():
    cfg = load_yaml("business.yaml")
    # Les secrets / valeurs d'environnement ont priorité sur le fichier.
    # Les promesses « IA » du site ne s'affichent que si la clé Claude est configurée.
    cfg["ia_active"] = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    cfg["email_proprietaire"] = os.getenv("OWNER_EMAIL") or cfg.get("email_proprietaire") or ""
    for key in ("essentiel", "pro"):
        env_link = os.getenv(f"STRIPE_LINK_{key.upper()}")
        if env_link:
            cfg["offres"][key]["lien_paiement"] = env_link
    return cfg


def metiers():
    return load_yaml("metiers.yaml")
