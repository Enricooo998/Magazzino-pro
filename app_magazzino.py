import streamlit as st
from supabase import create_client, Client
import io
import csv
import re
import traceback
import pandas as pd
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import pdfplumber

# --- CONFIGURAZIONE DATABASE ---
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CHIAVE_SUPABASE = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL_SUPABASE, CHIAVE_SUPABASE)
st.set_page_config(page_title="Gestione Magazzino Pro", layout="wide", page_icon="📦")

BUCKET_FOTO = "foto-prodotti"
SOGLIA_SOTTOSCORTA = 5.0

# ==========================================
# 0. TEMA VISIVO "MATERICO" (legni, laminati, superfici)
# ==========================================
COLORI_CATALOGO = {
    "Prodotti": "#8C6A4E",              # cuoio / noce medio
    "Krion": "#A9A297",                 # solid surface, grigio pietra caldo
    "Adesivi": "#B8863B",               # resina ambrata
    "LAMINATI&HPL": "#6E4B34",          # laminato noce scuro
    "TRANCIATI NATURALI": "#C7A26B",    # tranciato rovere chiaro
    "Duropal": "#4A4038",               # laminato grafite/carbone
}

CSS_TEMA = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Zilla+Slab:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

html, body, [class*="css"], .stMarkdown, .stTextInput, .stSelectbox, .stButton {
    font-family: 'Inter', sans-serif;
}
h1, h2, h3, .stTitle {
    font-family: 'Zilla Slab', serif !important;
    letter-spacing: 0.2px;
    color: #3A2E22 !important;
}
[data-testid="stAppViewContainer"] {
    background-color: #E8DFCE;
}
[data-testid="stSidebar"] {
    background-color: #DCCFB4;
    border-right: 1px solid #B9A688;
}
[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace;
    color: #3A2E22;
}
.stButton>button, .stDownloadButton>button {
    background-color: #8C6A4E;
    color: #F4EEE1;
    border: 1px solid #6E4B34;
    border-radius: 4px;
    font-family: 'Inter', sans-serif;
    font-weight: 500;
}
.stButton>button:hover, .stDownloadButton>button:hover {
    background-color: #6E4B34;
    border-color: #3A2E22;
    color: #F4EEE1;
}
[data-testid="stDataFrame"] {
    font-family: 'IBM Plex Mono', monospace;
    border: 1px solid #B9A688;
    border-radius: 4px;
}
div[data-testid="stExpander"] {
    border: 1px solid #B9A688;
    border-radius: 4px;
    background-color: #DCCFB4;
}
hr {
    border-top: 1px solid #B9A688;
}
.etichetta-campione {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 3px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    font-weight: 600;
    color: #F4EEE1;
    letter-spacing: 0.5px;
    vertical-align: middle;
    margin-left: 8px;
}

/* ===================================================
   OTTIMIZZAZIONE PER TELEFONO E TABLET (max 768px)
   =================================================== */
@media (max-width: 768px) {
    /* Le colonne affiancate (tabella+export, movimento+foto, modifica+elimina)
       diventano impilate verticalmente invece di schiacciarsi in orizzontale */
    [data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
        margin-bottom: 0.75rem;
    }

    /* Pulsanti e download più grandi, comodi da toccare col dito (min. 48px) */
    .stButton>button, .stDownloadButton>button {
        min-height: 48px;
        font-size: 1rem;
        width: 100%;
    }

    /* Titoli più compatti per non occupare tutto lo schermo */
    h1, .stTitle { font-size: 1.4rem !important; }
    h2 { font-size: 1.15rem !important; }
    h3 { font-size: 1.05rem !important; }

    /* Metriche della dashboard leggibili senza andare a capo male */
    [data-testid="stMetricValue"] { font-size: 1.25rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem !important; }

    /* Etichetta campione su riga propria invece che stretta accanto al titolo */
    .etichetta-campione {
        display: block;
        width: fit-content;
        margin: 6px 0 0 0;
    }

    /* Testo più leggibile nella tabella dati su schermo piccolo */
    [data-testid="stDataFrame"] { font-size: 0.85rem; }

    /* Input numerici e caselle di testo con testo/pulsanti più grandi */
    input, .stNumberInput button {
        font-size: 1rem !important;
    }

    /* Scroll orizzontale più fluido nelle tabelle su touch screen */
    [data-testid="stDataFrame"] div {
        -webkit-overflow-scrolling: touch;
    }

    /* Meno spazio sprecato ai bordi su schermi stretti */
    [data-testid="stMainBlockContainer"] {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 1.5rem !important;
    }
}
</style>
"""
st.markdown(CSS_TEMA, unsafe_allow_html=True)


def etichetta_campione(nome_catalogo):
    """Piccola 'etichetta campione' colorata, come nei campionari di materiali, per riconoscere il catalogo a colpo d'occhio."""
    colore = COLORI_CATALOGO.get(nome_catalogo, "#8C6A4E")
    return f'<span class="etichetta-campione" style="background-color:{colore};">{nome_catalogo.upper()}</span>'

# ==========================================
# 1. SISTEMA DI ACCESSO (LOGIN MULTI-UTENTE)
# ==========================================
if 'autenticato' not in st.session_state:
    st.session_state.autenticato = False
    st.session_state.utente_loggato = ""

if not st.session_state.autenticato:
    st.title("🔐 Accesso Magazzino")
    st.info("Area riservata. Inserisci le tue credenziali.")

    utente_inserito = st.text_input("Nome Utente:")
    password_inserita = st.text_input("Password:", type="password")

    if st.button("Entra"):
        if utente_inserito in st.secrets["utenti"] and st.secrets["utenti"][utente_inserito] == password_inserita:
            st.session_state.autenticato = True
            st.session_state.utente_loggato = utente_inserito
            st.rerun()
        else:
            st.error("Nome utente o password errati. Riprova.")
    st.stop()

# Pulsante di logout in sidebar (sempre visibile una volta loggati)
with st.sidebar:
    col_user, col_logout = st.columns([0.7, 0.3])
    col_user.markdown(f"👤 **{st.session_state.utente_loggato}**")
    if col_logout.button("🚪 Esci"):
        st.session_state.autenticato = False
        st.session_state.utente_loggato = ""
        st.rerun()
    st.markdown("---")

# ==========================================
# 2. FUNZIONI DI SUPPORTO E MEMORIA
# ==========================================


def carica_foto_su_supabase(file_caricato, nome_articolo):
    """Carica la foto su Supabase Storage e restituisce (url_pubblico, percorso_file)."""
    estensione = file_caricato.name.split('.')[-1]
    percorso_file = f"foto_{nome_articolo.replace(' ', '_')}.{estensione}"
    supabase.storage.from_(BUCKET_FOTO).upload(
        percorso_file, file_caricato.getvalue(), {"upsert": "true"}
    )
    url = supabase.storage.from_(BUCKET_FOTO).get_public_url(percorso_file)
    return url, percorso_file


def elimina_foto_da_supabase(percorso_file):
    """Elimina il file foto dal bucket, se presente. Non blocca in caso di errore."""
    if not percorso_file:
        return
    try:
        supabase.storage.from_(BUCKET_FOTO).remove([percorso_file])
    except Exception:
        pass  # foto già assente o bucket non raggiungibile: non è bloccante


@st.cache_data(ttl=30, show_spinner=False)
def leggi_tabella(nome_tabella):
    """Legge l'intera tabella con cache di 30s per ridurre le chiamate a Supabase."""
    risposta = supabase.table(nome_tabella).select("*").order("Articolo").execute()
    return risposta.data


@st.cache_data(ttl=30, show_spinner=False)
def leggi_storico():
    risposta = supabase.table("Storico").select("*").order("created_at", desc=True).limit(50).execute()
    return risposta.data


def invalida_cache():
    """Da chiamare dopo ogni scrittura, cosi la prossima lettura prende dati freschi."""
    leggi_tabella.clear()
    leggi_storico.clear()


def movimenta_quantita_atomica(nome_tabella, articolo_id, delta):
    """
    Aggiorna la quantita in modo ATOMICO tramite la funzione RPC 'movimenta_quantita'
    (vedi script SQL allegato: setup_supabase.sql). Se la funzione RPC non esiste ancora
    sul progetto Supabase, ripiega sul vecchio metodo "leggi poi scrivi" (meno sicuro in
    caso di piu utenti contemporanei) e avvisa in console.
    """
    try:
        risultato = supabase.rpc(
            "movimenta_quantita",
            {"nome_tabella": nome_tabella, "riga_id": articolo_id, "delta": delta},
        ).execute()
        return float(risultato.data)
    except Exception:
        # Fallback non atomico: usare solo finche' la funzione RPC non e' stata creata su Supabase.
        riga = supabase.table(nome_tabella).select("Quantità").eq("id", articolo_id).execute().data
        qta_attuale = float(riga[0]["Quantità"]) if riga else 0.0
        nuova_qta = qta_attuale + delta
        supabase.table(nome_tabella).update({"Quantità": nuova_qta}).eq("id", articolo_id).execute()
        return nuova_qta


def to_float_it(valore):
    """Converte una stringa numerica (anche in formato italiano 1.234,56) in float."""
    s = str(valore).strip()
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def estrai_righe_da_ddt(file_bytes):
    """
    Estrae le righe prodotto (Codice, Descrizione, Um, Quantità) da un PDF di Documento
    di Trasporto. Poiché il layout varia da azienda ad azienda, cerca l'intestazione della
    tabella per PAROLE CHIAVE (non per posizione fissa) e poi allinea posizionalmente le
    celle non vuote di ogni riga a quelle dell'intestazione. Restituisce (righe, avvisi).
    """
    PAROLE_CODICE = ["codice", "articolo", "cod."]
    PAROLE_DESCRIZIONE = ["descrizione", "prodotto"]
    PAROLE_UM = ["um", "u.m.", "unit", "unità"]
    PAROLE_QUANTITA = ["quantit", "qta", "qty", "colli"]

    def trova_indice(compattate, parole_chiave):
        for i, val in enumerate(compattate):
            v = val.lower()
            if any(p in v for p in parole_chiave):
                return i
        return None

    righe_estratte = []
    avvisi = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for pagina in pdf.pages:
            for tabella in pagina.extract_tables():
                if not tabella or len(tabella) < 2:
                    continue
                celle_intestazione = [str(c).strip() if c else "" for c in tabella[0]]
                compattate_intest = [c for c in celle_intestazione if c]
                if not compattate_intest:
                    continue

                idx_codice = trova_indice(compattate_intest, PAROLE_CODICE)
                idx_descrizione = trova_indice(compattate_intest, PAROLE_DESCRIZIONE)
                idx_um = trova_indice(compattate_intest, PAROLE_UM)
                idx_quantita = trova_indice(compattate_intest, PAROLE_QUANTITA)

                # Consideriamo "tabella prodotti" solo quella con almeno Codice e Quantità riconoscibili
                if idx_codice is None or idx_quantita is None:
                    continue

                for riga in tabella[1:]:
                    celle = [str(c).strip() if c else "" for c in riga]
                    compattate_riga = [c for c in celle if c]
                    if len(compattate_riga) < len(compattate_intest):
                        if compattate_riga:
                            avvisi.append(f"Riga incompleta ignorata: {compattate_riga}")
                        continue

                    codice_esterno = compattate_riga[idx_codice]
                    testo_descr = compattate_riga[idx_descrizione] if idx_descrizione is not None else ""
                    um_val = compattate_riga[idx_um] if idx_um is not None else ""
                    qta_val = compattate_riga[idx_quantita]

                    # In molti DDT la cella "Descrizione" contiene in realtà, sulla prima riga,
                    # il codice articolo interno del fornitore, e a seguire la descrizione vera.
                    righe_testo = [r.strip() for r in testo_descr.split("\n") if r.strip()]
                    codice_interno_guess = righe_testo[0] if righe_testo else codice_esterno
                    descrizione_pulita = " ".join(righe_testo[1:]) if len(righe_testo) > 1 else testo_descr

                    righe_estratte.append({
                        "Codice_DDT": codice_esterno,
                        "Codice_Articolo": codice_interno_guess,
                        "Descrizione": descrizione_pulita,
                        "Um": um_val,
                        "Quantità": to_float_it(qta_val),
                    })

    return righe_estratte, avvisi


def trova_corrispondenza_codice(codice_ddt, mappa_catalogo):
    """
    Cerca il codice del DDT nel catalogo, con fallback progressivi per gestire
    prefissi/suffissi diversi tra fornitore e catalogo (es. 'DTC.' davanti al codice):
    1) corrispondenza esatta
    2) corrispondenza ignorando punti/spazi/trattini e maiuscole/minuscole
    3) il codice di catalogo TERMINA con il codice del DDT (gestisce prefissi tipo 'DTC.')
    Restituisce il codice esatto trovato nel catalogo, oppure None se non c'è un match univoco.
    """
    if codice_ddt in mappa_catalogo:
        return codice_ddt

    codice_norm = re.sub(r"[^A-Za-z0-9]", "", codice_ddt).upper()
    if not codice_norm:
        return None

    candidati_suffisso = []
    for articolo_catalogo in mappa_catalogo:
        if not articolo_catalogo:  # salta gli articoli senza nome (Articolo = NULL/vuoto)
            continue
        art_norm = re.sub(r"[^A-Za-z0-9]", "", articolo_catalogo).upper()
        if art_norm == codice_norm:
            return articolo_catalogo
        if art_norm.endswith(codice_norm):
            candidati_suffisso.append(articolo_catalogo)

    if len(candidati_suffisso) == 1:
        return candidati_suffisso[0]
    return None  # nessun match univoco: lasciamo decidere all'utente nella tabella modificabile


def consolida_righe_ddt(righe):
    """Somma le quantità di righe con lo stesso codice articolo (capita che un DDT ripeta la stessa riga più volte)."""
    consolidato = {}
    ordine = []
    for r in righe:
        chiave = r["Codice_Articolo"]
        if chiave not in consolidato:
            consolidato[chiave] = dict(r)
            ordine.append(chiave)
        else:
            consolidato[chiave]["Quantità"] += r["Quantità"]
    return [consolidato[k] for k in ordine]


def genera_pdf_report(titolo, sottotitolo, righe, includi_soglia=True, includi_prezzo=False):
    """Genera un report PDF (tabella articoli) e restituisce un buffer in memoria pronto per il download."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                             leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    stili = getSampleStyleSheet()
    stile_titolo = ParagraphStyle(
        "TitoloReport", parent=stili["Title"],
        textColor=colors.HexColor("#3A2E22"), fontName="Helvetica-Bold", fontSize=16,
    )
    stile_sotto = ParagraphStyle(
        "SottoReport", parent=stili["Normal"],
        textColor=colors.HexColor("#6E4B34"), fontSize=9,
    )

    elementi = [
        Paragraph(titolo, stile_titolo),
        Paragraph(sottotitolo, stile_sotto),
        Spacer(1, 14),
    ]

    intestazione = ["Articolo", "Descrizione", "Um", "Quantità"]
    if includi_soglia:
        intestazione.append("Soglia sottoscorta")
    if includi_prezzo:
        intestazione += ["Prezzo (€)", "Valore (€)"]
    dati_tabella = [intestazione]

    valore_totale = 0.0
    for r in righe:
        try:
            qta = float(r.get('Quantità', 0))
        except (ValueError, TypeError):
            qta = 0.0
        riga = [
            str(r.get("Articolo", "")),
            str(r.get("Descrizione", "") or ""),
            str(r.get("Um", "") or ""),
            f"{qta:g}",
        ]
        if includi_soglia:
            try:
                soglia_fmt = f"{float(r.get('soglia_minima', SOGLIA_SOTTOSCORTA)):g}"
            except (ValueError, TypeError):
                soglia_fmt = str(SOGLIA_SOTTOSCORTA)
            riga.append(soglia_fmt)
        if includi_prezzo:
            try:
                prezzo = float(r.get("Prezzo", 0.0))
            except (ValueError, TypeError):
                prezzo = 0.0
            valore_riga = qta * prezzo
            valore_totale += valore_riga
            riga += [f"{prezzo:,.2f}", f"{valore_riga:,.2f}"]
        dati_tabella.append(riga)

    n_colonne = len(intestazione)
    if len(dati_tabella) == 1:
        dati_tabella.append(["Nessun articolo"] + [""] * (n_colonne - 1))
    elif includi_prezzo:
        riga_totale = [""] * (n_colonne - 2) + ["Totale:", f"€ {valore_totale:,.2f}"]
        dati_tabella.append(riga_totale)

    tabella = Table(dati_tabella, repeatRows=1)
    tabella.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8C6A4E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#F4EEE1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F4EEE1"), colors.HexColor("#E8DFCE")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9A688")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elementi.append(tabella)

    doc.build(elementi)
    buffer.seek(0)
    return buffer


if 'ultimo_articolo' not in st.session_state:
    st.session_state.ultimo_articolo = None
if 'messaggio_successo' not in st.session_state:
    st.session_state.messaggio_successo = None
if 'dati_annulla' not in st.session_state:
    st.session_state.dati_annulla = None

# ==========================================
# 3. BARRA LATERALE: SELEZIONE CATALOGO E NUOVO ARTICOLO
# ==========================================
with st.sidebar:
    st.header("📂 Cambia Catalogo")
    lista_cataloghi = ["Prodotti", "Krion", "Adesivi", "LAMINATI&HPL", "TRANCIATI NATURALI", "Duropal"]
    NOME_TABELLA = st.selectbox("Seleziona il magazzino da gestire:", lista_cataloghi)
    st.markdown(etichetta_campione(NOME_TABELLA), unsafe_allow_html=True)

    st.markdown("---")

    st.header(f"➕ Nuovo in {NOME_TABELLA}")
    with st.form("form_nuovo_articolo", clear_on_submit=True):
        nuovo_codice = st.text_input("Articolo (Codice/Nome) *obbligatorio")
        nuova_descrizione = st.text_input("Descrizione")
        nuova_um = st.text_input("Unità di misura")
        nuova_quantita = st.number_input("Quantità Iniziale", min_value=0.0, value=0.0, step=1.0)
        nuova_soglia = st.number_input(
            "Soglia sottoscorta (avviso sotto questo valore)", min_value=0.0, value=SOGLIA_SOTTOSCORTA, step=1.0
        )
        nuovo_prezzo = st.number_input("Prezzo unitario (€)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        foto_caricata = st.file_uploader("Carica foto prodotto (Opzionale)", type=['png', 'jpg', 'jpeg'])

        if st.form_submit_button("Salva Nuovo Articolo"):
            codice_pulito = nuovo_codice.strip()
            if codice_pulito == "":
                st.error("Devi inserire almeno il nome dell'articolo!")
            else:
                try:
                    # Controllo duplicati lato applicazione (in aggiunta al vincolo
                    # UNIQUE consigliato a livello di database, vedi setup_supabase.sql)
                    esistente = (
                        supabase.table(NOME_TABELLA)
                        .select("id")
                        .eq("Articolo", codice_pulito)
                        .execute()
                        .data
                    )
                    if esistente:
                        st.error(f"Esiste già un articolo chiamato '{codice_pulito}' in questo catalogo!")
                    else:
                        url_foto, percorso_foto = (None, None)
                        if foto_caricata:
                            url_foto, percorso_foto = carica_foto_su_supabase(foto_caricata, codice_pulito)

                        nuovi_dati = {
                            "Articolo": codice_pulito,
                            "Descrizione": nuova_descrizione.strip(),
                            "Um": nuova_um.strip(),
                            "Quantità": nuova_quantita,
                            "soglia_minima": nuova_soglia,
                            "Prezzo": nuovo_prezzo,
                            "immagine": url_foto,
                            "foto_path": percorso_foto,
                        }
                        supabase.table(NOME_TABELLA).insert(nuovi_dati).execute()
                        invalida_cache()
                        st.session_state.messaggio_successo = f"🎉 Nuovo articolo '{codice_pulito}' creato nel catalogo {NOME_TABELLA}!"
                        st.rerun()
                except Exception as e:
                    st.error(f"Errore durante il salvataggio: {e}")

    st.markdown("---")

    with st.expander(f"📤 Importa da CSV in {NOME_TABELLA}"):
        st.caption("Il file deve avere le colonne: Articolo, Descrizione, Um, Quantità (Soglia e Prezzo opzionali).")
        file_csv = st.file_uploader("Scegli file CSV", type=["csv"], key="import_csv")
        if file_csv is not None:
            try:
                testo_csv = file_csv.getvalue().decode("utf-8-sig")
                lettore = csv.DictReader(io.StringIO(testo_csv))
                righe_csv = list(lettore)
                colonne_richieste = {"Articolo"}
                if not righe_csv or not colonne_richieste.issubset(set(lettore.fieldnames or [])):
                    st.error("Il CSV deve contenere almeno la colonna 'Articolo'.")
                else:
                    st.write(f"Trovate **{len(righe_csv)}** righe nel file. Anteprima:")
                    st.dataframe(righe_csv[:5], use_container_width=True)

                    if st.button("✅ Conferma import", key="conferma_import_csv"):
                        esistenti = {
                            r["Articolo"] for r in supabase.table(NOME_TABELLA).select("Articolo").execute().data
                        }
                        da_inserire = []
                        saltati = []
                        for riga in righe_csv:
                            nome_art = str(riga.get("Articolo", "")).strip()
                            if not nome_art:
                                continue
                            if nome_art in esistenti:
                                saltati.append(nome_art)
                                continue
                            try:
                                qta = float(str(riga.get("Quantità", "0")).replace(",", "."))
                            except ValueError:
                                qta = 0.0
                            try:
                                soglia = float(str(riga.get("Soglia", SOGLIA_SOTTOSCORTA)).replace(",", "."))
                            except ValueError:
                                soglia = SOGLIA_SOTTOSCORTA
                            try:
                                prezzo = float(str(riga.get("Prezzo", "0")).replace(",", "."))
                            except ValueError:
                                prezzo = 0.0
                            da_inserire.append({
                                "Articolo": nome_art,
                                "Descrizione": str(riga.get("Descrizione", "")).strip(),
                                "Um": str(riga.get("Um", "")).strip(),
                                "Quantità": qta,
                                "soglia_minima": soglia,
                                "Prezzo": prezzo,
                            })
                            esistenti.add(nome_art)  # evita doppioni interni allo stesso file

                        if da_inserire:
                            supabase.table(NOME_TABELLA).insert(da_inserire).execute()
                            invalida_cache()
                        msg = f"✅ Importate {len(da_inserire)} righe."
                        if saltati:
                            msg += f" Saltate {len(saltati)} righe già esistenti (nomi duplicati)."
                        st.session_state.messaggio_successo = msg
                        st.rerun()
            except Exception as e:
                st.error(f"Errore nella lettura del CSV: {e}")

    with st.expander(f"💶 Aggiorna prezzi da listino (CSV) in {NOME_TABELLA}"):
        st.caption(
            "Aggiorna SOLO il prezzo degli articoli già esistenti in questo catalogo (li trova per nome articolo). "
            "Il file deve avere almeno le colonne: Articolo, Prezzo."
        )
        file_listino = st.file_uploader("Scegli file CSV del listino", type=["csv"], key="import_listino")
        if file_listino is not None:
            try:
                testo_listino = file_listino.getvalue().decode("utf-8-sig")
                lettore_listino = csv.DictReader(io.StringIO(testo_listino))
                righe_listino = list(lettore_listino)
                colonne_richieste_listino = {"Articolo", "Prezzo"}
                if not righe_listino or not colonne_richieste_listino.issubset(set(lettore_listino.fieldnames or [])):
                    st.error("Il CSV deve contenere le colonne 'Articolo' e 'Prezzo'.")
                else:
                    st.write(f"Trovate **{len(righe_listino)}** righe nel listino. Anteprima:")
                    st.dataframe(righe_listino[:5], use_container_width=True)

                    if st.button("💾 Aggiorna prezzi", key="conferma_import_listino"):
                        catalogo_attuale = {
                            r["Articolo"]: r["id"]
                            for r in supabase.table(NOME_TABELLA).select("id, Articolo").execute().data
                        }
                        aggiornati = []
                        non_trovati = []
                        for riga in righe_listino:
                            nome_art = str(riga.get("Articolo", "")).strip()
                            if not nome_art:
                                continue
                            try:
                                nuovo_prezzo_listino = float(str(riga.get("Prezzo", "")).replace(",", "."))
                            except ValueError:
                                non_trovati.append(f"{nome_art} (prezzo non valido)")
                                continue
                            id_trovato = catalogo_attuale.get(nome_art)
                            if id_trovato is None:
                                non_trovati.append(nome_art)
                                continue
                            supabase.table(NOME_TABELLA).update({"Prezzo": nuovo_prezzo_listino}).eq("id", id_trovato).execute()
                            aggiornati.append(nome_art)

                        invalida_cache()
                        msg = f"✅ Prezzi aggiornati per {len(aggiornati)} articoli."
                        if non_trovati:
                            msg += f" Non trovati/non validi: {len(non_trovati)} → {', '.join(non_trovati[:10])}"
                            if len(non_trovati) > 10:
                                msg += "..."
                        st.session_state.messaggio_successo = msg
                        st.rerun()
            except Exception as e:
                st.error(f"Errore nella lettura del listino: {e}")

# ==========================================
# 4. DASHBOARD RIEPILOGATIVA (tutti i cataloghi)
# ==========================================
with st.expander("📊 Riepilogo generale su tutti i cataloghi", expanded=False):
    dati_riepilogo = []
    for catalogo in lista_cataloghi:
        try:
            righe_catalogo = leggi_tabella(catalogo)
        except Exception:
            righe_catalogo = []
        righe_valide = [r for r in (righe_catalogo or []) if r.get("Articolo")]

        n_sottoscorta = 0
        qta_totale = 0.0
        for r in righe_valide:
            try:
                qta = float(str(r.get("Quantità", 0)).replace(",", "."))
            except ValueError:
                qta = 0.0
            try:
                soglia = float(str(r.get("soglia_minima", SOGLIA_SOTTOSCORTA)).replace(",", ".")) if r.get("soglia_minima") not in (None, "") else SOGLIA_SOTTOSCORTA
            except ValueError:
                soglia = SOGLIA_SOTTOSCORTA
            qta_totale += qta
            if qta <= soglia:
                n_sottoscorta += 1

        dati_riepilogo.append({
            "Catalogo": catalogo,
            "N. articoli": len(righe_valide),
            "Quantità totale": qta_totale,
            "In sottoscorta": n_sottoscorta,
        })

    df_riepilogo = pd.DataFrame(dati_riepilogo).set_index("Catalogo")

    col_r1, col_r2, col_r3 = st.columns(3)
    col_r1.metric("Cataloghi totali", len(lista_cataloghi))
    col_r2.metric("Articoli totali", int(df_riepilogo["N. articoli"].sum()))
    col_r3.metric("Articoli in sottoscorta (tutti i cataloghi)", int(df_riepilogo["In sottoscorta"].sum()))

    st.caption("Numero di articoli per catalogo")
    st.bar_chart(df_riepilogo["N. articoli"])

    st.caption("Quantità totale in giacenza per catalogo")
    st.bar_chart(df_riepilogo["Quantità totale"])

    st.dataframe(df_riepilogo, use_container_width=True)

st.markdown(f"# 📦 Magazzino: {NOME_TABELLA} {etichetta_campione(NOME_TABELLA)}", unsafe_allow_html=True)

try:
    dati_grezzi = leggi_tabella(NOME_TABELLA)

    if dati_grezzi:
        dati = [r for r in dati_grezzi if r.get("Articolo") and str(r.get("Articolo")).strip() != ""]

        # --- SCUDO: SUPPORTO AI DECIMALI (anche con virgola italiana) ---
        for r in dati:
            qta_grezza = r.get("Quantità")
            if qta_grezza is None or str(qta_grezza).strip() == "":
                r["Quantità"] = 0.0
            else:
                try:
                    qta_pulita = str(qta_grezza).replace(',', '.')
                    r["Quantità"] = float(qta_pulita)
                except ValueError:
                    r["Quantità"] = 0.0

            prezzo_grezzo = r.get("Prezzo")
            if prezzo_grezzo is None or str(prezzo_grezzo).strip() == "":
                r["Prezzo"] = 0.0
            else:
                try:
                    r["Prezzo"] = float(str(prezzo_grezzo).replace(',', '.'))
                except ValueError:
                    r["Prezzo"] = 0.0

        # --- ALLARME SOTTOSCORTA (soglia personalizzabile per articolo, con fallback a quella globale) ---
        for r in dati:
            soglia_grezza = r.get("soglia_minima")
            try:
                r["soglia_minima"] = float(str(soglia_grezza).replace(',', '.')) if soglia_grezza not in (None, "") else SOGLIA_SOTTOSCORTA
            except ValueError:
                r["soglia_minima"] = SOGLIA_SOTTOSCORTA

        sottoscorta = [riga for riga in dati if riga["Quantità"] <= riga["soglia_minima"]]
        if sottoscorta:
            st.error(f"🚨 **ALLARME SOTTOSCORTA in {NOME_TABELLA}:** Ci sono {len(sottoscorta)} articoli in esaurimento!")
            with st.expander("👀 Clicca qui per vedere gli articoli in sottoscorta"):
                for art in sottoscorta:
                    st.warning(f"⚠️ **{art['Articolo']}** - Quantità residua: **{art['Quantità']}**")

        st.markdown("---")

        valore_totale_catalogo = sum(r["Quantità"] * r.get("Prezzo", 0.0) for r in dati)
        st.metric(f"💶 Valore totale magazzino ({NOME_TABELLA})", f"€ {valore_totale_catalogo:,.2f}")

        with st.expander(f"📈 Grafico quantità in {NOME_TABELLA}"):
            df_grafico = pd.DataFrame(dati)[["Articolo", "Quantità"]].set_index("Articolo")
            st.bar_chart(df_grafico)

        st.markdown("---")

        testo_ricerca = st.text_input(f"🔎 Filtra in {NOME_TABELLA} (Cerca per nome o descrizione):", "")
        if testo_ricerca:
            dati_filtrati = [
                r for r in dati
                if testo_ricerca.lower() in str(r.get("Articolo", "")).lower()
                or testo_ricerca.lower() in str(r.get("Descrizione", "")).lower()
            ]
        else:
            dati_filtrati = dati

        col_tab, col_btn = st.columns([0.8, 0.2])
        with col_tab:
            st.dataframe(
                dati_filtrati,
                column_order=("Articolo", "Descrizione", "Um", "Quantità", "Prezzo", "immagine"),
                column_config={
                    "immagine": st.column_config.ImageColumn("Foto"),
                    "Prezzo": st.column_config.NumberColumn("Prezzo (€)", format="€ %.2f"),
                    "created_at": None,
                    "id": None,
                    "foto_path": None,
                    "soglia_minima": None,
                },
                use_container_width=True
            )
        with col_btn:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["Articolo", "Descrizione", "Um", "Quantità", "Prezzo", "Link_Foto"])
            writer.writeheader()
            dati_puliti = [
                {
                    "Articolo": r.get("Articolo"),
                    "Descrizione": r.get("Descrizione"),
                    "Um": r.get("Um"),
                    "Quantità": r.get("Quantità"),
                    "Prezzo": r.get("Prezzo"),
                    "Link_Foto": r.get("immagine"),
                }
                for r in dati_filtrati
            ]
            writer.writerows(dati_puliti)
            st.download_button(
                f"📥 Scarica CSV ({NOME_TABELLA})",
                data=output.getvalue().encode('utf-8'),
                file_name=f"magazzino_{NOME_TABELLA}.csv",
                mime="text/csv",
                use_container_width=True
            )

        st.markdown("---")

        with st.expander("📄 Genera Report PDF"):
            tipo_report = st.radio(
                "Tipo di report:",
                [
                    "Catalogo corrente completo",
                    "Solo sottoscorta (catalogo corrente)",
                    "Solo sottoscorta (tutti i cataloghi)",
                    "Selezione personalizzata articoli",
                ],
                key="tipo_report_pdf",
            )

            righe_report = []
            titolo_report = ""
            sottotitolo_report = f"Generato da {st.session_state.utente_loggato} il {datetime.now().strftime('%d/%m/%Y %H:%M')}"

            if tipo_report == "Catalogo corrente completo":
                righe_report = dati
                titolo_report = f"Report Magazzino — {NOME_TABELLA}"

            elif tipo_report == "Solo sottoscorta (catalogo corrente)":
                righe_report = sottoscorta
                titolo_report = f"Report Sottoscorta — {NOME_TABELLA}"

            elif tipo_report == "Solo sottoscorta (tutti i cataloghi)":
                for catalogo in lista_cataloghi:
                    try:
                        righe_cat = leggi_tabella(catalogo) or []
                    except Exception:
                        righe_cat = []
                    for r in righe_cat:
                        if not r.get("Articolo"):
                            continue
                        try:
                            qta = float(str(r.get("Quantità", 0)).replace(",", "."))
                        except ValueError:
                            qta = 0.0
                        soglia_grezza = r.get("soglia_minima")
                        try:
                            soglia = float(str(soglia_grezza).replace(",", ".")) if soglia_grezza not in (None, "") else SOGLIA_SOTTOSCORTA
                        except ValueError:
                            soglia = SOGLIA_SOTTOSCORTA
                        if qta <= soglia:
                            riga_copia = dict(r)
                            riga_copia["Quantità"] = qta
                            riga_copia["soglia_minima"] = soglia
                            riga_copia["Articolo"] = f"[{catalogo}] {r.get('Articolo')}"
                            righe_report.append(riga_copia)
                titolo_report = "Report Sottoscorta — Tutti i Cataloghi"

            else:  # Selezione personalizzata articoli
                mappa_articoli_pdf = {f"{r['Articolo']} - {r.get('Descrizione', '')}": r for r in dati}
                selezione = st.multiselect(
                    "Seleziona gli articoli da includere nel report:",
                    options=list(mappa_articoli_pdf.keys()),
                    key="selezione_pdf",
                )
                righe_report = [mappa_articoli_pdf[s] for s in selezione]
                titolo_report = f"Report Selezione Articoli — {NOME_TABELLA}"

            includi_prezzo_pdf = st.checkbox("💶 Includi prezzi e valore totale nel report", value=False, key="includi_prezzo_pdf")

            if st.button("🖨️ Genera PDF", key="genera_pdf_btn", use_container_width=True):
                if not righe_report:
                    st.warning("Nessun articolo da includere nel report: controlla la selezione.")
                else:
                    buffer_pdf = genera_pdf_report(titolo_report, sottotitolo_report, righe_report, includi_prezzo=includi_prezzo_pdf)
                    st.session_state.pdf_pronto = buffer_pdf.getvalue()
                    st.session_state.pdf_nome_file = f"report_{NOME_TABELLA}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"

            if st.session_state.get("pdf_pronto"):
                st.download_button(
                    "📥 Scarica Report PDF",
                    data=st.session_state.pdf_pronto,
                    file_name=st.session_state.pdf_nome_file,
                    mime="application/pdf",
                    use_container_width=True,
                    key="download_pdf_btn",
                )

        st.markdown("---")

        with st.expander("🚚 Registra Documento di Trasporto (DDT) — carico da PDF"):
            st.caption(
                f"Carica il PDF del DDT del fornitore: verranno cercate le righe prodotto (codice, descrizione, "
                f"UM, quantità) e sommate eventuali righe ripetute. Il carico verrà applicato al catalogo "
                f"**{NOME_TABELLA}**, corrispondente ai codici che combaciano con l'Articolo già presente. "
                f"Formati diversi da fornitore a fornitore sono supportati solo in parte automaticamente: "
                f"controlla sempre la tabella prima di confermare."
            )
            file_ddt = st.file_uploader("Scegli il PDF del DDT", type=["pdf"], key="upload_ddt")

            if file_ddt is not None:
                chiave_file = f"{file_ddt.name}_{file_ddt.size}"
                if st.session_state.get("ddt_chiave_file") != chiave_file:
                    try:
                        righe_ddt, avvisi_ddt = estrai_righe_da_ddt(file_ddt.getvalue())
                        righe_consolidate = consolida_righe_ddt(righe_ddt)
                    except Exception as e:
                        righe_consolidate = []
                        avvisi_ddt = [f"Errore durante la lettura del PDF: {e}"]

                    # Tentativo di match automatico intelligente contro il catalogo corrente:
                    # in un blocco SEPARATO, così se questo fallisce non perdiamo comunque
                    # le righe già estratte correttamente dal PDF.
                    if righe_consolidate:
                        try:
                            mappa_catalogo_match = {
                                r["Articolo"]: r["id"]
                                for r in supabase.table(NOME_TABELLA).select("id, Articolo").execute().data
                            }
                            for r in righe_consolidate:
                                match_trovato = trova_corrispondenza_codice(r["Codice_Articolo"], mappa_catalogo_match)
                                if match_trovato:
                                    r["Codice_Articolo"] = match_trovato
                                    r["Trovato"] = "✅"
                                else:
                                    r["Trovato"] = "❓"
                        except Exception as e:
                            avvisi_ddt.append(f"Match automatico non riuscito (le righe restano comunque modificabili a mano): {e}")
                            for r in righe_consolidate:
                                r["Trovato"] = "❓"

                    st.session_state.ddt_righe = righe_consolidate
                    st.session_state.ddt_avvisi = avvisi_ddt
                    st.session_state.ddt_chiave_file = chiave_file

                if not st.session_state.get("ddt_righe"):
                    st.warning(
                        "Non ho trovato una tabella prodotti riconoscibile in questo PDF "
                        "(nessuna colonna Codice/Quantità individuata). Il formato di questo fornitore "
                        "potrebbe essere troppo diverso — puoi comunque inserire i dati manualmente o via CSV."
                    )
                    if st.session_state.get("ddt_avvisi"):
                        for a in st.session_state.ddt_avvisi:
                            st.caption(f"⚠️ {a}")
                else:
                    if st.session_state.get("ddt_avvisi"):
                        with st.expander("⚠️ Avvisi durante l'estrazione"):
                            for a in st.session_state.ddt_avvisi:
                                st.caption(a)

                    st.write(
                        f"Trovate **{len(st.session_state.ddt_righe)}** righe prodotto (dopo aver unito i duplicati "
                        f"nello stesso documento). Correggi il **Codice articolo** dove non combacia col tuo catalogo "
                        f"prima di confermare:"
                    )

                    col_pref1, col_pref2 = st.columns([0.7, 0.3])
                    with col_pref1:
                        prefisso_ddt = st.text_input(
                            "Prefisso da aggiungere a tutti i codici (es. 'DTC.' se il tuo catalogo li salva così)",
                            value="", key="prefisso_ddt",
                        )
                    with col_pref2:
                        st.write("")
                        st.write("")
                        if st.button("🔧 Applica prefisso a tutte le righe", use_container_width=True):
                            if prefisso_ddt:
                                mappa_catalogo_match = {
                                    r["Articolo"]: r["id"]
                                    for r in supabase.table(NOME_TABELLA).select("id, Articolo").execute().data
                                }
                                for r in st.session_state.ddt_righe:
                                    if r.get("Trovato") != "✅" and not r["Codice_Articolo"].startswith(prefisso_ddt):
                                        r["Codice_Articolo"] = prefisso_ddt + r["Codice_Articolo"]
                                    # ricalcolo comunque il Trovato, anche se il prefisso c'era già
                                    r["Trovato"] = "✅" if r["Codice_Articolo"] in mappa_catalogo_match else "❓"
                                st.rerun()

                    df_ddt = pd.DataFrame(st.session_state.ddt_righe)[["Trovato", "Codice_Articolo", "Descrizione", "Um", "Quantità"]]
                    df_modificato = st.data_editor(
                        df_ddt,
                        key="editor_ddt",
                        use_container_width=True,
                        column_config={
                            "Trovato": st.column_config.TextColumn("Trovato?", disabled=True, width="small"),
                            "Codice_Articolo": st.column_config.TextColumn("Codice articolo (per il matching)"),
                            "Descrizione": st.column_config.TextColumn("Descrizione (dal DDT)", disabled=True),
                            "Um": st.column_config.TextColumn("Um"),
                            "Quantità": st.column_config.NumberColumn("Quantità da caricare", min_value=0.0),
                        },
                    )

                    crea_nuovi_da_ddt = st.checkbox(
                        "Se un codice non viene trovato nel catalogo, crealo come nuovo articolo con questa quantità",
                        value=False, key="ddt_crea_nuovi",
                    )

                    if st.button("📦 Conferma carico da DDT", key="conferma_ddt", use_container_width=True):
                        catalogo_attuale = {
                            r["Articolo"]: r["id"]
                            for r in supabase.table(NOME_TABELLA).select("id, Articolo").execute().data
                        }
                        aggiornati = []
                        creati = []
                        non_trovati = []

                        for _, riga in df_modificato.iterrows():
                            codice = str(riga["Codice_Articolo"]).strip()
                            try:
                                qta = float(riga["Quantità"])
                            except (ValueError, TypeError):
                                qta = 0.0
                            if not codice or qta <= 0:
                                continue

                            id_trovato = catalogo_attuale.get(codice)
                            if id_trovato is not None:
                                nuova_qta = movimenta_quantita_atomica(NOME_TABELLA, id_trovato, qta)
                                supabase.table("Storico").insert({
                                    "Utente": st.session_state.utente_loggato,
                                    "Catalogo": NOME_TABELLA,
                                    "Articolo": codice,
                                    "Operazione": "CARICO (DDT)",
                                    "Quantita": qta,
                                }).execute()
                                aggiornati.append(f"{codice} (+{qta:g} → {nuova_qta:g})")
                            elif crea_nuovi_da_ddt:
                                supabase.table(NOME_TABELLA).insert({
                                    "Articolo": codice,
                                    "Descrizione": str(riga.get("Descrizione", ""))[:500],
                                    "Um": str(riga.get("Um", "")),
                                    "Quantità": qta,
                                }).execute()
                                creati.append(codice)
                            else:
                                non_trovati.append(codice)

                        invalida_cache()
                        msg = f"✅ Aggiornati {len(aggiornati)} articoli esistenti da DDT."
                        if creati:
                            msg += f" Creati {len(creati)} nuovi articoli."
                        if non_trovati:
                            msg += f" Non trovati (saltati): {', '.join(non_trovati)}."
                        st.session_state.messaggio_successo = msg
                        st.session_state.ddt_righe = None
                        st.session_state.ddt_chiave_file = None
                        st.rerun()

        if st.session_state.messaggio_successo:
            st.success(st.session_state.messaggio_successo)
            st.session_state.messaggio_successo = None  # evita che resti visibile per sempre

        if st.session_state.dati_annulla:
            if st.button("⏪ Annulla ultima operazione di carico/scarico"):
                info_annulla = st.session_state.dati_annulla
                supabase.table(NOME_TABELLA).update(
                    {"Quantità": info_annulla["quantita_precedente"]}
                ).eq("id", info_annulla["id"]).execute()
                invalida_cache()
                st.session_state.messaggio_successo = f"⏪ Annullato! '{info_annulla['articolo']}' è tornato a {info_annulla['quantita_precedente']}."
                st.session_state.dati_annulla = None
                st.rerun()

        st.markdown("---")

        col_movimenti, col_foto = st.columns([0.6, 0.4])

        with col_movimenti:
            st.subheader(f"🔄 Movimentazione in {NOME_TABELLA}")
            mappa_opzioni = {f"{r['Articolo']} - {r.get('Descrizione', '')}": r for r in dati}
            lista_opzioni = list(mappa_opzioni.keys())

            indice_sel = 0
            if st.session_state.ultimo_articolo:
                for i, t in enumerate(lista_opzioni):
                    if mappa_opzioni[t]['Articolo'] == st.session_state.ultimo_articolo:
                        indice_sel = i
                        break

            testo_selezionato = st.selectbox("Seleziona l'articolo:", lista_opzioni, index=indice_sel)
            articolo_dati = mappa_opzioni[testo_selezionato]
            art_id = articolo_dati["id"]
            art_nome = articolo_dati['Articolo']
            qta_attuale = float(articolo_dati.get("Quantità", 0.0))

            st.info(f"📦 **Disponibili in magazzino:** {qta_attuale}")

            quantita_mov = st.number_input("Quantità da movimentare:", min_value=0.01, value=1.00, step=1.00)
            c1, c2 = st.columns(2)

            if c1.button("➕ CARICO", use_container_width=True):
                nuova_qta = movimenta_quantita_atomica(NOME_TABELLA, art_id, quantita_mov)

                dati_storico = {
                    "Utente": st.session_state.utente_loggato,
                    "Catalogo": NOME_TABELLA,
                    "Articolo": art_nome,
                    "Operazione": "CARICO",
                    "Quantita": quantita_mov,
                }
                supabase.table("Storico").insert(dati_storico).execute()
                invalida_cache()

                st.session_state.ultimo_articolo = art_nome
                st.session_state.dati_annulla = {"id": art_id, "articolo": art_nome, "quantita_precedente": qta_attuale}
                st.session_state.messaggio_successo = f"✅ Aggiunti {quantita_mov} a {art_nome}. Nuova quantità: {nuova_qta}."
                st.rerun()

            if c2.button("➖ SCARICO", use_container_width=True):
                if qta_attuale >= quantita_mov:
                    nuova_qta = movimenta_quantita_atomica(NOME_TABELLA, art_id, -quantita_mov)

                    dati_storico = {
                        "Utente": st.session_state.utente_loggato,
                        "Catalogo": NOME_TABELLA,
                        "Articolo": art_nome,
                        "Operazione": "SCARICO",
                        "Quantita": quantita_mov,
                    }
                    supabase.table("Storico").insert(dati_storico).execute()
                    invalida_cache()

                    st.session_state.ultimo_articolo = art_nome
                    st.session_state.dati_annulla = {"id": art_id, "articolo": art_nome, "quantita_precedente": qta_attuale}
                    st.session_state.messaggio_successo = f"✅ Tolti {quantita_mov} da {art_nome}. Nuova quantità: {nuova_qta}."
                    st.rerun()
                else:
                    st.error("⚠️ Non hai abbastanza materiale da scaricare!")

        with col_foto:
            if articolo_dati.get("immagine"):
                st.image(articolo_dati["immagine"], caption=art_nome, use_container_width=True)
            else:
                st.write("")
                st.info("Nessuna immagine per questo articolo.")

        st.markdown("---")

        # --- AREA GESTIONE: MODIFICA, FOTO, ELIMINAZIONE ---
        with st.expander(f"🛠️ Area Gestione: Modifica, Aggiungi Foto o Elimina in {NOME_TABELLA}"):
            mappa_totale = {}
            for r in dati_grezzi:
                a = str(r.get("Articolo", ""))
                d = str(r.get("Descrizione", ""))
                testo = f"{a} - {d}" if a.strip() != "" else f"[RIGA VUOTA] id: {r.get('id')}"
                mappa_totale[testo] = r  # ora salviamo la riga intera, non solo il nome

            art_mod_testo = st.selectbox("Scegli articolo da gestire:", list(mappa_totale.keys()), key="menu_mod")
            dati_art = mappa_totale[art_mod_testo]
            id_modifica = dati_art.get("id")

            col_m1, col_m2 = st.columns(2)
            with col_m1:
                agg_desc = st.text_input("Correggi Descrizione:", value=str(dati_art.get("Descrizione", "")).replace("None", ""))
                agg_um = st.text_input("Correggi Um:", value=str(dati_art.get("Um", "")).replace("None", ""))
                try:
                    soglia_corrente = float(str(dati_art.get("soglia_minima", SOGLIA_SOTTOSCORTA)).replace(",", ".")) if dati_art.get("soglia_minima") not in (None, "") else SOGLIA_SOTTOSCORTA
                except ValueError:
                    soglia_corrente = SOGLIA_SOTTOSCORTA
                agg_soglia = st.number_input("Soglia sottoscorta:", min_value=0.0, value=soglia_corrente, step=1.0)
                try:
                    prezzo_corrente = float(str(dati_art.get("Prezzo", 0.0)).replace(",", ".")) if dati_art.get("Prezzo") not in (None, "") else 0.0
                except ValueError:
                    prezzo_corrente = 0.0
                agg_prezzo = st.number_input("Prezzo unitario (€):", min_value=0.0, value=prezzo_corrente, step=0.01, format="%.2f")

                nuova_foto_mod = st.file_uploader("Aggiungi/Sostituisci Foto", type=['png', 'jpg', 'jpeg'], key="foto_mod")

                if st.button("💾 Salva Modifiche"):
                    dati_da_aggiornare = {
                        "Descrizione": agg_desc,
                        "Um": agg_um,
                        "soglia_minima": agg_soglia,
                        "Prezzo": agg_prezzo,
                    }
                    if nuova_foto_mod:
                        url_foto_nuova, percorso_nuovo = carica_foto_su_supabase(
                            nuova_foto_mod, dati_art.get("Articolo", f"id{id_modifica}")
                        )
                        dati_da_aggiornare["immagine"] = url_foto_nuova
                        dati_da_aggiornare["foto_path"] = percorso_nuovo

                    supabase.table(NOME_TABELLA).update(dati_da_aggiornare).eq("id", id_modifica).execute()
                    invalida_cache()
                    st.session_state.messaggio_successo = "✏️ Dati e/o Foto aggiornati con successo!"
                    st.rerun()

            with col_m2:
                st.warning("Azione irreversibile!")
                if st.checkbox("Sono sicuro di voler eliminare l'articolo"):
                    if st.button("🗑️ ELIMINA ARTICOLO"):
                        # Elimina prima la foto associata (se presente), poi la riga
                        elimina_foto_da_supabase(dati_art.get("foto_path"))
                        supabase.table(NOME_TABELLA).delete().eq("id", id_modifica).execute()
                        invalida_cache()
                        st.session_state.messaggio_successo = f"🗑️ '{dati_art.get('Articolo', '')}' eliminato."
                        st.session_state.ultimo_articolo = None
                        st.session_state.dati_annulla = None
                        st.rerun()

    else:
        st.info(f"Il catalogo '{NOME_TABELLA}' è vuoto. Aggiungi un articolo dalla barra laterale.")

except Exception as errore:
    st.error(f"Errore tecnico: Assicurati che la tabella '{NOME_TABELLA}' sia creata correttamente su Supabase. Dettagli: {errore}")
    # Log completo in console/log del server per il debug, senza mostrarlo all'utente
    print(traceback.format_exc())

# ==========================================
# 5. SEZIONE STORICO MOVIMENTI
# ==========================================
st.divider()
st.subheader("⏱️ Ultimi Movimenti in Magazzino")

try:
    dati_storico = leggi_storico()

    if dati_storico:
        st.dataframe(
            dati_storico,
            column_order=("created_at", "Utente", "Catalogo", "Articolo", "Operazione", "Quantita"),
            column_config={
                "created_at": st.column_config.DatetimeColumn("Data e Ora", format="DD/MM/YYYY - HH:mm"),
                "id": None,
            },
            use_container_width=True
        )
    else:
        st.info("Nessun movimento registrato finora.")
except Exception as e:
    st.error(f"Impossibile caricare lo storico. Assicurati di aver creato la tabella 'Storico' su Supabase. Errore: {e}")
    print(traceback.format_exc())
