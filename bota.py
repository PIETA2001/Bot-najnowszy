import os
import json
import logging
import io
from datetime import datetime
from dotenv import load_dotenv

# --- Importy Bibliotek ---
import google.generativeai as genai
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

# --- ZMIANA IMPORTÓW ---
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# --- 1. Konfiguracja Logowania ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. Ładowanie Kluczy API ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.critical("BŁĄD: Nie znaleziono tokenów (TELEGRAM_TOKEN lub GEMINI_API_KEY) w pliku .env")
    exit()

# --- 3. Konfiguracja Google ---
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
GOOGLE_TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
GOOGLE_SHEET_NAME = 'Odbiory_Kolonia_Warszawska'
WORKSHEET_NAME = 'Arkusz1'
G_DRIVE_MAIN_FOLDER_NAME = 'Lokale'
G_DRIVE_SZEREGI_FOLDER_NAME = 'Szeregi'

gc = None
worksheet = None
drive_service = None
g_drive_main_folder_id = None
g_drive_szeregi_folder_id = None

def get_google_creds():
    """Obsługuje logowanie OAuth 2.0 i przechowuje token."""
    creds = None
    
    # --- SEKCJA DLA RAILWAY ---
    creds_json_string = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json_string:
        logger.info("Wykryto credentials w zmiennej środowiskowej. Zapisywanie do pliku...")
        try:
            with open(GOOGLE_CREDENTIALS_FILE, 'w') as f:
                f.write(creds_json_string)
            logger.info(f"Pomyślnie zapisano credentials w {GOOGLE_CREDENTIALS_FILE}")
        except Exception as e:
            logger.error(f"Nie można zapisać credentials ze zmiennej: {e}")
    
    token_json_string = os.getenv('GOOGLE_TOKEN_JSON')
    if token_json_string:
        logger.info("Wykryto token w zmiennej środowiskowej. Zapisywanie do pliku...")
        try:
            with open(GOOGLE_TOKEN_FILE, 'w') as token:
                token.write(token_json_string)
            logger.info(f"Pomyślnie zapisano token w {GOOGLE_TOKEN_FILE}")
        except Exception as e:
            logger.error(f"Nie można zapisać tokenu ze zmiennej: {e}")
    # --- KONIEC SEKCJI ---
            
    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Token wygasł, odświeżanie...")
            creds.refresh(Request())
        else:
            logger.info("Brak tokenu lub token nieprawidłowy. Uruchamianie przepływu autoryzacji...")
            # ... (logika dla logowania lokalnego) ...
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logger.critical(f"BŁĄD KRYTYCZNY PRZY AUTORYZACJI: {e}")
                exit()

        with open(GOOGLE_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        logger.info(f"Pomyślnie zapisano/zaktualizowano token w {GOOGLE_TOKEN_FILE}")
    
    return creds

try:
    # --- 3a. Pobranie danych logowania (OAuth) ---
    creds = get_google_creds()
    logger.info("Pomyślnie uzyskano dane logowania Google (OAuth 2.0)")

    # --- 3b. Konfiguracja Google Sheets (gspread) ---
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    logger.info(f"Pomyślnie połączono z Arkuszem Google: {GOOGLE_SHEET_NAME}")

    # --- 3c. Konfiguracja Google Drive ---
    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("Pomyślnie połączono z Google Drive")

    # Funkcja pomocnicza do wyszukiwania folderu
    def find_folder(folder_name):
        logger.info(f"Szukanie folderu: '{folder_name}'...")
        response_folder = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=False",
            spaces='drive',
            fields='files(id, name)',
        ).execute()
        
        files = response_folder.get('files', [])
        if not files:
            logger.critical(f"BŁĄD KRYTYCZNY: Nie znaleziono folderu '{folder_name}' na Twoim 'Mój Dysk'!")
            return None
        
        folder_id = files[0].get('id')
        logger.info(f"Pomyślnie znaleziono folder '{folder_name}' (ID: {folder_id})")
        return folder_id

    # Wyszukaj oba foldery
    g_drive_main_folder_id = find_folder(G_DRIVE_MAIN_FOLDER_NAME)
    g_drive_szeregi_folder_id = find_folder(G_DRIVE_SZEREGI_FOLDER_NAME)

    if not g_drive_main_folder_id or not g_drive_szeregi_folder_id:
        logger.critical("Nie udało się znaleźć jednego z głównych folderów. Zamykanie.")
        exit()

except Exception as e:
    logger.critical(f"BŁĄD KRYTYCZNY: Nie można połączyć z Google: {e}")
    exit()


# --- 4. Konfiguracja Gemini (AI) ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
    "temperature": 0.2,
    "max_output_tokens": 2048,
    "response_mime_type": "application/json",
}
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config
)

# --- 5. Definicja Promptu dla AI ---
PROMPT_SYSTEMOWY = """
Twoim zadaniem jest analiza zgłoszenia serwisowego. Przetwórz wiadomość użytkownika i wyekstrahuj DOKŁADNIE 3 informacje: numer_lokalu_budynku, rodzaj_usterki, podmiot_odpowiedzialny.

Zawsze odpowiadaj WYŁĄCZNIE w formacie JSON, zgodnie z tym schematem:
{
  "numer_lokalu_budynku": "string",
  "rodzaj_usterki": "string",
  "podmiot_odpowiedzialny": "string"
}

Ustalenia:
1.  numer_lokalu_budynku: (np. "15", "104B", "Budynek C, klatka 2", "Lokal 46/2", "SZEREG 5")
2.  rodzaj_usterki: (np. "cieknący kran", "brak prądu", "winda nie działa", "porysowana szyba")
3.  podmiot_odpowiedzialny: (np. "administracja", "serwis", "konserwator", "deweloper", "domhomegroup", "Janusz Pelc", "Michał Piskorz").
4.  Jeśli jakiejś informacji brakuje, wstaw w jej miejsce "BRAK DANYCH".
5.  Jeśli wiadomość to 'Rozpoczęcie odbioru', 'rodzaj_usterki' powinien być "Rozpoczęcie odbioru".
6.  Nigdy nie dodawaj żadnego tekstu przed ani po obiekcie JSON.
"""

# --- NOWOŚĆ: Funkcja tworząca klawiaturę Inline ---
def get_inline_keyboard():
    """Tworzy i zwraca klawiaturę inline."""
    keyboard = [
        [
            InlineKeyboardButton("Cofnij ↩️", callback_data='cofnij'),
            InlineKeyboardButton("Zakończ Odbiór 🏁", callback_data='koniec_odbioru')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- 6. Funkcja do Zapisu w Arkuszu ---
def zapisz_w_arkuszu(dane_json: dict, data_telegram: datetime) -> bool:
    """Zapisuje przeanalizowane dane w nowym wierszu Arkusza Google."""
    try:
        data_str = data_telegram.strftime('%Y-%m-%d %H:%M:%S')
        
        nowy_wiersz = [
            data_str,
            dane_json.get('numer_lokalu_budynku', 'BŁĄD JSON'),
            dane_json.get('rodzaj_usterki', 'BŁĄD JSON'),
            dane_json.get('podmiot_odpowiedzialny', 'BŁĄD JSON'),
            dane_json.get('link_do_zdjecia', '')
        ]
        
        worksheet.append_row(nowy_wiersz, value_input_option='USER_ENTERED')
        logger.info(f"Dodano wiersz do arkusza: {nowy_wiersz}")
        return True
    except Exception as e:
        logger.error(f"Błąd podczas zapisu do Google Sheets: {e}")
        return False

# --- FUNKCJA WYSYŁANIA NA GOOGLE DRIVE ---
def upload_photo_to_drive(file_bytes, target_name, usterka_name, podmiot_name, tryb_odbioru='lokal'):
    """Wyszukuje podfolder (lokalu lub szeregu) i wysyła do niego zdjęcie."""
    global drive_service, g_drive_main_folder_id, g_drive_szeregi_folder_id, G_DRIVE_MAIN_FOLDER_NAME, G_DRIVE_SZEREGI_FOLDER_NAME
    
    try:
        # Krok 1: Wybierz nadrzędny folder na podstawie trybu
        parent_folder_id = None
        parent_folder_name = ""
        
        if tryb_odbioru == 'lokal':
            parent_folder_id = g_drive_main_folder_id
            parent_folder_name = G_DRIVE_MAIN_FOLDER_NAME
        elif tryb_odbioru == 'szereg':
            parent_folder_id = g_drive_szeregi_folder_id
            parent_folder_name = G_DRIVE_SZEREGI_FOLDER_NAME
        else:
            logger.error(f"Nierozpoznany tryb odbioru: {tryb_odbioru}")
            return False, f"Nierozpoznany tryb: {tryb_odbioru}", None

        # Krok 2: Znajdź podfolder
        q_str = f"name='{target_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_folder_id}' in parents and trashed=False"
        
        response = drive_service.files().list(
            q=q_str,
            spaces='drive',
            fields='files(id, name)',
        ).execute()
        
        target_folder = response.get('files', [])

        if not target_folder:
            logger.error(f"Nie znaleziono folderu dla celu: {target_name} wewnątrz '{parent_folder_name}'")
            return False, f"Nie znaleziono folderu Drive dla '{target_name}' w '{parent_folder_name}'", None

        target_folder_id = target_folder[0].get('id')
        
        # Krok 3: Przygotuj metadane i plik
        file_name = f"{usterka_name} - {podmiot_name}.jpg"
        file_metadata = {
            'name': file_name,
            'parents': [target_folder_id]
        }
        
        # Krok 4: Wyślij plik
        file_bytes.seek(0)
        media = MediaIoBaseUpload(file_bytes, mimetype='image/jpeg', resumable=True)
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
        ).execute()
        
        file_id = file.get('id')
        logger.info(f"Pomyślnie wysłano plik '{file_name}' do folderu '{target_name}' (ID: {file_id})")
        return True, file_name, file_id
    
    except Exception as e:
        logger.error(f"Błąd podczas wysyłania na Google Drive: {e}")
        return False, str(e), None


# --- Funkcja do usuwania pliku z Google Drive ---
def delete_file_from_drive(file_id):
    """Usuwa plik z Google Drive na podstawie jego ID."""
    global drive_service
    if not file_id:
        logger.warning("Próba usunięcia pliku, ale brak file_id.")
        return False, "Brak ID pliku"
        
    try:
        drive_service.files().delete(fileId=file_id).execute()
        logger.info(f"Pomyślnie usunięto plik z Drive (ID: {file_id})")
        return True, None
    except Exception as e:
        logger.error(f"Błąd podczas usuwania pliku {file_id} z Drive: {e}")
        return False, str(e)


# --- 7. Główny Handler (serce bota) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje wiadomość, sprawdza stan sesji i decyduje co robić."""
    
    if not update.message or (not update.message.text and not update.message.caption):
         logger.warning("Otrzymano pustą wiadomość (np. naklejkę). Ignorowanie.")
         return

    user_message = update.message.text
    if not user_message:
        if update.message.caption:
            logger.info("Wiadomość tekstowa jest pusta, ale jest caption. Przekazuję do handle_photo.")
            return
        else:
            logger.warning("Otrzymano wiadomość bez tekstu i bez caption. Ignorowanie.")
            return

    message_time = update.message.date
    chat_data = context.chat_data

    try:
        # --- LOGIKA SESJI ODBIORU ---

        # SCENARIUSZ 1: Użytkownik KOŃCZY odbiór (Fallback tekstowy)
        if user_message.lower().strip() == 'koniec odbioru':
            if chat_data.get('odbiur_aktywny'):
                lokal = chat_data.get('odbiur_lokal_do_arkusza')
                podmiot = chat_data.get('odbiur_podmiot')
                
                wpisy_lista = chat_data.get('odbiur_wpisy', [])
                
                if not wpisy_lista:
                    await update.message.reply_text(f"Zakończono odbiór dla lokalu {lokal}. Nie dodano żadnych usterek.",
                                                    reply_markup=ReplyKeyboardRemove())
                else:
                    logger.info(f"Zapisywanie {len(wpisy_lista)} usterek dla lokalu {lokal}...")
                    licznik_zapisanych = 0
                    
                    for wpis in wpisy_lista:
                        dane_json = {
                            "numer_lokalu_budynku": lokal,
                            "rodzaj_usterki": wpis.get('opis', 'BŁĄD WPISU'),
                            "podmiot_odpowiedzialny": podmiot,
                            "link_do_zdjecia": ""
                        }
                        file_id_ze_zdjecia = wpis.get('file_id')
                        if file_id_ze_zdjecia:
                            link_zdjecia = f"https://drive.google.com/file/d/{file_id_ze_zdjecia}/view"
                            dane_json['link_do_zdjecia'] = link_zdjecia
                        
                        if zapisz_w_arkuszu(dane_json, message_time):
                            licznik_zapisanych += 1
                    
                    await update.message.reply_text(f"✅ Zakończono odbiór.\nZapisano {licznik_zapisanych} z {len(wpisy_lista)} usterek dla lokalu {lokal}.",
                                                    reply_markup=ReplyKeyboardRemove())
                
                chat_data.clear()
            else:
                await update.message.reply_text("Żaden odbiór nie jest aktywny. Aby zakończyć, musisz najpierw go rozpocząć.",
                                                reply_markup=ReplyKeyboardRemove())
            return

        # --- SCENARIUSZ 1.5: Użytkownik COFA (Fallback tekstowy) ---
        if user_message.lower().strip() == 'cofnij':
            if not chat_data.get('odbiur_aktywny'):
                await update.message.reply_text("Nie można cofnąć. Żaden odbiór nie jest aktywny.")
                return
            
            wpisy_lista = chat_data.get('odbiur_wpisy', [])
            if not wpisy_lista:
                await update.message.reply_text("Nie można cofnąć. Lista usterek jest już pusta.")
                return

            try:
                ostatni_wpis = wpisy_lista.pop()
                chat_data['odbiur_wpisy'] = wpisy_lista
                opis_usunietego = ostatni_wpis.get('opis', 'NIEZNANY WPIS')
                
                if ostatni_wpis.get('typ') == 'zdjecie':
                    file_id_to_delete = ostatni_wpis.get('file_id')
                    if file_id_to_delete:
                        delete_success, delete_error = delete_file_from_drive(file_id_to_delete)
                        if delete_success:
                            await update.message.reply_text(f"↩️ Cofnięto i usunięto zdjęcie:\n'{opis_usunietego}'\n"
                                                            f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard()) # ZMIANA
                        else:
                            await update.message.reply_text(f"↩️ Cofnięto wpis: '{opis_usunietego}'.\n"
                                                            f"⚠️ BŁĄD: Nie udało się usunąć pliku z Google Drive: {delete_error}", reply_markup=get_inline_keyboard()) # ZMIANA
                    else:
                         await update.message.reply_text(f"↩️ Cofnięto wpis (bez ID pliku):\n'{opis_usunietego}'\n"
                                                        f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard()) # ZMIANA
                else:
                    await update.message.reply_text(f"↩️ Cofnięto wpis tekstowy:\n'{opis_usunietego}'\n"
                                                    f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard()) # ZMIANA
            
            except Exception as e:
                logger.error(f"Błąd podczas operacji 'cofnij': {e}")
                await update.message.reply_text(f"❌ Wystąpił błąd podczas cofania: {e}", reply_markup=get_inline_keyboard()) # ZMIANA
            
            return

        # SCENARIUSZ 2: Użytkownik ZACZYNA odbiór
        if user_message.lower().startswith('rozpoczęcie odbioru'):
            logger.info("Wykryto 'Rozpoczęcie odbioru', wysyłanie do Gemini po dane sesji...")
            await update.message.reply_text("Rozpoczynam odbiór... 🧠 Analizuję dane celu i firmy...")
            
            response = model.generate_content([PROMPT_SYSTEMOWY, user_message])
            cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            dane_startowe = json.loads(cleaned_text)
            
            lokal_raw = dane_startowe.get('numer_lokalu_budynku')
            podmiot = dane_startowe.get('podmiot_odpowiedzialny')

            if lokal_raw == "BRAK DANYCH" or podmiot == "BRAK DANYCH":
                await update.message.reply_text("❌ Nie udało się rozpoznać celu (lokalu/szeregu) lub firmy.\n"
                                                "Spróbuj ponownie, np: \n"
                                                "'Rozpoczęcie odbioru, lokal 46/2, firma X'\n"
                                                "'Rozpoczęcie odbioru, SZEREG 5, firma Y'",
                                                reply_markup=ReplyKeyboardRemove())
            else:
                target_name = ""
                tryb_odbioru = ""
                
                if "szereg" in lokal_raw.lower():
                    tryb_odbioru = "szereg"
                    target_name = lokal_raw.upper().strip()
                else:
                    tryb_odbioru = "lokal"
                    target_name = lokal_raw.lower().replace("lokal", "").strip().replace("/", ".")
                
                chat_data['odbiur_aktywny'] = True
                chat_data['odbiur_lokal_do_arkusza'] = target_name
                chat_data['odbiur_target_nazwa'] = target_name
                chat_data['tryb_odbioru'] = tryb_odbioru
                chat_data['odbiur_podmiot'] = podmiot
                chat_data['odbiur_wpisy'] = []
                
                await update.message.reply_text(f"✅ Rozpoczęto odbiór dla:\n\n"
                                                f"Cel: {target_name}\n"
                                                f"Firma: {podmiot}\n\n"
                                                f"Teraz wpisuj usterki (tekst lub zdjęcia z opisem).\n"
                                                f"Użyj przycisków poniżej, aby cofnąć lub zakończyć.\n",
                                                reply_markup=get_inline_keyboard()) # <-- ZMIANA: Pokaż klawiaturę INLINE
            
            return

        # SCENARIUSZ 3: Odbiór jest AKTYWNY, a to jest usterka TEKSTOWA
        if chat_data.get('odbiur_aktywny'):
            logger.info(f"Odbiór aktywny. Wysyłanie usterki '{user_message}' do Gemini w celu ekstrakcji...")
            
            response = model.generate_content([PROMPT_SYSTEMOWY, user_message])
            cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            dane_usterki = json.loads(cleaned_text)
            
            usterka_opis = dane_usterki.get('rodzaj_usterki', user_message)
            if usterka_opis == "BRAK DANYCH":
                usterka_opis = user_message
                
            nowy_wpis = {
                'typ': 'tekst',
                'opis': usterka_opis
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(f"➕ Dodano (tekst): '{usterka_opis}'\n"
                                            f"(Łącznie: {len(chat_data['odbiur_wpisy'])}). Wpisz kolejną, 'cofnij' lub 'Koniec odbioru'.",
                                            reply_markup=get_inline_keyboard()) # <-- ZMIANA: Pokaż klawiaturę INLINE
            return

    except json.JSONDecodeError as json_err:
        logger.error(f"Błąd parsowania JSON od Gemini (w logice sesji): {json_err}. Odpowiedź AI: {response.text}")
        await update.message.reply_text("❌ Błąd analizy AI. Spróbuj sformułować wiadomość inaczej.")
        return
    except Exception as session_err:
        logger.error(f"Wystąpił nieoczekiwany błąd w logice sesji: {session_err}")
        await update.message.reply_text(f"❌ Wystąpił krytyczny błąd: {session_err}")
        return

    # --- LOGIKA DOMYŚLNA (FALLBACK) ---
    
    logger.info(f"Brak aktywnego odbioru. Przetwarzanie jako pojedyncze zgłoszenie: '{user_message}'")
    
    try:
        await update.message.reply_text("Przetwarzam jako pojedyncze zgłoszenie... 🧠")
        
        logger.info("Wysyłanie do Gemini...")
        response = model.generate_content([PROMPT_SYSTEMOWY, user_message])
        
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        dane = json.loads(cleaned_text)
        logger.info(f"Gemini zwróciło JSON: {dane}")

        if zapisz_w_arkuszu(dane, message_time):
            await update.message.reply_text(f"✅ Zgłoszenie (pojedyncze) przyjęte i zapisane:\n\n"
                                            f"Lokal: {dane.get('numer_lokalu_budynku')}\n"
                                            f"Usterka: {dane.get('rodzaj_usterki')}\n"
                                            f"Podmiot: {dane.get('podmiot_odpowiedzialny')}",
                                            reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Błąd zapisu do bazy danych (Arkusza). Skontaktuj się z adminem.")

    except json.JSONDecodeError:
        logger.error(f"Błąd parsowania JSON od Gemini (fallback). Odpowiedź AI: {response.text}")
        await update.message.reply_text("❌ Błąd analizy AI (fallback). Spróbuj sformułować zgłoszenie inaczej.")
    except Exception as e:
        logger.error(f"Wystąpił nieoczekiwany błąd (fallback): {e}")
        await update.message.reply_text(f"❌ Wystąpił krytyczny błąd (fallback): {e}")


# --- 7b. HANDLER DLA ZDJĘĆ ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje zdjęcie W TRAKCIE aktywnej sesji odbioru."""
    chat_data = context.chat_data
    
    if not chat_data.get('odbiur_aktywny'):
        await update.message.reply_text("Wyślij zdjęcie *po* rozpoczęciu odbioru. Teraz ta fotka zostanie zignorowana.",
                                        reply_markup=ReplyKeyboardRemove())
        return

    usterka = update.message.caption
    if not usterka:
        await update.message.reply_text("❌ Zdjęcie musi mieć opis (usterkę)!\nInaczej nie wiem, co zapisać. Wyślij ponownie z opisem.",
                                        reply_markup=get_inline_keyboard()) # <-- ZMIANA
        return

    podmiot = chat_data.get('odbiur_podmiot')
    target_name = chat_data.get('odbiur_target_nazwa')
    tryb = chat_data.get('tryb_odbioru')
    
    await update.message.reply_text(f"Otrzymano zdjęcie dla usterki: '{usterka}'. Przetwarzam i wysyłam na Drive...",
                                    reply_markup=get_inline_keyboard()) # <-- ZMIANA

    try:
        photo_file = await update.message.photo[-1].get_file()
        
        file_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(file_bytes_io)
        
        success, message, file_id = upload_photo_to_drive(
            file_bytes_io,
            target_name,
            usterka,
            podmiot,
            tryb_odbioru=tryb
        )
        
        if success:
            opis_zdjecia = f"{usterka} (zdjęcie)"
            
            nowy_wpis = {
                'typ': 'zdjecie',
                'opis': opis_zdjecia,
                'file_id': file_id
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(f"✅ Zdjęcie zapisane na Drive jako: '{message}'\n"
                                            f"➕ Usterka dodana do listy: '{opis_zdjecia}'\n"
                                            f"(Łącznie: {len(chat_data['odbiur_wpisy'])}).",
                                            reply_markup=get_inline_keyboard()) # <-- ZMIANA
        else:
            await update.message.reply_text(f"❌ Błąd Google Drive: {message}",
                                            reply_markup=get_inline_keyboard()) # <-- ZMIANA
            
    except Exception as e:
        logger.error(f"Błąd podczas przetwarzania zdjęcia: {e}")
        await update.message.reply_text(f"❌ Wystąpił błąd przy pobieraniu zdjęcia: {e}",
                                        reply_markup=get_inline_keyboard()) # <-- ZMIANA


# --- 7c. NOWY HANDLER: Obsługa przycisków Inline ---
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje naciśnięcia przycisków inline."""
    query = update.callback_query
    
    # Ważne: Zawsze odpowiadaj na callback, inaczej klient Telegrama będzie czekał
    await query.answer() 
    
    chat_data = context.chat_data
    
    # --- LOGIKA COFNIJ (skopiowana z handle_message) ---
    if query.data == 'cofnij':
        logger.info("Otrzymano callback 'cofnij'")
        if not chat_data.get('odbiur_aktywny'):
            await query.message.reply_text("Nie można cofnąć. Żaden odbiór nie jest aktywny.")
            return
        
        wpisy_lista = chat_data.get('odbiur_wpisy', [])
        if not wpisy_lista:
            await query.message.reply_text("Nie można cofnąć. Lista usterek jest już pusta.")
            return

        try:
            ostatni_wpis = wpisy_lista.pop()
            chat_data['odbiur_wpisy'] = wpisy_lista
            opis_usunietego = ostatni_wpis.get('opis', 'NIEZNANY WPIS')
            
            if ostatni_wpis.get('typ') == 'zdjecie':
                file_id_to_delete = ostatni_wpis.get('file_id')
                if file_id_to_delete:
                    logger.info(f"Cofanie zdjęcia z callback. Usuwanie pliku z Drive: {file_id_to_delete}")
                    delete_success, delete_error = delete_file_from_drive(file_id_to_delete)
                    
                    if delete_success:
                        # Odpowiadamy na wiadomość i ponownie wysyłamy klawiaturę
                        await query.message.reply_text(f"↩️ Cofnięto i usunięto zdjęcie:\n'{opis_usunietego}'\n"
                                                       f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard())
                    else:
                        await query.message.reply_text(f"↩️ Cofnięto wpis: '{opis_usunietego}'.\n"
                                                       f"⚠️ BŁĄD: Nie udało się usunąć pliku z Google Drive: {delete_error}", reply_markup=get_inline_keyboard())
                else:
                     await query.message.reply_text(f"↩️ Cofnięto wpis (bez ID pliku):\n'{opis_usunietego}'\n"
                                                    f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard())
            else:
                await query.message.reply_text(f"↩️ Cofnięto wpis tekstowy:\n'{opis_usunietego}'\n"
                                                f"(Pozostało: {len(wpisy_lista)}).", reply_markup=get_inline_keyboard())
        
        except Exception as e:
            logger.error(f"Błąd podczas operacji 'cofnij' (callback): {e}")
            await query.message.reply_text(f"❌ Wystąpił błąd podczas cofania: {e}", reply_markup=get_inline_keyboard())

    # --- LOGIKA KONIEC ODBIORU (skopiowana z handle_message) ---
    elif query.data == 'koniec_odbioru':
        logger.info("Otrzymano callback 'koniec_odbioru'")
        if not chat_data.get('odbiur_aktywny'):
            await query.message.reply_text("Żaden odbiór nie jest aktywny.", reply_markup=ReplyKeyboardRemove())
            return
        
        lokal = chat_data.get('odbiur_lokal_do_arkusza')
        podmiot = chat_data.get('odbiur_podmiot')
        wpisy_lista = chat_data.get('odbiur_wpisy', [])
        
        # W callbacku nie mamy czasu wiadomości, więc bierzemy aktualny
        message_time = datetime.now() 
        
        if not wpisy_lista:
            await query.message.reply_text(f"Zakończono odbiór dla lokalu {lokal}. Nie dodano żadnych usterek.",
                                            reply_markup=ReplyKeyboardRemove())
        else:
            logger.info(f"Zapisywanie {len(wpisy_lista)} usterek dla lokalu {lokal}...")
            licznik_zapisanych = 0
            
            for wpis in wpisy_lista:
                dane_json = {
                    "numer_lokalu_budynku": lokal,
                    "rodzaj_usterki": wpis.get('opis', 'BŁĄD WPISU'),
                    "podmiot_odpowiedzialny": podmiot,
                    "link_do_zdjecia": ""
                }
                file_id_ze_zdjecia = wpis.get('file_id')
                if file_id_ze_zdjecia:
                    link_zdjecia = f"https://drive.google.com/file/d/{file_id_ze_zdjecia}/view"
                    dane_json['link_do_zdjecia'] = link_zdjecia
                
                if zapisz_w_arkuszu(dane_json, message_time):
                    licznik_zapisanych += 1
            
            await query.message.reply_text(f"✅ Zakończono odbiór.\nZapisano {licznik_zapisanych} z {len(wpisy_lista)} usterek dla lokalu {lokal}.",
                                            reply_markup=ReplyKeyboardRemove())
        
        chat_data.clear()
        
        # Po zakończeniu, edytujemy wiadomość z przyciskami, aby je usunąć
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning(f"Nie można edytować starej wiadomości (to normalne, jeśli została usunięta): {e}")


# --- 8. Uruchomienie Bota (WERSJA RAILWAY/RENDER WEBHOOK) ---
def main():
    """Główna funkcja uruchamiająca bota dla hostingu."""
    
    logger.info("Uruchamianie bota w trybie WEBHOOK...")
    
    PORT = int(os.environ.get('PORT', 8443))
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
    
    if domain:
        WEBHOOK_URL = f"https://{domain}"
        logger.info(f"Wykryto domenę Railway: {WEBHOOK_URL}")
    else:
        WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
        if not WEBHOOK_URL:
            logger.critical("BŁĄD: Nie znaleziono zmiennej RAILWAY_PUBLIC_DOMAIN ani WEBHOOK_URL!")
            exit()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # --- NOWA LINIA: Dodanie handlera dla przycisków ---
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Konfiguracja webhooka
    logger.info(f"Ustawianie webhooka na: {WEBHOOK_URL}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    )
    logger.info(f"Bot nasłuchuje na porcie {PORT}")

if __name__ == '__main__':

    main()
