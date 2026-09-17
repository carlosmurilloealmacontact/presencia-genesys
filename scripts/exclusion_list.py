"""
Módulo centralizado de exclusión y bloqueo de acceso.
Garantiza que el personal no operativo o expresamente restringido:
1. No tenga acceso al panel mediante autenticación (Google SSO).
2. No aparezca en selectores, filtros, tablas ni reportes del dashboard.
"""

import re
import unicodedata
import pandas as pd

# Personas excluidas de acceso y visualización
PERSONAS_EXCLUIDAS = [
    "PALACIOS SOLANO LUZ ELENA",
    "CORDOBA CASTAÑEDA LINA MARIA",
    "CORDOBA CASTANEDA LINA MARIA",
    "HERRERA NOGUERA MAURICIO",
    "VALDES GUZMAN WILFFIN STEFAN",
    "MEJIA GRONDONA JHONATAN ALBERTO",
    "TABARES VALLEJO ISABEL CRISTINA",
    "LEMUS PALACIOS EMIR ALEXANDER",
    "BALLEJO GONZALES SAMUEL DAVID",
]


def normalizar_texto(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("utf-8")
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s)
    return " ".join(s.upper().split())


# Token sets precalculados
_EXCLUIDAS_TOKEN_SETS = []
for _p in PERSONAS_EXCLUIDAS:
    _norm = normalizar_texto(_p)
    _tokens = set(w for w in _norm.split() if len(w) > 2)
    _EXCLUIDAS_TOKEN_SETS.append((_norm, _tokens))


def es_persona_excluida(val: str) -> bool:
    """Verifica si un nombre o etiqueta coincide con alguna persona excluida."""
    if not val:
        return False
    norm_val = normalizar_texto(str(val))
    if not norm_val:
        return False
    val_tokens = set(w for w in norm_val.split() if len(w) > 2)
    for full_norm, tset in _EXCLUIDAS_TOKEN_SETS:
        if full_norm in norm_val or norm_val in full_norm:
            return True
        inter = val_tokens.intersection(tset)
        if len(inter) >= 2:
            return True
    return False


def es_usuario_bloqueado(email: str, nombre: str = "") -> bool:
    """
    Verifica si el usuario autenticado (por email o nombre de cuenta Google)
    pertenece a la lista de personas sin autorización de acceso al panel.
    """
    if not email and not nombre:
        return False
    if nombre and es_persona_excluida(nombre):
        return True
    alias = email.split("@")[0] if "@" in email else email
    norm_alias = normalizar_texto(alias)
    alias_tokens = set(w for w in re.split(r"[._-]", norm_alias) if len(w) > 2)
    for _, tset in _EXCLUIDAS_TOKEN_SETS:
        inter = alias_tokens.intersection(tset)
        if len(inter) >= 2:
            return True
        for t1 in tset:
            for t2 in tset:
                if t1 != t2 and (t1.lower() + t2.lower() in alias.lower() or t2.lower() + t1.lower() in alias.lower()):
                    return True
    return False


def filtrar_df_exclusiones(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina del DataFrame cualquier fila vinculada a personas excluidas
    en columnas clave como coordinador, jefe_inmediato, supervisor o agente.
    """
    if df is None or df.empty:
        return df
    mascara = pd.Series(True, index=df.index)
    for col in ["coordinador", "jefe_inmediato", "supervisor", "agente", "nombre", "Coordinador", "Supervisor"]:
        if col in df.columns:
            mascara = mascara & (~df[col].astype(str).apply(es_persona_excluida))
    return df[mascara]
