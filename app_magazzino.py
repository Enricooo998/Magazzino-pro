import streamlit as st
from supabase import create_client, Client
import io
import csv
import traceback

# --- CONFIGURAZIONE DATABASE ---
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CHIAVE_SUPABASE = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL_SUPABASE, CHIAVE_SUPABASE)
st.set_page_config(page_title="Gestione Magazzino Pro", layout="wide")

BUCKET_FOTO = "foto-prodotti"
SOGLIA_SOTTOSCORTA = 5.0

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

    st.markdown("---")

    st.header(f"➕ Nuovo in {NOME_TABELLA}")
    with st.form("form_nuovo_articolo", clear_on_submit=True):
        nuovo_codice = st.text_input("Articolo (Codice/Nome) *obbligatorio")
        nuova_descrizione = st.text_input("Descrizione")
        nuova_um = st.text_input("Unità di misura")
        nuova_quantita = st.number_input("Quantità Iniziale", min_value=0.0, value=0.0, step=1.0)
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
                            "immagine": url_foto,
                            "foto_path": percorso_foto,
                        }
                        supabase.table(NOME_TABELLA).insert(nuovi_dati).execute()
                        invalida_cache()
                        st.session_state.messaggio_successo = f"🎉 Nuovo articolo '{codice_pulito}' creato nel catalogo {NOME_TABELLA}!"
                        st.rerun()
                except Exception as e:
                    st.error(f"Errore durante il salvataggio: {e}")

# ==========================================
# 4. SCHERMATA PRINCIPALE
# ==========================================
st.title(f"📦 Magazzino: {NOME_TABELLA}")

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

        # --- ALLARME SOTTOSCORTA ---
        sottoscorta = [riga for riga in dati if riga["Quantità"] <= SOGLIA_SOTTOSCORTA]
        if sottoscorta:
            st.error(f"🚨 **ALLARME SOTTOSCORTA in {NOME_TABELLA}:** Ci sono {len(sottoscorta)} articoli in esaurimento!")
            with st.expander("👀 Clicca qui per vedere gli articoli in sottoscorta"):
                for art in sottoscorta:
                    st.warning(f"⚠️ **{art['Articolo']}** - Quantità residua: **{art['Quantità']}**")

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
                column_order=("Articolo", "Descrizione", "Um", "Quantità", "immagine"),
                column_config={
                    "immagine": st.column_config.ImageColumn("Foto"),
                    "created_at": None,
                    "id": None,
                    "foto_path": None,
                },
                use_container_width=True
            )
        with col_btn:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["Articolo", "Descrizione", "Um", "Quantità", "Link_Foto"])
            writer.writeheader()
            dati_puliti = [
                {
                    "Articolo": r.get("Articolo"),
                    "Descrizione": r.get("Descrizione"),
                    "Um": r.get("Um"),
                    "Quantità": r.get("Quantità"),
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

                nuova_foto_mod = st.file_uploader("Aggiungi/Sostituisci Foto", type=['png', 'jpg', 'jpeg'], key="foto_mod")

                if st.button("💾 Salva Modifiche"):
                    dati_da_aggiornare = {
                        "Descrizione": agg_desc,
                        "Um": agg_um,
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
