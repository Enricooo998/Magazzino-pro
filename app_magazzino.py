import streamlit as st
from supabase import create_client, Client
import io
import csv

# --- CONFIGURAZIONE DATABASE ---
URL_SUPABASE = st.secrets["SUPABASE_URL"]
CHIAVE_SUPABASE = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL_SUPABASE, CHIAVE_SUPABASE)
st.set_page_config(page_title="Gestione Magazzino Pro", layout="wide")

BUCKET_FOTO = "foto-prodotti" 

# ==========================================
# 1. SISTEMA DI ACCESSO (LOGIN)
# ==========================================
if 'autenticato' not in st.session_state:
    st.session_state.autenticato = False

if not st.session_state.autenticato:
    st.title("🔐 Accesso Magazzino")
    password = st.text_input("Inserisci la password per accedere:", type="password")
    if st.button("Entra"):
        if password == "1234":
            st.session_state.autenticato = True
            st.rerun()
        else:
            st.error("Password errata. Riprova.")
    st.stop()

# ==========================================
# 2. FUNZIONI DI SUPPORTO E MEMORIA
# ==========================================
def carica_foto_su_supabase(file_caricato, nome_articolo):
    estensione = file_caricato.name.split('.')[-1]
    percorso_file = f"foto_{nome_articolo.replace(' ', '_')}.{estensione}"
    supabase.storage.from_(BUCKET_FOTO).upload(percorso_file, file_caricato.getvalue(), {"upsert": "true"})
    return supabase.storage.from_(BUCKET_FOTO).get_public_url(percorso_file)

if 'ultimo_articolo' not in st.session_state: st.session_state.ultimo_articolo = None
if 'messaggio_successo' not in st.session_state: st.session_state.messaggio_successo = None
if 'dati_annulla' not in st.session_state: st.session_state.dati_annulla = None

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
        # Aggiornato per permettere i decimali nella creazione
        nuova_quantita = st.number_input("Quantità Iniziale", min_value=0.0, value=0.0, step=1.0)
        foto_caricata = st.file_uploader("Carica foto prodotto (Opzionale)", type=['png', 'jpg', 'jpeg'])
        
        if st.form_submit_button("Salva Nuovo Articolo"):
            if nuovo_codice.strip() == "":
                st.error("Devi inserire almeno il nome dell'articolo!")
            else:
                try:
                    url_foto = None
                    if foto_caricata:
                        url_foto = carica_foto_su_supabase(foto_caricata, nuovo_codice)
                        
                    nuovi_dati = {
                        "Articolo": nuovo_codice.strip(),
                        "Descrizione": nuova_descrizione.strip(),
                        "Um": nuova_um.strip(),
                        "Quantità": nuova_quantita,
                        "immagine": url_foto
                    }
                    supabase.table(NOME_TABELLA).insert(nuovi_dati).execute()
                    st.session_state.messaggio_successo = f"🎉 Nuovo articolo '{nuovo_codice}' creato nel catalogo {NOME_TABELLA}!"
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore durante il salvataggio: {e}")

# ==========================================
# 4. SCHERMATA PRINCIPALE
# ==========================================
st.title(f"📦 Magazzino: {NOME_TABELLA}")

try:
    risposta = supabase.table(NOME_TABELLA).select("*").order("Articolo").execute()
    dati_grezzi = risposta.data
    
    if dati_grezzi:
        dati = [r for r in dati_grezzi if r.get("Articolo") and str(r.get("Articolo")).strip() != ""]
        
        # --- SCUDO AGGIORNATO: ORA SUPPORTA I DECIMALI ---
        for r in dati:
            qta_grezza = r.get("Quantità")
            if qta_grezza is None or str(qta_grezza).strip() == "":
                r["Quantità"] = 0.0
            else:
                try:
                    # Sostituisce la virgola italiana con il punto per farlo leggere a Python
                    qta_pulita = str(qta_grezza).replace(',', '.')
                    r["Quantità"] = float(qta_pulita)
                except ValueError:
                    r["Quantità"] = 0.0
        # ----------------------------------------

        # --- ALLARME SOTTOSCORTA ---
        sottoscorta = [riga for riga in dati if riga["Quantità"] <= 5.0]
        if sottoscorta:
            st.error(f"🚨 **ALLARME SOTTOSCORTA in {NOME_TABELLA}:** Ci sono {len(sottoscorta)} articoli in esaurimento!")
            with st.expander("👀 Clicca qui per vedere gli articoli in sottoscorta"):
                for art in sottoscorta:
                    st.warning(f"⚠️ **{art['Articolo']}** - Quantità residua: **{art['Quantità']}**")
            
        st.markdown("---")
        
        testo_ricerca = st.text_input(f"🔎 Filtra in {NOME_TABELLA} (Cerca per nome o descrizione):", "")
        if testo_ricerca:
            dati_filtrati = [r for r in dati if testo_ricerca.lower() in str(r.get("Articolo","")).lower() or testo_ricerca.lower() in str(r.get("Descrizione","")).lower()]
        else:
            dati_filtrati = dati

        col_tab, col_btn = st.columns([0.8, 0.2])
        with col_tab:
            st.dataframe(
                dati_filtrati, 
                column_config={
                    "immagine": st.column_config.ImageColumn("Foto"),
                    "created_at": None, 
                    "id": None          
                },
                use_container_width=True
            )
        with col_btn:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["Articolo", "Descrizione", "Um", "Quantità", "Link_Foto"])
            writer.writeheader()
            dati_puliti = [{"Articolo": r.get("Articolo"), "Descrizione": r.get("Descrizione"), "Um": r.get("Um"), "Quantità": r.get("Quantità"), "Link_Foto": r.get("immagine")} for r in dati_filtrati]
            writer.writerows(dati_puliti)
            st.download_button(f"📥 Scarica CSV ({NOME_TABELLA})", data=output.getvalue().encode('utf-8'), file_name=f"magazzino_{NOME_TABELLA}.csv", mime="text/csv", use_container_width=True)
        
        if st.session_state.messaggio_successo:
            st.success(st.session_state.messaggio_successo)
            
        if st.session_state.dati_annulla:
            if st.button("⏪ Annulla ultima operazione di carico/scarico"):
                art_annulla = st.session_state.dati_annulla["articolo"]
                qta_vecchia = st.session_state.dati_annulla["quantita_precedente"]
                supabase.table(NOME_TABELLA).update({"Quantità": qta_vecchia}).eq("Articolo", art_annulla).execute()
                st.session_state.messaggio_successo = f"⏪ Annullato! '{art_annulla}' è tornato a {qta_vecchia}."
                st.session_state.dati_annulla = None
                st.rerun()

        st.markdown("---")
        
        col_movimenti, col_foto = st.columns([0.6, 0.4])
        
        with col_movimenti:
            st.subheader(f"🔄 Movimentazione in {NOME_TABELLA}")
            mappa_opzioni = {f"{r['Articolo']} - {r.get('Descrizione','')}": r for r in dati}
            lista_opzioni = list(mappa_opzioni.keys())
            
            indice_sel = 0
            if st.session_state.ultimo_articolo:
                for i, t in enumerate(lista_opzioni):
                    if mappa_opzioni[t]['Articolo'] == st.session_state.ultimo_articolo:
                        indice_sel = i
                        break
            
            testo_selezionato = st.selectbox("Seleziona l'articolo:", lista_opzioni, index=indice_sel)
            articolo_dati = mappa_opzioni[testo_selezionato]
            art_nome = articolo_dati['Articolo']
            qta_attuale = float(articolo_dati.get("Quantità", 0.0))
            
            st.info(f"📦 **Disponibili in magazzino:** {qta_attuale}")
            
            # Aggiornato per permettere inserimento e step decimali (es. 0.5 metri)
            quantita_mov = st.number_input("Quantità da movimentare:", min_value=0.01, value=1.00, step=1.00)
            c1, c2 = st.columns(2)
            
            if c1.button("➕ CARICO", use_container_width=True):
                supabase.table(NOME_TABELLA).update({"Quantità": qta_attuale + quantita_mov}).eq("Articolo", art_nome).execute()
                st.session_state.ultimo_articolo = art_nome
                st.session_state.dati_annulla = {"articolo": art_nome, "quantita_precedente": qta_attuale}
                st.session_state.messaggio_successo = f"✅ Aggiunti {quantita_mov} a {art_nome}."
                st.rerun()
                
            if c2.button("➖ SCARICO", use_container_width=True):
                if qta_attuale >= quantita_mov:
                    supabase.table(NOME_TABELLA).update({"Quantità": qta_attuale - quantita_mov}).eq("Articolo", art_nome).execute()
                    st.session_state.ultimo_articolo = art_nome
                    st.session_state.dati_annulla = {"articolo": art_nome, "quantita_precedente": qta_attuale}
                    st.session_state.messaggio_successo = f"✅ Tolti {quantita_mov} da {art_nome}."
                    st.rerun()
                else:
                    st.error("⚠️ Non hai abbastanza materiale da scaricare!")
                    
        with col_foto:
            if articolo_dati.get("immagine"):
                st.image(articolo_dati["immagine"], caption=art_nome, use_column_width=True)
            else:
                st.write("")
                st.info("Nessuna immagine per questo articolo.")

        st.markdown("---")
        
        # --- MODIFICA: AGGIUNTA FOTO E DATI ---
        with st.expander(f"🛠️ Area Gestione: Modifica, Aggiungi Foto o Elimina in {NOME_TABELLA}"):
            mappa_totale = {}
            for r in dati_grezzi:
                a = str(r.get("Articolo", ""))
                d = str(r.get("Descrizione", ""))
                testo = f"{a} - {d}" if a.strip() != "" else f"[RIGA VUOTA] id: {r.get('id')}"
                mappa_totale[testo] = a
                
            art_mod_testo = st.selectbox("Scegli articolo da gestire:", list(mappa_totale.keys()), key="menu_mod")
            art_modifica = mappa_totale[art_mod_testo]
            
            dati_art = next((r for r in dati_grezzi if r.get("Articolo") == art_modifica), {})
            
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                agg_desc = st.text_input("Correggi Descrizione:", value=str(dati_art.get("Descrizione", "")).replace("None", ""))
                agg_um = st.text_input("Correggi Um:", value=str(dati_art.get("Um", "")).replace("None", ""))
                
                nuova_foto_mod = st.file_uploader("Aggiungi/Sostituisci Foto", type=['png', 'jpg', 'jpeg'], key="foto_mod")
                
                if st.button("💾 Salva Modifiche"):
                    dati_da_aggiornare = {
                        "Descrizione": agg_desc, 
                        "Um": agg_um
                    }
                    if nuova_foto_mod:
                        url_foto_nuova = carica_foto_su_supabase(nuova_foto_mod, art_modifica)
                        dati_da_aggiornare["immagine"] = url_foto_nuova
                        
                    supabase.table(NOME_TABELLA).update(dati_da_aggiornare).eq("Articolo", art_modifica).execute()
                    st.session_state.messaggio_successo = "✏️ Dati e/o Foto aggiornati con successo!"
                    st.rerun()
                    
            with col_m2:
                st.warning("Azione irreversibile!")
                if st.checkbox("Sono sicuro di voler eliminare l'articolo"):
                    if st.button("🗑️ ELIMINA ARTICOLO"):
                        if art_modifica.strip() != "":
                            supabase.table(NOME_TABELLA).delete().eq("Articolo", art_modifica).execute()
                            st.session_state.messaggio_successo = f"🗑️ '{art_modifica}' eliminato."
                            st.session_state.ultimo_articolo = None 
                            st.session_state.dati_annulla = None
                            st.rerun()

    else:
        st.info(f"Il catalogo '{NOME_TABELLA}' è vuoto. Aggiungi un articolo dalla barra laterale.")

except Exception as errore:

    st.error(f"Errore tecnico: Assicurati che la tabella '{NOME_TABELLA}' sia creata correttamente su Supabase. Dettagli: {errore}")
