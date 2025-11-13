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

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# --- 1. Konfiguracja Logowania (Ważne do debugowania) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. Ładowanie Kluczy API (z pliku .env) ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.critical("BŁĄD: Nie znaleziono tokenów (TELEGRAM_TOKEN lub GEMINI_API_KEY) w pliku .env")
    exit()

# --- 3. NOWA KONFIGURACJA (OAuth 2.0 zamiast Service Account) ---
GOOGLE_CREDENTIALS_FILE = 'credentials.json' 
GOOGLE_TOKEN_FILE = 'token.json' 
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
GOOGLE_SHEET_NAME = 'Odbiory_Kolonia_Warszawska'
WORKSHEET_NAME = 'Arkusz1'
G_DRIVE_MAIN_FOLDER_NAME = 'Lokale' 

gc = None
worksheet = None
drive_service = None
g_drive_main_folder_id = None 

def get_google_creds():
    """Obsługuje logowanie OAuth 2.0 i przechowuje token."""
    creds = None
    
    # --- SEKCJA DLA RAILWAY ---
    # Krok 1: Stwórz credentials.json ze zmiennej środowiskowej
    creds_json_string = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json_string:
        logger.info("Wykryto credentials w zmiennej środowiskowej. Zapisywanie do pliku...")
        try:
            with open(GOOGLE_CREDENTIALS_FILE, 'w') as f:
                f.write(creds_json_string)
            logger.info(f"Pomyślnie zapisano credentials w {GOOGLE_CREDENTIALS_FILE}")
        except Exception as e:
            logger.error(f"Nie można zapisać credentials ze zmiennej: {e}")
    
    # Krok 2: Stwórz token.json ze zmiennej środowiskowej
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
            logger.info("-------------------------------------------------")
            logger.info("UWAGA: Ten krok (logowanie w przeglądarce) powinien być wykonany LOKALNIE.")
            logger.info("Jeśli widzisz to na serwerze, wdrożenie się nie powiedzie.")
            logger.info("-------------------------------------------------")
            
            # Ten kod zadziała tylko, jeśli plik credentials.json został 
            # pomyślnie utworzony w Kroku 1 (powyżej)
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0) 
            except Exception as e:
                logger.critical(f"BŁĄD KRYTYCZNY PRZY AUTORYZACJI: {e}")
                logger.critical("Upewnij się, że plik 'credentials.json' istnieje lub zmienna GOOGLE_CREDENTIALS_JSON jest ustawiona.")
                exit() 

        # Zapisz zaktualizowany token (szczególnie po odświeżeniu)
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

    logger.info(f"Szukanie folderu: '{G_DRIVE_MAIN_FOLDER_NAME}'...")
    
    response_folder = drive_service.files().list(
        q=f"name='{G_DRIVE_MAIN_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=False",
        spaces='drive',
        fields='files(id, name)',
    ).execute()
    
    files = response_folder.get('files', [])
    if not files:
        logger.critical(f"BŁĄD KRYTYCZNY: Nie znaleziono folderu '{G_DRIVE_MAIN_FOLDER_NAME}' na Twoim 'Mój Dysk'!")
        logger.critical(f"Upewnij się, że utworzyłeś folder '{G_DRIVE_MAIN_FOLDER_NAME}' na głównym poziomie 'Mój Dysk'.")
        exit()
    
    g_drive_main_folder_id = files[0].get('id')
    logger.info(f"Pomyślnie znaleziono folder '{G_DRIVE_MAIN_FOLDER_NAME}' (ID: {g_drive_main_folder_id})")

except Exception as e:
    logger.critical(f"BŁĄD KRYTYCZNY: Nie można połączyć z Google: {e}")
    logger.critical("Sprawdź, czy plik 'credentials.json' istnieje i czy API są włączone.")
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
# ZMIANA: Zaktualizowano zasady 3 i 5
PROMPT_SYSTEMOWY = """
Twoim zadaniem jest analiza zgłoszenia serwisowego. Przetwórz wiadomość użytkownika i wyekstrahuj DOKŁADNIE 3 informacje: numer_lokalu_budynku, rodzaj_usterki, podmiot_odpowiedzialny.

Zawsze odpowiadaj WYŁĄCZNIE w formacie JSON, zgodnie z tym schematem:
{
  "numer_lokalu_budynku": "string",
  "rodzaj_usterki": "string",
  "podmiot_odpowiedzialny": "string"
}

Ustalenia:
1.  numer_lokalu_budynku: (np. "15", "104B", "Budynek C, klatka 2", "Lokal 46/2")
2.  rodzaj_usterki: (np. "cieknący kran", "brak prądu", "winda nie działa", "porysowana szyba")
3.  podmiot_odpowiedzialny: (np. "administracja", "serwis", "konserwator", "deweloper", "domhomegroup", "Janusz Pelc", "Michał Piskorz"). Jeśli widzisz imię i nazwisko, potraktuj je jako podmiot odpowiedzialny.
4.  Jeśli jakiejś informacji brakuje, wstaw w jej miejsce "BRAK DANYCH".
5.  Jeśli wiadomość to 'Rozpoczęcie odbioru', 'rodzaj_usterki' powinien być "Rozpoczęcie odbioru".
6.  Nigdy nie dodawaj żadnego tekstu przed ani po obiekcie JSON. Ani '```json' ani '```'.

Wiadomość użytkownika do analizy znajduje się poniżej.
"""

# --- 6. Funkcja do Zapisu w Arkuszu ---
def zapisz_w_arkuszu(dane_json: dict, data_telegram: datetime) -> bool:
    """Zapisuje przeanalizowane dane w nowym wierszu Arkusza Google."""
    try:
        data_str = data_telegram.strftime('%Y-%m-%d %H:%M:%S')
        nowy_wiersz = [
            data_str,
            dane_json.get('numer_lokalu_budynku', 'BŁĄD JSON'),
            dane_json.get('rodzaj_usterki', 'BŁĄD JSON'),
            dane_json.get('podmiot_odpowiedzialny', 'BŁĄD JSON')
        ]
        worksheet.append_row(nowy_wiersz, value_input_option='USER_ENTERED')
        logger.info(f"Dodano wiersz do arkusza: {nowy_wiersz}")
        return True
    except Exception as e:
        logger.error(f"Błąd podczas zapisu do Google Sheets: {e}")
        return False

# --- FUNKCJA WYSYŁANIA NA GOOGLE DRIVE ---
# ZMIANA: Zwraca teraz (success, message, file_id)
def upload_photo_to_drive(file_bytes, lokal_name, usterka_name, podmiot_name):
    """Wyszukuje podfolder lokalu i wysyła do niego zdjęcie. Zwraca ID pliku."""
    global drive_service, g_drive_main_folder_id
    
    try:
        # Krok 1: Znajdź podfolder dla lokalu
        q_str = f"name='{lokal_name}' and mimeType='application/vnd.google-apps.folder' and '{g_drive_main_folder_id}' in parents and trashed=False"
        
        response = drive_service.files().list(
            q=q_str, 
            spaces='drive', 
            fields='files(id, name)',
        ).execute()
        
        lokal_folder = response.get('files', [])

        if not lokal_folder:
            logger.error(f"Nie znaleziono folderu dla lokalu: {lokal_name} wewnątrz '{G_DRIVE_MAIN_FOLDER_NAME}'")
            logger.error(f"Upewnij się, że utworzyłeś podfoldery (np. '46.2') wewnątrz folderu 'Lokale' na 'Mój Dysk'.")
            return False, f"Nie znaleziono folderu Drive dla '{lokal_name}'", None

        lokal_folder_id = lokal_folder[0].get('id')
        
        # Krok 2: Przygotuj metadane i plik
        file_name = f"{usterka_name} - {podmiot_name}.jpg"
        file_metadata = {
            'name': file_name,
            'parents': [lokal_folder_id] 
        }
        
        # Krok 3: Wyślij plik
        file_bytes.seek(0)
        media = MediaIoBaseUpload(file_bytes, mimetype='image/jpeg', resumable=True)
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
        ).execute()
        
        file_id = file.get('id')
        logger.info(f"Pomyślnie wysłano plik '{file_name}' do folderu '{lokal_name}' (ID: {file_id})")
        return True, file_name, file_id
    
    except Exception as e:
        logger.error(f"Błąd podczas wysyłania na Google Drive: {e}")
        return False, str(e), None


# --- NOWOŚĆ: Funkcja do usuwania pliku z Google Drive ---
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
        # Szczególnie ważny błąd 'fileNotFound'
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

        # SCENARIUSZ 1: Użytkownik KOŃCZY odbiór
        if user_message.lower().strip() == 'koniec odbioru':
            if chat_data.get('odbiur_aktywny'):
                lokal = chat_data.get('odbiur_lokal')
                podmiot = chat_data.get('odbiur_podmiot')
                
                # ZMIANA: Korzystamy z nowej listy 'odbiur_wpisy'
                wpisy_lista = chat_data.get('odbiur_wpisy', [])
                
                if not wpisy_lista:
                    await update.message.reply_text(f"Zakończono odbiór dla lokalu {lokal}. Nie dodano żadnych usterek.")
                else:
                    logger.info(f"Zapisywanie {len(wpisy_lista)} usterek dla lokalu {lokal}...")
                    licznik_zapisanych = 0
                    
                    # ZMIANA: Iterujemy po liście słowników i wyciągamy 'opis'
                    for wpis in wpisy_lista:
                        dane_json = {
                            "numer_lokalu_budynku": lokal,
                            "rodzaj_usterki": wpis.get('opis', 'BŁĄD WPISU'), # Wyciągamy opis
                            "podmiot_odpowiedzialny": podmiot
                        }
                        if zapisz_w_arkuszu(dane_json, message_time): 
                            licznik_zapisanych += 1
                    
                    await update.message.reply_text(f"✅ Zakończono odbiór.\nZapisano {licznik_zapisanych} z {len(wpisy_lista)} usterek dla lokalu {lokal}.")
                
                chat_data.clear() 
            else:
                await update.message.reply_text("Żaden odbiór nie jest aktywny. Aby zakończyć, musisz najpierw go rozpocząć.")
            return 

        # --- NOWOŚĆ: SCENARIUSZ 1.5: Użytkownik COFA ostatnią akcję ---
        if user_message.lower().strip() == 'cofnij':
            if not chat_data.get('odbiur_aktywny'):
                await update.message.reply_text("Nie można cofnąć. Żaden odbiór nie jest aktywny.")
                return
            
            # ZMIANA: Korzystamy z 'odbiur_wpisy'
            wpisy_lista = chat_data.get('odbiur_wpisy', [])
            if not wpisy_lista:
                await update.message.reply_text("Nie można cofnąć. Lista usterek jest już pusta.")
                return

            try:
                # Usuwamy ostatni wpis z listy
                ostatni_wpis = wpisy_lista.pop()
                chat_data['odbiur_wpisy'] = wpisy_lista # Nadpisujemy listę w chat_data
                
                opis_usunietego = ostatni_wpis.get('opis', 'NIEZNANY WPIS')
                
                # Jeśli to było zdjęcie, usuwamy je też z Google Drive
                if ostatni_wpis.get('typ') == 'zdjecie':
                    file_id_to_delete = ostatni_wpis.get('file_id')
                    
                    if file_id_to_delete:
                        logger.info(f"Cofanie zdjęcia. Usuwanie pliku z Drive: {file_id_to_delete}")
                        
                        # Wywołujemy synchroniczną funkcję usuwania
                        delete_success, delete_error = delete_file_from_drive(file_id_to_delete)
                        
                        if delete_success:
                            await update.message.reply_text(f"↩️ Cofnięto i usunięto zdjęcie:\n'{opis_usunietego}'\n"
                                                            f"(Pozostało: {len(wpisy_lista)}).")
                        else:
                            # Błąd krytyczny - wpis cofnięty, ale plik został na Drive
                            await update.message.reply_text(f"↩️ Cofnięto wpis: '{opis_usunietego}'.\n"
                                                            f"⚠️ BŁĄD: Nie udało się usunąć pliku z Google Drive: {delete_error}\n"
                                                            f"Plik 'zombie' mógł pozostać na Dysku!\n"
                                                            f"(Pozostało: {len(wpisy_lista)}).")
                    else:
                        logger.warning("Wpis 'zdjecie' nie miał file_id. Usunięto tylko wpis z listy.")
                        await update.message.reply_text(f"↩️ Cofnięto wpis (bez ID pliku):\n'{opis_usunietego}'\n"
                                                        f"(Pozostało: {len(wpisy_lista)}).")
                
                # Jeśli to był tekst (lub cokolwiek innego)
                else:
                    await update.message.reply_text(f"↩️ Cofnięto wpis tekstowy:\n'{opis_usunietego}'\n"
                                                    f"(Pozostało: {len(wpisy_lista)}).")
            
            except Exception as e:
                logger.error(f"Błąd podczas operacji 'cofnij': {e}")
                await update.message.reply_text(f"❌ Wystąpił błąd podczas cofania: {e}")
            
            return # Zakończ obsługę tej wiadomości

        # SCENARIUSZ 2: Użytkownik ZACZYNA odbiór
        if user_message.lower().startswith('rozpoczęcie odbioru'):
            logger.info("Wykryto 'Rozpoczęcie odbioru', wysyłanie do Gemini po dane sesji...")
            await update.message.reply_text("Rozpoczynam odbiór... 🧠 Analizuję dane lokalu i firmy...")
            
            response = model.generate_content([PROMPT_SYSTEMOWY, user_message])
            cleaned_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            dane_startowe = json.loads(cleaned_text)
            
            lokal = dane_startowe.get('numer_lokalu_budynku')
            podmiot = dane_startowe.get('podmiot_odpowiedzialny')

            if lokal == "BRAK DANYCH" or podmiot == "BRAK DANYCH":
                 await update.message.reply_text("❌ Nie udało się rozpoznać lokalu lub firmy.\nSpróbuj ponownie, np: 'Rozpoczęcie odbioru, lokal 46/2, firma domhomegroup'.")
            else:
                lokal_normalized = lokal.lower().replace("lokal", "").strip().replace("/", ".")
                
                chat_data['odbiur_aktywny'] = True
                chat_data['odbiur_lokal'] = lokal_normalized 
                chat_data['odbiur_podmiot'] = podmiot
                
                # ZMIANA: Inicjujemy nową listę 'odbiur_wpisy'
                chat_data['odbiur_wpisy'] = [] 
                
                await update.message.reply_text(f"✅ Rozpoczęto odbiór dla:\n\nLokal: {lokal_normalized}\nFirma: {podmiot}\n\n"
                                                f"Teraz wpisuj usterki (tekst lub zdjęcia z opisem).\n"
                                                f"Wpisz 'cofnij', aby usunąć ostatni wpis.\n"
                                                f"Zakończ pisząc 'Koniec odbioru'.")
            
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
                
            # ZMIANA: Dodajemy wpis jako słownik
            nowy_wpis = {
                'typ': 'tekst',
                'opis': usterka_opis
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            # ZMIANA: Używamy len(chat_data['odbiur_wpisy'])
            await update.message.reply_text(f"➕ Dodano (tekst): '{usterka_opis}'\n"
                                            f"(Łącznie: {len(chat_data['odbiur_wpisy'])}). Wpisz kolejną, 'cofnij' lub 'Koniec odbioru'.")
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
    # (Bez zmian)
    
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
                                          f"Podmiot: {dane.get('podmiot_odpowiedzialny')}")
        else:
            await update.message.reply_text("❌ Błąd zapisu do bazy danych (Arkusza). Skontaktuj się z adminem.")

    except json.JSONDecodeError:
        logger.error(f"Błąd parsowania JSON od Gemini (fallback). Odpowiedź AI: {response.text}")
        await update.message.reply_text("❌ Błąd analizy AI (fallback). Spróbuj sformułować zgłoszenie inaczej.")
    except Exception as e:
        logger.error(f"Wystąpił nieoczekiwany błąd (fallback): {e}")
        await update.message.reply_text(f"❌ Wystąpił krytyczny błąd (fallback): {e}")


# --- 7b. NOWY HANDLER DLA ZDJĘĆ ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje zdjęcie W TRAKCIE aktywnej sesji odbioru."""
    chat_data = context.chat_data
    
    if not chat_data.get('odbiur_aktywny'):
        await update.message.reply_text("Wyślij zdjęcie *po* rozpoczęciu odbioru. Teraz ta fotka zostanie zignorowana.")
        return

    usterka = update.message.caption
    if not usterka:
        await update.message.reply_text("❌ Zdjęcie musi mieć opis (usterkę)!\nInaczej nie wiem, co zapisać. Wyślij ponownie z opisem.")
        return

    lokal = chat_data.get('odbiur_lokal')
    podmiot = chat_data.get('odbiur_podmiot')
    
    await update.message.reply_text(f"Otrzymano zdjęcie dla usterki: '{usterka}'. Przetwarzam i wysyłam na Drive...")

    try:
        photo_file = await update.message.photo[-1].get_file()
        
        file_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(file_bytes_io)
        
        # ZMIANA: Odbieramy 3 wartości, w tym file_id
        success, message, file_id = upload_photo_to_drive(file_bytes_io, lokal, usterka, podmiot)
        
        if success:
            opis_zdjecia = f"{usterka} (zdjęcie)"
            
            # ZMIANA: Tworzymy słownik wpisu dla zdjęcia
            nowy_wpis = {
                'typ': 'zdjecie',
                'opis': opis_zdjecia,
                'file_id': file_id  # Zapisujemy ID pliku na Drive
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(f"✅ Zdjęcie zapisane na Drive jako: '{message}'\n"
                                          f"➕ Usterka dodana do listy: '{opis_zdjecia}'\n"
                                          f"(Łącznie: {len(chat_data['odbiur_wpisy'])}).")
        else:
            await update.message.reply_text(f"❌ Błąd Google Drive: {message}")
            
    except Exception as e:
        logger.error(f"Błąd podczas przetwarzania zdjęcia: {e}")
        await update.message.reply_text(f"❌ Wystąpił błąd przy pobieraniu zdjęcia: {e}")


# --- 8. Uruchomienie Bota (WERSJA RAILWAY/RENDER WEBHOOK) ---
def main():
    """Główna funkcja uruchamiająca bota dla hostingu."""
    
    logger.info("Uruchamianie bota w trybie WEBHOOK...")
    
    # PORT jest ustawiany automatycznie przez Railway.
    PORT = int(os.environ.get('PORT', 8443))
    
    # Railway automatycznie ustawi nazwę domeny jako 'RAILWAY_PUBLIC_DOMAIN'
    # lub możemy ją ustawić ręcznie jako WEBHOOK_URL
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
    
    if domain:
        WEBHOOK_URL = f"https://{domain}"
        logger.info(f"Wykryto domenę Railway: {WEBHOOK_URL}")
    else:
        # Fallback, gdybyśmy musieli ustawić to ręcznie
        WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
        if not WEBHOOK_URL:
            logger.critical("BŁĄD: Nie znaleziono zmiennej RAILWAY_PUBLIC_DOMAIN ani WEBHOOK_URL!")
            exit()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Konfiguracja webhooka
    logger.info(f"Ustawianie webhooka na: {WEBHOOK_URL}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN, # Używamy tokenu jako "sekretnego" URL
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    )
    logger.info(f"Bot nasłuchuje na porcie {PORT}")

if __name__ == '__main__':
    main()