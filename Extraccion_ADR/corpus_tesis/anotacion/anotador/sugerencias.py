"""Sugerencias automaticas de anotacion a partir de la estructura markdown de un ADR.

Solo propone filas: el anotador las revisa, corrige o borra en la herramienta.
Toda evidencia propuesta es un fragmento del propio fichero (los saltos de
linea dentro de un punto de lista se unen con un espacio; la comprobacion de
fundamentacion normaliza los espacios).
"""
import re

AUTO = "auto: revisar"

ESTADOS = [
    ("superseded", r"supers?[ec]ed(ed)? by|superseded|superceded|replaced"),
    ("amended", r"amended"),
    ("rejected", r"rejected|declined|not chosen"),
    ("deprecated", r"deprecated|obsolete|retired"),
    ("proposed", r"proposed|draft|in progress|open|pending|under review"),
    ("accepted", r"accepted|approved|implemented|adopted|done|completed|supers?[ec]edes"),
]


def normalizar_estado(literal):
    s = literal.lower()
    for est, pat in ESTADOS:
        if re.search(pat, s):
            return est
    return ""


def secciones(texto):
    """[(nivel, titulo, inicio_cuerpo, fin_cuerpo)] para encabezados de nivel >= 2."""
    hs = [(len(m.group(1)), m.group(2).strip(), m.start(), m.end())
          for m in re.finditer(r"^(#{2,6})\s*(.+?)\s*#*\s*$", texto, re.M)]
    out = []
    for i, (lvl, tit, s, e) in enumerate(hs):
        fin = len(texto)
        for lvl2, _, s2, _ in hs[i + 1:]:
            if lvl2 <= lvl:
                fin = s2
                break
        out.append((lvl, re.sub(r"<!--.*?-->", "", tit).strip(), e, fin))
    return out


def buscar(texto, patron):
    return [s for s in secciones(texto) if re.search(patron, s[1], re.I)]


def cuerpo_directo(texto, sec, todas):
    """Cuerpo de la seccion sin sus subsecciones."""
    lvl, _, ini, fin = sec
    for l2, _, i2, _ in todas:
        if ini < i2 <= fin and l2 > lvl:
            # el encabezado empieza antes del fin de linea anterior
            fin = texto.rfind("\n", ini, i2) if texto.rfind("\n", ini, i2) > ini else i2
            break
    return texto[ini:fin]


def vinetas(cuerpo):
    items = []
    for linea in cuerpo.split("\n"):
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)", linea)
        if m:
            items.append(m.group(1).strip())
        elif linea.strip() and items and linea.startswith((" ", "\t")):
            items[-1] += " " + linea.strip()
    return items


def parrafos(cuerpo):
    bloques = [re.sub(r"\s*\n\s*", " ", b).strip() for b in re.split(r"\n\s*\n", cuerpo)]
    return [b for b in bloques if b and not b.startswith(("#", "|", "```", "<!--", "---"))
            and not re.match(r"^(?:[-*+]|\d+[.)])\s", b)]


def primera_frase(s):
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    return m.group(1) if m else s


def fila(elemento, evidencia="", **kw):
    r = {"elemento": elemento, "evidencia": evidencia, "valor": "", "elegida": "", "estado_normalizado": "",
         "polaridad": "", "alternativa": "", "rol": "", "tipo_relacion": "", "adr_nuevo": "",
         "adr_afectado": "", "nota": AUTO}
    r.update(kw)
    return r


def polaridad_de(s, defecto=""):
    t = s.lower()
    if re.match(r"^\W*(good|pro|positive|\+)", t):
        return "positiva"
    if re.match(r"^\W*(bad|con|negative|-)\b", t):
        return "negativa"
    if re.match(r"^\W*neutral", t):
        return "neutra"
    if defecto:
        return defecto
    if re.search(r"\b(risk|drawback|cost|slower|harder|complex|downside|lose|lack|overhead|burden|limit)", t):
        return "negativa"
    if re.search(r"\b(benefit|easier|faster|improv|simpl|better|reduc|enable|allow)", t):
        return "positiva"
    return "neutra"


def sugerir(texto, repo_archivos, archivo, tipos_presentes, relaciones):
    """Filas sugeridas para los elementos que aun no tienen filas.

    repo_archivos: ficheros del mismo repositorio (para enlaces a otros ADR).
    tipos_presentes: elementos que ya tienen filas con contenido (no se duplican).
    relaciones: filas relacion ya existentes (se intenta rellenar su evidencia).
    """
    texto = texto.replace("\r", "")
    todas = secciones(texto)
    out = []

    def quiere(el):
        return el not in tipos_presentes

    # --- estado ------------------------------------------------------------------
    if quiere("estado"):
        lit = None
        m = re.search(r"^[\s*>-]*\**status\**\s*:\s*\**\s*(.+?)\s*$", texto, re.I | re.M)
        if m:
            lit = m.group(1).strip()
        else:
            for s in buscar(texto, r"^status$"):
                ls = [x.strip() for x in texto[s[2]:s[3]].split("\n") if x.strip()]
                if ls:
                    lit = re.sub(r"^[-*]\s*", "", ls[0])
                    break
        if lit:
            out.append(fila("estado", lit, estado_normalizado=normalizar_estado(lit)))

    # --- fecha declarada -----------------------------------------------------------
    if quiere("fecha"):
        m = re.search(r"^[\s*>-]*\**(?:date|last updated)\**\s*:\s*\**\s*(\d{4}-\d{2}-\d{2})", texto, re.I | re.M)
        if m:
            out.append(fila("fecha", m.group(0).strip(" *-"), valor=m.group(1)))

    # --- participantes -------------------------------------------------------------
    if quiere("participante"):
        for rol, pat in (("decision-maker", r"deciders|decision-makers|decision makers"),
                         ("consulted", r"consulted"), ("informed", r"informed")):
            m = re.search(r"^[\s*>-]*\**(?:%s)\**\s*:\s*(.+)$" % pat, texto, re.I | re.M)
            if m:
                for nombre in re.split(r",|;|\band\b", m.group(1)):
                    nombre = nombre.strip(" *.`<>")
                    if nombre and not nombre.startswith("{") and nombre in texto:
                        out.append(fila("participante", nombre, rol=rol))

    # --- contexto y problema -------------------------------------------------------
    ctx = buscar(texto, r"context|problem|background|motivation")
    if ctx:
        cuerpo = cuerpo_directo(texto, ctx[0], todas)
        ps = parrafos(cuerpo)
        if quiere("contexto") and ps:
            out.append(fila("contexto", ps[0]))
        if quiere("problema"):
            preg = re.findall(r"[^.!?\n]*\?", re.sub(r"\s*\n\s*", " ", cuerpo))
            if preg and preg[0].strip() in re.sub(r"\s+", " ", texto):
                out.append(fila("problema", preg[0].strip()))

    # --- criterios -------------------------------------------------------------------
    if quiere("driver"):
        for s in buscar(texto, r"decision drivers|^drivers|decision criteria|^forces"):
            for v in vinetas(texto[s[2]:s[3]]):
                out.append(fila("driver", v))

    # --- alternativas --------------------------------------------------------------------
    alts = []
    for s in buscar(texto, r"considered options|alternatives|options considered|solutions considered|^options$"):
        vs = vinetas(cuerpo_directo(texto, s, todas))
        if vs:
            alts += [re.sub(r"^\*\*(.+?)\*\*.*$", r"\1", v) if v.startswith("**") else v for v in vs]
        else:
            alts += [t for (l2, t, i2, _) in todas if s[2] < i2 <= s[3] and l2 == s[0] + 1]
    if not alts:
        alts = [t for (l2, t, _, _) in todas if re.match(r"option\s*\w+\s*[:\-–]", t, re.I)]
    alts = list(dict.fromkeys(a for a in alts if a in texto or a.replace(" ", "") in texto.replace(" ", "")))

    # --- decision y justificacion ------------------------------------------------------
    elegida = None
    m = re.search(r"^[\s*>-]*Chosen option:?\s*(.+)$", texto, re.I | re.M)
    if m:
        linea = m.group(0).strip(" *->")
        nombre = re.match(r'["“`*]*([^"”`*,]+)', m.group(1))
        elegida = nombre.group(1).strip() if nombre else None
        if quiere("decision"):
            out.append(fila("decision", linea, valor=(elegida or m.group(1)).strip()))
        b = re.search(r"\bbecause\b.*$", linea, re.I)
        if b and quiere("justificacion"):
            out.append(fila("justificacion", b.group(0).strip()))
    else:
        dec = buscar(texto, r"^decision( outcome)?$|^decision\b(?! drivers)|^outcomes?$")
        if dec and quiere("decision"):
            cuerpo = cuerpo_directo(texto, dec[0], todas)
            ps = parrafos(cuerpo) or vinetas(cuerpo)
            if ps:
                ev = primera_frase(ps[0])
                val = re.sub(r"^(we|the team) (will|have decided to|decided to|are going to)\s+", "", ev, flags=re.I)
                out.append(fila("decision", ev, valor=val[:1].upper() + val[1:].rstrip(".")))
    if quiere("justificacion"):
        for s in buscar(texto, r"^rationale|^justification|^reasoning"):
            ps = parrafos(cuerpo_directo(texto, s, todas))
            if ps:
                out.append(fila("justificacion", ps[0]))
                break
    if quiere("alternativa"):
        for a in alts:
            eleg = "1" if elegida and (elegida.lower() in a.lower() or a.lower() in elegida.lower()) else "0"
            out.append(fila("alternativa", a, elegida=eleg))

    # --- consecuencias -----------------------------------------------------------------------
    if quiere("consecuencia"):
        for s in buscar(texto, r"consequences|implications|^outcomes?$|risks|advantages|disadvantages|^positive$|^negative$"):
            t = s[1].lower()
            defecto = ("positiva" if re.search(r"positive|advantage|^pros", t) else
                       "negativa" if re.search(r"negative|disadvantage|risk|^cons", t) else "")
            cuerpo = cuerpo_directo(texto, s, todas)
            items = vinetas(cuerpo) or parrafos(cuerpo)
            for v in items:
                out.append(fila("consecuencia", v, polaridad=polaridad_de(v, defecto)))

    # --- argumentos por opcion ------------------------------------------------------------------
    if quiere("argumento"):
        for s in buscar(texto, r"pros and cons"):
            for (l2, tit, i2, f2) in todas:
                if s[2] < i2 <= s[3] and l2 == s[0] + 1:
                    ref = next((a for a in alts if a.lower() in tit.lower() or tit.lower() in a.lower()), tit)
                    for v in vinetas(texto[i2:f2]):
                        out.append(fila("argumento", v, polaridad=polaridad_de(v), alternativa=ref))

    # --- confirmacion ----------------------------------------------------------------------
    if quiere("confirmacion"):
        for s in buscar(texto, r"confirmation"):
            ps = parrafos(texto[s[2]:s[3]]) or vinetas(texto[s[2]:s[3]])
            if ps:
                out.append(fila("confirmacion", ps[0]))

    # --- informacion adicional y ADR relacionados --------------------------------------------
    ya = {(r["adr_nuevo"], r["adr_afectado"]) for r in relaciones}
    if quiere("info_adicional"):
        for s in buscar(texto, r"more information|^links|^references|^notes?$|related"):
            cuerpo = texto[s[2]:s[3]]
            for v in (vinetas(cuerpo) or parrafos(cuerpo)):
                out.append(fila("info_adicional", v))
    for m in re.finditer(r"\[[^\]]+\]\(\.?/?([^)#\s]+\.md)\)", texto):
        destino = m.group(1).split("/")[-1]
        if destino == archivo or destino not in repo_archivos:
            continue
        if (archivo, destino) in ya or (destino, archivo) in ya:
            continue
        linea = texto[texto.rfind("\n", 0, m.start()) + 1: texto.find("\n", m.end()) if texto.find("\n", m.end()) > 0 else len(texto)]
        if re.search(r"supers|superc|replac|amend", linea, re.I):
            continue
        ya.add((archivo, destino))
        out.append(fila("relacion", m.group(0), tipo_relacion="relatesTo", adr_nuevo=archivo,
                        adr_afectado=destino, nota="auto: posible relatesTo; borra si solo es una cita"))
    return out


def evidencia_relacion(texto, fila_rel):
    """Propone la evidencia de una relacion precargada: la frase de reemplazo/enmienda
    que menciona al otro ADR."""
    otro = fila_rel["adr_afectado"] if fila_rel["archivo"] == fila_rel["adr_nuevo"] else fila_rel["adr_nuevo"]
    num = re.match(r"^(?:adr[-_]?)?0*(\d+)", otro, re.I)
    claves = [re.escape(otro), re.escape(otro.removesuffix(".md"))]
    if num:
        claves.append(r"(?:ADR[-_ ]?|\[)0*%s\b" % num.group(1))
    patron = r"(supers\w*|superc\w*|replac\w*|amend\w*|obsolet\w*)[^\n]{0,80}?(%s)[^\n]*?(\)|\]|$)" % "|".join(claves)
    t = texto.replace("\r", "")
    m = re.search(patron, t, re.I | re.M)
    if m:
        ini = m.start() - 1 if m.start() > 0 and t[m.start() - 1] == "[" else m.start()
        return t[ini:m.end()].strip(" *")
    return ""
